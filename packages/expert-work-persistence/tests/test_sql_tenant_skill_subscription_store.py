"""Integration (RLS) tests for the SQL tenant skill subscription store.

Mirrors test_sql_tenant_mcp_server_store.py fixture setup: postgres_container
is session-scoped (root conftest.py), app role provisioned via psycopg sync
engine, RLS sessionmaker built from the app-role async DSN.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import pytest
import sqlalchemy.exc
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from expert_work.persistence import (
    DatabaseConfig,
    create_async_engine_from_config,
    create_async_session_factory,
)
from expert_work.persistence.rls import build_rls_sessionmaker, current_tenant_id_var
from expert_work.persistence.tenant_skill_subscription import (
    SqlTenantSkillSubscriptionStore,
)

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

APP_ROLE = "expert_work_app"
APP_PASSWORD = "expert_work_app_test_pw"  # test-only fixture password


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
def subscription_store(
    postgres_container: PostgresContainer,
) -> Iterator[tuple[SqlTenantSkillSubscriptionStore, AsyncEngine]]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")
    _provision_app_role(_sync_dsn(postgres_container))

    app_dsn = _rewrite_credentials(_async_dsn(postgres_container), APP_ROLE, APP_PASSWORD)
    engine = create_async_engine_from_config(DatabaseConfig(dsn=app_dsn))
    sf = build_rls_sessionmaker(create_async_session_factory(engine))
    yield SqlTenantSkillSubscriptionStore(sf), engine


@pytest.fixture(autouse=True)
def reset_rls() -> Iterator[None]:
    tok = current_tenant_id_var.set(None)
    try:
        yield
    finally:
        current_tenant_id_var.reset(tok)


@pytest.mark.asyncio
async def test_subscribe_round_trip(
    subscription_store: tuple[SqlTenantSkillSubscriptionStore, AsyncEngine],
) -> None:
    store, engine = subscription_store
    try:
        tid, sid = uuid4(), uuid4()
        current_tenant_id_var.set(tid)
        rec = await store.subscribe(tenant_id=tid, platform_skill_id=sid, created_by="admin@acme")
        assert rec.enabled is True
        assert await store.is_subscribed(tenant_id=tid, platform_skill_id=sid) is True
        rows = await store.list_for_tenant(tenant_id=tid)
        assert [r.platform_skill_id for r in rows] == [sid]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_subscribe_idempotent_reenables(
    subscription_store: tuple[SqlTenantSkillSubscriptionStore, AsyncEngine],
) -> None:
    store, engine = subscription_store
    try:
        tid, sid = uuid4(), uuid4()
        current_tenant_id_var.set(tid)
        first = await store.subscribe(tenant_id=tid, platform_skill_id=sid, created_by="a")
        await store.set_enabled(tenant_id=tid, platform_skill_id=sid, enabled=False)
        again = await store.subscribe(tenant_id=tid, platform_skill_id=sid, created_by="b")
        assert again.enabled is True
        assert again.id == first.id  # upsert hit the same row
        assert again.created_by == "a"  # original creator preserved
        assert len(await store.list_for_tenant(tenant_id=tid)) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_soft_stop_keeps_row(
    subscription_store: tuple[SqlTenantSkillSubscriptionStore, AsyncEngine],
) -> None:
    store, engine = subscription_store
    try:
        tid, sid = uuid4(), uuid4()
        current_tenant_id_var.set(tid)
        await store.subscribe(tenant_id=tid, platform_skill_id=sid, created_by="a")
        rec = await store.set_enabled(tenant_id=tid, platform_skill_id=sid, enabled=False)
        assert rec.enabled is False
        assert await store.is_subscribed(tenant_id=tid, platform_skill_id=sid) is False
        assert len(await store.list_for_tenant(tenant_id=tid)) == 1  # not deleted
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unsubscribe_hard_deletes(
    subscription_store: tuple[SqlTenantSkillSubscriptionStore, AsyncEngine],
) -> None:
    store, engine = subscription_store
    try:
        tid, sid = uuid4(), uuid4()
        current_tenant_id_var.set(tid)
        await store.subscribe(tenant_id=tid, platform_skill_id=sid, created_by="a")
        await store.unsubscribe(tenant_id=tid, platform_skill_id=sid)
        assert await store.list_for_tenant(tenant_id=tid) == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rls_isolation_between_tenants(
    subscription_store: tuple[SqlTenantSkillSubscriptionStore, AsyncEngine],
) -> None:
    store, engine = subscription_store
    a, b = uuid4(), uuid4()
    sid = uuid4()

    current_tenant_id_var.set(a)
    try:
        await store.subscribe(tenant_id=a, platform_skill_id=sid, created_by="a@x")
    finally:
        current_tenant_id_var.set(None)

    # Tenant B must NOT see tenant A's subscription.
    current_tenant_id_var.set(b)
    try:
        assert await store.list_for_tenant(tenant_id=a) == []
        assert await store.is_subscribed(tenant_id=a, platform_skill_id=sid) is False
    finally:
        current_tenant_id_var.set(None)
        await engine.dispose()


@pytest.mark.asyncio
async def test_rls_blocks_cross_tenant_write(
    subscription_store: tuple[SqlTenantSkillSubscriptionStore, AsyncEngine],
) -> None:
    store, engine = subscription_store
    a, b = uuid4(), uuid4()
    current_tenant_id_var.set(b)  # session scoped to tenant B
    try:
        with pytest.raises(sqlalchemy.exc.DBAPIError):  # INSERT WITH CHECK rejects tenant_id=a
            await store.subscribe(tenant_id=a, platform_skill_id=uuid4(), created_by="attacker")
    finally:
        current_tenant_id_var.set(None)
        await engine.dispose()
