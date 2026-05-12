"""End-to-end tests for ``/healthz/{live,ready,startup}``."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from helix_agent.common.lifecycle import Lifecycle, ShutdownState


@pytest.mark.asyncio
async def test_live_returns_200(client: AsyncClient) -> None:
    response = await client.get("/healthz/live")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "control_plane_test"


@pytest.mark.asyncio
async def test_ready_returns_200_when_running(client: AsyncClient) -> None:
    response = await client.get("/healthz/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"


@pytest.mark.asyncio
async def test_startup_returns_200_after_mark_ready(client: AsyncClient) -> None:
    response = await client.get("/healthz/startup")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"


@pytest.mark.asyncio
async def test_ready_flips_to_503_during_drain(
    client: AsyncClient,
    lifecycle: Lifecycle,
) -> None:
    # Simulate mid-drain: the LB must detach this pod.
    lifecycle._state = ShutdownState.DRAINING
    response = await client.get("/healthz/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


@pytest.mark.asyncio
async def test_live_stays_200_during_drain(
    client: AsyncClient,
    lifecycle: Lifecycle,
) -> None:
    """Liveness must NOT flip during DRAINING — k8s would restart the pod
    instead of letting traffic drain."""
    lifecycle._state = ShutdownState.DRAINING
    response = await client.get("/healthz/live")
    assert response.status_code == 200
