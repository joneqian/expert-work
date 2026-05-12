"""Tests for :class:`control_plane.middleware.InFlightMiddleware`."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.responses import JSONResponse

from control_plane.middleware import InFlightMiddleware
from helix_agent.common.lifecycle import Lifecycle


@pytest.mark.asyncio
async def test_in_flight_counter_increments_during_request() -> None:
    lc = Lifecycle()
    lc.mark_ready()

    observed: list[int] = []

    app = FastAPI()
    app.add_middleware(InFlightMiddleware, lifecycle=lc)

    @app.get("/slow")
    async def slow() -> JSONResponse:
        observed.append(lc.in_flight)
        return JSONResponse({"ok": True})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/slow")

    assert observed == [1]
    # Counter must return to zero once the response is emitted.
    assert lc.in_flight == 0


@pytest.mark.asyncio
async def test_in_flight_counter_handles_concurrent_requests() -> None:
    lc = Lifecycle()
    lc.mark_ready()

    started = asyncio.Event()
    release = asyncio.Event()

    app = FastAPI()
    app.add_middleware(InFlightMiddleware, lifecycle=lc)

    @app.get("/gate")
    async def gate() -> JSONResponse:
        started.set()
        await release.wait()
        return JSONResponse({"ok": True})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Fire two concurrent requests; expect in_flight == 2 mid-flight.
        task_a = asyncio.create_task(client.get("/gate"))
        task_b = asyncio.create_task(client.get("/gate"))
        await started.wait()
        # Both should be in-flight now (the gate fires set() on the first
        # one, but the second is also blocked in dispatch).
        assert lc.in_flight >= 1
        release.set()
        await asyncio.gather(task_a, task_b)

    assert lc.in_flight == 0
