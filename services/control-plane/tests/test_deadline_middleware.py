"""Tests for :class:`control_plane.middleware.DeadlineMiddleware`."""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from starlette.responses import JSONResponse

from control_plane.middleware import CancellationMiddleware, DeadlineMiddleware
from control_plane.middleware.deadline import DEADLINE_HEADER
from expert_work.common.deadline import CancelToken, get_current_deadline


def _build_probe_app() -> FastAPI:
    app = FastAPI()
    # Mount Deadline (inner) inside Cancellation (outer) so cancel_token
    # is set on request.state before Deadline reads it.
    app.add_middleware(DeadlineMiddleware)
    app.add_middleware(CancellationMiddleware, poll_interval_s=0.05)

    @app.get("/probe")
    async def probe(request: Request) -> JSONResponse:
        ctx = get_current_deadline()
        deadline_ms = None
        layer = None
        shares_cancel_token = None
        if ctx is not None:
            deadline_ms = ctx.deadline_ms
            layer = ctx.layer
            shares_cancel_token = ctx.cancel_token is request.state.cancel_token
        return JSONResponse(
            {
                "has_deadline": ctx is not None,
                "deadline_ms": deadline_ms,
                "layer": layer,
                "shares_cancel_token": shares_cancel_token,
            }
        )

    return app


def _now_ms() -> int:
    return int(time.time() * 1000)


@pytest.mark.asyncio
async def test_no_header_means_no_deadline_scope() -> None:
    app = _build_probe_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/probe")
    assert response.status_code == 200
    assert response.json()["has_deadline"] is False


@pytest.mark.asyncio
async def test_future_deadline_seeds_context() -> None:
    deadline = _now_ms() + 60_000
    app = _build_probe_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/probe",
            headers={DEADLINE_HEADER: str(deadline)},
        )
    body = response.json()
    assert body["has_deadline"] is True
    assert body["layer"] == "request"
    # The middleware computes remaining_ms = deadline - now and feeds it
    # into ``with_deadline``, which then re-anchors. Tolerance covers the
    # microsecond drift between the two ``time.time()`` calls.
    assert abs(body["deadline_ms"] - deadline) < 1_000
    assert body["shares_cancel_token"] is True


@pytest.mark.asyncio
async def test_expired_deadline_returns_504() -> None:
    expired = _now_ms() - 1_000
    app = _build_probe_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/probe",
            headers={DEADLINE_HEADER: str(expired)},
        )
    assert response.status_code == 504
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "DEADLINE_EXCEEDED"


@pytest.mark.asyncio
async def test_malformed_header_is_ignored() -> None:
    app = _build_probe_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/probe", headers={DEADLINE_HEADER: "not-a-number"})
    # Malformed header is best-effort; we don't 400 the request.
    assert response.status_code == 200
    assert response.json()["has_deadline"] is False


@pytest.mark.asyncio
async def test_works_without_cancel_token_on_state() -> None:
    """When deadline runs alone (no CancellationMiddleware), the with_deadline
    scope must still open — it just mints its own CancelToken."""
    app = FastAPI()
    app.add_middleware(DeadlineMiddleware)

    @app.get("/probe")
    async def probe(request: Request) -> JSONResponse:
        ctx = get_current_deadline()
        return JSONResponse(
            {
                "has_deadline": ctx is not None,
                "has_token": ctx is not None and isinstance(ctx.cancel_token, CancelToken),
            }
        )

    deadline = _now_ms() + 5_000
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/probe", headers={DEADLINE_HEADER: str(deadline)})
    body = response.json()
    assert body["has_deadline"] is True
    assert body["has_token"] is True
