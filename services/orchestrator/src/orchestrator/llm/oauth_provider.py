"""Stream L.L8 — :class:`OAuthCapableProvider` Protocol.

LLM providers backed by OAuth (Google CloudCode, Codex with refresh
tokens, future VL providers under J.6 Path B) need a refresh path
when the access token expires — a 401 from such a provider is
recoverable by minting a new token rather than failing the call.

This module declares the minimum opt-in Protocol an
:class:`~orchestrator.llm.router.LLMProvider` implementation must
satisfy for :class:`~orchestrator.llm.router.LLMRouter` to invoke
the refresh + retry path. Static API-key providers
(:class:`~orchestrator.llm.providers.anthropic.AnthropicProvider`,
:class:`~orchestrator.llm.providers.openai.OpenAIProvider`) do **not**
implement this Protocol — their 401s are real auth failures, not
expired-token recoverable, so the router's existing 4xx-no-fallback
semantics apply unchanged.

Mini-ADR L-8 anchors:

- **Protocol over base class** — Anthropic / OpenAI providers stay
  free of refresh boilerplate they will never use.
- **Router controls refresh count** — the Protocol contract is
  "refresh + return success/failure"; the router enforces "at most
  one refresh per call" so a buggy provider implementation can't
  loop on 401.
- **No OAuth flow in L8 itself** — token endpoint client / refresh
  token persistence land with the first real OAuth provider (likely
  J.6 VL); L8 only locks the contract so the router is ready.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class OAuthCapableProvider(Protocol):
    """Marker Protocol — an :class:`LLMProvider` whose 401s may be
    recoverable through a credential refresh.

    Implementations should attempt to mint a fresh access token
    (consult the OAuth token endpoint with a stored refresh token,
    rotate a step-up assertion, etc.) and return whether the attempt
    succeeded. The router will retry the original call at most once
    after a ``True`` return; a ``False`` return tells the router to
    give up and fall back to the next provider in the chain.

    Refresh implementations MUST NOT raise on the routine "credentials
    were never valid" / "refresh-token expired" path — return ``False``
    so the router writes the right telemetry and moves on. Raising is
    reserved for genuinely unexpected programmer errors (the router
    catches and treats as ``False``).
    """

    async def refresh_credentials(self) -> bool:
        """Refresh the provider's credentials.

        Returns ``True`` if a subsequent call is likely to succeed,
        ``False`` otherwise. The router invokes this exactly once per
        ``_call_one`` invocation after observing
        :class:`~expert_work.runtime.middleware.LLMUnauthorizedError`.
        """
