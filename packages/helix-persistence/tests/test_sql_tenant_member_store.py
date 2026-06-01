"""Integration tests for SqlTenantMemberStore against a real Postgres — Stream R.

Exercises the partial-unique active-email index, the state machine guard, and
the Keycloak-id reverse lookup on real Postgres (the in-memory store covers the
same contract for the fast suite).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from helix_agent.persistence import (
    DatabaseConfig,
    DuplicateMemberError,
    SqlTenantMemberStore,
    create_async_engine_from_config,
    create_async_session_factory,
)

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

SqlStoreFixture = tuple[SqlTenantMemberStore, AsyncEngine]


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
    yield SqlTenantMemberStore(session_factory), engine


@pytest.mark.asyncio
async def test_create_and_get_round_trip(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        tenant = uuid4()
        m = await store.create(
            tenant_id=tenant, email="Eng@Co.com", role="operator", invited_by="admin"
        )
        assert m.status == "invited"
        assert m.invited_at is not None
        got = await store.get(tenant_id=tenant, member_id=m.id)
        assert got is not None and got.email == "Eng@Co.com"
        assert await store.get(tenant_id=uuid4(), member_id=m.id) is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_active_email_partial_unique(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        tenant = uuid4()
        await store.create(tenant_id=tenant, email="dup@co.com", role="viewer", invited_by="a")
        with pytest.raises(DuplicateMemberError):
            await store.create(tenant_id=tenant, email="DUP@co.com", role="viewer", invited_by="a")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_revoked_email_reinvitable(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        tenant = uuid4()
        m = await store.create(tenant_id=tenant, email="x@co.com", role="viewer", invited_by="a")
        assert await store.transition(
            member_id=m.id, tenant_id=tenant, to="revoked", now=datetime.now(UTC)
        )
        m2 = await store.create(tenant_id=tenant, email="x@co.com", role="viewer", invited_by="a")
        assert m2.id != m.id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_state_machine_and_active_consistency(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        tenant = uuid4()
        m = await store.create(tenant_id=tenant, email="e@co.com", role="admin", invited_by="a")
        # invited→suspended is illegal.
        assert not await store.transition(
            member_id=m.id, tenant_id=tenant, to="suspended", now=datetime.now(UTC)
        )
        await store.set_keycloak_user_id(member_id=m.id, keycloak_user_id="kc-1")
        user_id = uuid4()
        assert await store.transition(
            member_id=m.id,
            tenant_id=tenant,
            to="active",
            now=datetime.now(UTC),
            subject_id=user_id,
        )
        got = await store.get(tenant_id=tenant, member_id=m.id)
        assert got is not None and got.status == "active"
        assert got.subject_id == user_id and got.activated_at is not None
        # Idempotent: second activate matches no row.
        assert not await store.transition(
            member_id=m.id,
            tenant_id=tenant,
            to="active",
            now=datetime.now(UTC),
            subject_id=uuid4(),
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_kc_reverse_lookup_and_list(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        t1, t2 = uuid4(), uuid4()
        a = await store.create(tenant_id=t1, email="a@co.com", role="viewer", invited_by="x")
        await store.create(tenant_id=t1, email="b@co.com", role="viewer", invited_by="x")
        await store.create(tenant_id=t2, email="c@co.com", role="viewer", invited_by="x")
        await store.set_keycloak_user_id(member_id=a.id, keycloak_user_id="kc-a")

        found = await store.get_by_keycloak_user_id(keycloak_user_id="kc-a")
        assert found is not None and found.id == a.id

        t1_members = await store.list_for_tenant(tenant_id=t1)
        assert len(t1_members) == 2
        invited = await store.list_for_tenant(tenant_id=t1, status="invited")
        assert {x.email for x in invited} == {"a@co.com", "b@co.com"}
    finally:
        await engine.dispose()
