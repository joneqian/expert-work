"""Integration tests for DbEventStore against a real Postgres."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from helix_agent.persistence import (
    DatabaseConfig,
    create_async_engine_from_config,
    create_async_session_factory,
)
from helix_agent.protocol import EventRecord, EventType
from helix_agent.runtime.event_log import DbEventStore

pytestmark = pytest.mark.integration

PERSISTENCE_ROOT = Path(__file__).resolve().parents[2] / "helix-persistence"
ALEMBIC_INI = PERSISTENCE_ROOT / "alembic.ini"

DbStoreFixture = tuple[DbEventStore, AsyncEngine]


def _sync_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+psycopg").replace("postgresql://", "postgresql+psycopg://", 1)


def _async_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture
def db_store(postgres_container: PostgresContainer) -> Iterator[DbStoreFixture]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")

    engine = create_async_engine_from_config(DatabaseConfig(dsn=_async_dsn(postgres_container)))
    session_factory = create_async_session_factory(engine)
    store = DbEventStore(session_factory)
    yield store, engine
    # event_log is append-only at app-level; tests truncate via raw SQL between cases.


@pytest.mark.asyncio
async def test_put_persists_and_assigns_seq(db_store: DbStoreFixture) -> None:
    store, engine = db_store
    try:
        thread, tenant = uuid4(), uuid4()
        r1 = await store.put(
            thread_id=thread,
            tenant_id=tenant,
            event_type=EventType.SESSION_START,
            payload={"agent": "demo"},
            trace_id="00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01",
        )
        r2 = await store.put(thread_id=thread, tenant_id=tenant, event_type=EventType.LLM_CALL)
        assert r1.seq == 1
        assert r2.seq == 2
        assert r1.payload == {"agent": "demo"}
        assert r1.created_at is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_put_batch_serializes_seq_for_thread(db_store: DbStoreFixture) -> None:
    store, engine = db_store
    try:
        thread, tenant = uuid4(), uuid4()
        out = await store.put_batch(
            [
                EventRecord(
                    thread_id=thread, tenant_id=tenant, seq=0, event_type=EventType.TOOL_CALL
                ),
                EventRecord(
                    thread_id=thread, tenant_id=tenant, seq=0, event_type=EventType.TOOL_RESULT
                ),
                EventRecord(thread_id=thread, tenant_id=tenant, seq=0, event_type=EventType.STATE),
            ]
        )
        assert [r.seq for r in out] == [1, 2, 3]
        assert await store.count(thread) == 3
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_list_events_pagination(db_store: DbStoreFixture) -> None:
    store, engine = db_store
    try:
        thread, tenant = uuid4(), uuid4()
        for _ in range(5):
            await store.put(thread_id=thread, tenant_id=tenant, event_type=EventType.STATE)

        latest = await store.list_events(thread, limit=2)
        assert [r.seq for r in latest] == [4, 5]

        forward = await store.list_events(thread, after_seq=2, limit=2)
        assert [r.seq for r in forward] == [3, 4]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_payload_truncation(db_store: DbStoreFixture) -> None:
    store, engine = db_store
    store._max_payload_bytes = 64  # small cap to force truncation in test
    try:
        thread, tenant = uuid4(), uuid4()
        big = {"text": "x" * 5_000}
        r = await store.put(
            thread_id=thread,
            tenant_id=tenant,
            event_type=EventType.STATE,
            payload=big,
        )
        assert r.payload.get("_truncated") is True
        assert r.payload.get("_original_bytes", 0) > 64
        assert "_excerpt" in r.payload
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_seq_uniqueness_enforced(
    db_store: DbStoreFixture, postgres_container: PostgresContainer
) -> None:
    """UNIQUE(thread_id, seq) catches races even if FOR UPDATE is bypassed."""
    store, engine = db_store
    try:
        thread, tenant = uuid4(), uuid4()
        await store.put(thread_id=thread, tenant_id=tenant, event_type=EventType.STATE)
        # Manually insert a duplicate seq via raw SQL
        async with engine.begin() as conn:
            with pytest.raises(sa.exc.IntegrityError):
                await conn.execute(
                    sa.text(
                        "INSERT INTO event_log (thread_id, tenant_id, seq, event_type, payload) "
                        "VALUES (:t, :te, 1, 'state', '{}'::jsonb)"
                    ),
                    {"t": thread, "te": tenant},
                )
    finally:
        await engine.dispose()
