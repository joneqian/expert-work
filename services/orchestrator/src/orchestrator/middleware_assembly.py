"""Assemble per-anchor :class:`MiddlewareChain`\\s from an :class:`AgentSpec`.

STREAM-E-DESIGN Mini-ADR E-15: middleware splits into two groups.

* **always-on** — :class:`DynamicContextMiddleware`,
  :class:`LLMErrorHandlingMiddleware`, :class:`LoopDetectionMiddleware`,
  :class:`SandboxAuditMiddleware`. No platform dependency; every agent
  gets them (cost / stability / safety floor). ``SandboxAuditMiddleware``
  self-filters by tool name — a no-op until an ``exec_python`` tool
  dispatches (Stream F.4).
* **env-gated** — :class:`PIIRedactorMiddleware`,
  :class:`LLMCacheLookupMiddleware` / :class:`LLMCacheStoreMiddleware`,
  :class:`LangfuseMiddleware`. Each needs a platform runtime dep
  (redactor / cache / Langfuse client) injected via :class:`MiddlewareEnv`;
  absent the dep, the middleware is silently skipped.
"""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.messages import BaseMessage

from helix_agent.persistence.token_usage_store import TokenUsageStore
from helix_agent.protocol import AgentSpec
from helix_agent.runtime.llm.cache import LLMResponseCache
from helix_agent.runtime.middleware import (
    BreakerRegistry,
    DynamicContextMiddleware,
    LangfuseClient,
    LangfuseMiddleware,
    LLMCacheLookupMiddleware,
    LLMCacheStoreMiddleware,
    LLMErrorHandlingMiddleware,
    LoopDetectionMiddleware,
    Middleware,
    MiddlewareChain,
    PIIRedactorMiddleware,
    RedactText,
    SandboxAuditMiddleware,
    TokenUsageMiddleware,
)
from helix_agent.runtime.tokens import TokenEstimator, flatten_message

#: Mirror of :class:`DynamicContextMiddleware`'s constructor defaults —
#: used when the manifest's ``policies.context_compression`` block omits
#: a key.
_DEFAULT_MAX_TURNS = 20
_DEFAULT_MAX_TOKENS = 8000


@dataclass(frozen=True)
class MiddlewareEnv:
    """Platform runtime deps for the env-gated middlewares.

    A field left ``None`` means that middleware is not wired (Mini-ADR
    E-15). An empty ``MiddlewareEnv()`` still yields the three always-on
    middlewares.
    """

    langfuse_client: LangfuseClient | None = None
    response_cache: LLMResponseCache | None = None
    redact_text: RedactText | None = None
    #: Shared circuit-breaker registry. ``None`` → each agent gets its
    #: own; inject a shared one to pool breaker state across agents.
    breaker_registry: BreakerRegistry | None = None
    #: Stream G.9 — per-LLM-call token-usage recorder. ``None`` skips
    #: both the Prometheus counter (still defined but never incremented
    #: by this agent) and the DB persistence. M0 wires the store from
    #: ``control_plane.app``; tests can leave it unset.
    token_usage_store: TokenUsageStore | None = None


@dataclass(frozen=True)
class MiddlewareChains:
    """The four anchor chains :func:`build_agent` threads into the graph
    and the router. An anchor with no middleware is ``None`` so the graph
    keeps its no-chain fast path."""

    before_llm_call: MiddlewareChain | None
    around_llm_call: MiddlewareChain | None
    after_llm_call: MiddlewareChain | None
    before_tool_dispatch: MiddlewareChain | None


def build_middleware_chains(
    spec: AgentSpec,
    *,
    env: MiddlewareEnv | None = None,
    estimator: TokenEstimator | None = None,
) -> MiddlewareChains:
    """Build the anchor chains for ``spec`` (Mini-ADR E-15).

    ``estimator`` (Stream HX-1, Mini-ADR HX-A1) threads the shared token
    estimator into the dynamic-context trim and the token-usage drift
    metric; ``None`` keeps the legacy ``chars // 4`` heuristic.
    """
    env = env or MiddlewareEnv()
    model = spec.spec.model
    middlewares: list[Middleware] = [
        _dynamic_context(spec, estimator=estimator),
        LLMErrorHandlingMiddleware(breaker_registry=env.breaker_registry or BreakerRegistry()),
        LoopDetectionMiddleware(),
        SandboxAuditMiddleware(),
    ]

    if env.redact_text is not None:
        middlewares.append(PIIRedactorMiddleware(redact_text=env.redact_text))
    # Stream K.K4 (Mini-ADR K-3) — manifest can opt out of the LLM
    # response cache entirely. Time-sensitive agents (date / latest-news
    # / per-call randomness) must set ``spec.cache.enabled: false`` so
    # cache hits don't return stale answers. Default ``CacheSpec()`` is
    # ``enabled=True`` to preserve existing manifests.
    if env.response_cache is not None and spec.spec.cache.enabled:
        middlewares.append(
            LLMCacheLookupMiddleware(
                cache=env.response_cache,
                model=model.name,
                temperature=model.temperature,
                max_tokens=model.max_tokens,
            )
        )
        middlewares.append(
            LLMCacheStoreMiddleware(
                cache=env.response_cache,
                model=model.name,
                temperature=model.temperature,
                max_tokens=model.max_tokens,
            )
        )
    if env.langfuse_client is not None:
        middlewares.append(LangfuseMiddleware(client=env.langfuse_client))
    if env.token_usage_store is not None:
        middlewares.append(
            TokenUsageMiddleware(
                store=env.token_usage_store,
                agent_name=spec.metadata.name,
                agent_version=spec.metadata.version,
                model=model.name,
                provider=model.provider,
                estimator=estimator,
            )
        )

    return MiddlewareChains(
        before_llm_call=_chain("before_llm_call", middlewares),
        around_llm_call=_chain("around_llm_call", middlewares),
        after_llm_call=_chain("after_llm_call", middlewares),
        before_tool_dispatch=_chain("before_tool_dispatch", middlewares),
    )


def _dynamic_context(
    spec: AgentSpec,
    *,
    estimator: TokenEstimator | None = None,
) -> DynamicContextMiddleware:
    """Build the context middleware, reading ``max_turns`` / ``max_tokens``
    from the manifest's ``policies.context_compression`` block.

    Stream L.L2 — ``context_compression`` is now the
    :class:`ContextCompressionPolicy` model, not a permissive dict; the
    legacy ``max_turns`` / ``max_tokens`` keys preserved as typed
    fields so existing manifests keep loading.

    Stream HX-1 — ``estimator`` (when injected) replaces the middleware's
    default ``chars // 4`` per-message heuristic through its existing
    ``token_estimator`` seam, so all three context gates share one
    estimation basis."""
    cc = spec.spec.policies.context_compression
    if estimator is None:
        return DynamicContextMiddleware(
            max_turns=cc.max_turns,
            max_tokens=cc.max_tokens,
        )
    shared = estimator

    def _per_message(msg: BaseMessage) -> int:
        return shared.count(flatten_message(msg))

    return DynamicContextMiddleware(
        max_turns=cc.max_turns,
        max_tokens=cc.max_tokens,
        token_estimator=_per_message,
    )


def _chain(anchor: str, middlewares: list[Middleware]) -> MiddlewareChain | None:
    """A chain for ``anchor``, or ``None`` when no middleware binds there."""
    scoped = [m for m in middlewares if m.anchor == anchor]
    if not scoped:
        return None
    return MiddlewareChain.from_middlewares(anchor, scoped)
