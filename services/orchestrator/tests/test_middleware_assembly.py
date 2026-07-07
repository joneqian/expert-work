"""Unit tests for :func:`build_middleware_chains` — manifest → anchor chains."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import UUID

from expert_work.persistence.token_usage_store import InMemoryTokenUsageStore
from expert_work.protocol import AgentSpec
from expert_work.runtime.llm import InMemoryRedisCache, LLMResponseCache
from expert_work.runtime.middleware import RecordingLangfuseClient, TokenUsageMiddleware
from orchestrator import MiddlewareEnv, build_middleware_chains
from orchestrator.middleware_assembly import _dynamic_context

_MINIMAL: dict[str, Any] = {
    "apiVersion": "expert_work.io/v1",
    "kind": "Agent",
    "metadata": {"name": "mw-agent", "version": "1.0.0", "tenant": "platform-eng"},
    "spec": {
        "tenant_config": {},
        "model": {"provider": "anthropic", "name": "claude-sonnet-4-6"},
        "system_prompt": {"template": "you are a test agent"},
        "sandbox": {
            "resources": {"cpu": "1.0", "memory": "1Gi"},
            "network": {"egress": "proxy", "allowlist": ["api.anthropic.com"]},
            "filesystem": {"readonly_root": True, "writable": ["/workspace"]},
        },
    },
}


def _spec(*, context_compression: dict[str, Any] | None = None) -> AgentSpec:
    doc = deepcopy(_MINIMAL)
    if context_compression is not None:
        doc["spec"]["policies"] = {"context_compression": context_compression}
    return AgentSpec.model_validate(doc)


def _redact(text: str, _tenant: UUID | None) -> str:
    return text


def _cache() -> LLMResponseCache:
    return LLMResponseCache(redis=InMemoryRedisCache())


# ---------------------------------------------------------------------------
# always-on (empty env)
# ---------------------------------------------------------------------------


def test_always_on_middlewares_wired() -> None:
    chains = build_middleware_chains(_spec(), env=MiddlewareEnv())
    # Stream HX-1 (Mini-ADR HX-A5): the E.3 view trim is opt-in — with no
    # explicit caps the before_llm_call anchor has no middleware at all.
    assert chains.before_llm_call is None
    assert chains.around_llm_call is not None
    assert chains.after_llm_call is not None
    assert chains.around_llm_call.ordered_names == ("llm_error_handling",)
    assert chains.after_llm_call.ordered_names == ("loop_detection",)


def test_no_sandbox_audit_denylist_wired() -> None:
    """The sandbox-exec call denylist was removed (audit over blocking): the
    gVisor sandbox is the real boundary and submitted code is recorded into the
    tool audit instead, so nothing binds the before_tool_dispatch anchor."""
    chains = build_middleware_chains(_spec())
    assert chains.before_tool_dispatch is None or "sandbox_audit" not in (
        chains.before_tool_dispatch.ordered_names
    )


def test_default_env_is_empty() -> None:
    """``env`` omitted behaves like an empty MiddlewareEnv."""
    chains = build_middleware_chains(_spec(context_compression={"max_turns": 20}))
    assert chains.before_llm_call is not None
    assert chains.before_llm_call.ordered_names == ("dynamic_context",)


# ---------------------------------------------------------------------------
# env-gated
# ---------------------------------------------------------------------------


def test_pii_redactor_wired_when_redact_text_present() -> None:
    chains = build_middleware_chains(
        _spec(context_compression={"max_tokens": 8000}),
        env=MiddlewareEnv(redact_text=_redact),
    )
    assert chains.before_llm_call is not None
    # dynamic_context.before=(pii_redact,) → dynamic_context sorts first.
    assert chains.before_llm_call.ordered_names == ("dynamic_context", "pii_redact")


def test_pii_redactor_wired_without_view_trim() -> None:
    """HX-A5 — env-gated middlewares stand alone when the trim is off."""
    chains = build_middleware_chains(_spec(), env=MiddlewareEnv(redact_text=_redact))
    assert chains.before_llm_call is not None
    assert chains.before_llm_call.ordered_names == ("pii_redact",)


def test_cache_middlewares_wired_when_cache_present() -> None:
    chains = build_middleware_chains(_spec(), env=MiddlewareEnv(response_cache=_cache()))
    assert chains.before_llm_call is not None
    assert chains.after_llm_call is not None
    assert "llm_cache_lookup" in chains.before_llm_call.ordered_names
    assert "llm_cache_store" in chains.after_llm_call.ordered_names


def test_cache_middlewares_skipped_when_manifest_disables() -> None:
    """Stream K.K4 — ``spec.cache.enabled: false`` opts out per manifest.

    The cache backend is wired into ``MiddlewareEnv`` once for the
    whole orchestrator, but a time-sensitive agent must be able to
    refuse caching at the manifest level. Even with ``response_cache``
    present, the lookup / store middlewares must not be attached for
    this agent.
    """
    doc = deepcopy(_MINIMAL)
    doc["spec"]["cache"] = {"enabled": False}
    spec = AgentSpec.model_validate(doc)

    chains = build_middleware_chains(spec, env=MiddlewareEnv(response_cache=_cache()))
    assert chains.before_llm_call is None  # nothing else binds the anchor
    assert chains.after_llm_call is not None
    assert "llm_cache_store" not in chains.after_llm_call.ordered_names


def test_langfuse_wired_when_client_present() -> None:
    chains = build_middleware_chains(
        _spec(), env=MiddlewareEnv(langfuse_client=RecordingLangfuseClient())
    )
    assert chains.around_llm_call is not None
    # langfuse.before=(llm_error_handling,) → langfuse sorts first.
    assert chains.around_llm_call.ordered_names == ("langfuse", "llm_error_handling")


def test_all_env_gated_middlewares_wired_together() -> None:
    chains = build_middleware_chains(
        _spec(),
        env=MiddlewareEnv(
            redact_text=_redact,
            response_cache=_cache(),
            langfuse_client=RecordingLangfuseClient(),
        ),
    )
    assert chains.before_llm_call is not None
    assert chains.after_llm_call is not None
    assert set(chains.before_llm_call.ordered_names) == {
        "pii_redact",
        "llm_cache_lookup",
    }
    assert set(chains.after_llm_call.ordered_names) == {
        "loop_detection",
        "llm_cache_store",
    }


def test_token_usage_middleware_wired_when_store_present() -> None:
    """Stream G.9 — when the env supplies a TokenUsageStore, the
    ``after_llm_call`` chain picks up the token_usage middleware bound
    to this agent's identity (name + version + model)."""
    chains = build_middleware_chains(
        _spec(),
        env=MiddlewareEnv(token_usage_store=InMemoryTokenUsageStore()),
    )
    assert chains.after_llm_call is not None
    assert "token_usage" in chains.after_llm_call.ordered_names


