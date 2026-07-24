"""Integration tests for :class:`SqlMcpOAuthConnectionStore` against a real
Postgres — Stream MCP-OAUTH (OA-1b) + deletion-hygiene PR2 Task 3.

Mirrors ``test_sql_agent_spec_store.py``'s fixture style: the session
factory connects with the testcontainers superuser DSN directly (no
``APP_ROLE`` rewrite, no RLS-context wiring). That matches runtime reality
(see the ``rls-inert-runtime-superuser`` finding) where the app DB role is
itself a Postgres superuser/BYPASSRLS, so ``mcp_oauth_connection``'s FORCE
RLS policy never actually restricts queries — tenant isolation on the
existing per-user methods rides entirely on the explicit ``tenant_id``
predicate. The catalog-scoped methods added here deliberately carry no
``tenant_id`` predicate (platform-scope, cross-tenant by design), and this
fixture is the "superuser session" precedent the store's own base.py
docstrings point to.
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

from expert_work.persistence import (
    DatabaseConfig,
    create_async_engine_from_config,
    create_async_session_factory,
)
from expert_work.persistence.mcp_connector_catalog import SqlMcpConnectorCatalogStore
from expert_work.persistence.mcp_oauth_connection import SqlMcpOAuthConnectionStore
from expert_work.protocol import (
    McpConnectorAuthField,
    McpConnectorAuthSchema,
    McpConnectorCatalogUpsert,
    TenantPlan,
)

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

SqlStoreFixture = tuple[SqlMcpOAuthConnectionStore, SqlMcpConnectorCatalogStore, AsyncEngine]


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
    sf = create_async_session_factory(engine)
    yield SqlMcpOAuthConnectionStore(sf), SqlMcpConnectorCatalogStore(sf), engine


async def _make_catalog_entry(catalog_store: SqlMcpConnectorCatalogStore) -> UUID:
    """A minimal, FK-satisfying ``mcp_connector_catalog`` row.

    ``mcp_oauth_connection.catalog_id`` FKs to this table (ondelete
    CASCADE) — the connections created in these tests need a real catalog
    row to reference.
    """
    upsert = McpConnectorCatalogUpsert(
        name=f"conn-{uuid4().hex[:12]}",
        display_name="Test Connector",
        transport="streamable_http",
        url_template="https://mcp.example.com/{org}/sse",
        auth_type="bearer",
        auth_schema=McpConnectorAuthSchema(
            fields=[
                McpConnectorAuthField(key="token", label="API Token", kind="secret"),
                McpConnectorAuthField(key="org", label="Organization", kind="param"),
            ]
        ),
        required_tier=TenantPlan.FREE,
    )
    created = await catalog_store.create(upsert=upsert, actor_id="sysadmin")
    return created.id


@pytest.mark.asyncio
async def test_count_list_delete_for_catalog_cross_tenant(sql_store: SqlStoreFixture) -> None:
    """Platform-scope catalog methods (MCP catalog delete-guard, Task 8)
    operate across every tenant — no tenant_id predicate."""
    store, catalog_store, engine = sql_store
    try:
        tid_1, tid_2 = uuid4(), uuid4()
        cat_a = await _make_catalog_entry(catalog_store)
        cat_b = await _make_catalog_entry(catalog_store)

        a1 = await store.create(
            tenant_id=tid_1,
            user_id="u1",
            catalog_id=cat_a,
            name="linear",
            resolved_url="https://mcp.linear.app/sse",
        )
        a2 = await store.create(
            tenant_id=tid_2,
            user_id="u2",
            catalog_id=cat_a,
            name="linear",
            resolved_url="https://mcp.linear.app/sse",
        )
        b1 = await store.create(
            tenant_id=tid_1,
            user_id="u1",
            catalog_id=cat_b,
            name="github",
            resolved_url="https://mcp.github.com/sse",
        )
        b2 = await store.create(
            tenant_id=tid_2,
            user_id="u2",
            catalog_id=cat_b,
            name="github",
            resolved_url="https://mcp.github.com/sse",
        )

        assert await store.count_for_catalog(catalog_id=cat_a) == 2

        listed = await store.list_for_catalog(catalog_id=cat_a)
        assert [r.id for r in listed] == [a1.id, a2.id]
        assert {r.tenant_id for r in listed} == {tid_1, tid_2}

        deleted = await store.delete_for_catalog(catalog_id=cat_a)
        assert deleted == 2
        assert await store.get(connection_id=a1.id, tenant_id=tid_1, user_id="u1") is None
        assert await store.get(connection_id=a2.id, tenant_id=tid_2, user_id="u2") is None

        # catalog B untouched by the catalog-A delete.
        assert await store.count_for_catalog(catalog_id=cat_b) == 2
        still_b = await store.list_for_catalog(catalog_id=cat_b)
        assert {r.id for r in still_b} == {b1.id, b2.id}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_count_list_delete_for_catalog_empty(sql_store: SqlStoreFixture) -> None:
    store, catalog_store, engine = sql_store
    try:
        empty_cat = await _make_catalog_entry(catalog_store)
        assert await store.count_for_catalog(catalog_id=empty_cat) == 0
        assert await store.list_for_catalog(catalog_id=empty_cat) == []
        assert await store.delete_for_catalog(catalog_id=empty_cat) == 0
    finally:
        await engine.dispose()
