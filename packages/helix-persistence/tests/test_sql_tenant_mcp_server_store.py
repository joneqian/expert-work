"""Integration (RLS) tests for the SQL tenant MCP server store.

Mirrors test_sql_tenant_config_store.py fixture setup: postgres_container
is session-scoped (root conftest.py), app role provisioned via psycopg sync
engine, RLS sessionmaker built from the app-role async DSN.
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
from helix_agent.persistence.rls import build_rls_sessionmaker, current_tenant_id_var
from helix_agent.persistence.tenant_mcp_server import (
    SqlTenantMcpServerStore,
    TenantMcpServerAlreadyExistsError,
)
from helix_agent.protocol import TenantMcpServerPatch

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
def tenant_mcp_server_store(
    postgres_container: PostgresContainer,
) -> Iterator[tuple[SqlTenantMcpServerStore, AsyncEngine]]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")
    _provision_app_role(_sync_dsn(postgres_container))

    app_dsn = _rewrite_credentials(_async_dsn(postgres_container), APP_ROLE, APP_PASSWORD)
    engine = create_async_engine_from_config(DatabaseConfig(dsn=app_dsn))
    sf = build_rls_sessionmaker(create_async_session_factory(engine))
    yield SqlTenantMcpServerStore(sf), engine


@pytest.fixture(autouse=True)
def reset_rls() -> Iterator[None]:
    tok = current_tenant_id_var.set(None)
    try:
        yield
    finally:
        current_tenant_id_var.reset(tok)


@pytest.mark.asyncio
async def test_create_get_round_trip(tenant_mcp_server_store) -> None:
    store, engine = tenant_mcp_server_store
    try:
        tid = uuid4()
        current_tenant_id_var.set(tid)
        created = await store.create(
            tenant_id=tid,
            name="github",
            transport="streamable_http",
            url="https://mcp.example.com/mcp",
            auth_type="bearer",
            token_secret_ref="secret://helix-agent/t/mcp/github/token",
            timeout_s=30.0,
            created_by="admin@acme",
        )
        assert created.name == "github"
        got = await store.get(tenant_id=tid, name="github")
        assert got is not None and got.id == created.id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rls_isolation_between_tenants(tenant_mcp_server_store) -> None:
    store, engine = tenant_mcp_server_store
    a, b = uuid4(), uuid4()

    current_tenant_id_var.set(a)
    try:
        await store.create(
            tenant_id=a,
            name="github",
            transport="streamable_http",
            url="https://a.example.com/mcp",
            auth_type="none",
            token_secret_ref=None,
            timeout_s=30.0,
            created_by="a@x",
        )
    finally:
        current_tenant_id_var.set(None)

    # Tenant B must NOT see tenant A's row.
    current_tenant_id_var.set(b)
    try:
        assert await store.get(tenant_id=a, name="github") is None
        assert await store.list_for_tenant(tenant_id=a) == []
    finally:
        current_tenant_id_var.set(None)
        await engine.dispose()


@pytest.mark.asyncio
async def test_duplicate_name_rejected(tenant_mcp_server_store) -> None:
    store, engine = tenant_mcp_server_store
    try:
        tid = uuid4()
        current_tenant_id_var.set(tid)
        kwargs = {
            "tenant_id": tid,
            "name": "github",
            "transport": "streamable_http",
            "url": "https://a.example.com/mcp",
            "auth_type": "none",
            "token_secret_ref": None,
            "timeout_s": 30.0,
            "created_by": "a@x",
        }
        await store.create(**kwargs)
        with pytest.raises(TenantMcpServerAlreadyExistsError):
            await store.create(**kwargs)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_update_and_delete(tenant_mcp_server_store) -> None:
    store, engine = tenant_mcp_server_store
    try:
        tid = uuid4()
        current_tenant_id_var.set(tid)
        await store.create(
            tenant_id=tid,
            name="github",
            transport="streamable_http",
            url="https://a.example.com/mcp",
            auth_type="none",
            token_secret_ref=None,
            timeout_s=30.0,
            created_by="a@x",
        )
        updated = await store.update(
            tenant_id=tid,
            name="github",
            patch=TenantMcpServerPatch(enabled=False, url="https://b.example.com/mcp"),
        )
        assert updated.enabled is False
        assert updated.url == "https://b.example.com/mcp"
        await store.delete(tenant_id=tid, name="github")
        assert await store.get(tenant_id=tid, name="github") is None
    finally:
        await engine.dispose()
