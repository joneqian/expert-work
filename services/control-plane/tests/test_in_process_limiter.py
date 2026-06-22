"""Unit tests for :class:`InProcessTokenBucketLimiter`."""

from __future__ import annotations

import asyncio

import pytest

from control_plane.ratelimit import InProcessTokenBucketLimiter


class _FakeClock:
    """Manually-advanced monotonic clock (ms)."""

    def __init__(self) -> None:
        self.now_ms = 0

    def advance(self, ms: int) -> None:
        self.now_ms += ms

    def __call__(self) -> int:
        return self.now_ms


def _build(
    *, capacity: int = 5, refill_per_sec: float = 1.0
) -> tuple[InProcessTokenBucketLimiter, _FakeClock]:
    clock = _FakeClock()
    limiter = InProcessTokenBucketLimiter(
        capacity=capacity,
        refill_per_sec=refill_per_sec,
        clock=clock,
    )
    return limiter, clock


@pytest.mark.asyncio
async def test_first_acquire_starts_full() -> None:
    limiter, _ = _build(capacity=3, refill_per_sec=1.0)
    decision = await limiter.acquire(dimension="ip", key="1.1.1.1")
    assert decision.allowed
    # Started full=3, consumed 1 → 2 remaining.
    assert decision.remaining == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_burst_then_deny() -> None:
    limiter, _ = _build(capacity=3, refill_per_sec=1.0)
    for _ in range(3):
        assert (await limiter.acquire(dimension="ip", key="1.1.1.1")).allowed
    denied = await limiter.acquire(dimension="ip", key="1.1.1.1")
    assert not denied.allowed
    assert denied.retry_after_s == pytest.approx(1.0, abs=0.01)


@pytest.mark.asyncio
async def test_refill_after_time_passes() -> None:
    limiter, clock = _build(capacity=2, refill_per_sec=4.0)
    await limiter.acquire(dimension="ip", key="x")
    await limiter.acquire(dimension="ip", key="x")
    assert not (await limiter.acquire(dimension="ip", key="x")).allowed
    # 250 ms at 4/s = +1 token.
    clock.advance(250)
    assert (await limiter.acquire(dimension="ip", key="x")).allowed


@pytest.mark.asyncio
async def test_keys_are_isolated() -> None:
    limiter, _ = _build(capacity=1, refill_per_sec=1.0)
    assert (await limiter.acquire(dimension="ip", key="a")).allowed
    # Same dimension, different key → independent bucket.
    assert (await limiter.acquire(dimension="ip", key="b")).allowed
    # Same key → second call denied.
    assert not (await limiter.acquire(dimension="ip", key="a")).allowed


@pytest.mark.asyncio
async def test_dimensions_are_isolated() -> None:
    limiter, _ = _build(capacity=1, refill_per_sec=1.0)
    assert (await limiter.acquire(dimension="ip", key="same")).allowed
    # apikey vs ip → distinct buckets even with identical key string.
    assert (await limiter.acquire(dimension="apikey", key="same")).allowed


@pytest.mark.asyncio
async def test_concurrent_acquire_is_safe() -> None:
    """Lock ensures the bucket never goes negative under concurrent load."""
    limiter, _ = _build(capacity=5, refill_per_sec=0.001)  # ~no refill
    decisions = await asyncio.gather(
        *(limiter.acquire(dimension="ip", key="shared") for _ in range(20))
    )
    allowed_count = sum(1 for d in decisions if d.allowed)
    assert allowed_count == 5


def test_rejects_non_positive_capacity() -> None:
    with pytest.raises(ValueError):
        InProcessTokenBucketLimiter(capacity=0, refill_per_sec=1.0)


def test_rejects_non_positive_refill_rate() -> None:
    with pytest.raises(ValueError):
        InProcessTokenBucketLimiter(capacity=1, refill_per_sec=0)


@pytest.mark.asyncio
async def test_per_call_override_tightens_below_default() -> None:
    # Stream C.6 — a per-call capacity override caps a generous limiter to a
    # small bucket for THIS key (rate_limit_override applied per tenant).
    limiter, _ = _build(capacity=100, refill_per_sec=0.001)
    d1 = await limiter.acquire(dimension="tenant", key="t1", capacity=1, refill_per_sec=0.001)
    d2 = await limiter.acquire(dimension="tenant", key="t1", capacity=1, refill_per_sec=0.001)
    assert d1.allowed is True  # bucket starts at the override capacity (1)
    assert d2.allowed is False  # second call drained the 1-token override bucket


@pytest.mark.asyncio
async def test_per_call_override_loosens_above_default() -> None:
    # A bigger override capacity lets more through than the limiter's default.
    limiter, _ = _build(capacity=1, refill_per_sec=0.001)
    allowed = 0
    for _ in range(5):
        d = await limiter.acquire(dimension="tenant", key="t2", capacity=5, refill_per_sec=0.001)
        allowed += 1 if d.allowed else 0
    assert allowed == 5  # override raised the burst from 1 → 5


@pytest.mark.asyncio
async def test_omitting_override_uses_configured_defaults() -> None:
    limiter, _ = _build(capacity=2, refill_per_sec=0.001)
    results = [(await limiter.acquire(dimension="tenant", key="t3")).allowed for _ in range(3)]
    assert results == [True, True, False]  # default capacity=2
