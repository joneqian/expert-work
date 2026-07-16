"""Unit tests for :class:`LLMRouter` (Stream E.11).

Covers test matrix #20 (primary fail → fallback success), #21 (4xx no
fallback), #22 (all exhausted) from STREAM-E-DESIGN § 5.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from expert_work.runtime.middleware import (
    CircuitOpenError,
    LangfuseMiddleware,
    LLMClientError,
    LLMKeyUnavailableError,
    LLMNetworkError,
    LLMRateLimitError,
    LLMServerError,
    LLMStreamStaleError,
    LLMUnauthorizedError,
    MiddlewareChain,
    RecordingLangfuseClient,
)
from orchestrator.llm import (
    AllProvidersExhaustedError,
    LLMProvider,
    LLMRouter,
    ProviderHandle,
)
from orchestrator.tools.registry import ToolSpec

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


@dataclass
class _ScriptedProvider:
    """LLMProvider stub. Either returns ``response`` or raises ``raise_with``."""

    response: AIMessage | None = None
    raise_with: BaseException | None = None
    calls: list[dict[str, object]] = field(default_factory=list)

    async def complete(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
    ) -> AIMessage:
        self.calls.append({"messages": list(messages), "tools": list(tools)})
        if self.raise_with is not None:
            raise self.raise_with
        assert self.response is not None
        return self.response


def _handle(provider: _ScriptedProvider, key: str = "test:primary") -> ProviderHandle:
    return ProviderHandle(provider=provider, key=key)


def _msgs() -> list[BaseMessage]:
    return [HumanMessage(content="hello")]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_primary_succeeds_no_fallback() -> None:
    expected = AIMessage(content="hi")
    primary = _ScriptedProvider(response=expected)
    fallback = _ScriptedProvider(response=AIMessage(content="should not be called"))

    router = LLMRouter(
        providers=[_handle(primary, "p"), _handle(fallback, "f")],
    )
    result = await router(messages=_msgs(), tools=[])

    assert result is expected
    assert len(primary.calls) == 1
    assert fallback.calls == []


# ---------------------------------------------------------------------------
# Test matrix #20 — primary fails, fallback succeeds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_server_error_falls_back() -> None:
    primary = _ScriptedProvider(raise_with=LLMServerError("anthropic 503"))
    fallback = _ScriptedProvider(response=AIMessage(content="from fallback"))
    router = LLMRouter(providers=[_handle(primary, "p"), _handle(fallback, "f")])

    result = await router(messages=_msgs(), tools=[])

    assert result.content == "from fallback"
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1


@pytest.mark.asyncio
async def test_rate_limit_falls_back() -> None:
    primary = _ScriptedProvider(raise_with=LLMRateLimitError("anthropic 429"))
    fallback = _ScriptedProvider(response=AIMessage(content="ok"))
    router = LLMRouter(providers=[_handle(primary, "p"), _handle(fallback, "f")])

    result = await router(messages=_msgs(), tools=[])

    assert result.content == "ok"


@pytest.mark.asyncio
async def test_network_error_falls_back() -> None:
    primary = _ScriptedProvider(raise_with=LLMNetworkError("connection reset"))
    fallback = _ScriptedProvider(response=AIMessage(content="ok"))
    router = LLMRouter(providers=[_handle(primary, "p"), _handle(fallback, "f")])

    result = await router(messages=_msgs(), tools=[])

    assert result.content == "ok"


@pytest.mark.asyncio
async def test_circuit_open_falls_back() -> None:
    """E.4 ``LLMErrorHandlingMiddleware`` may pre-raise ``CircuitOpenError``
    without ever calling the provider — the router still treats it as a
    fallback trigger because the upstream key is unusable."""
    primary = _ScriptedProvider(raise_with=CircuitOpenError("p"))
    fallback = _ScriptedProvider(response=AIMessage(content="ok"))
    router = LLMRouter(providers=[_handle(primary, "p"), _handle(fallback, "f")])

    result = await router(messages=_msgs(), tools=[])

    assert result.content == "ok"


@pytest.mark.asyncio
async def test_three_provider_chain_second_works() -> None:
    a = _ScriptedProvider(raise_with=LLMServerError("a 503"))
    b = _ScriptedProvider(response=AIMessage(content="b"))
    c = _ScriptedProvider(response=AIMessage(content="should not be called"))
    router = LLMRouter(
        providers=[_handle(a, "a"), _handle(b, "b"), _handle(c, "c")],
    )

    result = await router(messages=_msgs(), tools=[])

    assert result.content == "b"
    assert len(a.calls) == 1
    assert len(b.calls) == 1
    assert c.calls == []


# ---------------------------------------------------------------------------
# Test matrix #21 — 4xx never falls back
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_error_does_not_fall_back() -> None:
    primary = _ScriptedProvider(raise_with=LLMClientError("anthropic 400 bad request"))
    fallback = _ScriptedProvider(response=AIMessage(content="should not be called"))
    router = LLMRouter(providers=[_handle(primary, "p"), _handle(fallback, "f")])

    with pytest.raises(LLMClientError, match="400 bad request"):
        await router(messages=_msgs(), tools=[])

    assert fallback.calls == []


# ---------------------------------------------------------------------------
# Test matrix #22 — all providers exhausted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_providers_5xx_raises_exhausted() -> None:
    first_exc = LLMServerError("a 503")
    last_exc = LLMServerError("b 502")
    a = _ScriptedProvider(raise_with=first_exc)
    b = _ScriptedProvider(raise_with=last_exc)
    router = LLMRouter(providers=[_handle(a, "a"), _handle(b, "b")])

    with pytest.raises(AllProvidersExhaustedError) as exc_info:
        await router(messages=_msgs(), tools=[])

    assert exc_info.value.last_exc is last_exc
    assert "503" not in str(exc_info.value)  # last error attached, not first
    assert "502" in str(exc_info.value)


@pytest.mark.asyncio
async def test_empty_provider_chain_raises_exhausted_immediately() -> None:
    router = LLMRouter(providers=[])
    with pytest.raises(AllProvidersExhaustedError) as exc_info:
        await router(messages=_msgs(), tools=[])

    assert isinstance(exc_info.value.last_exc, RuntimeError)


@pytest.mark.asyncio
async def test_single_provider_5xx_raises_exhausted() -> None:
    primary = _ScriptedProvider(raise_with=LLMServerError("503"))
    router = LLMRouter(providers=[_handle(primary, "p")])

    with pytest.raises(AllProvidersExhaustedError):
        await router(messages=_msgs(), tools=[])


# ---------------------------------------------------------------------------
# Mixed-error chains — ordering preserved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mixed_retryable_errors_continue_until_success() -> None:
    a = _ScriptedProvider(raise_with=LLMRateLimitError("a 429"))
    b = _ScriptedProvider(raise_with=LLMServerError("b 503"))
    c = _ScriptedProvider(raise_with=LLMNetworkError("c reset"))
    d = _ScriptedProvider(response=AIMessage(content="finally"))
    router = LLMRouter(
        providers=[
            _handle(a, "a"),
            _handle(b, "b"),
            _handle(c, "c"),
            _handle(d, "d"),
        ],
    )

    result = await router(messages=_msgs(), tools=[])

    assert result.content == "finally"
    assert len(a.calls) == 1
    assert len(b.calls) == 1
    assert len(c.calls) == 1
    assert len(d.calls) == 1


@pytest.mark.asyncio
async def test_client_error_after_fallbacks_still_short_circuits() -> None:
    """Even mid-chain, a 4xx still aborts immediately — the next
    provider would see the same bad request."""
    a = _ScriptedProvider(raise_with=LLMServerError("a 503"))
    b = _ScriptedProvider(raise_with=LLMClientError("b 400"))
    c = _ScriptedProvider(response=AIMessage(content="should not be called"))
    router = LLMRouter(
        providers=[_handle(a, "a"), _handle(b, "b"), _handle(c, "c")],
    )

    with pytest.raises(LLMClientError):
        await router(messages=_msgs(), tools=[])

    assert c.calls == []


# ---------------------------------------------------------------------------
# Protocol contract
# ---------------------------------------------------------------------------


def test_router_satisfies_llm_provider_input_protocol() -> None:
    """Router's call signature matches :class:`LLMProvider.complete` —
    the integration with E.6 graph_builder relies on it being callable
    as an :class:`LLMCaller`."""
    # Smoke check via duck-typing: router has the expected attributes.
    router = LLMRouter(providers=[])
    assert callable(router)


def test_handle_is_frozen() -> None:
    """``ProviderHandle`` is ``frozen=True`` so it can be reused as a
    dict key / set member without mutability surprises."""
    primary = _ScriptedProvider()
    handle = ProviderHandle(provider=primary, key="x")
    with pytest.raises(Exception):  # noqa: B017 - frozen dataclass raises FrozenInstanceError
        handle.key = "y"  # type: ignore[misc]


def test_scripted_provider_satisfies_protocol() -> None:
    """Confidence check on the test helper itself."""
    assert isinstance(_ScriptedProvider(response=AIMessage(content="x")), LLMProvider)


# ---------------------------------------------------------------------------
# Stream L.L3 — provider-level stream stale-detection
# ---------------------------------------------------------------------------


@dataclass
class _HangingProvider:
    """LLMProvider stub that sleeps ``sleep_s`` before returning.

    Used by L3 tests to exercise the ``first_token_timeout_s`` wall-clock
    cap in :class:`LLMRouter._call_one`.
    """

    sleep_s: float
    response: AIMessage = field(default_factory=lambda: AIMessage(content=""))
    calls: list[dict[str, object]] = field(default_factory=list)

    async def complete(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
    ) -> AIMessage:
        self.calls.append({"messages": list(messages), "tools": list(tools)})
        await asyncio.sleep(self.sleep_s)
        return self.response


@pytest.mark.asyncio
async def test_stream_deadline_triggers_stale_error_on_single_provider() -> None:
    """A provider that hangs past the deadline raises :class:`LLMStreamStaleError`
    rather than the call locking forever."""
    hanger = _HangingProvider(sleep_s=10)
    router = LLMRouter(
        providers=[_handle(hanger, "slow")],
        first_token_timeout_s=0.05,
    )

    with pytest.raises(AllProvidersExhaustedError) as exc_info:
        await router(messages=_msgs(), tools=[])

    assert isinstance(exc_info.value.last_exc, LLMStreamStaleError)
    assert "slow" in str(exc_info.value.last_exc)
    assert len(hanger.calls) == 1


@pytest.mark.asyncio
async def test_stream_stale_falls_back_to_next_provider() -> None:
    """``LLMStreamStaleError`` inherits :class:`LLMServerError`, so the
    router treats it as retryable and tries the next provider — Mini-ADR
    L-3's central guarantee that a hung provider doesn't lock the chain."""
    primary = _HangingProvider(sleep_s=10)
    fallback = _ScriptedProvider(response=AIMessage(content="recovered"))
    router = LLMRouter(
        providers=[_handle(primary, "slow"), _handle(fallback, "fast")],
        first_token_timeout_s=0.05,
    )

    result = await router(messages=_msgs(), tools=[])

    assert result.content == "recovered"
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1


@pytest.mark.asyncio
async def test_stream_deadline_zero_disables_timeout() -> None:
    """``first_token_timeout_s=None`` (or ``0``) skips ``asyncio.wait_for`` —
    dev / long-batch paths can run without an upper bound when explicitly
    configured."""
    # A short sleep that would normally clear the 50ms deadline if
    # deadline were active — we let it complete to confirm the wait_for
    # wrap is bypassed.
    slow = _HangingProvider(sleep_s=0.1)
    router = LLMRouter(
        providers=[_handle(slow, "slow")],
        first_token_timeout_s=None,
    )
    result = await router(messages=_msgs(), tools=[])
    assert result is slow.response


@pytest.mark.asyncio
async def test_stream_deadline_zero_explicit_int_disables_timeout() -> None:
    """Manifest-level ``stream_deadline_s: 0`` propagates through
    :func:`build_step_routers` as ``None`` — but the router also accepts
    a literal ``0`` and treats it the same way (defense in depth)."""
    slow = _HangingProvider(sleep_s=0.1)
    router = LLMRouter(
        providers=[_handle(slow, "slow")],
        first_token_timeout_s=0,
    )
    result = await router(messages=_msgs(), tools=[])
    assert result is slow.response


@pytest.mark.asyncio
async def test_fast_provider_under_deadline_succeeds() -> None:
    """Happy-path regression — a provider that finishes well inside the
    deadline runs without interference from the wait_for wrap."""
    fast = _ScriptedProvider(response=AIMessage(content="quick"))
    router = LLMRouter(
        providers=[_handle(fast, "fast")],
        first_token_timeout_s=5.0,
    )
    result = await router(messages=_msgs(), tools=[])
    assert result.content == "quick"
    assert len(fast.calls) == 1


@pytest.mark.asyncio
async def test_stream_stale_emits_counter() -> None:
    """L3 verification — a stale event increments
    ``expert_work_llm_stream_stale_total{provider_key=...}``."""
    from prometheus_client import REGISTRY

    metric = "expert_work_llm_stream_stale_total"
    labels = {"provider_key": "counter-test"}
    before = REGISTRY.get_sample_value(metric, labels=labels) or 0.0

    hanger = _HangingProvider(sleep_s=10)
    router = LLMRouter(
        providers=[_handle(hanger, "counter-test")],
        first_token_timeout_s=0.05,
    )
    with pytest.raises(AllProvidersExhaustedError):
        await router(messages=_msgs(), tools=[])

    after = REGISTRY.get_sample_value(metric, labels=labels) or 0.0
    assert after == before + 1


# ---------------------------------------------------------------------------
# Stream Y-MK — per-provider multi-key, two-level failover
#
# ProviderHandle.group identifies sibling keys of the same provider/model.
# Key-level errors (rate-limit / account-dead / revoked key / circuit-open)
# advance to the next sibling key; provider-level errors (5xx / network /
# timeout) skip the remaining siblings and jump to the next provider.
# ---------------------------------------------------------------------------


def _kh(provider: _ScriptedProvider, key: str, group: str) -> ProviderHandle:
    return ProviderHandle(provider=provider, key=key, group=group)


@pytest.mark.asyncio
async def test_mk1_rate_limit_tries_sibling_key_not_next_provider() -> None:
    k1 = _ScriptedProvider(raise_with=LLMRateLimitError("p:m 429"))
    k2 = _ScriptedProvider(response=AIMessage(content="from sibling key"))
    other = _ScriptedProvider(response=AIMessage(content="should not be called"))
    router = LLMRouter(
        providers=[
            _kh(k1, "p:m#1", "p:m"),
            _kh(k2, "p:m#2", "p:m"),
            _kh(other, "q:m#1", "q:m"),
        ]
    )
    result = await router(messages=_msgs(), tools=[])
    assert result.content == "from sibling key"
    assert len(k1.calls) == 1
    assert len(k2.calls) == 1
    assert other.calls == []


@pytest.mark.asyncio
async def test_mk2_account_dead_tries_sibling_key() -> None:
    k1 = _ScriptedProvider(raise_with=LLMKeyUnavailableError("deepseek 402"))
    k2 = _ScriptedProvider(response=AIMessage(content="paid sibling"))
    router = LLMRouter(providers=[_kh(k1, "p:m#1", "p:m"), _kh(k2, "p:m#2", "p:m")])
    result = await router(messages=_msgs(), tools=[])
    assert result.content == "paid sibling"
    assert len(k1.calls) == 1
    assert len(k2.calls) == 1


@pytest.mark.asyncio
async def test_mk2b_revoked_key_tries_sibling_then_next_provider() -> None:
    """Non-OAuth 401 is now key-level (Y-MK): try sibling, then next provider."""
    k1 = _ScriptedProvider(raise_with=LLMUnauthorizedError("anthropic 401 revoked"))
    k2 = _ScriptedProvider(raise_with=LLMUnauthorizedError("anthropic 401 also revoked"))
    other = _ScriptedProvider(response=AIMessage(content="other provider"))
    router = LLMRouter(
        providers=[
            _kh(k1, "a:m#1", "a:m"),
            _kh(k2, "a:m#2", "a:m"),
            _kh(other, "b:m#1", "b:m"),
        ]
    )
    result = await router(messages=_msgs(), tools=[])
    assert result.content == "other provider"
    assert len(k1.calls) == 1
    assert len(k2.calls) == 1
    assert len(other.calls) == 1


@pytest.mark.asyncio
async def test_mk3_all_keys_dead_then_next_provider() -> None:
    a1 = _ScriptedProvider(raise_with=LLMKeyUnavailableError("a 402"))
    a2 = _ScriptedProvider(raise_with=LLMRateLimitError("a 429"))
    b1 = _ScriptedProvider(response=AIMessage(content="provider b"))
    router = LLMRouter(
        providers=[
            _kh(a1, "a:m#1", "a:m"),
            _kh(a2, "a:m#2", "a:m"),
            _kh(b1, "b:m#1", "b:m"),
        ]
    )
    result = await router(messages=_msgs(), tools=[])
    assert result.content == "provider b"
    assert len(a1.calls) == 1
    assert len(a2.calls) == 1
    assert len(b1.calls) == 1


@pytest.mark.asyncio
async def test_mk4_malformed_400_fails_fast_no_sibling_no_fallback() -> None:
    """400 malformed stays fail-fast (E.11 #21): no sibling, no provider switch."""
    a1 = _ScriptedProvider(raise_with=LLMClientError("a 400 bad tool schema"))
    a2 = _ScriptedProvider(response=AIMessage(content="sibling not tried"))
    b1 = _ScriptedProvider(response=AIMessage(content="provider not tried"))
    router = LLMRouter(
        providers=[
            _kh(a1, "a:m#1", "a:m"),
            _kh(a2, "a:m#2", "a:m"),
            _kh(b1, "b:m#1", "b:m"),
        ]
    )
    with pytest.raises(LLMClientError, match="400 bad tool schema"):
        await router(messages=_msgs(), tools=[])
    assert len(a1.calls) == 1
    assert a2.calls == []
    assert b1.calls == []


@pytest.mark.asyncio
async def test_mk5_provider_5xx_skips_siblings_jumps_to_next_provider() -> None:
    a1 = _ScriptedProvider(raise_with=LLMServerError("a 503"))
    a2 = _ScriptedProvider(response=AIMessage(content="sibling not tried"))
    b1 = _ScriptedProvider(response=AIMessage(content="provider b"))
    router = LLMRouter(
        providers=[
            _kh(a1, "a:m#1", "a:m"),
            _kh(a2, "a:m#2", "a:m"),
            _kh(b1, "b:m#1", "b:m"),
        ]
    )
    result = await router(messages=_msgs(), tools=[])
    assert result.content == "provider b"
    assert len(a1.calls) == 1
    assert a2.calls == [], "5xx is provider-level → skip sibling keys"
    assert len(b1.calls) == 1


@pytest.mark.asyncio
async def test_mk6_all_keys_all_providers_dead_raises_exhausted() -> None:
    last = LLMRateLimitError("b 429 final")
    a1 = _ScriptedProvider(raise_with=LLMKeyUnavailableError("a 402"))
    b1 = _ScriptedProvider(raise_with=last)
    router = LLMRouter(providers=[_kh(a1, "a:m#1", "a:m"), _kh(b1, "b:m#1", "b:m")])
    with pytest.raises(AllProvidersExhaustedError) as exc_info:
        await router(messages=_msgs(), tools=[])
    assert exc_info.value.last_exc is last


@pytest.mark.asyncio
async def test_mk7_single_key_default_group_regression() -> None:
    """No group set (legacy single-key handle) → key-level error has no
    sibling, so the chain exhausts exactly as before."""
    k1 = _ScriptedProvider(raise_with=LLMRateLimitError("only 429"))
    router = LLMRouter(providers=[_handle(k1, "solo")])
    with pytest.raises(AllProvidersExhaustedError):
        await router(messages=_msgs(), tools=[])
    assert len(k1.calls) == 1


# ---------------------------------------------------------------------------
# Task 4 — terminal populates ctx.payload["llm_response"] for LangfuseMiddleware
#
# Spike finding: LangfuseMiddleware._record_response_safe only records
# output/usage when ctx.payload["llm_response"] is a Mapping (langfuse.py),
# but production's terminal closure in _invoke_once only ever set
# ctx.payload["response"] (the raw AIMessage) — never "llm_response". Unit
# tests for LangfuseMiddleware hid the gap by hand-constructing
# ctx.payload["llm_response"] directly instead of driving the real
# terminal. These tests drive the real LLMRouter + terminal through a real
# LangfuseMiddleware to catch that class of gap.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terminal_populates_llm_response_for_langfuse_middleware() -> None:
    """Real terminal (not a hand-built ctx) must set llm_response so
    LangfuseMiddleware.record_output / record_usage actually fire."""
    client = RecordingLangfuseClient()
    chain = MiddlewareChain.from_middlewares("around_llm_call", [LangfuseMiddleware(client=client)])
    response = AIMessage(
        content="hello",
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )
    provider = _ScriptedProvider(response=response)
    router = LLMRouter(providers=[_handle(provider)], around_llm_chain=chain)

    result = await router(messages=_msgs(), tools=[])

    assert result is response
    assert len(client.spans) == 1
    span = client.spans[0]
    assert span.output == "hello"
    assert span.usage == {"input_tokens": 10, "output_tokens": 5}


@pytest.mark.asyncio
async def test_terminal_llm_response_flattens_block_list_content() -> None:
    """Anthropic-shaped block-list content must flatten to plain text
    for Langfuse's output field, not the raw block list."""
    client = RecordingLangfuseClient()
    chain = MiddlewareChain.from_middlewares("around_llm_call", [LangfuseMiddleware(client=client)])
    response = AIMessage(
        content=[{"type": "text", "text": "block "}, {"type": "text", "text": "answer"}]
    )
    provider = _ScriptedProvider(response=response)
    router = LLMRouter(providers=[_handle(provider)], around_llm_chain=chain)

    await router(messages=_msgs(), tools=[])

    assert client.spans[0].output == "block answer"


@pytest.mark.asyncio
async def test_terminal_llm_response_omits_usage_when_no_usage_metadata() -> None:
    """AIMessage without usage_metadata must not crash the terminal —
    usage stays an empty mapping rather than raising."""
    client = RecordingLangfuseClient()
    chain = MiddlewareChain.from_middlewares("around_llm_call", [LangfuseMiddleware(client=client)])
    response = AIMessage(content="no usage here")
    provider = _ScriptedProvider(response=response)
    router = LLMRouter(providers=[_handle(provider)], around_llm_chain=chain)

    await router(messages=_msgs(), tools=[])

    span = client.spans[0]
    assert span.output == "no usage here"
    assert span.usage == {}
