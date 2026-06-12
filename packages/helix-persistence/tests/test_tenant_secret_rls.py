"""Integration: tenant_provider_secret / tenant_tool_secret RLS — Stream HX-8.

The override tables are ENABLE-only RLS (no FORCE): the platform service
reads them through ``bypass_rls_session()`` on the owner connection (owner
exemption — same pattern as ``agent_approval`` / the skill tables). This
test pins the defence-in-depth property for a NON-owner app role: a
tenant-scoped session sees only its own override rows.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from helix_agent.persistence import (
    DatabaseConfig,
    SqlPlatformSecretStore,
    create_async_engine_from_config,
    create_async_session_factory,
)
from helix_agent.persistence.rls import build_rls_sessionmaker, current_tenant_id_var

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

APP_ROLE = "helix_app"
APP_PASSWORD = "helix_app_test_pw"  # test-only fixture password

_TENANT_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_TENANT_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


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
def stores(
    postgres_container: PostgresContainer,
) -> Iterator[tuple[SqlPlatformSecretStore, SqlPlatformSecretStore, AsyncEngine, AsyncEngine]]:
    """(owner_store, app_role_store, owner_engine, app_engine)."""
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")
    _provision_app_role(_sync_dsn(postgres_container))

    owner_engine = create_async_engine_from_config(
        DatabaseConfig(dsn=_async_dsn(postgres_container))
    )
    owner_store = SqlPlatformSecretStore(create_async_session_factory(owner_engine))

    app_dsn = _rewrite_credentials(_async_dsn(postgres_container), APP_ROLE, APP_PASSWORD)
    app_engine = create_async_engine_from_config(DatabaseConfig(dsn=app_dsn))
    app_store = SqlPlatformSecretStore(
        build_rls_sessionmaker(create_async_session_factory(app_engine))
    )
    yield owner_store, app_store, owner_engine, app_engine


@pytest.fixture(autouse=True)
def reset_rls() -> Iterator[None]:
    tok = current_tenant_id_var.set(None)
    try:
        yield
    finally:
        current_tenant_id_var.reset(tok)


@pytest.mark.asyncio
async def test_tenant_scoped_session_sees_only_own_override_rows(
    stores: tuple[SqlPlatformSecretStore, SqlPlatformSecretStore, AsyncEngine, AsyncEngine],
) -> None:
    owner_store, app_store, owner_engine, app_engine = stores
    try:
        # Seed two tenants' rows on the owner connection (RLS-exempt).
        await owner_store.upsert_tenant_provider(
            tenant_id=_TENANT_A,
            provider="anthropic",
            secret_ref="kms://tenant-a/anthropic",
            enabled=True,
            actor_id="admin",
        )
        await owner_store.upsert_tenant_provider(
            tenant_id=_TENANT_B,
            provider="anthropic",
            secret_ref="kms://tenant-b/anthropic",
            enabled=True,
            actor_id="admin",
        )

        # Owner sees both (the service bypass path).
        assert len(await owner_store.list_tenant_providers()) == 2

        # A tenant-scoped non-owner session sees only its own rows, even
        # when asking for the all-tenants view (defence in depth).
        tok = current_tenant_id_var.set(_TENANT_A)
        try:
            visible = await app_store.list_tenant_providers()
        finally:
            current_tenant_id_var.reset(tok)
        assert [r.tenant_id for r in visible] == [_TENANT_A]
    finally:
        await owner_engine.dispose()
        await app_engine.dispose()
