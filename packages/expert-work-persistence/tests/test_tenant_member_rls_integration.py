"""Integration: ``tenant_member`` cross-tenant platform-admin read — Stream ACCT.

Pins the cross-tenant member roster against a real Postgres (testcontainers):

1.  ``tenant_member`` is FORCE ROW LEVEL SECURITY (migration 0051). Invites
    are written under each tenant's RLS scope (GUC set) so the policy
    ``WITH CHECK`` passes.

2.  A normally-scoped session sees only its own tenant's members
    (``list_for_tenant`` — isolation intact).

3.  ``list_all_tenants`` (the path ``GET /v1/members?tenant_id=*`` uses,
    wrapped in ``bypass_rls_session()``) returns EVERY tenant's members —
    because the store does ``SET LOCAL ROLE audit_reader`` (BYPASSRLS,
    migration 0005; GRANTed SELECT on this table by migration 0085).
    Merely flipping ``bypass_rls_var`` would NOT be enough: the app role
    is not BYPASSRLS, so on a FORCE table the policy collapses to
    ``tenant_id = NULL`` → zero rows. This test would FAIL without it.

Mirrors ``test_billing_ledger_rls_integration.py`` — the same cross-tenant
FORCE-RLS read precedent.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from expert_work.persistence import (
    DatabaseConfig,
    SqlTenantMemberStore,
    create_async_engine_from_config,
    create_async_session_factory,
)
from expert_work.persistence.rls import (
    build_rls_sessionmaker,
    bypass_rls_var,
    current_tenant_id_var,
)

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

APP_ROLE = "expert_work_app_member_acct"
APP_PASSWORD = "expert_work_app_member_acct_pw"  # test-only fixture password


def _sync_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+psycopg").replace("postgresql://", "postgresql+psycopg://", 1)


def _async_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://", 1)


def _rewrite_credentials(dsn: str, user: str, password: str) -> str:
    parsed = urlparse(dsn)
    netloc = f"{user}:{password}@{parsed.hostname}"
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


def _provision_app_role(sync_dsn: str) -> None:
    """Non-superuser app role + CRUD grants + ``audit_reader`` membership.

    Idempotent — the integration session reuses the container. The
    ``GRANT audit_reader TO`` membership lets the store's
    ``SET LOCAL ROLE audit_reader`` succeed (production provisions the
    same way).
    """
    admin = create_engine(sync_dsn, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :r"),
                {"r": APP_ROLE},
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
            conn.execute(text(f"GRANT audit_reader TO {APP_ROLE}"))
    finally:
        admin.dispose()


@pytest.fixture
def member_store(
    postgres_container: PostgresContainer,
) -> Iterator[tuple[SqlTenantMemberStore, AsyncEngine]]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")
    _provision_app_role(_sync_dsn(postgres_container))
    app_dsn = _rewrite_credentials(_async_dsn(postgres_container), APP_ROLE, APP_PASSWORD)
    engine = create_async_engine_from_config(DatabaseConfig(dsn=app_dsn))
    session_factory = build_rls_sessionmaker(create_async_session_factory(engine))
    yield SqlTenantMemberStore(session_factory), engine


@pytest.fixture(autouse=True)
def reset_rls_context() -> Iterator[None]:
    t = current_tenant_id_var.set(None)
    b = bypass_rls_var.set(False)
    try:
        yield
    finally:
        current_tenant_id_var.reset(t)
        bypass_rls_var.reset(b)


@contextmanager
def _bypass_rls() -> Iterator[None]:
    """Mirror the API's ``bypass_rls_session()``: skip the GUC, no role change here."""
    b = bypass_rls_var.set(True)
    t = current_tenant_id_var.set(None)
    try:
        yield
    finally:
        current_tenant_id_var.reset(t)
        bypass_rls_var.reset(b)


@pytest.mark.asyncio
async def test_list_all_tenants_crosses_tenants_via_set_role(
    member_store: tuple[SqlTenantMemberStore, AsyncEngine],
) -> None:
    """Cross-tenant member read sees BOTH tenants; scoped reads stay isolated.

    Without the store's ``SET LOCAL ROLE audit_reader`` this fails with an
    empty list — the app role is non-BYPASSRLS and the FORCE-RLS policy
    denies every row when the GUC is unset.
    """
    store, engine = member_store
    try:
        tenant_a, tenant_b = UUID(int=1), UUID(int=2)

        # Seed under each tenant's RLS scope so WITH CHECK passes.
        current_tenant_id_var.set(tenant_a)
        await store.create(tenant_id=tenant_a, email="a@t1.com", role="viewer", invited_by="x")

        current_tenant_id_var.set(tenant_b)
        await store.create(tenant_id=tenant_b, email="b@t2.com", role="operator", invited_by="y")

        # Isolation: a tenant-scoped read sees only its own member.
        current_tenant_id_var.set(tenant_a)
        a_rows = await store.list_for_tenant(tenant_id=tenant_a)
        assert [r.tenant_id for r in a_rows] == [tenant_a]

        # Cross-tenant platform-admin read — same code path the API uses.
        # The integration suite shares one Postgres container, so other tests
        # seed their own tenant_member rows; assert our two tenants are present
        # (subset), not exact-equality on the whole table.
        with _bypass_rls():
            all_rows = await store.list_all_tenants(limit=1000)
        mine = [r for r in all_rows if r.tenant_id in {tenant_a, tenant_b}]
        assert {r.tenant_id for r in mine} == {tenant_a, tenant_b}
        assert len(mine) == 2
    finally:
        await engine.dispose()
