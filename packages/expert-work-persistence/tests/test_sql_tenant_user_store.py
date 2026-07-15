"""Integration tests for SqlTenantUserStore against a real Postgres — J.14."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from expert_work.persistence import (
    DatabaseConfig,
    SqlTenantUserStore,
    create_async_engine_from_config,
    create_async_session_factory,
)

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

SqlStoreFixture = tuple[SqlTenantUserStore, AsyncEngine]


def _sync_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+psycopg").replace("postgresql://", "postgresql+psycopg://", 1)


def _async_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture
def sql_store(postgres_container: PostgresContainer) -> Iterator[SqlStoreFixture]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")

    engine = create_async_engine_from_config(DatabaseConfig(dsn=_async_dsn(postgres_container)))
    session_factory = create_async_session_factory(engine)
    yield SqlTenantUserStore(session_factory), engine


@pytest.mark.asyncio
async def test_resolve_upsert_round_trip(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        tenant_id = uuid4()
        first = await store.resolve(tenant_id=tenant_id, subject_type="user", subject_id="oidc-1")
        assert first.tenant_id == tenant_id
        assert first.subject_id == "oidc-1"

        again = await store.resolve(tenant_id=tenant_id, subject_type="user", subject_id="oidc-1")
        # ON CONFLICT path: same identity → same surrogate id.
        assert again.id == first.id
        assert again.created_at == first.created_at
        assert again.last_active_at is not None
        assert first.last_active_at is not None
        assert again.last_active_at >= first.last_active_at
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_resolve_display_name_coalesce(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        tenant_id = uuid4()
        await store.resolve(
            tenant_id=tenant_id,
            subject_type="user",
            subject_id="u",
            display_name="Grace",
        )
        # A nameless resolve must keep the stored name (COALESCE).
        preserved = await store.resolve(tenant_id=tenant_id, subject_type="user", subject_id="u")
        assert preserved.display_name == "Grace"

        renamed = await store.resolve(
            tenant_id=tenant_id,
            subject_type="user",
            subject_id="u",
            display_name="Grace H.",
        )
        assert renamed.display_name == "Grace H."
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_filters_by_tenant(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        owner, other = uuid4(), uuid4()
        user = await store.resolve(tenant_id=owner, subject_type="user", subject_id="u")

        fetched = await store.get(user.id, tenant_id=owner)
        assert fetched is not None
        assert fetched.id == user.id

        assert await store.get(user.id, tenant_id=other) is None
        assert await store.get(uuid4(), tenant_id=owner) is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_resolve_distinguishes_identity_axes(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        tenant_a, tenant_b = uuid4(), uuid4()
        u1 = await store.resolve(tenant_id=tenant_a, subject_type="user", subject_id="x")
        u2 = await store.resolve(tenant_id=tenant_b, subject_type="user", subject_id="x")
        u3 = await store.resolve(tenant_id=tenant_a, subject_type="service_account", subject_id="x")
        u4 = await store.resolve(tenant_id=tenant_a, subject_type="user", subject_id="y")
        assert len({u1.id, u2.id, u3.id, u4.id}) == 4
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_list_by_tenant_filters_orders_paginates(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        owner, other = uuid4(), uuid4()
        u1 = await store.resolve(tenant_id=owner, subject_type="user", subject_id="u1")
        await store.resolve(tenant_id=owner, subject_type="user", subject_id="u2")
        await store.resolve(tenant_id=owner, subject_type="service_account", subject_id="svc")
        await store.resolve(tenant_id=other, subject_type="user", subject_id="foreign")
        # Bump u1 to most-recently-active.
        u1 = await store.resolve(tenant_id=owner, subject_type="user", subject_id="u1")

        rows = await store.list_by_tenant(owner, subject_type="user")
        # subject_type filter excludes the service account + the other tenant.
        assert {r.subject_id for r in rows} == {"u1", "u2"}
        # Ordered by last_active_at desc — u1 (just bumped) is first.
        assert rows[0].id == u1.id
        # No filter → includes the service account (3 rows this tenant).
        assert len(await store.list_by_tenant(owner)) == 3
        # Pagination.
        page = await store.list_by_tenant(owner, subject_type="user", limit=1, offset=1)
        assert len(page) == 1
    finally:
        await engine.dispose()
