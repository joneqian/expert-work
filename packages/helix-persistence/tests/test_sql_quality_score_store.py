"""Integration test for :class:`SqlQualityScoreStore` — Stream RT-5 (RT-ADR-24).

Exercises the 0117_quality_score migration + the store (idempotent insert /
count / list / RLS isolation) against a real Postgres, mirroring
test_sql_agent_disable_store.py.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from helix_agent.persistence import (
    DatabaseConfig,
    SqlQualityScoreStore,
    create_async_engine_from_config,
    create_async_session_factory,
)
from helix_agent.persistence.rls import build_rls_sessionmaker, current_tenant_id_var
from helix_agent.protocol import QualityScoreRecord

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

APP_ROLE = "helix_app"
APP_PASSWORD = "helix_app_test_pw"  # test-only fixture password


def _sync_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+psycopg").replace("postgresql://", "postgresql+psycopg://", 1)


def _async_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://", 1)


def _rewrite_credentials(dsn: str, user: str, password: str) -> str:
    parsed = urlparse(dsn)
    new_netloc = f"{user}:{password}@{parsed.hostname}"
    if parsed.port is not None:
        new_netloc = f"{new_netloc}:{parsed.port}"
    return urlunparse(parsed._replace(netloc=new_netloc))


def _provision_app_role(sync_dsn: str) -> None:
    admin_engine = create_engine(sync_dsn, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
                {"role": APP_ROLE},
            ).first()
            if exists is None:
                conn.execute(text(f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}'"))
            conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}"))
            conn.execute(
                text(
                    f"GRANT SELECT, INSERT, UPDATE, DELETE "
                    f"ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"
                )
            )
            conn.execute(
                text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")
            )
    finally:
        admin_engine.dispose()


@pytest.fixture
def quality_store(
    postgres_container: PostgresContainer,
) -> Iterator[tuple[SqlQualityScoreStore, AsyncEngine]]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")
    _provision_app_role(_sync_dsn(postgres_container))

    app_dsn = _rewrite_credentials(_async_dsn(postgres_container), APP_ROLE, APP_PASSWORD)
    engine = create_async_engine_from_config(DatabaseConfig(dsn=app_dsn))
    sf = build_rls_sessionmaker(create_async_session_factory(engine))
    yield SqlQualityScoreStore(sf), engine


@pytest.fixture(autouse=True)
def reset_rls() -> Iterator[None]:
    tok = current_tenant_id_var.set(None)
    try:
        yield
    finally:
        current_tenant_id_var.reset(tok)


def _record(
    *, tenant: UUID, run: UUID, agent: str = "support-bot", overall: int = 4
) -> QualityScoreRecord:
    return QualityScoreRecord(
        tenant_id=tenant,
        agent_name=agent,
        agent_version="1",
        run_id=run,
        thread_id=uuid4(),
        overall=overall,
        dimensions={"addressed_request": 5, "coherence": 4, "safety": 5},
        rationale="clear",
        judge_model="claude-haiku-4-5-20251001",
    )


@pytest.mark.asyncio
async def test_insert_round_trip_and_idempotent(
    quality_store: tuple[SqlQualityScoreStore, AsyncEngine],
) -> None:
    store, engine = quality_store
    try:
        tenant, run = uuid4(), uuid4()
        current_tenant_id_var.set(tenant)
        stored = await store.insert(_record(tenant=tenant, run=run, overall=4))
        assert stored.id is not None
        assert stored.observed_at is not None
        assert stored.dimensions["addressed_request"] == 5

        # Re-insert of the same run is a no-op (ON CONFLICT); original wins.
        again = await store.insert(_record(tenant=tenant, run=run, overall=1))
        assert again.id == stored.id
        assert again.overall == 4
        rows = await store.list_scores(tenant_id=tenant)
        assert len(rows) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_count_since_backs_daily_cap(
    quality_store: tuple[SqlQualityScoreStore, AsyncEngine],
) -> None:
    store, engine = quality_store
    try:
        tenant = uuid4()
        current_tenant_id_var.set(tenant)
        await store.insert(_record(tenant=tenant, run=uuid4()))
        await store.insert(_record(tenant=tenant, run=uuid4()))
        day_start = datetime.now(tz=UTC) - timedelta(hours=1)
        assert await store.count_since(tenant_id=tenant, since=day_start) == 2
        future = datetime.now(tz=UTC) + timedelta(hours=1)
        assert await store.count_since(tenant_id=tenant, since=future) == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_exists_tracks_scored_runs(
    quality_store: tuple[SqlQualityScoreStore, AsyncEngine],
) -> None:
    store, engine = quality_store
    try:
        tenant, run = uuid4(), uuid4()
        current_tenant_id_var.set(tenant)
        assert await store.exists(tenant_id=tenant, run_id=run) is False
        await store.insert(_record(tenant=tenant, run=run))
        assert await store.exists(tenant_id=tenant, run_id=run) is True
        assert await store.exists(tenant_id=tenant, run_id=uuid4()) is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rls_blocks_cross_tenant_read(
    quality_store: tuple[SqlQualityScoreStore, AsyncEngine],
) -> None:
    store, engine = quality_store
    try:
        tenant_a, tenant_b = uuid4(), uuid4()
        current_tenant_id_var.set(tenant_a)
        await store.insert(_record(tenant=tenant_a, run=uuid4()))
        assert len(await store.list_scores(tenant_id=tenant_a)) == 1
        # Scope to B: A's rows are invisible under RLS.
        current_tenant_id_var.set(tenant_b)
        assert await store.list_scores(tenant_id=tenant_a) == []
        assert (
            await store.count_since(
                tenant_id=tenant_a, since=datetime.now(tz=UTC) - timedelta(hours=1)
            )
            == 0
        )
    finally:
        await engine.dispose()
