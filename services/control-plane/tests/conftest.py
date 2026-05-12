"""Shared fixtures for the Control Plane test suite."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.settings import DEFAULT_DEV_TENANT_ID, Settings
from helix_agent.common.lifecycle import Lifecycle


@pytest.fixture
def settings() -> Settings:
    return Settings(
        service_name="control_plane_test",
        env="dev",
        auth_mode="dev",
        default_dev_tenant_id=DEFAULT_DEV_TENANT_ID,
        default_dev_actor_id="anonymous",
        # Avoid touching a real Postgres in unit-only tests.
        db_dsn="postgresql+asyncpg://test@localhost/test",
    )


@pytest.fixture
def lifecycle() -> Lifecycle:
    lc = Lifecycle()
    # B.1 health tests want a service that's already past STARTING; the
    # integration test that exercises the lifespan path drives state
    # transitions explicitly.
    lc.mark_ready()
    return lc


@pytest.fixture
async def client(settings: Settings, lifecycle: Lifecycle) -> AsyncIterator[AsyncClient]:
    app = create_app(settings=settings, lifecycle=lifecycle)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://control-plane.test") as client:
        yield client


def known_dev_tenant() -> UUID:
    return DEFAULT_DEV_TENANT_ID
