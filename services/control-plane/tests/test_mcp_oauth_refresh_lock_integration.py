"""Integration test for the OA-6 cross-replica MCP OAuth refresh lock.

Boots a real Postgres (testcontainers) and drives :class:`PgMcpOAuthRefreshLock`
from concurrent coroutines on independent sessions — the cross-replica contract:

- two refreshes of the *same* (tenant, user) serialise (advisory exclusive) — the
  guarantee that stops two replicas racing the rotated refresh token;
- different (tenant, user) keys run concurrently (no false contention);
- its ``classid`` (2) does NOT collide with the workspace lock's (1) — the same
  key string under both locks runs concurrently.

No schema is needed — ``pg_advisory_xact_lock`` is a built-in.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from control_plane.mcp_oauth_refresh_lock import PgMcpOAuthRefreshLock
from control_plane.workspace_lock import PgWorkspaceLock
from expert_work.persistence.database import (
    DatabaseConfig,
    create_async_engine_from_config,
    create_async_session_factory,
)

pytestmark = pytest.mark.integration

_HOLD_S = 0.3


def _async_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture
async def engine(postgres_container: PostgresContainer) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine_from_config(
        DatabaseConfig(dsn=_async_dsn(postgres_container), pgbouncer_mode=False)
    )
    try:
        yield eng
    finally:
        await eng.dispose()


async def test_same_user_refreshes_serialise(engine: AsyncEngine) -> None:
    lock = PgMcpOAuthRefreshLock(create_async_session_factory(engine))
    tenant, user = uuid4(), "kc-subject-1"
    order: list[str] = []

    async def worker(name: str) -> None:
        async with lock.acquire(tenant_id=tenant, user_id=user):
            order.append(f"{name}-enter")
            await asyncio.sleep(_HOLD_S)
            order.append(f"{name}-exit")

    await asyncio.gather(worker("A"), worker("B"))

    # Exclusive: one fully completes before the other enters (no rotated-token race).
    assert order in (
        ["A-enter", "A-exit", "B-enter", "B-exit"],
        ["B-enter", "B-exit", "A-enter", "A-exit"],
    )


async def test_different_users_run_concurrently(engine: AsyncEngine) -> None:
    lock = PgMcpOAuthRefreshLock(create_async_session_factory(engine))
    tenant = uuid4()
    order: list[str] = []

    async def worker(name: str, user: str) -> None:
        async with lock.acquire(tenant_id=tenant, user_id=user):
            order.append(f"{name}-enter")
            await asyncio.sleep(_HOLD_S)
            order.append(f"{name}-exit")

    await asyncio.gather(worker("A", "user-a"), worker("B", "user-b"))

    # Different keys don't contend: both enter before either exits.
    assert order[0].endswith("-enter")
    assert order[1].endswith("-enter")


async def test_classid_does_not_collide_with_workspace_lock(engine: AsyncEngine) -> None:
    """The OAuth-refresh lock (classid 2) and the workspace lock (classid 1) use
    separate advisory key spaces, so the same key string under both runs
    concurrently — never a cross-feature false deadlock."""
    sf = create_async_session_factory(engine)
    oauth_lock = PgMcpOAuthRefreshLock(sf)
    ws_lock = PgWorkspaceLock(sf)
    tenant = uuid4()
    user = uuid4()
    order: list[str] = []

    async def oauth_worker() -> None:
        async with oauth_lock.acquire(tenant_id=tenant, user_id=str(user)):
            order.append("oauth-enter")
            await asyncio.sleep(_HOLD_S)
            order.append("oauth-exit")

    async def ws_worker() -> None:
        async with ws_lock.acquire(tenant_id=tenant, user_id=user):
            order.append("ws-enter")
            await asyncio.sleep(_HOLD_S)
            order.append("ws-exit")

    await asyncio.gather(oauth_worker(), ws_worker())

    # Distinct key spaces → both enter before either exits.
    assert order[0].endswith("-enter")
    assert order[1].endswith("-enter")
