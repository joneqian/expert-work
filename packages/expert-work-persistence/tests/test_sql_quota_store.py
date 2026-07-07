"""Integration tests for :class:`SqlTenantQuotaStore` + :class:`SqlTokenReservationStore`."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import pytest
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
from expert_work.persistence.quota import (
    SqlTenantQuotaStore,
    SqlTokenReservationStore,
)
from expert_work.persistence.rls import build_rls_sessionmaker, current_tenant_id_var
from expert_work.protocol import (
    QuotaDimension,
    ReservationState,
    TenantQuotaPatch,
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
def quota_stores(
    postgres_container: PostgresContainer,
) -> Iterator[tuple[SqlTenantQuotaStore, SqlTokenReservationStore, AsyncEngine]]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")
    _provision_app_role(_sync_dsn(postgres_container))

    app_dsn = _rewrite_credentials(_async_dsn(postgres_container), APP_ROLE, APP_PASSWORD)
    engine = create_async_engine_from_config(DatabaseConfig(dsn=app_dsn))
    session_factory = build_rls_sessionmaker(create_async_session_factory(engine))
    quota_store = SqlTenantQuotaStore(session_factory)
    reservation_store = SqlTokenReservationStore(session_factory)
    yield quota_store, reservation_store, engine


@pytest.fixture(autouse=True)
def reset_rls() -> Iterator[None]:
    token = current_tenant_id_var.set(None)
    try:
        yield
    finally:
        current_tenant_id_var.reset(token)


# ---------------------------------------------------------------------------
# tenant_quota CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_then_list_round_trip(
    quota_stores: tuple[SqlTenantQuotaStore, SqlTokenReservationStore, AsyncEngine],
) -> None:
    quota_store, _, engine = quota_stores
    try:
        tenant = uuid4()
        current_tenant_id_var.set(tenant)
        patch = TenantQuotaPatch(
            dimension=QuotaDimension.QPS,
            scope={"agent": "alpha"},
            limit_value=20,
            burst=40,
        )
        created = await quota_store.upsert(tenant_id=tenant, patch=patch, updated_by="admin")
        assert created.tenant_id == tenant
        assert created.scope == {"agent": "alpha"}
        assert created.limit_value == 20

        # Upsert again with a different limit — same row updates in place.
        patch2 = TenantQuotaPatch(
            dimension=QuotaDimension.QPS,
            scope={"agent": "alpha"},
            limit_value=50,
            burst=100,
        )
        updated = await quota_store.upsert(tenant_id=tenant, patch=patch2, updated_by="admin")
        assert updated.id == created.id
        assert updated.limit_value == 50

        rows = await quota_store.list_by_tenant(tenant_id=tenant)
        assert len(rows) == 1
        assert rows[0].limit_value == 50
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_delete_returns_true_only_when_row_existed(
    quota_stores: tuple[SqlTenantQuotaStore, SqlTokenReservationStore, AsyncEngine],
) -> None:
    quota_store, _, engine = quota_stores
    try:
        tenant = uuid4()
        current_tenant_id_var.set(tenant)
        row = await quota_store.upsert(
            tenant_id=tenant,
            patch=TenantQuotaPatch(dimension=QuotaDimension.QPS, scope={}, limit_value=1, burst=1),
            updated_by="admin",
        )
        # Bind the await result before asserting — ``python -O`` strips
        # ``assert`` and would drop the mutating ``delete`` call entirely.
        first = await quota_store.delete(quota_id=row.id, tenant_id=tenant)
        second = await quota_store.delete(quota_id=row.id, tenant_id=tenant)
        assert first is True
        assert second is False
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# reservation flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reserve_commit_flow_updates_ledger(
    quota_stores: tuple[SqlTenantQuotaStore, SqlTokenReservationStore, AsyncEngine],
) -> None:
    _, reservation_store, engine = quota_stores
    try:
        tenant = uuid4()
        current_tenant_id_var.set(tenant)
        row = await reservation_store.reserve(
            tenant_id=tenant,
            agent_name="alpha",
            thread_id=uuid4(),
            estimated=400,
        )
        assert row.state is ReservationState.RESERVED

        month = datetime.now(tz=UTC).date().replace(day=1)
        budget = await reservation_store.get_budget(tenant_id=tenant, month=month)
        assert budget is not None
        assert budget.reserved_total == 400

        committed = await reservation_store.commit(
            reservation_id=row.id,
            tenant_id=tenant,
            actual_tokens=320,
        )
        assert committed.state is ReservationState.COMMITTED
        assert committed.actual == 320

        budget = await reservation_store.get_budget(tenant_id=tenant, month=month)
        assert budget is not None
        assert budget.used_total == 320
        assert budget.reserved_total == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_release_refunds_reserved_total(
    quota_stores: tuple[SqlTenantQuotaStore, SqlTokenReservationStore, AsyncEngine],
) -> None:
    _, reservation_store, engine = quota_stores
    try:
        tenant = uuid4()
        current_tenant_id_var.set(tenant)
        row = await reservation_store.reserve(
            tenant_id=tenant,
            agent_name="alpha",
            thread_id=uuid4(),
            estimated=200,
        )
        released = await reservation_store.release(reservation_id=row.id, tenant_id=tenant)
        assert released.state is ReservationState.RELEASED

        month = datetime.now(tz=UTC).date().replace(day=1)
        budget = await reservation_store.get_budget(tenant_id=tenant, month=month)
        assert budget is not None
        assert budget.reserved_total == 0
        assert budget.used_total == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_list_expired_finds_stale_reservations(
    quota_stores: tuple[SqlTenantQuotaStore, SqlTokenReservationStore, AsyncEngine],
) -> None:
    """Back-date a reservation by updating reserved_at directly via SQL."""
    _, reservation_store, engine = quota_stores
    try:
        tenant = uuid4()
        current_tenant_id_var.set(tenant)
        row = await reservation_store.reserve(
            tenant_id=tenant,
            agent_name="alpha",
            thread_id=uuid4(),
            estimated=100,
        )
        # Back-date by 1 hour via the engine directly. Cannot use the
        # store's own factory (RLS would block raw UPDATE on a row not
        # in the right tenant context — actually it would work since
        # we're scoped to ``tenant``, but the SET LOCAL machinery only
        # fires through the wrapped factory).
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"),
                {"tid": str(tenant)},
            )
            await conn.execute(
                text(
                    "UPDATE token_reservation "
                    "SET reserved_at = reserved_at - interval '1 hour' "
                    "WHERE id = :rid"
                ),
                {"rid": str(row.id)},
            )

        expired = await reservation_store.list_expired(max_age_seconds=600)
        assert len(expired) == 1
        assert expired[0].id == row.id
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Stream 9.5 — exactly-once under real concurrency (FOR UPDATE + atomic ledger)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_expire_reserved_wins_exactly_once(
    quota_stores: tuple[SqlTenantQuotaStore, SqlTokenReservationStore, AsyncEngine],
) -> None:
    """16 reapers race one stale reservation — the ``FOR UPDATE`` lock lets
    exactly one ``expire_reserved`` win, and the ledger is refunded once
    (``reserved_total`` lands on 0, never negative)."""
    _, reservation_store, engine = quota_stores
    try:
        tenant = uuid4()
        current_tenant_id_var.set(tenant)
        row = await reservation_store.reserve(
            tenant_id=tenant,
            agent_name="alpha",
            thread_id=uuid4(),
            estimated=100,
        )

        async def _expire() -> bool:
            return await reservation_store.expire_reserved(reservation_id=row.id, tenant_id=tenant)

        results = await asyncio.gather(*[_expire() for _ in range(16)])
        assert sum(1 for won in results if won) == 1

        final = await reservation_store.get(reservation_id=row.id, tenant_id=tenant)
        assert final is not None
        assert final.state is ReservationState.EXPIRED

        month = datetime.now(tz=UTC).date().replace(day=1)
        budget = await reservation_store.get_budget(tenant_id=tenant, month=month)
        assert budget is not None
        assert budget.reserved_total == 0  # refunded exactly once
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_reserve_no_lost_update(
    quota_stores: tuple[SqlTenantQuotaStore, SqlTokenReservationStore, AsyncEngine],
) -> None:
    """N concurrent reserves on the same ``(tenant, month)`` ledger row — the
    atomic SQL increment means none of the ``+= estimated`` updates is lost."""
    _, reservation_store, engine = quota_stores
    try:
        tenant = uuid4()
        current_tenant_id_var.set(tenant)

        async def _reserve() -> None:
            await reservation_store.reserve(
                tenant_id=tenant,
                agent_name="alpha",
                thread_id=uuid4(),
                estimated=10,
            )

        await asyncio.gather(*[_reserve() for _ in range(16)])

        month = datetime.now(tz=UTC).date().replace(day=1)
        budget = await reservation_store.get_budget(tenant_id=tenant, month=month)
        assert budget is not None
        assert budget.reserved_total == 160  # 16 * 10 — no lost increment
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_commit_release_refunds_once(
    quota_stores: tuple[SqlTenantQuotaStore, SqlTokenReservationStore, AsyncEngine],
) -> None:
    """A client commit racing the reaper's release on one reservation — the
    row lock serialises them so exactly one transition lands and the reserved
    budget is refunded once (never double-decremented)."""
    _, reservation_store, engine = quota_stores
    try:
        tenant = uuid4()
        current_tenant_id_var.set(tenant)
        row = await reservation_store.reserve(
            tenant_id=tenant,
            agent_name="alpha",
            thread_id=uuid4(),
            estimated=200,
        )

        async def _commit() -> str:
            r = await reservation_store.commit(
                reservation_id=row.id, tenant_id=tenant, actual_tokens=200
            )
            return r.state.value

        async def _release() -> str:
            r = await reservation_store.release(reservation_id=row.id, tenant_id=tenant)
            return r.state.value

        await asyncio.gather(_commit(), _release())

        final = await reservation_store.get(reservation_id=row.id, tenant_id=tenant)
        assert final is not None
        # Whichever won, the row is terminal and reserved is refunded once.
        assert final.state in (ReservationState.COMMITTED, ReservationState.RELEASED)

        month = datetime.now(tz=UTC).date().replace(day=1)
        budget = await reservation_store.get_budget(tenant_id=tenant, month=month)
        assert budget is not None
        assert budget.reserved_total == 0  # refunded once, not -200
        # used_total is 200 iff commit won, else 0 — never double-counted.
        assert budget.used_total in (0, 200)
    finally:
        await engine.dispose()
