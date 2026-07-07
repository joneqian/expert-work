"""Integration tests for :class:`SqlBackupRecordStore` against real Postgres."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from expert_work.persistence import (
    DatabaseConfig,
    SqlBackupRecordStore,
    create_async_engine_from_config,
    create_async_session_factory,
)
from expert_work.protocol import (
    BackupAssetType,
    BackupRecord,
    BackupStatus,
    BackupTier,
    DrillRecord,
    DrillType,
)

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

SqlStoreFixture = tuple[SqlBackupRecordStore, AsyncEngine]


def _sync_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+psycopg").replace("postgresql://", "postgresql+psycopg://", 1)


def _async_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://", 1)


def _record(
    *,
    asset_ref: str = "s3://expert-work-dev/backups/postgres/2026-05-12.dump",
    started_at: datetime | None = None,
    status: BackupStatus = BackupStatus.RUNNING,
    finished_at: datetime | None = None,
    size_bytes: int | None = None,
    sha256: str | None = None,
    error: str | None = None,
) -> BackupRecord:
    return BackupRecord(
        asset_type=BackupAssetType.POSTGRES_FULL,
        asset_ref=asset_ref,
        started_at=started_at or datetime.now(UTC),
        finished_at=finished_at,
        size_bytes=size_bytes,
        sha256=sha256,
        status=status,
        error=error,
        region="cn-hangzhou",
        tier=BackupTier.TIER_0,
    )


@pytest.fixture
def sql_store(postgres_container: PostgresContainer) -> Iterator[SqlStoreFixture]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")

    # The postgres_container fixture is session-scoped, so backup_record and
    # dr_drill data accumulates across tests. ``latest()`` has no natural
    # tenant scope (unlike audit_log) so we truncate to isolate each test.
    sync_url = _sync_dsn(postgres_container)
    from sqlalchemy import create_engine, text

    sync_engine = create_engine(sync_url)
    with sync_engine.begin() as conn:
        conn.execute(text("TRUNCATE backup_record, dr_drill RESTART IDENTITY"))
    sync_engine.dispose()

    engine = create_async_engine_from_config(DatabaseConfig(dsn=_async_dsn(postgres_container)))
    session_factory = create_async_session_factory(engine)
    yield SqlBackupRecordStore(session_factory), engine


@pytest.mark.asyncio
async def test_first_insert_round_trip(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        written = await store.record(_record())
        assert written.id is not None
        assert written.status == BackupStatus.RUNNING
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_upsert_preserves_id_on_status_transition(
    sql_store: SqlStoreFixture,
) -> None:
    """RUNNING → SUCCESS on the same ``asset_ref`` must update in place."""
    store, engine = sql_store
    try:
        initial = await store.record(_record(status=BackupStatus.RUNNING))
        finalized = await store.record(
            _record(
                status=BackupStatus.SUCCESS,
                finished_at=datetime.now(UTC),
                size_bytes=42 * 1024,
                sha256="a" * 64,
            )
        )
        assert finalized.id == initial.id
        assert finalized.status == BackupStatus.SUCCESS
        assert finalized.size_bytes == 42 * 1024
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_latest_returns_newest_for_asset_type(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        older = datetime(2026, 5, 1, tzinfo=UTC)
        newer = datetime(2026, 5, 11, tzinfo=UTC)
        await store.record(_record(asset_ref="s3://b/old.dump", started_at=older))
        await store.record(_record(asset_ref="s3://b/new.dump", started_at=newer))

        latest = await store.latest(BackupAssetType.POSTGRES_FULL)
        assert latest is not None
        assert latest.asset_ref == "s3://b/new.dump"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_record_drill_round_trip(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        started = datetime.now(UTC)
        drill = DrillRecord(
            drill_type=DrillType.RESTORE_POSTGRES,
            started_at=started,
            finished_at=started + timedelta(hours=2),
            rpo_actual_s=3600,
            rto_actual_s=2 * 3600,
            target_rpo_s=24 * 3600,
            target_rto_s=4 * 3600,
            passed=True,
            notes="M0 first drill",
        )
        written = await store.record_drill(drill)
        assert written.id is not None
        assert written.drill_type == DrillType.RESTORE_POSTGRES
        assert written.passed is True
    finally:
        await engine.dispose()
