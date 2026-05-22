"""Integration tests for ``SqlRunStore`` against a real Postgres — Mini-ADR J-41."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from helix_agent.persistence import (
    DatabaseConfig,
    create_async_engine_from_config,
    create_async_session_factory,
)
from helix_agent.runtime.runs import DisconnectMode, RunInfo, RunStatus, SqlRunStore

pytestmark = pytest.mark.integration

PERSISTENCE_ROOT = Path(__file__).resolve().parents[2] / "helix-persistence"
ALEMBIC_INI = PERSISTENCE_ROOT / "alembic.ini"

_BASE = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)


def _sync_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+psycopg").replace("postgresql://", "postgresql+psycopg://", 1)


def _async_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture
def run_store(postgres_container: PostgresContainer) -> Iterator[SqlRunStore]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")

    engine: AsyncEngine = create_async_engine_from_config(
        DatabaseConfig(dsn=_async_dsn(postgres_container))
    )
    yield SqlRunStore(create_async_session_factory(engine))


def _info(
    *,
    run_id: UUID,
    tenant_id: UUID,
    thread_id: UUID | None = None,
    user_id: UUID | None = None,
    status: RunStatus = RunStatus.PENDING,
    created_at: datetime | None = None,
) -> RunInfo:
    return RunInfo(
        run_id=run_id,
        tenant_id=tenant_id,
        thread_id=thread_id or uuid4(),
        user_id=user_id,
        status=status,
        on_disconnect=DisconnectMode.CANCEL,
        is_resume=False,
        error=None,
        created_at=created_at or _BASE,
        updated_at=created_at or _BASE,
        finished_at=None,
    )


@pytest.mark.asyncio
async def test_create_then_get_round_trips(run_store: SqlRunStore) -> None:
    run_id, tenant_id, user_id = uuid4(), uuid4(), uuid4()
    await run_store.create(
        _info(run_id=run_id, tenant_id=tenant_id, user_id=user_id, status=RunStatus.RUNNING)
    )

    fetched = await run_store.get(run_id=run_id, tenant_id=tenant_id)
    assert fetched is not None
    assert fetched.run_id == run_id
    assert fetched.user_id == user_id
    assert fetched.status is RunStatus.RUNNING
    assert fetched.on_disconnect is DisconnectMode.CANCEL
    assert fetched.is_resume is False
    assert fetched.finished_at is None


@pytest.mark.asyncio
async def test_get_unknown_returns_none(run_store: SqlRunStore) -> None:
    assert await run_store.get(run_id=uuid4(), tenant_id=uuid4()) is None


@pytest.mark.asyncio
async def test_get_cross_tenant_returns_none(run_store: SqlRunStore) -> None:
    run_id, tenant_a, tenant_b = uuid4(), uuid4(), uuid4()
    await run_store.create(_info(run_id=run_id, tenant_id=tenant_a))

    assert await run_store.get(run_id=run_id, tenant_id=tenant_b) is None


@pytest.mark.asyncio
async def test_set_status_updates_row(run_store: SqlRunStore) -> None:
    run_id, tenant_id = uuid4(), uuid4()
    await run_store.create(_info(run_id=run_id, tenant_id=tenant_id))

    finished = _BASE + timedelta(seconds=12)
    hit = await run_store.set_status(
        run_id=run_id,
        tenant_id=tenant_id,
        status=RunStatus.SUCCESS,
        updated_at=finished,
        finished_at=finished,
    )
    assert hit is True
    fetched = await run_store.get(run_id=run_id, tenant_id=tenant_id)
    assert fetched is not None
    assert fetched.status is RunStatus.SUCCESS
    assert fetched.finished_at == finished


@pytest.mark.asyncio
async def test_set_status_error_persists_detail(run_store: SqlRunStore) -> None:
    run_id, tenant_id = uuid4(), uuid4()
    await run_store.create(_info(run_id=run_id, tenant_id=tenant_id))

    await run_store.set_status(
        run_id=run_id,
        tenant_id=tenant_id,
        status=RunStatus.ERROR,
        updated_at=_BASE,
        error="provider 503",
        finished_at=_BASE,
    )
    fetched = await run_store.get(run_id=run_id, tenant_id=tenant_id)
    assert fetched is not None
    assert fetched.status is RunStatus.ERROR
    assert fetched.error == "provider 503"


@pytest.mark.asyncio
async def test_set_status_unknown_returns_false(run_store: SqlRunStore) -> None:
    miss = await run_store.set_status(
        run_id=uuid4(),
        tenant_id=uuid4(),
        status=RunStatus.SUCCESS,
        updated_at=_BASE,
    )
    assert miss is False


@pytest.mark.asyncio
async def test_list_by_thread_filters_and_sorts(run_store: SqlRunStore) -> None:
    thread_id, tenant_a, tenant_b = uuid4(), uuid4(), uuid4()
    newer, older = uuid4(), uuid4()
    await run_store.create(
        _info(
            run_id=newer,
            tenant_id=tenant_a,
            thread_id=thread_id,
            created_at=_BASE + timedelta(minutes=1),
        )
    )
    await run_store.create(
        _info(run_id=older, tenant_id=tenant_a, thread_id=thread_id, created_at=_BASE)
    )
    await run_store.create(_info(run_id=uuid4(), tenant_id=tenant_b, thread_id=thread_id))

    listed = await run_store.list_by_thread(thread_id=thread_id, tenant_id=tenant_a)
    assert [r.run_id for r in listed] == [older, newer]
