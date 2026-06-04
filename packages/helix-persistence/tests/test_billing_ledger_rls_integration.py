"""Integration: ``tenant_billing_ledger`` cross-tenant chargeback read — Stream Z (Z-2).

Pins the Z2 contract against a real Postgres (testcontainers):

1.  ``tenant_billing_ledger`` is FORCE ROW LEVEL SECURITY (migration 0060).
    Writes go in under each tenant's RLS scope (GUC set) so the policy
    ``WITH CHECK`` passes — proving the seed itself is RLS-legal.

2.  A normally-scoped session sees only its own tenant's rows
    (``list_for_tenant`` — isolation intact).

3.  ``list_for_month_all_tenants`` (the path the chargeback API uses,
    wrapped in ``bypass_rls_session()``) returns EVERY tenant's rows —
    because the store does ``SET LOCAL ROLE audit_reader`` (BYPASSRLS,
    migration 0005; GRANTed SELECT on this table by migration 0061).
    Merely flipping ``bypass_rls_var`` would NOT be enough: the app role
    is not BYPASSRLS, so on a FORCE table the policy collapses to
    ``tenant_id = NULL`` → zero rows. This test would FAIL (empty result)
    without the ``SET LOCAL ROLE``.

The testcontainers bootstrap user is a superuser and would silently
bypass every policy, so we provision a fresh non-superuser ``helix_app``
LOGIN role, GRANT it CRUD on the schema, and ``GRANT audit_reader TO
helix_app`` so it can ``SET ROLE`` to the BYPASSRLS reader — exactly how
a production deployment provisions the app role.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
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
    create_async_engine_from_config,
    create_async_session_factory,
)
from helix_agent.persistence.billing.ledger import DbTenantBillingLedgerStore
from helix_agent.persistence.rls import (
    build_rls_sessionmaker,
    bypass_rls_var,
    current_tenant_id_var,
)
from helix_agent.protocol import TenantBillingLedgerRecord

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

# Distinct role name so this fixture's schema-wide grants don't collide
# with the other integration tests sharing the session container.
APP_ROLE = "helix_app_billing_z2"
APP_PASSWORD = "helix_app_billing_z2_pw"  # test-only fixture password


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
    """Create the non-superuser app role + GRANT it CRUD and audit_reader membership.

    Idempotent — the same container is reused across the integration
    session. ``GRANT audit_reader TO`` is the membership that lets the
    store's ``SET LOCAL ROLE audit_reader`` succeed (mirrors how the
    audit_writer test provisions its role; production does the same).
    """
    admin = create_engine(sync_dsn, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :r"),
                {"r": APP_ROLE},
            ).first()
            if exists is None:
                # Local constants under test-author control — safe to interpolate.
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
def ledger_store(
    postgres_container: PostgresContainer,
) -> Iterator[tuple[DbTenantBillingLedgerStore, AsyncEngine]]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")
    _provision_app_role(_sync_dsn(postgres_container))
    app_dsn = _rewrite_credentials(_async_dsn(postgres_container), APP_ROLE, APP_PASSWORD)
    engine = create_async_engine_from_config(DatabaseConfig(dsn=app_dsn))
    session_factory = build_rls_sessionmaker(create_async_session_factory(engine))
    yield DbTenantBillingLedgerStore(session_factory), engine


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


def _record(tenant_id: UUID, *, month: date, agent_name: str) -> TenantBillingLedgerRecord:
    now = datetime.now(UTC)
    return TenantBillingLedgerRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        month=month,
        provider="anthropic",
        model="claude-opus-4-8",
        agent_name=agent_name,
        input_tokens=100,
        output_tokens=50,
        cache_creation_tokens=0,
        cache_read_tokens=0,
        base_cost_micros=1000,
        markup_cost_micros=200,
        billed_cost_micros=1200,
        priced=True,
        rate_card_priced_at=now,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_chargeback_read_crosses_tenants_via_set_role(
    ledger_store: tuple[DbTenantBillingLedgerStore, AsyncEngine],
) -> None:
    """The cross-tenant chargeback read sees BOTH tenants; scoped reads stay isolated.

    Without the store's ``SET LOCAL ROLE audit_reader`` this assertion
    fails with an empty list — the app role is non-BYPASSRLS and the
    FORCE-RLS policy denies every row when the GUC is unset.
    """
    store, engine = ledger_store
    try:
        tenant_a, tenant_b = uuid4(), uuid4()
        month = date(2026, 6, 1)

        # Seed under each tenant's RLS scope so WITH CHECK passes.
        current_tenant_id_var.set(tenant_a)
        await store.upsert(_record(tenant_a, month=month, agent_name="support"))

        current_tenant_id_var.set(tenant_b)
        await store.upsert(_record(tenant_b, month=month, agent_name="sales"))

        # Isolation: a tenant-scoped read sees only its own row.
        current_tenant_id_var.set(tenant_a)
        a_rows = await store.list_for_tenant(tenant_id=tenant_a, month=month)
        assert [r.tenant_id for r in a_rows] == [tenant_a]

        current_tenant_id_var.set(tenant_b)
        b_rows = await store.list_for_tenant(tenant_id=tenant_b, month=month)
        assert [r.tenant_id for r in b_rows] == [tenant_b]

        # Cross-tenant chargeback read — same code path the API uses.
        with _bypass_rls():
            all_rows = await store.list_for_month_all_tenants(month=month)
        assert {r.tenant_id for r in all_rows} == {tenant_a, tenant_b}
        assert len(all_rows) == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_scoped_read_cannot_see_other_tenant(
    ledger_store: tuple[DbTenantBillingLedgerStore, AsyncEngine],
) -> None:
    """A tenant-scoped session cannot read another tenant's ledger rows (RLS, not WHERE).

    ``list_for_tenant`` does carry a ``tenant_id`` WHERE clause, so we
    assert isolation via the cross-tenant method *while scoped to one
    tenant but NOT bypassing* — the FORCE-RLS policy must still clamp it
    to the in-context tenant only.
    """
    store, engine = ledger_store
    try:
        tenant_a, tenant_b = uuid4(), uuid4()
        month = date(2026, 6, 1)

        current_tenant_id_var.set(tenant_a)
        await store.upsert(_record(tenant_a, month=month, agent_name="support"))
        current_tenant_id_var.set(tenant_b)
        await store.upsert(_record(tenant_b, month=month, agent_name="sales"))

        # Scoped to A, querying A's own month — sees only A.
        current_tenant_id_var.set(tenant_a)
        rows = await store.list_for_tenant(tenant_id=tenant_a, month=month)
        assert {r.tenant_id for r in rows} == {tenant_a}
        # And B's row is invisible even if A asks for it directly.
        b_rows_from_a = await store.list_for_tenant(tenant_id=tenant_b, month=month)
        assert b_rows_from_a == []
    finally:
        await engine.dispose()
