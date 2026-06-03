"""Integration: MCP connector catalog RLS (W-8 trap) + migration safety (W-2).

The catalog is a platform (NULL-tenant) table with an ``IS NOT DISTINCT FROM``
policy. The W-8 trap: a TENANT-scoped session (``app.tenant_id`` set) sees ZERO
catalog rows — the NULL-tenant platform rows are hidden. Tenants must therefore
read the catalog via ``bypass_rls_session()`` (an UNSCOPED session), not their
normal scoped session. This test pins that core safety property.

It also proves the W-2 additive migration is safe: a ``tenant_mcp_server`` row
created without ``catalog_id`` stays ``catalog_id IS NULL`` (a valid off-catalog
custom row) after 0056.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from helix_agent.persistence import (
    DatabaseConfig,
    create_async_engine_from_config,
    create_async_session_factory,
)
from helix_agent.persistence.mcp_connector_catalog import SqlMcpConnectorCatalogStore
from helix_agent.persistence.rls import build_rls_sessionmaker, current_tenant_id_var
from helix_agent.persistence.tenant_mcp_server import SqlTenantMcpServerStore
from helix_agent.protocol import McpConnectorCatalogUpsert

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
def catalog_rls(
    postgres_container: PostgresContainer,
) -> Iterator[tuple[SqlMcpConnectorCatalogStore, SqlTenantMcpServerStore, AsyncEngine]]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")
    _provision_app_role(_sync_dsn(postgres_container))

    app_dsn = _rewrite_credentials(_async_dsn(postgres_container), APP_ROLE, APP_PASSWORD)
    engine = create_async_engine_from_config(DatabaseConfig(dsn=app_dsn))
    sf = build_rls_sessionmaker(create_async_session_factory(engine))
    yield SqlMcpConnectorCatalogStore(sf), SqlTenantMcpServerStore(sf), engine


@pytest.fixture(autouse=True)
def reset_rls() -> Iterator[None]:
    tok = current_tenant_id_var.set(None)
    try:
        yield
    finally:
        current_tenant_id_var.reset(tok)


def _upsert() -> McpConnectorCatalogUpsert:
    return McpConnectorCatalogUpsert(
        name=f"conn-{uuid4().hex[:12]}",
        display_name="GitHub",
        transport="streamable_http",
        url_template="https://api.github.com/mcp",
        auth_type="none",
    )


@pytest.mark.asyncio
async def test_w8_tenant_scoped_session_cannot_see_platform_catalog(
    catalog_rls: tuple[SqlMcpConnectorCatalogStore, SqlTenantMcpServerStore, AsyncEngine],
) -> None:
    catalog, _servers, engine = catalog_rls
    try:
        # Unscoped (platform) session inserts a NULL-tenant catalog row.
        current_tenant_id_var.set(None)
        created = await catalog.create(upsert=_upsert(), actor_id="sysadmin")

        # Unscoped session sees it (the bypass path). (Membership, not exact
        # equality: the session-scoped DB may hold rows from other tests.)
        assert await catalog.get_by_id(created.id) is not None
        assert created.name in {r.name for r in await catalog.list()}

        # W-8 trap: a TENANT-scoped session sees ZERO catalog rows — ALL
        # NULL-tenant platform rows are hidden by IS NOT DISTINCT FROM.
        current_tenant_id_var.set(uuid4())
        assert await catalog.list() == []
        assert await catalog.get_by_id(created.id) is None
    finally:
        current_tenant_id_var.set(None)
        await engine.dispose()


@pytest.mark.asyncio
async def test_w2_additive_migration_leaves_catalog_id_null(
    catalog_rls: tuple[SqlMcpConnectorCatalogStore, SqlTenantMcpServerStore, AsyncEngine],
) -> None:
    _catalog, servers, engine = catalog_rls
    try:
        tid = uuid4()
        current_tenant_id_var.set(tid)
        # A tenant MCP server registered WITHOUT a catalog_id (the Stream V
        # shape) — after migration 0056 this is a valid off-catalog custom row.
        created = await servers.create(
            tenant_id=tid,
            name="custom",
            transport="streamable_http",
            url="https://custom.example.com/mcp",
            auth_type="none",
            token_secret_ref=None,
            timeout_s=30.0,
            created_by="admin@acme",
        )
        assert created.catalog_id is None
        got = await servers.get(tenant_id=tid, name="custom")
        assert got is not None and got.catalog_id is None
    finally:
        current_tenant_id_var.set(None)
        await engine.dispose()
