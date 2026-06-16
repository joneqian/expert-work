"""Tests for :class:`control_plane.middleware.BackpressureMiddleware` (16.3)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.responses import JSONResponse

from control_plane.middleware import BackpressureMiddleware, InFlightMiddleware
from helix_agent.common.lifecycle import Lifecycle


def _app(*, max_in_flight: int, lc: Lifecycle) -> FastAPI:
    """App with the real InFlight (inner) + Backpressure (outer) stacking."""
    app = FastAPI()
    # Added inner-first: InFlight increments the depth counter Backpressure reads.
    app.add_middleware(InFlightMiddleware, lifecycle=lc)
    app.add_middleware(
        BackpressureMiddleware,
        lifecycle=lc,
        max_in_flight=max_in_flight,
        retry_after_s=2,
    )
    return app


@pytest.mark.asyncio
async def test_passes_through_under_cap() -> None:
    lc = Lifecycle()
    lc.mark_ready()
    app = _app(max_in_flight=10, lc=lc)

    @app.get("/v1/thing")
    async def thing() -> JSONResponse:
        return JSONResponse({"ok": True})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/thing")

    assert resp.status_code == 200
    assert lc.in_flight == 0


@pytest.mark.asyncio
async def test_sheds_over_cap() -> None:
    lc = Lifecycle()
    lc.mark_ready()
    app = _app(max_in_flight=1, lc=lc)

    started = asyncio.Event()
    release = asyncio.Event()

    @app.get("/v1/gate")
    async def gate() -> JSONResponse:
        started.set()
        await release.wait()
        return JSONResponse({"ok": True})

    @app.get("/v1/thing")
    async def thing() -> JSONResponse:
        return JSONResponse({"ok": True})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Hold one request in-flight (depth == 1 == cap).
        held = asyncio.create_task(client.get("/v1/gate"))
        await started.wait()
        assert lc.in_flight == 1

        # A second request must be shed.
        shed = await client.get("/v1/thing")
        assert shed.status_code == 503
        assert shed.headers["Retry-After"] == "2"
        body = shed.json()
        assert body["success"] is False
        assert body["error"]["code"] == "SERVER_OVERLOADED"

        release.set()
        held_resp = await held
        assert held_resp.status_code == 200

    assert lc.in_flight == 0


@pytest.mark.asyncio
async def test_exempt_path_never_shed() -> None:
    lc = Lifecycle()
    lc.mark_ready()
    app = _app(max_in_flight=1, lc=lc)

    started = asyncio.Event()
    release = asyncio.Event()

    @app.get("/v1/gate")
    async def gate() -> JSONResponse:
        started.set()
        await release.wait()
        return JSONResponse({"ok": True})

    @app.get("/healthz/live")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        held = asyncio.create_task(client.get("/v1/gate"))
        await started.wait()
        assert lc.in_flight == 1

        # Over cap, but health is exempt → must answer.
        health_resp = await client.get("/healthz/live")
        assert health_resp.status_code == 200

        release.set()
        _ = await held  # drain the held request (assign so CodeQL sees an effect)


@pytest.mark.asyncio
async def test_disabled_when_max_zero() -> None:
    lc = Lifecycle()
    lc.mark_ready()
    # Pretend the heavy chain is saturated; with the guard off nothing sheds.
    lc._in_flight = 99  # drive the depth signal directly in test
    app = _app(max_in_flight=0, lc=lc)

    @app.get("/v1/thing")
    async def thing() -> JSONResponse:
        return JSONResponse({"ok": True})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/thing")

    assert resp.status_code == 200
