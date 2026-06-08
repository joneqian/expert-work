"""Auto-promote guardrails (Stream SE, SE-7b) — rate limiter + circuit breaker.

These compute the two flags the SE-7a policy consumes:

* :class:`RateLimiter` — a per-key sliding window cap on auto-promotions, so a
  runaway loop can't flood a tenant with self-promoted skills (``within_limit``
  → SE-7a ``within_rate_limit``).
* :class:`CircuitBreaker` — opens when the recent auto-promotion failure rate
  exceeds a threshold, degrading the whole auto channel to human review
  (``is_open`` → SE-7a ``breaker_open``). This is the SE-A12 anti-runaway stop.

Both are pure: the clock is injected (``now``), so they are fully
deterministic + unit-testable. State is in-process (per worker) — adequate for
a single evolution worker; distributed enforcement is a future concern.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

__all__ = ["CircuitBreaker", "RateLimiter"]


@dataclass
class RateLimiter:
    """Per-key sliding-window cap (e.g. N auto-promotes per hour per agent)."""

    max_per_window: int
    window: timedelta
    _events: dict[str, list[datetime]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        if self.max_per_window <= 0:
            raise ValueError("max_per_window must be positive")
        if self.window <= timedelta(0):
            raise ValueError("window must be positive")

    def within_limit(self, key: str, now: datetime) -> bool:
        """True if another event for ``key`` is allowed in the current window."""
        self._prune(key, now)
        return len(self._events.get(key, [])) < self.max_per_window

    def record(self, key: str, now: datetime) -> None:
        """Record an event (call after an auto-promote actually happens)."""
        self._events.setdefault(key, []).append(now)

    def _prune(self, key: str, now: datetime) -> None:
        cutoff = now - self.window
        events = self._events.get(key)
        if events is not None:
            self._events[key] = [t for t in events if t > cutoff]


@dataclass
class CircuitBreaker:
    """Opens when the windowed failure rate for a key exceeds a threshold."""

    failure_threshold: float
    min_samples: int
    window: timedelta
    _samples: dict[str, list[tuple[datetime, bool]]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        if not 0.0 <= self.failure_threshold <= 1.0:
            raise ValueError("failure_threshold must be in [0, 1]")
        if self.min_samples < 1:
            raise ValueError("min_samples must be >= 1")
        if self.window <= timedelta(0):
            raise ValueError("window must be positive")

    def record(self, key: str, *, ok: bool, now: datetime) -> None:
        """Record an auto-promotion outcome (``ok`` = the skill held up)."""
        self._samples.setdefault(key, []).append((now, ok))

    def is_open(self, key: str, now: datetime) -> bool:
        """True if the breaker is open for ``key`` (degrade to human review)."""
        self._prune(key, now)
        samples = self._samples.get(key, [])
        if len(samples) < self.min_samples:
            return False
        failures = sum(1 for _, ok in samples if not ok)
        return (failures / len(samples)) > self.failure_threshold

    def _prune(self, key: str, now: datetime) -> None:
        cutoff = now - self.window
        samples = self._samples.get(key)
        if samples is not None:
            self._samples[key] = [(t, ok) for t, ok in samples if t > cutoff]
