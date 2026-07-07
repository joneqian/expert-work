"""Cancellation middleware — Stream B.3, ADR B-2.

End-to-end target: a TCP-level client disconnect must propagate into the
in-flight handler within ≤ 200 ms (verification gate #3 from
STREAM-B-DESIGN.md).

Implementation note (Starlette interop):

The original B.3 cut polled ``request.is_disconnected()`` from a
background task while sitting inside ``BaseHTTPMiddleware``. That call
issues ``await receive()``, which **consumes the next ASGI message** —
fine for GET / DELETE, but for POST / PUT it ate the request body and
the downstream handler saw an empty payload (caught by Stream B.5).

The middleware is now a **raw ASGI app** that wraps ``receive`` once,
intercepts ``http.disconnect`` messages on their natural arrival, and
forwards every body message unchanged. The :class:`CancelToken` is
published via ``scope["state"]`` so ``request.state.cancel_token`` /
``request.state.cancel_reason`` work identically to the previous
implementation.

The ``poll_interval_s`` constructor arg is kept for API compatibility
with B.3 callers but is unused — the wrapped-receive pattern reacts to
disconnect events directly, so a poll cadence is unnecessary.
"""

from __future__ import annotations

import logging

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from expert_work.common.deadline import CancelToken

logger = logging.getLogger("expert_work.control_plane.cancellation")

#: Cancellation reason published on ``request.state.cancel_reason`` when
#: the receive wrapper observes ``http.disconnect``.
CLIENT_DISCONNECTED = "client_disconnected"


class CancellationMiddleware:
    """Raw ASGI middleware: per-request CancelToken + disconnect detection."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        poll_interval_s: float = 0.05,
    ) -> None:
        if poll_interval_s <= 0:
            msg = f"poll_interval_s must be > 0, got {poll_interval_s}"
            raise ValueError(msg)
        self._app = app
        # Retained for API parity with the B.3 polling variant; the
        # wrapped-receive pattern doesn't need a cadence.
        self._poll_interval_s = poll_interval_s

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        cancel_token = CancelToken()
        # Starlette's ``Request.state`` is backed by ``scope['state']``; we
        # pre-populate that dict so ``request.state.cancel_token`` works in
        # any downstream handler.
        state_dict = scope.setdefault("state", {})
        state_dict["cancel_token"] = cancel_token
        state_dict["cancel_reason"] = None

        async def wrapped_receive() -> Message:
            message = await receive()
            if message.get("type") == "http.disconnect" and not cancel_token.cancelled:
                cancel_token.cancel()
                state_dict["cancel_reason"] = CLIENT_DISCONNECTED
                logger.debug("cancellation.client_disconnected")
            return message

        await self._app(scope, wrapped_receive, send)