# ---------------------------------------------------------------------------
# manifest config
# ---------------------------------------------------------------------------


def test_dynamic_context_reads_manifest_config() -> None:
    spec = _spec(context_compression={"max_turns": 3, "max_tokens": 99})
    mw = _dynamic_context(spec)
    assert mw is not None
    assert mw.max_turns == 3
    assert mw.max_tokens == 99


def test_dynamic_context_absent_when_unconfigured() -> None:
    """HX-A5 — no explicit caps → the E.3 view trim is not built."""
    assert _dynamic_context(_spec()) is None


def test_dynamic_context_single_axis_uses_class_default() -> None:
    mw = _dynamic_context(_spec(context_compression={"max_tokens": 9000}))
    assert mw is not None
    assert mw.max_tokens == 9000
    assert mw.max_turns == 20  # unset axis falls to the constructor default


# ---------------------------------------------------------------------------
# Stream HX-1 — shared token estimator threads into the chains
# ---------------------------------------------------------------------------


class _OnePerCharEstimator:
    def count(self, text: str) -> int:
        return len(text)


def test_estimator_threads_into_dynamic_context() -> None:
    from langchain_core.messages import HumanMessage

    mw = _dynamic_context(
        _spec(context_compression={"max_tokens": 8000}), estimator=_OnePerCharEstimator()
    )
    assert mw is not None
    # 8 chars → 8 tokens through the injected estimator (default chars//4
    # heuristic would report 2).
    assert mw.token_estimator(HumanMessage(content="abcdefgh")) == 8


def test_estimator_absent_keeps_legacy_heuristic() -> None:
    from langchain_core.messages import HumanMessage

    mw = _dynamic_context(_spec(context_compression={"max_tokens": 8000}))
    assert mw is not None
    assert mw.token_estimator(HumanMessage(content="abcdefgh")) == 2


def test_estimator_threads_into_token_usage_middleware() -> None:
    estimator = _OnePerCharEstimator()
    chains = build_middleware_chains(
        _spec(),
        env=MiddlewareEnv(token_usage_store=InMemoryTokenUsageStore()),
        estimator=estimator,
    )
    assert chains.after_llm_call is not None
    token_usage = next(m for m in chains.after_llm_call._ordered if m.name == "token_usage")
    assert isinstance(token_usage, TokenUsageMiddleware)
    assert token_usage.estimator is estimator


# ---------------------------------------------------------------------------
# 3.3 — context-pressure feedback
# ---------------------------------------------------------------------------


def test_context_pressure_default_on_with_window() -> None:
    chains = build_middleware_chains(_spec(), env=MiddlewareEnv(), context_window=200_000)
    assert chains.before_llm_call is not None
    assert "context_pressure" in chains.before_llm_call.ordered_names


def test_context_pressure_skipped_without_window() -> None:
    # No resolved window → no ratio → middleware not built (default callers).
    chains = build_middleware_chains(_spec(), env=MiddlewareEnv())
    assert chains.before_llm_call is None


def test_context_pressure_opt_out() -> None:
    spec = _spec(context_compression={"pressure_feedback": False})
    chains = build_middleware_chains(spec, env=MiddlewareEnv(), context_window=200_000)
    assert chains.before_llm_call is None


def test_context_pressure_warn_pct_threaded() -> None:
    spec = _spec(context_compression={"pressure_warn_pct": 0.6})
    chains = build_middleware_chains(spec, env=MiddlewareEnv(), context_window=200_000)
    assert chains.before_llm_call is not None
    mw = next(m for m in chains.before_llm_call._ordered if m.name == "context_pressure")
    assert mw.warn_pct == 0.6
    assert mw.context_window == 200_000
