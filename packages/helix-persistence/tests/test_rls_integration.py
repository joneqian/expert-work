"""Integration test: RLS isolation against a real Postgres.

Spins up the session-scoped ``postgres_container``, applies all
alembic migrations (so 0005_rls_baseline runs and enables RLS), then
verifies that:

* Two distinct tenant ids inserted with one factory + ContextVar
  setter pair are mutually invisible to each other.
* Setting the ContextVar to ``None`` returns zero rows (fail-closed).
* ``bypass_rls_var=True`` does NOT bypass RLS for a non-BYPASSRLS
  role — the policy still enforces; only the migration-level admin
  role attribute (BYPASSRLS) actually skips. This test pins that
  the application code can't accidentally subvert RLS by flipping a
  ContextVar.
"""

from __future__ import annotations

from collections.abc import Iterator
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
from helix_agent.persistence.rls import (
    build_rls_sessionmaker,
    bypass_rls_var,
    current_tenant_id_var,
)
from helix_agent.persistence.thread_meta import SqlThreadMetaStore

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _sync_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+psycopg").replace("postgresql://", "postgresql+psycopg://", 1)


def _async_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture
def rls_store(
    postgres_container: PostgresContainer,
) -> Iterator[tuple[SqlThreadMetaStore, AsyncEngine]]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")

    engine = create_async_engine_from_config(DatabaseConfig(dsn=_async_dsn(postgres_container)))
    session_factory = build_rls_sessionmaker(create_async_session_factory(engine))
    yield SqlThreadMetaStore(session_factory), engine


@pytest.fixture(autouse=True)
def reset_rls_context() -> Iterator[None]:
    t1 = current_tenant_id_var.set(None)
    t2 = bypass_rls_var.set(False)
    try:
        yield
    finally:
        current_tenant_id_var.reset(t1)
        bypass_rls_var.reset(t2)


async def _seed(store: SqlThreadMetaStore, tenant_id: UUID) -> UUID:
    """Insert one thread_meta row for ``tenant_id`` and return its thread_id."""
    thread_id = uuid4()
    await store.create(
        thread_id=thread_id,
        tenant_id=tenant_id,
        created_by="rls-test",
    )
    return thread_id


@pytest.mark.asyncio
async def test_tenants_cannot_see_each_other(
    rls_store: tuple[SqlThreadMetaStore, AsyncEngine],
) -> None:
    store, engine = rls_store
    try:
        tenant_a, tenant_b = uuid4(), uuid4()

        current_tenant_id_var.set(tenant_a)
        id_a = await _seed(store, tenant_a)

        current_tenant_id_var.set(tenant_b)
        id_b = await _seed(store, tenant_b)

        # A scoped to its own tenant: own row visible, other tenant invisible.
        current_tenant_id_var.set(tenant_a)
        assert await store.get(id_a, tenant_id=tenant_a) is not None
        assert await store.get(id_b, tenant_id=tenant_a) is None

        current_tenant_id_var.set(tenant_b)
        assert await store.get(id_b, tenant_id=tenant_b) is not None
        assert await store.get(id_a, tenant_id=tenant_b) is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unset_tenant_id_returns_no_rows(
    rls_store: tuple[SqlThreadMetaStore, AsyncEngine],
) -> None:
    store, engine = rls_store
    try:
        tenant_a = uuid4()
        current_tenant_id_var.set(tenant_a)
        await _seed(store, tenant_a)

        # Without a tenant in context, set_config is skipped. The
        # ``USING (tenant_id = current_setting('app.tenant_id', true)::uuid)``
        # predicate then evaluates to NULL → policy filters everything.
        current_tenant_id_var.set(None)
        listed = await store.list_by_tenant(tenant_a, limit=10, offset=0)
        assert listed == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_bypass_var_does_not_subvert_rls_for_application_role(
    rls_store: tuple[SqlThreadMetaStore, AsyncEngine],
) -> None:
    """``bypass_rls_var`` only skips the application-side ``SET LOCAL``;
    it does NOT change the connection's role. The default test user is
    not BYPASSRLS, so even with the flag the policy still applies.
    """
    store, engine = rls_store
    try:
        tenant_a, tenant_b = uuid4(), uuid4()

        current_tenant_id_var.set(tenant_a)
        await _seed(store, tenant_a)

        # Now claim "I want to bypass" but stay on the non-BYPASSRLS
        # application role. The set_config call is skipped, so
        # ``current_setting('app.tenant_id', true)`` is ``''`` → the
        # policy denies (zero rows seen — A's row is not visible from
        # an unset session even with bypass).
        current_tenant_id_var.set(tenant_b)
        bypass_rls_var.set(True)
        listed = await store.list_by_tenant(tenant_a, limit=10, offset=0)
        assert listed == []
    finally:
        await engine.dispose()
