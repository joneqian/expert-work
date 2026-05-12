"""Tests for :class:`control_plane.middleware.CancellationMiddleware`."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from starlette.responses import JSONResponse
from starlette.types import Message, Receive, Scope, Send

from control_plane.middleware import CancellationMiddleware
from control_plane.middleware.cancellation import CLIENT_DISCONNECTED
from helix_agent.common.deadline import CancelToken

# ---------------------------------------------------------------------------
# Happy-path: a normal request leaves CancelToken intact + uncancelled.
# ---------------------------------------------------------------------------


def _build_probe_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(CancellationMiddleware, poll_interval_s=0.01)

    @app.get("/probe")
    async def probe(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "has_token": isinstance(request.state.cancel_token, CancelToken),
                "reason": request.state.cancel_reason,
                "cancelled": request.state.cancel_token.cancelled,
            }
        )

    @app.post("/echo")
    async def echo(request: Request) -> JSONResponse:
        body = await request.json()
        return JSONResponse({"received": body})

    return app


@pytest.mark.asyncio
async def test_request_state_carries_fresh_cancel_token() -> None:
    app = _build_probe_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/probe")
    body = response.json()
    assert body["has_token"] is True
    assert body["reason"] is None
    assert body["cancelled"] is False


@pytest.mark.asyncio
async def test_post_body_passes_through() -> None:
    """Regression test: B.5 caught the polling variant of this middleware
    consuming the POST body via ``request.is_disconnected()``. The
    wrapped-receive rewrite must forward every message untouched."""
    app = _build_probe_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/echo", json={"hello": "world"})
    assert response.status_code == 200
    assert response.json() == {"received": {"hello": "world"}}


# ---------------------------------------------------------------------------
# Disconnect propagation — drive ``http.disconnect`` directly via the ASGI
# interface (httpx's ASGITransport never emits one in-process).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_disconnect_message_triggers_cancel() -> None:
    captured: dict[str, Any] = {}

    async def downstream_app(scope: Scope, receive: Receive, send: Send) -> None:
        captured["token"] = scope["state"]["cancel_token"]
        # Consume the body messages first (an HTTP body always arrives
        # via ``http.request`` before any ``http.disconnect``).
        while True:
            msg = await receive()
            if msg["type"] != "http.request":
                captured["last_message_type"] = msg["type"]
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = CancellationMiddleware(downstream_app)

    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/x",
        "headers": [],
    }
    incoming_messages: list[Message] = [
        {"type": "http.request", "body": b"hi", "more_body": False},
        {"type": "http.disconnect"},
    ]

    async def receive() -> Message:
        return incoming_messages.pop(0)

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    await middleware(scope, receive, send)

    token = captured["token"]
    assert isinstance(token, CancelToken)
    assert token.cancelled is True
    assert scope["state"]["cancel_reason"] == CLIENT_DISCONNECTED
    assert captured["last_message_type"] == "http.disconnect"


@pytest.mark.asyncio
async def test_lifespan_passes_through() -> None:
    """Non-http scopes must pass straight through (no state side effects)."""
    received: list[Scope] = []

    async def downstream_app(scope: Scope, receive: Receive, send: Send) -> None:
        received.append(scope)

    middleware = CancellationMiddleware(downstream_app)
    scope: Scope = {"type": "lifespan"}

    async def receive() -> Message:
        return {"type": "lifespan.startup"}

    async def send(message: Message) -> None:
        pass

    await middleware(scope, receive, send)
    # No ``state`` key was injected on a non-http scope.
    assert received[0] is scope
    assert "state" not in scope


@pytest.mark.asyncio
async def test_poll_interval_must_be_positive() -> None:
    """Argument retained for API parity with B.3; zero / negative still rejected."""

    async def _stub(_scope: Scope, _receive: Receive, _send: Send) -> None:
        await asyncio.sleep(0)

    with pytest.raises(ValueError):
        CancellationMiddleware(_stub, poll_interval_s=0)


def test_client_disconnected_constant() -> None:
    assert CLIENT_DISCONNECTED == "client_disconnected"
