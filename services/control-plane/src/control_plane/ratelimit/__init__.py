"""Rate-limit primitives — Stream B.2.

ADR B-1 (STREAM-B-DESIGN § 3): the :class:`RateLimiter` Protocol is the
seam that the Redis-backed implementation (Stream C.6) will slot into
zero-touch. M0 ships the in-process token-bucket variant.
"""

from control_plane.ratelimit.base import RateLimitDecision, RateLimiter
from control_plane.ratelimit.in_process import InProcessTokenBucketLimiter
from control_plane.ratelimit.redis_impl import RedisTokenBucketLimiter

__all__ = [
    "InProcessTokenBucketLimiter",
    "RateLimitDecision",
    "RateLimiter",
    "RedisTokenBucketLimiter",
]
