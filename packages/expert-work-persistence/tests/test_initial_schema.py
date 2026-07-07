"""A.1 integration test — applies migrations + round-trips data on each table.

Requires Docker for testcontainers Postgres. Run via:

    uv run pytest packages/expert-work-persistence -m integration
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

from expert_work.persistence import (
    AuditLogRow,
    DatabaseConfig,
    EventLogRow,
    ThreadMetaRow,
    create_async_engine_from_config,
    create_async_session_factory,
)

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _apply_migrations(sync_dsn: str) -> None:
    """Run Alembic upgrade head against a sync DSN (psycopg)."""
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", sync_dsn)
    command.upgrade(cfg, "head")


def _async_dsn(container: PostgresContainer) -> str:
    """testcontainers default URL uses psycopg2; rewrite to asyncpg for SQLAlchemy async."""
    url: str = str(container.get_connection_url())
    # `postgresql+psycopg2://...` -> `postgresql+asyncpg://...`
    return url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://", 1)


def _sync_dsn(container: PostgresContainer) -> str:
    """Sync DSN for Alembic (psycopg v3)."""
    url: str = str(container.get_connection_url())
    return url.replace("+psycopg2", "+psycopg").replace("postgresql://", "postgresql+psycopg://", 1)


@pytest.mark.asyncio
async def test_migrations_create_all_tables(postgres_container: PostgresContainer) -> None:
    _apply_migrations(_sync_dsn(postgres_container))

    engine = create_async_engine_from_config(DatabaseConfig(dsn=_async_dsn(postgres_container)))
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                sa.text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' ORDER BY table_name"
                )
            )
            tables = {row[0] for row in result}
    finally:
        await engine.dispose()

    assert {"event_log", "thread_meta", "audit_log", "alembic_version"} <= tables
    # ``app_user`` was dropped in migration 0016 (superseded by tenant_user).
    assert "app_user" not in tables
    assert "tenant_user" in tables


@pytest.mark.asyncio
async def test_event_log_insert_and_unique(postgres_container: PostgresContainer) -> None:
    _apply_migrations(_sync_dsn(postgres_container))
    engine = create_async_engine_from_config(DatabaseConfig(dsn=_async_dsn(postgres_container)))
    session_factory = create_async_session_factory(engine)
    try:
        thread_id, tenant_id = uuid4(), uuid4()
        async with session_factory() as session:
            session.add(
                EventLogRow(
                    thread_id=thread_id,
                    tenant_id=tenant_id,
                    seq=1,
                    event_type="session_start",
                    payload={"agent": "demo"},
                    trace_id="00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01",
                )
            )
            await session.commit()

        async with session_factory() as session:
            row = (await session.execute(sa.select(EventLogRow))).scalar_one()
            assert row.event_type == "session_start"
            assert row.payload == {"agent": "demo"}
            assert row.thread_id == thread_id
            assert row.created_at is not None

        # UNIQUE (thread_id, seq) should reject duplicate
        async with session_factory() as session:
            session.add(
                EventLogRow(
                    thread_id=thread_id,
                    tenant_id=tenant_id,
                    seq=1,
                    event_type="state",
                    payload={},
                )
            )
            with pytest.raises(sa.exc.IntegrityError):
                await session.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_thread_meta_and_audit_log_round_trip(postgres_container: PostgresContainer) -> None:
    _apply_migrations(_sync_dsn(postgres_container))
    engine = create_async_engine_from_config(DatabaseConfig(dsn=_async_dsn(postgres_container)))
    session_factory = create_async_session_factory(engine)
    try:
        thread_id, tenant_id, request_id = uuid4(), uuid4(), uuid4()

        async with session_factory() as session:
            session.add(
                ThreadMetaRow(
                    thread_id=thread_id,
                    tenant_id=tenant_id,
                    created_by="user-1",
                    agent_name="demo",
                    agent_version="0.1.0",
                )
            )
            session.add(
                AuditLogRow(
                    tenant_id=tenant_id,
                    actor_type="user",
                    actor_id="user-1",
                    action="session:write",
                    resource_type="session",
                    resource_id=str(thread_id),
                    result="success",
                    request_id=request_id,
                    details={"client": "admin-ui"},
                )
            )
            await session.commit()

        async with session_factory() as session:
            # Filter by ``tenant_id`` so this test is order-independent
            # — the ``postgres_container`` fixture is session-scoped
            # and other integration tests in the same session may
            # insert their own rows into ``thread_meta`` / ``audit_log``.
            thread: ThreadMetaRow = (
                await session.execute(
                    sa.select(ThreadMetaRow).where(ThreadMetaRow.tenant_id == tenant_id)
                )
            ).scalar_one()
            assert thread.status == "active"
            assert thread.agent_version == "0.1.0"

            audit: AuditLogRow = (
                await session.execute(
                    sa.select(AuditLogRow).where(AuditLogRow.tenant_id == tenant_id)
                )
            ).scalar_one()
            assert UUID(str(audit.request_id)) == request_id
            assert audit.details == {"client": "admin-ui"}
            assert audit.occurred_at < datetime.now(UTC).astimezone(audit.occurred_at.tzinfo)
    finally:
        await engine.dispose()
