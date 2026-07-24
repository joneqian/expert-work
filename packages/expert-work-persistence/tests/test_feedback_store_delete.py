"""Tests for ``FeedbackStore.delete_for_threads`` — deletion hygiene PR1, Task 2.

Purge-user cascade (Task 8) hard-deletes a tenant's 👍/👎 feedback rows
scoped to the set of thread ids being purged. Covers both store
implementations against the same scenario:

* :class:`InMemoryFeedbackStore` — unit-level, in-process.
* :class:`DbFeedbackStore` — Postgres integration (chunked ``IN`` DELETE
  + ``rowcount`` aggregation), mirroring ``test_sql_memory_store.py``'s
  container-fixture style.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from expert_work.persistence import (
    DatabaseConfig,
    create_async_engine_from_config,
    create_async_session_factory,
)
from expert_work.persistence.feedback_store import (
    DbFeedbackStore,
    FeedbackRecord,
    FeedbackStore,
    InMemoryFeedbackStore,
)

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

SqlStoreFixture = tuple[DbFeedbackStore, AsyncEngine]


def _sync_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+psycopg").replace("postgresql://", "postgresql+psycopg://", 1)


def _async_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://", 1)


async def _seed(
    store: FeedbackStore,
    *,
    tenant_a: UUID,
    thread_a: UUID,
    thread_b: UUID,
    tenant_b: UUID,
) -> None:
    """t1/threadA x2, t1/threadB x1, plus one t2 row sharing threadA's id."""
    await store.insert(
        FeedbackRecord(tenant_id=tenant_a, thread_id=thread_a, rating="up", actor_id="u1")
    )
    await store.insert(
        FeedbackRecord(tenant_id=tenant_a, thread_id=thread_a, rating="down", actor_id="u1")
    )
    await store.insert(
        FeedbackRecord(tenant_id=tenant_a, thread_id=thread_b, rating="up", actor_id="u2")
    )
    await store.insert(
        FeedbackRecord(tenant_id=tenant_b, thread_id=thread_a, rating="up", actor_id="u3")
    )


# --------------------------------------------------------------------------- #
# InMemoryFeedbackStore
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_in_memory_delete_for_threads_scopes_by_tenant_and_thread() -> None:
    store = InMemoryFeedbackStore()
    tenant_a, tenant_b = uuid4(), uuid4()
    thread_a, thread_b = uuid4(), uuid4()
    await _seed(store, tenant_a=tenant_a, thread_a=thread_a, thread_b=thread_b, tenant_b=tenant_b)

    deleted = await store.delete_for_threads(tenant_id=tenant_a, thread_ids=[thread_a])

    assert deleted == 2
    # threadB (tenant_a) survives.
    remaining_thread_b = await store.list_for_thread(thread_id=thread_b)
    assert len(remaining_thread_b) == 1
    assert remaining_thread_b[0].tenant_id == tenant_a
    # the other tenant's row on the *same* thread_id survives.
    remaining_thread_a = await store.list_for_thread(thread_id=thread_a)
    assert len(remaining_thread_a) == 1
    assert remaining_thread_a[0].tenant_id == tenant_b


@pytest.mark.asyncio
async def test_in_memory_delete_for_threads_empty_list_returns_zero() -> None:
    store = InMemoryFeedbackStore()
    deleted = await store.delete_for_threads(tenant_id=uuid4(), thread_ids=[])
    assert deleted == 0


# --------------------------------------------------------------------------- #
# DbFeedbackStore
# --------------------------------------------------------------------------- #
@pytest.fixture
def sql_store(postgres_container: PostgresContainer) -> Iterator[SqlStoreFixture]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")

    engine = create_async_engine_from_config(DatabaseConfig(dsn=_async_dsn(postgres_container)))
    session_factory = create_async_session_factory(engine)
    yield DbFeedbackStore(session_factory), engine


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sql_delete_for_threads_scopes_by_tenant_and_thread(
    sql_store: SqlStoreFixture,
) -> None:
    store, engine = sql_store
    try:
        tenant_a, tenant_b = uuid4(), uuid4()
        thread_a, thread_b = uuid4(), uuid4()
        await _seed(
            store, tenant_a=tenant_a, thread_a=thread_a, thread_b=thread_b, tenant_b=tenant_b
        )

        deleted = await store.delete_for_threads(tenant_id=tenant_a, thread_ids=[thread_a])

        assert deleted == 2
        remaining_thread_b = await store.list_for_thread(thread_id=thread_b)
        assert len(remaining_thread_b) == 1
        assert remaining_thread_b[0].tenant_id == tenant_a
        remaining_thread_a = await store.list_for_thread(thread_id=thread_a)
        assert len(remaining_thread_a) == 1
        assert remaining_thread_a[0].tenant_id == tenant_b
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sql_delete_for_threads_empty_list_returns_zero(
    sql_store: SqlStoreFixture,
) -> None:
    store, engine = sql_store
    try:
        deleted = await store.delete_for_threads(tenant_id=uuid4(), thread_ids=[])
        assert deleted == 0
    finally:
        await engine.dispose()
