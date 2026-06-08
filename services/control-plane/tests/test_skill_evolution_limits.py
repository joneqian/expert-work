"""Tests for the SE-7b rate limiter + circuit breaker (auto-promote guardrails)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from control_plane.skill_evolution_limits import CircuitBreaker, RateLimiter

_T0 = datetime(2026, 6, 8, 12, 0, 0, tzinfo=UTC)


def _at(seconds: int) -> datetime:
    return _T0 + timedelta(seconds=seconds)


# --------------------------------------------------------------------------- #
# RateLimiter
# --------------------------------------------------------------------------- #


def test_rate_limiter_allows_under_limit() -> None:
    rl = RateLimiter(max_per_window=2, window=timedelta(hours=1))
    assert rl.within_limit("k", _T0) is True
    rl.record("k", _T0)
    assert rl.within_limit("k", _at(1)) is True
    rl.record("k", _at(1))
    assert rl.within_limit("k", _at(2)) is False  # 2 in window == limit


def test_rate_limiter_window_prunes_old_events() -> None:
    rl = RateLimiter(max_per_window=1, window=timedelta(seconds=60))
    rl.record("k", _T0)
    assert rl.within_limit("k", _at(30)) is False
    # after the window passes, the old event drops out
    assert rl.within_limit("k", _at(61)) is True


def test_rate_limiter_keys_isolated() -> None:
    rl = RateLimiter(max_per_window=1, window=timedelta(hours=1))
    rl.record("a", _T0)
    assert rl.within_limit("a", _at(1)) is False
    assert rl.within_limit("b", _at(1)) is True


def test_rate_limiter_rejects_bad_config() -> None:
    with pytest.raises(ValueError):
        RateLimiter(max_per_window=0, window=timedelta(hours=1))
    with pytest.raises(ValueError):
        RateLimiter(max_per_window=1, window=timedelta(0))


# --------------------------------------------------------------------------- #
# CircuitBreaker
# --------------------------------------------------------------------------- #


def test_breaker_closed_below_min_samples() -> None:
    cb = CircuitBreaker(failure_threshold=0.5, min_samples=5, window=timedelta(hours=1))
    for i in range(4):
        cb.record("k", ok=False, now=_at(i))
    assert cb.is_open("k", _at(5)) is False  # only 4 samples < min


def test_breaker_opens_when_failure_rate_exceeds_threshold() -> None:
    cb = CircuitBreaker(failure_threshold=0.5, min_samples=4, window=timedelta(hours=1))
    cb.record("k", ok=False, now=_at(0))
    cb.record("k", ok=False, now=_at(1))
    cb.record("k", ok=False, now=_at(2))
    cb.record("k", ok=True, now=_at(3))
    assert cb.is_open("k", _at(4)) is True  # 3/4 = 0.75 > 0.5


def test_breaker_stays_closed_at_or_below_threshold() -> None:
    cb = CircuitBreaker(failure_threshold=0.5, min_samples=4, window=timedelta(hours=1))
    cb.record("k", ok=False, now=_at(0))
    cb.record("k", ok=False, now=_at(1))
    cb.record("k", ok=True, now=_at(2))
    cb.record("k", ok=True, now=_at(3))
    assert cb.is_open("k", _at(4)) is False  # 2/4 = 0.5, not > 0.5


def test_breaker_prunes_old_samples() -> None:
    cb = CircuitBreaker(failure_threshold=0.5, min_samples=2, window=timedelta(seconds=60))
    cb.record("k", ok=False, now=_T0)
    cb.record("k", ok=False, now=_at(10))
    assert cb.is_open("k", _at(20)) is True
    # both samples age out of the window
    assert cb.is_open("k", _at(120)) is False


def test_breaker_rejects_bad_config() -> None:
    with pytest.raises(ValueError):
        CircuitBreaker(failure_threshold=1.5, min_samples=5, window=timedelta(hours=1))
    with pytest.raises(ValueError):
        CircuitBreaker(failure_threshold=0.5, min_samples=0, window=timedelta(hours=1))
