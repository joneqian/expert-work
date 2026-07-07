"""Application-layer backpressure — Stream 16.3 (subsystems/14 § 6 补强).

OOM / fork-bomb resource exhaustion inside a sandbox is delegated to the
host cgroup (subsystems/14 § 6). This middleware is the *control-plane's own*
overload guard: it fast-fails new requests with ``429`` once too many are
already in flight, so a request flood sheds load at the edge instead of
collapsing the process (memory blowup, event-loop starvation, cascading
timeouts).

Depth signal: :attr:`Lifecycle.in_flight` — the count of requests currently
inside :class:`InFlightMiddleware` (the heavy inner chain: auth already done,
now doing DB / LLM / sandbox work). This middleware sits *outside* InFlight, so
a shed request never reaches auth / DB — the cheapest possible rejection. The
check is a soft cap (a small burst can momentarily exceed it under a race);
that is fine for overload protection, which only needs to bound, not serialise.

Health + metrics paths are exempt: liveness probes and Prometheus scrapes must
answer even while the app is shedding, otherwise the orchestrator kills a busy
(but healthy) replica and the scrape that would explain the overload is lost.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from expert_work.common.lifecycle import Lifecycle
from expert_work.common.observability import expert_work_counter

logger = logging.getLogger("expert_work.control_plane.backpressure")

_backpressure_shed = expert_work_counter(
    "expert_work_control_plane_backpressure_shed_total",
    "Requests shed by the control-plane overload guard (in-flight depth cap).",
)


class BackpressureMiddleware(BaseHTTPMiddleware):
    """Shed requests with ``429`` once in-flight depth hits the cap.

    ``max_in_flight <= 0`` disables the guard (always pass through).
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        lifecycle: Lifecycle,
        max_in_flight: int,
        retry_after_s: int = 1,
        exempt_path_prefixes: tuple[str, ...] = ("/healthz", "/metrics"),
    ) -> None:
        super().__init__(app)
        self._lifecycle = lifecycle
        self._max_in_flight = max_in_flight
        self._retry_after_s = max(1, retry_after_s)
        self._exempt = exempt_path_prefixes

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if self._max_in_flight <= 0 or request.url.path.startswith(self._exempt):
            return await call_next(request)

        if self._lifecycle.in_flight >= self._max_in_flight:
            _backpressure_shed.inc()
            logger.warning(
                "backpressure.shed",
                extra={"in_flight": self._lifecycle.in_flight, "cap": self._max_in_flight},
            )
            return JSONResponse(
                status_code=503,
                headers={"Retry-After": str(self._retry_after_s)},
                content={
                    "success": False,
                    "data": None,
                    "error": {
                        "code": "SERVER_OVERLOADED",
                        "message": "Server is shedding load; retry after a moment.",
                    },
                },
            )

        return await call_next(request)
