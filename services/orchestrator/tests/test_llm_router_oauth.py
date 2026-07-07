"""Stream L.L8 — OAuth credential refresh + at-most-one retry.

Covers the path :class:`~orchestrator.llm.router.LLMRouter._handle_unauthorized`:

* :class:`OAuthCapableProvider` Protocol runtime check
* Refresh + retry succeeds → router returns the second attempt's
  response
* Refresh succeeds but retry still 401 → :class:`LLMAuthError`
  (retryable) so the outer router falls back
* Refresh returns ``False`` → :class:`LLMAuthError` immediately; no
  retry attempted
* Refresh implementation raises → treated as ``False``; no retry
* Non-OAuth provider 401 → original :class:`LLMUnauthorizedError`
  re-raised (no refresh, no fallback — preserves the existing
  4xx semantics for static API-key providers)
* ``expert_work_llm_auth_refresh_total{provider_key, result}`` counter
  emits the correct ``success`` / ``fail`` label

See [STREAM-L-DESIGN § 3.L8](../../../../docs/streams/STREAM-L-DESIGN.md)
+ Mini-ADR L-8.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from expert_work.runtime.middleware import (
    LLMAuthError,
    LLMClientError,
    LLMUnauthorizedError,
)
from orchestrator.llm import (
    AllProvidersExhaustedError,
    LLMProvider,
    LLMRouter,
    OAuthCapableProvider,
    ProviderHandle,
)
from orchestrator.tools.registry import ToolSpec

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


@dataclass
class _OAuthProvider:
    """Scripted OAuth-capable provider.

    Behaviour:

    * Each ``complete()`` call returns ``responses[idx]`` if it's an
      :class:`AIMessage`, or raises it if it's an exception.
    * ``refresh_credentials()`` returns ``refresh_result``, or raises
      ``refresh_raises`` if set.

    Implements both :class:`LLMProvider` and :class:`OAuthCapableProvider`
    so it exercises the Protocol-driven router branch.
    """

    responses: list[AIMessage | BaseException]
    refresh_result: bool = True
    refresh_raises: BaseException | None = None
    refresh_calls: int = 0
    calls: int = 0

    async def complete(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
    ) -> AIMessage:
        del messages, tools
        idx = self.calls
        self.calls += 1
        if idx >= len(self.responses):
            msg = f"_OAuthProvider ran out of scripted responses at call {idx}"
            raise RuntimeError(msg)
        item = self.responses[idx]
        if isinstance(item, BaseException):
            raise item
        return item

    async def refresh_credentials(self) -> bool:
        self.refresh_calls += 1
        if self.refresh_raises is not None:
            raise self.refresh_raises
        return self.refresh_result


@dataclass
class _StaticProvider:
    """Plain :class:`LLMProvider` that does *not* implement the OAuth
    Protocol — represents AnthropicProvider / OpenAIProvider's static
    API-key path."""

    responses: list[AIMessage | BaseException]
    calls: int = 0
    calls_log: list[dict[str, object]] = field(default_factory=list)

    async def complete(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
    ) -> AIMessage:
        self.calls_log.append({"messages": list(messages), "tools": list(tools)})
        idx = self.calls
        self.calls += 1
        if idx >= len(self.responses):
            msg = f"_StaticProvider ran out of scripted responses at call {idx}"
            raise RuntimeError(msg)
        item = self.responses[idx]
        if isinstance(item, BaseException):
            raise item
        return item


def _msgs() -> list[BaseMessage]:
    return [HumanMessage(content="hello")]


# ---------------------------------------------------------------------------
# Protocol runtime check
# ---------------------------------------------------------------------------


def test_oauth_provider_protocol_runtime_check() -> None:
    """``isinstance`` against the runtime-checkable Protocol returns
    ``True`` for an implementer and ``False`` for a static provider."""
    oauth = _OAuthProvider(responses=[])
    static = _StaticProvider(responses=[])
    assert isinstance(oauth, OAuthCapableProvider)
    assert not isinstance(static, OAuthCapableProvider)


def test_oauth_provider_still_satisfies_llm_provider_protocol() -> None:
    """An OAuth-capable provider must also be a valid ``LLMProvider``."""
    oauth = _OAuthProvider(responses=[])
    assert isinstance(oauth, LLMProvider)


# ---------------------------------------------------------------------------
# Refresh + retry success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_router_calls_refresh_on_401_and_retries() -> None:
    """The central L8 guarantee: an OAuth-capable provider's 401
    triggers a refresh + a single retry. A successful retry returns
    the response to the caller without falling back."""
    expected = AIMessage(content="recovered after refresh")
    provider = _OAuthProvider(
        responses=[LLMUnauthorizedError("provider 401: token expired"), expected],
        refresh_result=True,
    )
    router = LLMRouter(providers=[ProviderHandle(provider=provider, key="oauth:p")])

    result = await router(messages=_msgs(), tools=[])

    assert result is expected
    assert provider.calls == 2
    assert provider.refresh_calls == 1


# ---------------------------------------------------------------------------
# At-most-one retry — no 401 loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_router_does_not_retry_more_than_once_on_persistent_401() -> None:
    """If the refreshed credentials are also rejected, the router
    wraps the second 401 as ``LLMAuthError`` (retryable) and stops
    trying — Mini-ADR L-8's loop prevention."""
    provider = _OAuthProvider(
        responses=[
            LLMUnauthorizedError("provider 401: original"),
            LLMUnauthorizedError("provider 401: still no luck"),
        ],
        refresh_result=True,
    )
    router = LLMRouter(providers=[ProviderHandle(provider=provider, key="oauth:p")])

    with pytest.raises(AllProvidersExhaustedError) as exc_info:
        await router(messages=_msgs(), tools=[])

    # Single-provider chain → exhausted wraps the LLMAuthError.
    assert isinstance(exc_info.value.last_exc, LLMAuthError)
    assert "still unauthorized after refresh" in str(exc_info.value.last_exc)
    assert provider.calls == 2
    assert provider.refresh_calls == 1  # exactly one refresh


