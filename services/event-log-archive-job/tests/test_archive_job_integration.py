"""Integration: ``EventLogArchiveJob`` end-to-end against real Postgres.

Seeds ``event_log`` rows (old + recent), runs the sweep against an
in-memory object store, and verifies the aged rows are archived to
object storage and deleted from Postgres while recent rows survive.

#66 / #67 of the test matrix.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from event_log_archive_job.job import EventLogArchiveJob
from expert_work.persistence import (
    DatabaseConfig,
    create_async_engine_from_config,
    create_async_session_factory,
)
from expert_work.runtime.storage.memory import InMemoryObjectStore

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parents[3] / "packages/expert-work-persistence/alembic.ini"


def _sync_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+psycopg").replace("postgresql://", "postgresql+psycopg://", 1)


def _async_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture
def archive_db(postgres_container: PostgresContainer) -> Iterator[tuple[AsyncEngine, str]]:
    """Migrate to head, truncate event_log, yield ``(async_engine, sync_admin_dsn)``."""
    sync_admin = _sync_dsn(postgres_container)
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", sync_admin)
    command.upgrade(cfg, "head")

    # Session-scoped postgres_container is shared — clear event_log first.
    admin = create_engine(sync_admin, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text("TRUNCATE TABLE event_log RESTART IDENTITY"))
    finally:
        admin.dispose()

    engine = create_async_engine_from_config(DatabaseConfig(dsn=_async_dsn(postgres_container)))
    yield engine, sync_admin


def _insert_event(
    sync_admin: str, *, thread_id: UUID, tenant_id: UUID, seq: int, days_ago: int
) -> None:
    """Seed one event_log row aged ``days_ago`` via the bootstrap superuser."""
    admin = create_engine(sync_admin, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO event_log "
                    "(thread_id, tenant_id, seq, event_type, payload, created_at) "
                    "VALUES (:th, :te, :sq, 'tick', '{\"k\": 1}'::jsonb, "
                    "        now() - make_interval(days => :d))"
                ),
                {"th": str(thread_id), "te": str(tenant_id), "sq": seq, "d": days_ago},
            )
    finally:
        admin.dispose()


async def _event_count(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        return int((await conn.execute(text("SELECT count(*) FROM event_log"))).scalar() or 0)


@pytest.mark.asyncio
async def test_archive_moves_old_rows_to_object_store(
    archive_db: tuple[AsyncEngine, str],
) -> None:
    """#66 — aged rows are archived to object storage and deleted; recent rows survive."""
    engine, sync_admin = archive_db
    try:
        tenant = uuid4()
        old_thread = uuid4()
        _insert_event(sync_admin, thread_id=old_thread, tenant_id=tenant, seq=1, days_ago=200)
        _insert_event(sync_admin, thread_id=old_thread, tenant_id=tenant, seq=2, days_ago=200)
        recent_thread = uuid4()
        _insert_event(sync_admin, thread_id=recent_thread, tenant_id=tenant, seq=1, days_ago=1)

        store = InMemoryObjectStore()
        job = EventLogArchiveJob(
            db_session_factory=create_async_session_factory(engine),
            object_store=store,
            archive_age_days=180,
            batch_size=100,
        )
        report = await job.run_once()

        assert report.archived_objects == 1
        assert report.archived_rows == 2

        keys = await store.list_prefix("event-log/")
        assert len(keys) == 1
        blob = await store.get(keys[0])
        archived = [json.loads(line) for line in blob.decode("utf-8").splitlines() if line]
        assert len(archived) == 2
        assert {row["seq"] for row in archived} == {1, 2}
        assert all(row["thread_id"] == str(old_thread) for row in archived)

        # Only the recent row survives in Postgres.
        assert await _event_count(engine) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_archive_rerun_overwrites_and_is_idempotent(
    archive_db: tuple[AsyncEngine, str],
) -> None:
    """#67 — a re-run overwrites the same key (no dup); an empty sweep is a no-op."""
    engine, sync_admin = archive_db
    try:
        tenant = uuid4()
        thread = uuid4()
        _insert_event(sync_admin, thread_id=thread, tenant_id=tenant, seq=1, days_ago=200)

        store = InMemoryObjectStore()
        job = EventLogArchiveJob(
            db_session_factory=create_async_session_factory(engine),
            object_store=store,
            archive_age_days=180,
            batch_size=100,
        )

        report1 = await job.run_once()
        assert report1.archived_objects == 1
        keys_after_1 = await store.list_prefix("event-log/")
        assert len(keys_after_1) == 1

        # Re-seed the same thread/month — simulates rows still to process.
        _insert_event(sync_admin, thread_id=thread, tenant_id=tenant, seq=2, days_ago=200)
        report2 = await job.run_once()
        assert report2.archived_objects == 1
        # Deterministic key → overwritten, not duplicated.
        assert await store.list_prefix("event-log/") == keys_after_1

        # Nothing aged left — a further run is a clean no-op.
        report3 = await job.run_once()
        assert report3.archived_objects == 0
        assert report3.archived_rows == 0
        assert await _event_count(engine) == 0
    finally:
        await engine.dispose()
