"""Integration test for :class:`SqlQualityDriftAlertStore` — RT-5 (RT-ADR-24).

Exercises the 0118_quality_drift_alert migration + the store (insert /
latest_alert_at / list / RLS isolation) against a real Postgres.
"""

from __future__ import annotations

from collections.abc import Iterator
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
    SqlQualityDriftAlertStore,
    create_async_engine_from_config,
    create_async_session_factory,
)
from helix_agent.persistence.rls import build_rls_sessionmaker, current_tenant_id_var
from helix_agent.protocol import QualityDriftAlertRecord

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
def alert_store(
    postgres_container: PostgresContainer,
) -> Iterator[tuple[SqlQualityDriftAlertStore, AsyncEngine]]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")
    _provision_app_role(_sync_dsn(postgres_container))

    app_dsn = _rewrite_credentials(_async_dsn(postgres_container), APP_ROLE, APP_PASSWORD)
    engine = create_async_engine_from_config(DatabaseConfig(dsn=app_dsn))
    sf = build_rls_sessionmaker(create_async_session_factory(engine))
    yield SqlQualityDriftAlertStore(sf), engine


@pytest.fixture(autouse=True)
def reset_rls() -> Iterator[None]:
    tok = current_tenant_id_var.set(None)
    try:
        yield
    finally:
        current_tenant_id_var.reset(tok)


def _alert(*, tenant: UUID, agent: str = "support-bot") -> QualityDriftAlertRecord:
    return QualityDriftAlertRecord(
        tenant_id=tenant,
        agent_name=agent,
        recent_mean=3.1,
        baseline_mean=4.2,
        drift_pct=0.26,
        recent_count=12,
        baseline_count=80,
    )


@pytest.mark.asyncio
async def test_insert_and_latest_and_list(
    alert_store: tuple[SqlQualityDriftAlertStore, AsyncEngine],
) -> None:
    store, engine = alert_store
    try:
        tenant = uuid4()
        current_tenant_id_var.set(tenant)
        assert await store.latest_alert_at(tenant_id=tenant, agent_name="support-bot") is None
        first = await store.insert(_alert(tenant=tenant))
        assert first.id is not None
        assert first.detected_at is not None
        second = await store.insert(_alert(tenant=tenant))
        latest = await store.latest_alert_at(tenant_id=tenant, agent_name="support-bot")
        assert latest == second.detected_at

        rows = await store.list_alerts(tenant_id=tenant)
        assert len(rows) == 2
        assert rows[0].detected_at is not None
        assert rows[1].detected_at is not None
        assert rows[0].detected_at >= rows[1].detected_at  # newest first
        assert rows[0].drift_pct == 0.26
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rls_blocks_cross_tenant_read(
    alert_store: tuple[SqlQualityDriftAlertStore, AsyncEngine],
) -> None:
    store, engine = alert_store
    try:
        tenant_a, tenant_b = uuid4(), uuid4()
        current_tenant_id_var.set(tenant_a)
        await store.insert(_alert(tenant=tenant_a))
        assert len(await store.list_alerts(tenant_id=tenant_a)) == 1
        # Scope to B: A's alert is invisible under RLS.
        current_tenant_id_var.set(tenant_b)
        assert await store.list_alerts(tenant_id=tenant_a) == []
        assert await store.latest_alert_at(tenant_id=tenant_a, agent_name="support-bot") is None
    finally:
        await engine.dispose()
