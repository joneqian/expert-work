"""Tests for :class:`control_plane.middleware.CancellationMiddleware`."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from starlette.responses import JSONResponse

from control_plane.middleware import CancellationMiddleware
from control_plane.middleware.cancellation import CLIENT_DISCONNECTED, _poll_disconnect
from helix_agent.common.deadline import CancelToken

# ---------------------------------------------------------------------------
# _poll_disconnect unit tests — drive the loop with plain async functions.
#
# CodeQL's type tracer does not recognise class instances whose only
# callability comes from ``__call__`` as ``Callable[[], Awaitable[bool]]``;
# stubs here are therefore plain ``async def`` closures.
# ---------------------------------------------------------------------------


def _disconnect_source(flag: list[bool]) -> Callable[[], Awaitable[bool]]:
    """Return an async callable that reads from a single-element list."""

    async def _check() -> bool:
        return flag[0]

    return _check


async def _always_raising_disconnect_source() -> bool:
    raise RuntimeError("ASGI receive blew up")


@pytest.mark.asyncio
async def test_poll_cancels_token_when_disconnect_observed() -> None:
    token = CancelToken()
    flag = [False]
    poll_task = asyncio.create_task(
        _poll_disconnect(_disconnect_source(flag), token, poll_interval_s=0.005)
    )
    await asyncio.sleep(0.02)
    # Sanity: poll has been running without a disconnect.
    assert not poll_task.done()
    flag[0] = True
    # Within ~one poll interval the poll must finish and the token must flip.
    await asyncio.wait_for(poll_task, timeout=0.5)
    assert token.cancelled is True


@pytest.mark.asyncio
async def test_poll_exits_quickly_when_token_already_cancelled() -> None:
    token = CancelToken()
    token.cancel()
    flag = [False]
    start = time.perf_counter()
    await asyncio.wait_for(
        _poll_disconnect(_disconnect_source(flag), token, poll_interval_s=10.0),
        timeout=0.1,
    )
    elapsed = time.perf_counter() - start
    # The loop checks the cancellation flag *before* awaiting, so it
    # short-circuits immediately even though the sleep was 10 s.
    assert elapsed < 0.05


@pytest.mark.asyncio
async def test_poll_treats_receive_exception_as_disconnect() -> None:
    token = CancelToken()
    await asyncio.wait_for(
        _poll_disconnect(_always_raising_disconnect_source, token, poll_interval_s=0.001),
        timeout=0.5,
    )
    assert token.cancelled


@pytest.mark.asyncio
async def test_poll_fires_on_cancel_callback() -> None:
    token = CancelToken()
    flag = [True]
    callback_fired = False

    def _cb() -> None:
        nonlocal callback_fired
        callback_fired = True

    await asyncio.wait_for(
        _poll_disconnect(
            _disconnect_source(flag), token, poll_interval_s=0.001, on_cancel=_cb
        ),
        timeout=0.5,
    )
    assert callback_fired


# ---------------------------------------------------------------------------
# Middleware behaviour via httpx — happy path only; disconnect is
# exercised through the unit test above (httpx ASGI transport does not
# emit ``http.disconnect`` cleanly for in-process tests).
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
async def test_poll_interval_must_be_positive() -> None:
    with pytest.raises(ValueError):
        CancellationMiddleware(app=FastAPI(), poll_interval_s=0)


def test_client_disconnected_constant() -> None:
    # Captured for downstream emitters (audit / log) to import.
    assert CLIENT_DISCONNECTED == "client_disconnected"
