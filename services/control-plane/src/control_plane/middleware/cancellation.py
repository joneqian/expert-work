"""Cancellation middleware — Stream B.3, ADR B-2.

End-to-end target: a TCP-level client disconnect must propagate into the
in-flight handler within ≤ 200 ms (verification gate #3 from
STREAM-B-DESIGN.md).

Mechanism:

1. Per-request, mint a fresh :class:`CancelToken` and stash on
   ``request.state.cancel_token`` so downstream code can ``await
   token.wait()`` or check ``token.cancelled``.
2. Spawn a background task that polls ``request.is_disconnected()`` on a
   short interval (default 50 ms). On disconnect → ``token.cancel()`` +
   record the reason on ``request.state.cancel_reason``.
3. When the handler returns, cancel the poll task cleanly so nothing
   leaks past the response.

Budget breakdown for ≤ 200 ms detection:

* 50 ms poll interval (settings)
* 50 ms scheduler / event-loop drift
* 100 ms handler ``deadline_check()`` cadence
* total ≈ 200 ms

The :class:`DeadlineMiddleware` runs **inside** this one and reuses the
same :class:`CancelToken` when seeding ``DeadlineContext`` so cancel +
deadline share one source of truth.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from helix_agent.common.deadline import CancelToken

logger = logging.getLogger("helix.control_plane.cancellation")

#: Cancellation reason published on ``request.state.cancel_reason`` when
#: the poll task observes ``http.disconnect``.
CLIENT_DISCONNECTED = "client_disconnected"


async def _poll_disconnect(
    is_disconnected: Callable[[], Awaitable[bool]],
    cancel_token: CancelToken,
    poll_interval_s: float,
    *,
    on_cancel: Callable[[], None] | None = None,
) -> None:
    """Loop until cancel or disconnect; cheap enough at 50 ms cadence."""
    while not cancel_token.cancelled:
        try:
            disconnected = await is_disconnected()
        except Exception:
            # ASGI receive can raise on shutdown; treat as disconnect.
            logger.debug("cancellation.is_disconnected_raised", exc_info=True)
            disconnected = True
        if disconnected:
            cancel_token.cancel()
            if on_cancel is not None:
                on_cancel()
            return
        await asyncio.sleep(poll_interval_s)


class CancellationMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        *,
        poll_interval_s: float = 0.05,
    ) -> None:
        super().__init__(app)
        if poll_interval_s <= 0:
            msg = f"poll_interval_s must be > 0, got {poll_interval_s}"
            raise ValueError(msg)
        self._poll_interval_s = poll_interval_s

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        cancel_token = CancelToken()
        request.state.cancel_token = cancel_token
        request.state.cancel_reason = None

        def _record_reason() -> None:
            request.state.cancel_reason = CLIENT_DISCONNECTED

        poll_task = asyncio.create_task(
            _poll_disconnect(
                request.is_disconnected,
                cancel_token,
                self._poll_interval_s,
                on_cancel=_record_reason,
            )
        )
        try:
            return await call_next(request)
        finally:
            poll_task.cancel()
            # ``asyncio.wait`` swallows ``CancelledError`` from the
            # awaited task; its return value (done/pending sets) is the
            # cleanup signal so the line isn't an ineffectual statement.
            await asyncio.wait((poll_task,))
