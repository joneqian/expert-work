"""Gateway-tier rate-limit middleware — Stream B.2.

Implements layer 1 of the three-tier limiter stack in
subsystems/16-quota-rate-limit § 5.1 (network frontline). The other two
tiers — per-tenant business limit (C.6) and per-LLM-provider key limit
(E.6) — slot in later via the same :class:`RateLimiter` Protocol seam.

Bucket key selection:

* ``X-API-Key`` header present → ``("apikey", sha256(key)[:16])``.
  Hashing the key keeps the in-memory bucket map free of raw secrets.
* otherwise → ``("ip", request.client.host)``. We do **not** trust
  ``X-Forwarded-For`` until the nginx-fronting story lands; M0 deploys
  expose uvicorn directly.

When the bucket is empty the middleware short-circuits with HTTP 429
plus a ``Retry-After`` header and the project-wide error envelope.
"""

from __future__ import annotations

import hmac
import logging
import math
import secrets
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from control_plane.ratelimit import RateLimiter
from expert_work.common.observability import expert_work_counter

API_KEY_HEADER = "X-API-Key"

logger = logging.getLogger("expert_work.control_plane.rate_limit")

_rate_limit_decisions = expert_work_counter(
    "expert_work_control_plane_rate_limit_decisions_total",
    "Gateway rate-limit decisions by dimension and outcome.",
    ("dimension", "decision"),
)

# Process-local HMAC key used to derive bucket identifiers from raw header
# values. HMAC (rather than a bare hash) keeps the bucket map free of any
# reversible secret material: if the process is dumped, an attacker cannot
# recover the raw header without also recovering this key, and this key
# never leaves the process. Rotated implicitly on every restart.
_BUCKET_HMAC_KEY = secrets.token_bytes(32)


def _derive_bucket_id(value: str) -> str:
    """Return a stable, non-reversible 16-char id for a request-scoped value.

    Not credential storage — this is purely a bucket-index derivation, so
    HMAC-SHA-256 (fast + keyed) is the correct primitive over a slow KDF.
    """
    return hmac.new(_BUCKET_HMAC_KEY, value.encode("utf-8"), "sha256").hexdigest()[:16]


def _resolve_bucket(request: Request) -> tuple[str, str]:
    header_value = request.headers.get(API_KEY_HEADER)
    if header_value:
        return "apikey", _derive_bucket_id(header_value)
    host = request.client.host if request.client else "unknown"
    return "ip", host


def _retry_after_seconds(retry_after_s: float) -> int:
    """Round up to the next whole second (Retry-After is int per RFC 7231)."""
    if retry_after_s <= 0:
        return 1
    if math.isinf(retry_after_s):
        return 60
    return max(1, math.ceil(retry_after_s))


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: RateLimiter,
        enabled: bool = True,
    ) -> None:
        super().__init__(app)
        self._limiter = limiter
        self._enabled = enabled

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not self._enabled:
            return await call_next(request)

        dimension, key = _resolve_bucket(request)
        decision = await self._limiter.acquire(dimension=dimension, key=key)
        outcome = "allowed" if decision.allowed else "denied"
        _rate_limit_decisions.labels(dimension=dimension, decision=outcome).inc()

        if decision.allowed:
            return await call_next(request)

        retry_after = _retry_after_seconds(decision.retry_after_s)
        logger.info(
            "rate_limit.denied",
            extra={"dimension": dimension, "retry_after_s": retry_after},
        )
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(retry_after)},
            content={
                "success": False,
                "data": None,
                "error": {
                    "code": "RATE_LIMIT_EXCEEDED",
                    "message": "Rate limit exceeded; please retry later.",
                    "retry_after_s": retry_after,
                },
            },
        )
