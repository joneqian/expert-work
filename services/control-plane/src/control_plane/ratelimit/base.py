"""``RateLimiter`` Protocol — the seam between M0 in-process and M1 Redis.

Per subsystems/16 § 3.2 the gateway tier (Stream B) limits along a
``(dimension, key)`` pair: ``("ip", "1.2.3.4")`` or
``("apikey", "<hash>")``. Business-tier (Stream C.6) and provider-tier
(Stream E.6) limiters reuse the same Protocol with different dimensions
(``tenant``, ``model`` etc.).

Implementations are **async** even when synchronous internally — the
Redis impl (C.6) will await over a socket so client code shouldn't have
to branch on Protocol shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class RateLimitDecision:
    """Outcome of one ``acquire`` call.

    :attr allowed:        whether the caller may proceed
    :attr retry_after_s:  hint for the ``Retry-After`` header / 429 body;
                          ``0.0`` when ``allowed`` is ``True``
    :attr remaining:      best-effort snapshot of bucket fullness; the
                          Redis impl may emit a slightly stale value
    """

    allowed: bool
    retry_after_s: float
    remaining: float


@runtime_checkable
class RateLimiter(Protocol):
    """Take one token off the ``(dimension, key)`` bucket."""

    async def acquire(
        self,
        *,
        dimension: str,
        key: str,
        capacity: int | None = None,
        refill_per_sec: float | None = None,
    ) -> RateLimitDecision:
        """Return :class:`RateLimitDecision`; never raises on bucket miss.

        ``capacity`` / ``refill_per_sec`` override the limiter's configured
        defaults for THIS bucket (Stream C.6 per-tenant ``rate_limit_override``);
        ``None`` uses the limiter's own caps. Callers that don't do per-key
        tuning (gateway / provider tiers) simply omit them.
        """
