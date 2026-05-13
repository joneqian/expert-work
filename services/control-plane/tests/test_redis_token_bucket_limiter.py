"""Unit tests for :class:`RedisTokenBucketLimiter` — Stream C.6.

The unit tests pin the Python-side glue (Lua loading, retry on
NOSCRIPT, result decoding) using a fake redis client. The full
round-trip against real Redis is covered by the integration test in
``test_redis_token_bucket_limiter_integration.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from redis.exceptions import NoScriptError

from control_plane.ratelimit import RateLimitDecision, RedisTokenBucketLimiter


@pytest.mark.asyncio
async def test_acquire_allowed_returns_decision() -> None:
    client = AsyncMock()
    client.script_load.return_value = "sha-aabb"
    # Lua returns [allowed=1, retry_after_ms=0, remaining_milli=5500]
    client.evalsha.return_value = [1, 0, 5500]
    limiter = RedisTokenBucketLimiter(redis_client=client, capacity=10, refill_per_sec=5.0)
    decision = await limiter.acquire(dimension="tenant", key="t-1")
    assert isinstance(decision, RateLimitDecision)
    assert decision.allowed is True
    assert decision.retry_after_s == 0.0
    assert decision.remaining == 5.5


@pytest.mark.asyncio
async def test_acquire_denied_returns_retry_after() -> None:
    client = AsyncMock()
    client.script_load.return_value = "sha-bbcc"
    # 1500 ms retry, 0 tokens remaining
    client.evalsha.return_value = [0, 1500, 0]
    limiter = RedisTokenBucketLimiter(redis_client=client, capacity=10, refill_per_sec=5.0)
    decision = await limiter.acquire(dimension="tenant", key="t-1")
    assert decision.allowed is False
    assert decision.retry_after_s == 1.5
    assert decision.remaining == 0.0


@pytest.mark.asyncio
async def test_noscript_triggers_reload_and_retry() -> None:
    """``NoScriptError`` on first evalsha → reload + retry once."""
    client = AsyncMock()
    client.script_load.side_effect = ["sha-1", "sha-2"]
    client.evalsha.side_effect = [NoScriptError("evicted"), [1, 0, 1000]]
    limiter = RedisTokenBucketLimiter(redis_client=client, capacity=10, refill_per_sec=5.0)
    decision = await limiter.acquire(dimension="tenant", key="t-1")
    assert decision.allowed is True
    # Loaded twice: lazy first load + reload after NOSCRIPT.
    assert client.script_load.await_count == 2
    assert client.evalsha.await_count == 2


@pytest.mark.asyncio
async def test_key_namespacing_includes_dimension_and_key() -> None:
    """Bucket key shape ``rl:{dimension}:{key}`` keeps namespaces apart."""
    client = AsyncMock()
    client.script_load.return_value = "sha"
    client.evalsha.return_value = [1, 0, 1000]
    limiter = RedisTokenBucketLimiter(redis_client=client, capacity=10, refill_per_sec=5.0)
    await limiter.acquire(dimension="tenant", key="t-1")

    # First positional arg after sha + numkeys is the bucket key.
    args, _ = client.evalsha.call_args
    sha, numkeys, bucket_key, *_argv = args
    assert sha == "sha"
    assert numkeys == 1
    assert bucket_key == "rl:tenant:t-1"


def test_invalid_capacity_raises() -> None:
    client = AsyncMock()
    with pytest.raises(ValueError, match="capacity must be > 0"):
        RedisTokenBucketLimiter(redis_client=client, capacity=0, refill_per_sec=1.0)


def test_invalid_refill_raises() -> None:
    client = AsyncMock()
    with pytest.raises(ValueError, match="refill_per_sec must be > 0"):
        RedisTokenBucketLimiter(redis_client=client, capacity=10, refill_per_sec=0.0)
