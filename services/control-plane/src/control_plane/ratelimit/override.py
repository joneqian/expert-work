"""Parse a tenant's ``rate_limit_override`` into token-bucket caps (Stream C.6).

``tenant_config.rate_limit_override`` is an operator-facing JSON knob that
overrides the default tenant request rate for one tenant:

    {"requests_per_minute": 600}          # sustained rate; burst defaults to it
    {"requests_per_minute": 600, "burst": 1200}   # higher short burst allowance

It maps to the token bucket the limiter uses: ``refill_per_sec`` = rpm / 60 (the
sustained rate) and ``capacity`` = ``burst`` (max instantaneous tokens, default
= rpm). ``parse_rate_limit_override`` validates the shape (raising ``ValueError``
so the API rejects a bad config at write time) and the middleware applies it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Sanity bounds so a typo can't disable limiting (0) or set an absurd cap.
_MIN_RPM = 1
_MAX_RPM = 10_000_000


@dataclass(frozen=True)
class RateLimitOverride:
    """A validated per-tenant rate-limit override."""

    requests_per_minute: int
    burst: int

    @property
    def capacity(self) -> int:
        """Max instantaneous tokens (the token-bucket capacity)."""
        return self.burst

    @property
    def refill_per_sec(self) -> float:
        """Sustained refill rate (tokens/sec)."""
        return self.requests_per_minute / 60.0


def _coerce_positive_int(raw: Any, field: str) -> int:
    # Reject bool explicitly — bool is an int subclass and `True`/`False`
    # should never be a valid count.
    if isinstance(raw, bool) or not isinstance(raw, int):
        msg = f"rate_limit_override.{field} must be an integer"
        raise ValueError(msg)
    return raw


def parse_rate_limit_override(raw: dict[str, Any] | None) -> RateLimitOverride | None:
    """Validate ``raw`` and return a :class:`RateLimitOverride`, or ``None`` when
    no override is set (empty / missing). Raises ``ValueError`` on a bad shape."""
    if not raw:
        return None
    rpm = _coerce_positive_int(raw.get("requests_per_minute"), "requests_per_minute")
    if not (_MIN_RPM <= rpm <= _MAX_RPM):
        msg = f"rate_limit_override.requests_per_minute must be in [{_MIN_RPM}, {_MAX_RPM}]"
        raise ValueError(msg)
    burst_raw = raw.get("burst")
    burst = rpm if burst_raw is None else _coerce_positive_int(burst_raw, "burst")
    if burst < 1 or burst > _MAX_RPM:
        msg = f"rate_limit_override.burst must be in [1, {_MAX_RPM}]"
        raise ValueError(msg)
    # Reject unknown keys so a typo (e.g. "requests_per_min") fails loudly
    # rather than silently doing nothing.
    unknown = set(raw) - {"requests_per_minute", "burst"}
    if unknown:
        msg = f"rate_limit_override has unknown keys: {sorted(unknown)}"
        raise ValueError(msg)
    return RateLimitOverride(requests_per_minute=rpm, burst=burst)
