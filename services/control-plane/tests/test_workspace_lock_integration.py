"""Integration test for the Stream TE-8 PG advisory workspace lock.

Boots a real Postgres (testcontainers) and drives :class:`PgWorkspaceLock`
from two concurrent coroutines on independent sessions/connections to prove
the cross-replica contract:

- two writers to the *same* workspace serialise (advisory lock is exclusive);
- writers to *different* workspaces run concurrently (no false contention);
- an ephemeral workspace (``user_id=None``) takes no lock.

No schema is needed — ``pg_advisory_xact_lock`` is a built-in.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from control_plane.workspace_lock import PgWorkspaceLock
from helix_agent.persistence.database import (
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


async def test_same_workspace_writes_serialise(engine: AsyncEngine) -> None:
    lock = PgWorkspaceLock(create_async_session_factory(engine))
    tenant, user = uuid4(), uuid4()
    order: list[str] = []

    async def worker(name: str) -> None:
        async with lock.acquire(tenant_id=tenant, user_id=user):
            order.append(f"{name}-enter")
            await asyncio.sleep(_HOLD_S)
            order.append(f"{name}-exit")

    await asyncio.gather(worker("A"), worker("B"))

    # Exclusive: one holder fully completes before the other enters.
    assert order in (
        ["A-enter", "A-exit", "B-enter", "B-exit"],
        ["B-enter", "B-exit", "A-enter", "A-exit"],
    )


async def test_different_workspaces_run_concurrently(engine: AsyncEngine) -> None:
    lock = PgWorkspaceLock(create_async_session_factory(engine))
    tenant = uuid4()
    user_a, user_b = uuid4(), uuid4()
    order: list[str] = []

    async def worker(name: str, user: object) -> None:
        async with lock.acquire(tenant_id=tenant, user_id=user):  # type: ignore[arg-type]
            order.append(f"{name}-enter")
            await asyncio.sleep(_HOLD_S)
            order.append(f"{name}-exit")

    await asyncio.gather(worker("A", user_a), worker("B", user_b))

    # Different keys don't contend: both enter before either exits.
    assert order[0].endswith("-enter")
    assert order[1].endswith("-enter")


async def test_ephemeral_workspace_takes_no_lock(engine: AsyncEngine) -> None:
    lock = PgWorkspaceLock(create_async_session_factory(engine))
    tenant = uuid4()
    order: list[str] = []

    async def worker(name: str) -> None:
        async with lock.acquire(tenant_id=tenant, user_id=None):
            order.append(f"{name}-enter")
            await asyncio.sleep(_HOLD_S)
            order.append(f"{name}-exit")

    await asyncio.gather(worker("A"), worker("B"))

    # user_id=None → no lock → concurrent.
    assert order[0].endswith("-enter")
    assert order[1].endswith("-enter")


async def test_lock_contention_no_starvation(engine: AsyncEngine) -> None:
    # TE-10 — N writers contend for one workspace lock. All must complete
    # exactly once (no deadlock, no starvation); advisory serialises them.
    lock = PgWorkspaceLock(create_async_session_factory(engine))
    tenant, user = uuid4(), uuid4()
    n_workers = 10
    completed: list[int] = []

    async def worker(idx: int) -> None:
        async with lock.acquire(tenant_id=tenant, user_id=user):
            completed.append(idx)
            await asyncio.sleep(0.02)

    await asyncio.gather(*(worker(i) for i in range(n_workers)))
    assert sorted(completed) == list(range(n_workers))


async def test_lock_distinct_workspaces_scale_concurrently(engine: AsyncEngine) -> None:
    # TE-10 — distinct workspaces must NOT serialise: their holds overlap.
    lock = PgWorkspaceLock(create_async_session_factory(engine))
    tenant = uuid4()
    users = [uuid4() for _ in range(5)]
    state = {"cur": 0, "max": 0}

    async def worker(u: object) -> None:
        async with lock.acquire(tenant_id=tenant, user_id=u):  # type: ignore[arg-type]
            state["cur"] += 1
            state["max"] = max(state["max"], state["cur"])
            await asyncio.sleep(0.1)
            state["cur"] -= 1

    await asyncio.gather(*(worker(u) for u in users))
    assert state["max"] >= 2  # genuine concurrency across workspaces
