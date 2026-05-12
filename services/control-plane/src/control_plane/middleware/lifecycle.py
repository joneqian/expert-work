"""In-flight tracker middleware — Stream B.1.

Wraps every request in :meth:`Lifecycle.track_in_flight` so
``graceful_shutdown`` can drain on SIGTERM. Sits inside the audit-context
middleware (so the in-flight counter only includes requests that have
passed observability + audit context resolution).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from helix_agent.common.lifecycle import Lifecycle


class InFlightMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, lifecycle: Lifecycle) -> None:
        super().__init__(app)
        self._lifecycle = lifecycle

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        async with self._lifecycle.track_in_flight():
            return await call_next(request)
