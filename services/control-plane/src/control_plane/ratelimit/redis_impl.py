"""Redis-backed :class:`RateLimiter` — Stream C.6.

Closes ADR B-1: the gateway / tenant-tier rate limit can finally span
horizontal replicas. The implementation mirrors the in-process variant
exactly — same capacity, same refill semantics, same
:class:`RateLimitDecision` shape — but the bucket state lives in Redis
under one hash per ``(dimension, key)`` pair, mutated atomically via a
Lua script.

Key format::

    rl:{dimension}:{key}

Lua script returns ``{allowed, retry_after_ms, remaining_tokens_x1000}``
because Lua only does integer math reliably; the Python side divides
back to floats. Bucket TTL is 30 days (idle buckets eventually
disappear so memory is bounded; busy buckets are touched on every
``acquire``).

Failure modes (subsystems/16 § 6):

* Connection refused / timeout → :class:`redis.exceptions.RedisError`
  propagates. Middleware decides whether to fail-open (per-IP gateway)
  or fail-closed (per-tenant business tier).
* ``NOSCRIPT`` cache eviction → auto re-load + retry once.
"""

from __future__ import annotations

from typing import Any

import redis.asyncio as redis_async
from redis.exceptions import NoScriptError

from control_plane.ratelimit.base import RateLimitDecision

# ``rl:`` prefix keeps these keys distinguishable from quota buckets
# (``qb:``) when both share the same Redis db.
_KEY_PREFIX = "rl:"

# 30-day TTL on each bucket, in milliseconds. Idle dims age out so the
# keyspace size stays bounded.
_BUCKET_TTL_MS = 30 * 86_400 * 1_000


# KEYS[1] = bucket key
# ARGV: 1=capacity_x1000, 2=refill_per_s_x1000, 3=now_ms, 4=cost_x1000, 5=ttl_ms
# Returns {allowed(0/1), retry_after_ms, remaining_tokens_x1000}
_LUA_ACQUIRE_SOURCE = """\
-- helix-agent ratelimit bucket
local b = redis.call('HMGET', KEYS[1], 'tokens', 'last_ms')
local cap_milli = tonumber(ARGV[1])
local rate_milli = tonumber(ARGV[2])
local now_ms = tonumber(ARGV[3])
local cost_milli = tonumber(ARGV[4])
local ttl_ms = tonumber(ARGV[5])
local tokens_milli = tonumber(b[1]) or cap_milli
local last_ms = tonumber(b[2]) or now_ms
local elapsed = math.max(0, now_ms - last_ms)
tokens_milli = math.min(cap_milli, tokens_milli + elapsed * rate_milli / 1000)
if tokens_milli < cost_milli then
  local need = cost_milli - tokens_milli
  local retry_ms = math.ceil(need * 1000 / rate_milli)
  redis.call('HMSET', KEYS[1], 'tokens', tokens_milli, 'last_ms', now_ms)
  redis.call('PEXPIRE', KEYS[1], ttl_ms)
  return {0, retry_ms, math.floor(tokens_milli)}
end
tokens_milli = tokens_milli - cost_milli
redis.call('HMSET', KEYS[1], 'tokens', tokens_milli, 'last_ms', now_ms)
redis.call('PEXPIRE', KEYS[1], ttl_ms)
return {1, 0, math.floor(tokens_milli)}
"""


class RedisTokenBucketLimiter:
    """Async :class:`RateLimiter` backed by a Redis hash + Lua script."""

    def __init__(
        self,
        *,
        redis_client: redis_async.Redis,
        capacity: int,
        refill_per_sec: float,
    ) -> None:
        if capacity <= 0:
            msg = f"capacity must be > 0, got {capacity}"
            raise ValueError(msg)
        if refill_per_sec <= 0:
            msg = f"refill_per_sec must be > 0, got {refill_per_sec}"
            raise ValueError(msg)
        self._redis = redis_client
        # Lua works in integers; scale by 1000 so fractional refill
        # rates and fractional remaining tokens round-trip cleanly.
        self._capacity_milli = int(capacity * 1000)
        self._refill_milli = int(refill_per_sec * 1000)
        self._cost_milli = 1000  # 1 token per acquire
        self._lua_sha: str | None = None

    async def acquire(self, *, dimension: str, key: str) -> RateLimitDecision:
        bucket_key = f"{_KEY_PREFIX}{dimension}:{key}"
        now_ms = self._now_ms()
        argv = [
            str(self._capacity_milli),
            str(self._refill_milli),
            str(now_ms),
            str(self._cost_milli),
            str(_BUCKET_TTL_MS),
        ]
        try:
            if self._lua_sha is None:
                loaded = await self._redis.script_load(_LUA_ACQUIRE_SOURCE)  # type: ignore[misc]
                self._lua_sha = str(loaded)
            result: Any = await self._redis.evalsha(self._lua_sha, 1, bucket_key, *argv)
        except NoScriptError:
            loaded = await self._redis.script_load(_LUA_ACQUIRE_SOURCE)  # type: ignore[misc]
            self._lua_sha = str(loaded)
            result = await self._redis.evalsha(self._lua_sha, 1, bucket_key, *argv)

        allowed_raw, retry_ms_raw, remaining_milli_raw = result
        allowed = bool(int(allowed_raw))
        retry_after_s = max(0.0, int(retry_ms_raw) / 1000.0)
        remaining = max(0.0, int(remaining_milli_raw) / 1000.0)
        return RateLimitDecision(
            allowed=allowed,
            retry_after_s=retry_after_s,
            remaining=remaining,
        )

    @staticmethod
    def _now_ms() -> int:
        # Wall clock is OK here — Lua uses ``elapsed = now - last``, so
        # only the delta matters. Drift between replicas of < 1s is the
        # tradeoff for not needing redis-server time.
        import time

        return int(time.time() * 1000)


# Backwards-compat for code that imports the older suffix style.
RedisRateLimiter = RedisTokenBucketLimiter