# ---------------------------------------------------------------------------
# Refresh failure path → LLMAuthError → fallback chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_returns_false_triggers_fallback() -> None:
    """A refresh that returns ``False`` means "I cannot fix this" —
    the router raises :class:`LLMAuthError` immediately (no retry)
    and the outer fallback loop tries the next provider."""

    @dataclass
    class _PassiveScripted:
        response: AIMessage
        calls: int = 0

        async def complete(
            self,
            *,
            messages: Sequence[BaseMessage],
            tools: Sequence[ToolSpec],
        ) -> AIMessage:
            del messages, tools
            self.calls += 1
            return self.response

    primary = _OAuthProvider(
        responses=[LLMUnauthorizedError("oauth 401")],
        refresh_result=False,
    )
    fallback = _PassiveScripted(response=AIMessage(content="fallback served"))
    router = LLMRouter(
        providers=[
            ProviderHandle(provider=primary, key="oauth:primary"),
            ProviderHandle(provider=fallback, key="static:fallback"),
        ]
    )

    result = await router(messages=_msgs(), tools=[])

    assert result.content == "fallback served"
    assert primary.refresh_calls == 1
    assert primary.calls == 1  # refresh failed → no retry
    assert fallback.calls == 1


@pytest.mark.asyncio
async def test_refresh_implementation_raising_is_treated_as_failure() -> None:
    """A refresh that raises must not crash the run — the router
    catches and treats it as a refresh failure (per the L8 Protocol
    contract: "MUST NOT raise on routine paths" — raising is reserved
    for programmer errors and the router defends against it)."""
    provider = _OAuthProvider(
        responses=[LLMUnauthorizedError("oauth 401")],
        refresh_raises=RuntimeError("oauth client misconfigured"),
    )
    router = LLMRouter(providers=[ProviderHandle(provider=provider, key="oauth:p")])

    with pytest.raises(AllProvidersExhaustedError) as exc_info:
        await router(messages=_msgs(), tools=[])

    assert isinstance(exc_info.value.last_exc, LLMAuthError)
    assert "credential refresh failed" in str(exc_info.value.last_exc)
    assert provider.calls == 1  # no retry happened
    assert provider.refresh_calls == 1


# ---------------------------------------------------------------------------
# Non-OAuth provider — preserve existing 4xx semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_oauth_provider_401_is_key_level_falls_to_next_handle() -> None:
    """Stream Y-MK — a non-OAuth provider's 401 (revoked static key) is now
    *key-level*: the router tries the next handle (a sibling key first, or the
    next provider when no sibling remains) rather than dying. A revoked
    Anthropic key with no sibling falls through to the next provider, whose
    credentials are independent — so a single dead key no longer locks the run.
    No OAuth refresh happens (static provider)."""
    primary = _StaticProvider(responses=[LLMUnauthorizedError("anthropic 401: bad key")])
    fallback = _StaticProvider(responses=[AIMessage(content="next provider answers")])
    router = LLMRouter(
        providers=[
            ProviderHandle(provider=primary, key="anthropic:claude#1", group="anthropic:claude"),
            ProviderHandle(provider=fallback, key="openai:gpt#1", group="openai:gpt"),
        ]
    )

    result = await router(messages=_msgs(), tools=[])

    assert result.content == "next provider answers"
    assert fallback.calls == 1


def test_unauthorized_error_is_client_error_subclass() -> None:
    """Belt-and-braces: ``LLMUnauthorizedError`` is still an
    :class:`LLMClientError` so any existing ``except LLMClientError``
    catch site keeps the same behaviour."""
    assert issubclass(LLMUnauthorizedError, LLMClientError)


# ---------------------------------------------------------------------------
# Counter emission
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_success_counter_increments() -> None:
    """``expert_work_llm_auth_refresh_total{provider_key=..., result=success}``
    increments on the refresh + retry success path."""
    from prometheus_client import REGISTRY

    metric = "expert_work_llm_auth_refresh_total"
    labels = {"provider_key": "oauth:counter-success", "result": "success"}
    before = REGISTRY.get_sample_value(metric, labels=labels) or 0.0

    provider = _OAuthProvider(
        responses=[LLMUnauthorizedError("401"), AIMessage(content="ok")],
        refresh_result=True,
    )
    router = LLMRouter(providers=[ProviderHandle(provider=provider, key="oauth:counter-success")])
    result = await router(messages=_msgs(), tools=[])
    assert isinstance(result, AIMessage)

    after = REGISTRY.get_sample_value(metric, labels=labels) or 0.0
    assert after == before + 1


@pytest.mark.asyncio
async def test_refresh_failure_counter_increments() -> None:
    """``expert_work_llm_auth_refresh_total{provider_key=..., result=fail}``
    increments on both the refresh-returns-False path and the
    retry-still-401 path."""
    from prometheus_client import REGISTRY

    metric = "expert_work_llm_auth_refresh_total"
    labels = {"provider_key": "oauth:counter-fail", "result": "fail"}
    before = REGISTRY.get_sample_value(metric, labels=labels) or 0.0

    provider = _OAuthProvider(
        responses=[LLMUnauthorizedError("401")],
        refresh_result=False,
    )
    router = LLMRouter(providers=[ProviderHandle(provider=provider, key="oauth:counter-fail")])
    with pytest.raises(AllProvidersExhaustedError):
        await router(messages=_msgs(), tools=[])

    after = REGISTRY.get_sample_value(metric, labels=labels) or 0.0
    assert after == before + 1
