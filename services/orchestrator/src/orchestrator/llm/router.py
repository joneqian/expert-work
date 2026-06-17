"""LLM provider fallback router — Stream E.11 + E.12.5.

:class:`LLMRouter` implements the :class:`~orchestrator.llm.caller.LLMCaller`
protocol so the ReAct graph (E.6) treats it as a single callable; under
the hood it walks a chain of :class:`ProviderHandle` entries (primary
first, then fallbacks) and falls back on **retryable** errors only.

Fallback semantics — straight from
[STREAM-E-DESIGN § 2.4](../../../../../docs/streams/STREAM-E-DESIGN.md):

- :class:`LLMClientError` (4xx) → re-raise immediately. The caller is
  malformed; the next provider would reject it for the same reason and
  waste its rate-limit budget.
- :class:`LLMServerError` / :class:`LLMRateLimitError` /
  :class:`LLMNetworkError` / :class:`CircuitOpenError` → log + continue.
- All providers exhausted → :class:`AllProvidersExhaustedError` wrapping
  the last attempt's exception for diagnostic context.

The router **does not** retry within a single provider — that's
:class:`~helix_agent.runtime.middleware.LLMErrorHandlingMiddleware`'s
job (E.4 ``around_llm_call``). E.12.5 wires the middleware chain in:

::

    LLMRouter
      → (per provider) chain.invoke("around_llm_call", ctx, terminal=provider.complete)
                       ↑
                       │  ctx.payload contains provider_key / messages /
                       │  tools / response — Mini-ADR E-13 explains why
                       │  the wrap is per-provider and not per-router
                       │  (E.4 breaker per-key isolation + langfuse
                       │  per-provider span split).

Without the middleware chain (``chain=None``), each provider gets one
attempt before fallback — the M0 unit-test path and a valid degraded
mode for early-stage runs that haven't booted the chain yet.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from langchain_core.messages import AIMessage, BaseMessage

from helix_agent.common.observability import helix_counter
from helix_agent.runtime.middleware import (
    CircuitOpenError,
    LLMAuthError,
    LLMClientError,
    LLMError,
    LLMKeyUnavailableError,
    LLMRateLimitError,
    LLMStreamStaleError,
    LLMUnauthorizedError,
    MiddlewareChain,
    MiddlewareContext,
)
from orchestrator.llm.oauth_provider import OAuthCapableProvider
from orchestrator.tools.registry import ToolSpec

logger = logging.getLogger(__name__)

# Stream L.L3 — counter for provider-level stream stale timeouts. Labeled by
# ``provider_key`` so dashboards can show which upstream is hanging.
_llm_stream_stale_total = helix_counter(
    "helix_llm_stream_stale_total",
    "Provider calls that exceeded LLMRouter.stream_deadline_s (Stream L.L3).",
    ("provider_key",),
)

# Stream L.L8 — counter for OAuth credential refresh attempts and outcomes.
# ``result`` is ``success`` when the second attempt returns a response, or
# ``fail`` when refresh itself returned False / the retry hit another 401.
_llm_auth_refresh_total = helix_counter(
    "helix_llm_auth_refresh_total",
    "Credential refreshes triggered by OAuth-capable provider 401s (Stream L.L8).",
    ("provider_key", "result"),
)


@runtime_checkable
class LLMProvider(Protocol):
    """Wire-level LLM caller — one provider, one model.

    Concrete adapters
    (:class:`~orchestrator.llm.providers.anthropic.AnthropicProvider`,
    :class:`~orchestrator.llm.providers.openai.OpenAIProvider`)
    translate :class:`BaseMessage` / :class:`ToolSpec` into the provider's
    wire format and back, raising :class:`LLMError` subclasses for
    transport / vendor failures. The router treats every
    :class:`LLMProvider` interchangeably; differences between Anthropic
    and OpenAI (system prompt placement, tool schemas, etc.) are an
    adapter concern.
    """

    async def complete(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
    ) -> AIMessage:
        """Call the upstream provider and return the LLM's response.

        Implementations MUST raise :class:`LLMError` subclasses for any
        failure (transport, 4xx, 5xx, rate-limit, parse). Letting raw
        :class:`httpx.HTTPError` / :class:`ValueError` propagate would
        defeat the router's fallback classification.
        """


@dataclass(frozen=True)
class ProviderHandle:
    """One node in the router's fallback chain.

    ``key`` identifies the upstream rate-limit bucket — typically
    ``"<provider>:<model>#<key_id>"`` (e.g. ``"anthropic:claude#1"``,
    ``"anthropic:claude#2"``). It is passed downstream as the
    ``provider_key`` payload field so E.4's
    :class:`~helix_agent.runtime.middleware.BreakerRegistry` builds
    per-key circuit breakers (Mini-ADR E-4: breakers are per upstream
    key, not per provider, because one tenant can hold multiple keys
    for the same vendor and they must fail in isolation).

    ``group`` (Stream Y-MK) ties together *sibling keys* of the same
    provider/model — typically ``"<provider>:<model>"``. The router's
    two-level fallback advances within a group on key-level failures
    (rate-limit / dead account / revoked key / open breaker) and skips
    the rest of a group on provider-level failures (5xx / network /
    timeout). Defaults to empty, in which case :func:`_group_of` falls
    back to ``key`` so a legacy single-key handle is its own singleton
    group — preserving the pre-Y-MK flat-chain behaviour exactly.
    """

    provider: LLMProvider
    key: str
    group: str = ""


# Stream Y-MK — key/account-level failures. The router advances to the next
# *sibling key* of the same provider/model on these before falling through to
# the next provider. ``LLMUnauthorizedError`` reaches ``__call__`` only as a
# non-OAuth revoked static key (``_call_one``'s OAuth refresh path has already
# run for OAuth providers), so a revoked key tries a sibling too.
# ``CircuitOpenError`` is per-key (E.4 breaker), so an open breaker on one key
# should try a sibling, not abandon the whole provider.
_KEY_LEVEL_ERRORS: tuple[type[LLMError], ...] = (
    LLMRateLimitError,
    LLMKeyUnavailableError,
    LLMUnauthorizedError,
    CircuitOpenError,
)


def _group_of(handle: ProviderHandle) -> str:
    """The sibling-key group for a handle (Stream Y-MK).

    Falls back to ``key`` when ``group`` is empty so a legacy single-key
    handle forms its own singleton group — the pre-Y-MK flat chain behaves
    identically (skipping "the rest of the group" skips only itself).
    """
    return handle.group or handle.key


class AllProvidersExhaustedError(LLMError):
    """Every :class:`ProviderHandle` in the chain failed with a retryable
    error. Wraps the **last** attempt's exception so callers (and tests)
    can inspect what finally tripped the chain.

    Inherits :class:`LLMError` so it composes with E.4 error-handling
    middleware's exception classification — wrappers can treat
    "exhausted" as terminal rather than retryable.
    """

    def __init__(self, last_exc: BaseException) -> None:
        super().__init__(
            f"all LLM providers exhausted; last error: {type(last_exc).__name__}: {last_exc}"
        )
        self.last_exc = last_exc


@dataclass
class LLMRouter:
    """Try each :class:`ProviderHandle` in order; fall back on retryable errors.

    When ``around_llm_chain`` is set, each provider call is wrapped in
    ``chain.invoke("around_llm_call", ...)`` — letting E.4 retry /
    breaker, E.5 langfuse span recording, and any future
    around-LLM-call middleware run **per provider** (Mini-ADR E-13).

    See module docstring for the full fallback semantics. The router is
    stateless — all per-provider state (breaker counters, rate-limit
    tokens) lives in middleware / inside :class:`LLMProvider` adapters
    so swapping the router or wrapping it with retries is safe.
    """

    providers: Sequence[ProviderHandle]
    around_llm_chain: MiddlewareChain | None = field(default=None)
    #: Stream L.L3 — wall-clock cap on a single provider's ``complete()``
    #: call. ``None`` or ``0`` disables the timeout (dev / long batch
    #: paths); positive values wrap each provider attempt in
    #: ``asyncio.wait_for`` and translate ``TimeoutError`` into
    #: :class:`LLMStreamStaleError` so the router falls back to the next
    #: provider rather than locking the run.
    stream_deadline_s: float | None = field(default=None)

    async def __call__(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
    ) -> AIMessage:
        handles = self.providers
        if not handles:
            raise AllProvidersExhaustedError(
                RuntimeError("LLMRouter constructed with empty provider chain")
            )

        # Stream Y-MK — two-level walk over a flat handle list:
        #   * key-level error  → next handle (sibling key first; once a
        #     provider's siblings are exhausted the index naturally reaches
        #     the next provider's group).
        #   * 400 malformed    → re-raise, no fallback (E.11 #21 unchanged).
        #   * provider-level   → skip the rest of THIS group's sibling keys
        #     and jump to the next provider (a 5xx/network/timeout hits every
        #     sibling key identically, so trying them wastes wall-clock).
        last_exc: LLMError | None = None
        n = len(handles)
        i = 0
        while i < n:
            handle = handles[i]
            try:
                return await self._call_one(handle, messages=messages, tools=tools)
            except _KEY_LEVEL_ERRORS as exc:
                last_exc = exc
                logger.warning(
                    "llm_router.key_failed idx=%d key=%s err=%s",
                    i,
                    handle.key,
                    type(exc).__name__,
                )
                i += 1
            except LLMClientError:
                logger.warning(
                    "llm_router.client_error_no_fallback idx=%d key=%s",
                    i,
                    handle.key,
                )
                raise
            except LLMError as exc:
                last_exc = exc
                group = _group_of(handle)
                logger.warning(
                    "llm_router.provider_failed idx=%d key=%s err=%s",
                    i,
                    handle.key,
                    type(exc).__name__,
                )
                i += 1
                while i < n and _group_of(handles[i]) == group:
                    i += 1

        assert last_exc is not None  # noqa: S101 - loop invariant
        raise AllProvidersExhaustedError(last_exc)

    async def _call_one(
        self,
        handle: ProviderHandle,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
    ) -> AIMessage:
        """Invoke one provider, with the Stream L.L8 OAuth refresh hook.

        The :class:`LLMUnauthorizedError` catch is the L8 entry point —
        non-OAuth providers re-raise unchanged (existing 4xx-no-fallback
        semantics); OAuth-capable providers get one refresh + retry
        before failing. See :meth:`_handle_unauthorized`.
        """
        try:
            return await self._attempt_call(handle, messages=messages, tools=tools)
        except LLMUnauthorizedError as exc:
            return await self._handle_unauthorized(
                handle, messages=messages, tools=tools, original=exc
            )

    async def _attempt_call(
        self,
        handle: ProviderHandle,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
    ) -> AIMessage:
        """One provider attempt, optionally wrapped in the around-LLM chain.

        Without ``around_llm_chain`` we just delegate to ``provider.complete``
        — the M0 unit-test path. With the chain set, build a
        :class:`MiddlewareContext` carrying ``provider_key`` + the call
        inputs, let the chain run middlewares around a terminal that
        actually calls the provider and stashes the result in
        ``ctx.payload["response"]``.
        """
        if self.around_llm_chain is None:
            result = await self._invoke_with_deadline(
                handle,
                handle.provider.complete(messages=messages, tools=tools),
            )
            assert isinstance(result, AIMessage)  # noqa: S101 - provider Protocol contract
            return result

        ctx = MiddlewareContext(
            payload={
                "provider_key": handle.key,
                "messages": list(messages),
                "tools": list(tools),
            }
        )

        async def terminal(c: MiddlewareContext) -> None:
            response = await handle.provider.complete(
                messages=c.payload["messages"],
                tools=c.payload["tools"],
            )
            c.payload["response"] = response

        await self._invoke_with_deadline(handle, self.around_llm_chain.invoke(ctx, terminal))
        response = ctx.payload.get("response")
        if not isinstance(response, AIMessage):
            # Middleware mis-handled the terminal (didn't call call_next, or
            # cleared the response) → surface clearly rather than silently
            # returning a falsy / wrong-typed value.
            raise RuntimeError(
                f"around_llm_call chain finished without populating an AIMessage "
                f"response for provider_key={handle.key!r}"
            )
        return response

    async def _handle_unauthorized(
        self,
        handle: ProviderHandle,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
        original: LLMUnauthorizedError,
    ) -> AIMessage:
        """Stream L.L8 — credential refresh + at-most-one retry.

        Non-OAuth providers re-raise the original :class:`LLMUnauthorizedError`
        so the existing 4xx-no-fallback path stays intact for static API-key
        providers. OAuth-capable providers get exactly one refresh attempt:

        * ``refresh_credentials()`` returns ``True`` → retry the call. A
          successful retry returns the response. Another 401 wraps as
          :class:`LLMAuthError` (retryable) so the outer router falls
          back to the next provider.
        * ``refresh_credentials()`` returns ``False`` (or raises) → no
          retry; raise :class:`LLMAuthError` immediately so the router
          falls back.

        Mini-ADR L-8: the router (not the provider) enforces "at most
        one refresh per call" — a buggy provider implementation cannot
        loop on persistent 401.
        """
        if not isinstance(handle.provider, OAuthCapableProvider):
            # Non-OAuth provider — preserve the existing 4xx semantics:
            # the router's outer loop re-raises LLMClientError without
            # fallback (a bad API key on Anthropic / OpenAI is a real
            # auth failure, not an expired-token recoverable).
            raise original

        try:
            refreshed = await handle.provider.refresh_credentials()
        except Exception as exc:
            # A misbehaving refresh implementation must not crash the
            # run — treat as a refresh failure (per the L8 Protocol
            # contract: "MUST NOT raise on routine paths").
            logger.warning(
                "llm_router.refresh_raised key=%s err=%s",
                handle.key,
                type(exc).__name__,
            )
            refreshed = False

        if not refreshed:
            _llm_auth_refresh_total.labels(provider_key=handle.key, result="fail").inc()
            logger.info("llm_router.refresh_failed key=%s", handle.key)
            raise LLMAuthError(
                f"provider {handle.key!r} credential refresh failed; falling back"
            ) from original

        # Refresh succeeded — retry exactly once. Another 401 means the
        # refreshed credentials are also rejected; treat as a real auth
        # failure on this provider and let the router fall back.
        try:
            result = await self._attempt_call(handle, messages=messages, tools=tools)
        except LLMUnauthorizedError as retry_exc:
            _llm_auth_refresh_total.labels(provider_key=handle.key, result="fail").inc()
            logger.info("llm_router.refresh_retry_still_401 key=%s", handle.key)
            raise LLMAuthError(
                f"provider {handle.key!r} still unauthorized after refresh; falling back"
            ) from retry_exc

        _llm_auth_refresh_total.labels(provider_key=handle.key, result="success").inc()
        logger.info("llm_router.refresh_recovered key=%s", handle.key)
        return result

    async def _invoke_with_deadline(
        self,
        handle: ProviderHandle,
        coro: Awaitable[Any],
    ) -> Any:
        """Stream L.L3 — wrap a provider invocation in ``asyncio.wait_for``.

        ``stream_deadline_s`` is per-provider (Mini-ADR L-3): a hung
        provider trips the timeout, raises :class:`LLMStreamStaleError`
        (a retryable :class:`LLMServerError` subclass), and the surrounding
        :meth:`__call__` loop falls back to the next provider rather than
        locking the run. When ``stream_deadline_s`` is ``None`` or ``0``
        the call is awaited directly (dev / long-batch path).
        """
        deadline = self.stream_deadline_s
        if deadline is None or deadline <= 0:
            return await coro
        try:
            return await asyncio.wait_for(coro, timeout=deadline)
        except TimeoutError as exc:
            _llm_stream_stale_total.labels(provider_key=handle.key).inc()
            logger.warning(
                "llm_router.stream_stale key=%s deadline_s=%.1f",
                handle.key,
                deadline,
            )
            raise LLMStreamStaleError(
                f"provider {handle.key!r} exceeded stream_deadline_s={deadline:.1f}"
            ) from exc
