"""Per-provider-key token-bucket rate limiter — Stream E.12.

Wraps any :class:`~orchestrator.llm.router.LLMProvider` with an async
leaky bucket so over-limit calls **await** instead of dispatching and
getting rejected with HTTP 429. Done at the provider layer (not the
router) because:

1. **Per-key isolation** — two ``ProviderHandle`` entries for the same
   vendor (e.g. ``anthropic:primary`` + ``anthropic:fallback``) need
   independent buckets (Mini-ADR E-4: breakers / limits are per
   upstream key, not per vendor). Wrapping at provider construction
   time gives each handle its own limiter automatically.
2. **No 429 → breaker poison** — if we let the limiter trip a 429 at
   the vendor, the E.4 ``LLMErrorHandlingMiddleware`` records a
   :class:`LLMRateLimitError` and that counts toward circuit-breaker
   failure threshold. Awaiting instead keeps the breaker clean and
   the fallback chain quiet.
3. **Composability** — :class:`RateLimitedProvider` implements the
   :class:`LLMProvider` Protocol, so it stacks transparently:
   ``LLMRouter → RateLimitedProvider → AnthropicProvider`` is one
   configuration; ``LLMRouter → RateLimitedProvider → MockProvider``
   for tests is another.

Per [STREAM-E-DESIGN § 2.4 + Mini-ADR E-4](../../../../../docs/streams/STREAM-E-DESIGN.md):
"每个 ProviderHandle 内部维护 ``aiolimiter.AsyncLimiter(rate_limit_rpm, 60)``,
超限 await (不直接抛 429)". This module realises that contract.

``aiolimiter.AsyncLimiter`` implements a leaky-bucket — tokens refill
continuously at ``max_rate / time_period`` per second, not in
discrete tick boundaries. So a `2-rpm` limiter doesn't queue 60s
between the 2nd and 3rd call; it refills at 1 token per 30s.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Self

from aiolimiter import AsyncLimiter
from langchain_core.messages import AIMessage, BaseMessage

from orchestrator.llm.router import LLMProvider
from orchestrator.tools.registry import ToolSpec

logger = logging.getLogger(__name__)

#: 60-second sliding window — the canonical RPM denominator.
#: Test code that wants faster bucket cycles passes a custom
#: ``time_period_s`` to :meth:`RateLimitedProvider.with_rpm`.
DEFAULT_TIME_PERIOD_S = 60.0


@dataclass
class RateLimitedProvider:
    """Wraps an :class:`LLMProvider` with a per-instance token bucket.

    Each instance holds its own :class:`AsyncLimiter`, so creating two
    :class:`RateLimitedProvider` around the same inner provider yields
    two independent buckets — that's how primary + fallback keys for
    the same vendor stay isolated (one key getting throttled doesn't
    block the other).

    Construct via :meth:`with_rpm` for the common ``rpm-per-60s`` case;
    pass a hand-built :class:`AsyncLimiter` directly when test code
    needs a non-default ``time_period_s`` to keep tests fast.
    """

    inner: LLMProvider
    limiter: AsyncLimiter

    @classmethod
    def with_rpm(
        cls,
        inner: LLMProvider,
        *,
        rate_limit_rpm: int,
        time_period_s: float = DEFAULT_TIME_PERIOD_S,
    ) -> Self:
        """Build a :class:`RateLimitedProvider` from an RPM number.

        ``rate_limit_rpm`` tokens refill across ``time_period_s``
        seconds. Tests can shorten ``time_period_s`` to validate
        bucket behaviour without 60-second waits — the ratio is what
        matters, not the absolute window.
        """
        if rate_limit_rpm <= 0:
            raise ValueError(f"rate_limit_rpm must be positive, got {rate_limit_rpm!r}")
        if time_period_s <= 0:
            raise ValueError(f"time_period_s must be positive, got {time_period_s!r}")
        return cls(
            inner=inner,
            limiter=AsyncLimiter(max_rate=rate_limit_rpm, time_period=time_period_s),
        )

    async def complete(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
    ) -> AIMessage:
        """Acquire a token, then delegate to the wrapped provider.

        Exceptions from the inner provider propagate **unchanged** —
        the limiter only governs admission, not error semantics. This
        keeps the E.4 ``LLMErrorHandlingMiddleware`` classification +
        E.11 ``LLMRouter`` fallback logic intact.

        Acquisition is **fair** in the sense that aiolimiter wakes
        waiters in roughly arrival order, but ``aiolimiter`` does not
        guarantee strict FIFO — under high contention the throughput
        property is what we care about, not request order.
        """
        async with self.limiter:
            return await self.inner.complete(messages=messages, tools=tools)
