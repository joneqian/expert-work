"""Deadline-propagation middleware — Stream B.3.

Honors an inbound absolute deadline carried in the ``X-Expert-Work-Deadline-Ms``
header. The value is parsed as a unix-epoch millisecond integer and fed
into :func:`expert_work.common.deadline.with_deadline` so every handler
running inside the request sees it via ``get_current_deadline()``.

The :class:`CancelToken` produced by :class:`CancellationMiddleware`
(which sits **outside** this one) is reused as the deadline's cancel
source — that keeps "client disconnected" and "deadline exceeded" sharing
one truth.

Header semantics (per [STREAM-B-DESIGN ADR B-2](../../../../docs/streams/STREAM-B-DESIGN.md)):

* present + integer + future → open ``with_deadline("request", remaining, ...)``
* present but already past → reject with HTTP 504 + project envelope
* present but unparseable → log warning and ignore (best-effort; deadline
  is an optimisation, not authentication)
* absent → no deadline scope is opened
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from expert_work.common.deadline import CancelToken, with_deadline

DEADLINE_HEADER = "X-Expert-Work-Deadline-Ms"

logger = logging.getLogger("expert_work.control_plane.deadline")


def _parse_deadline_ms(raw: str) -> int | None:
    """Return the integer ms, or ``None`` if the header is malformed."""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _now_ms() -> int:
    return int(time.time() * 1000)


def _deadline_exceeded_response() -> JSONResponse:
    return JSONResponse(
        status_code=504,
        content={
            "success": False,
            "data": None,
            "error": {
                "code": "DEADLINE_EXCEEDED",
                "message": "X-Expert-Work-Deadline-Ms has already passed.",
            },
        },
    )


class DeadlineMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        raw = request.headers.get(DEADLINE_HEADER)
        if raw is None:
            return await call_next(request)

        deadline_ms = _parse_deadline_ms(raw)
        if deadline_ms is None:
            logger.warning(
                "deadline.header_malformed",
                extra={"header_value": raw[:64]},
            )
            return await call_next(request)

        remaining_ms = deadline_ms - _now_ms()
        if remaining_ms <= 0:
            logger.info(
                "deadline.already_expired",
                extra={"deadline_ms": deadline_ms, "now_ms": _now_ms()},
            )
            return _deadline_exceeded_response()

        cancel_token: CancelToken | None = getattr(request.state, "cancel_token", None)
        async with with_deadline(
            "request",
            max_ms=float(remaining_ms),
            cancel_token=cancel_token,
        ) as ctx:
            request.state.deadline_ctx = ctx
            return await call_next(request)
