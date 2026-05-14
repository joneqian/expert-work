"""Integration: ``RetentionCleanupJob`` end-to-end against real Postgres.

Seeds rows into ``audit_log`` (mix of acked + unacked, old + new),
``event_log`` (old + new), and ``jwt_blacklist`` (expired + future).
Runs ``run_once`` and verifies:

1.  Only acked + past-retention audit rows are deleted.
2.  Unacked rows past retention are counted in ``audit_skipped_unacked``
    but **never** deleted.
3.  event_log honours per-tenant ``event_log_retention_days``.
4.  ``jwt_blacklist`` rows past ``expires_at`` are deleted.

The test app role is granted both ``audit_writer`` (for seeding) and
``retention_cleanup_worker`` (the role the job assumes).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
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
    SqlAuditLogStore,
    create_async_engine_from_config,
    create_async_session_factory,
)
from helix_agent.protocol import (
    AuditAction,
    AuditEntry,
    AuditResult,
)
from retention_cleanup_job.job import RetentionCleanupJob

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parents[3] / "packages/helix-persistence/alembic.ini"

APP_ROLE = "helix_app_d3_retention"
APP_PASSWORD = "helix_app_d3_retention_pw"  # test-only fixture password


def _sync_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+psycopg").replace("postgresql://", "postgresql+psycopg://", 1)


def _async_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://", 1)


def _rewrite(dsn: str, user: str, password: str) -> str:
    parsed = urlparse(dsn)
    netloc = f"{user}:{password}@{parsed.hostname}"
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


def _provision_app_role(sync_dsn: str) -> None:
    """Create NOINHERIT app role with all three D-stream worker memberships."""
    admin = create_engine(sync_dsn, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}
            ).first()
            if exists is None:
                conn.execute(
                    text(f"CREATE ROLE {APP_ROLE} LOGIN NOINHERIT PASSWORD '{APP_PASSWORD}'")
                )
            conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}"))
            # Read access to inspect after the sweep.
            conn.execute(
                text(
                    f"GRANT SELECT ON TABLE audit_log, event_log, jwt_blacklist, "
                    f"tenant_config TO {APP_ROLE}"
                )
            )
            # Seeding event_log + jwt_blacklist + tenant_config directly.
            conn.execute(
                text(f"GRANT INSERT ON TABLE event_log, jwt_blacklist, tenant_config TO {APP_ROLE}")
            )
            # ``SqlTenantConfigStore.upsert`` uses ON CONFLICT DO UPDATE
            # which requires UPDATE on the target table even on the
            # first insert path.
            conn.execute(text(f"GRANT UPDATE ON TABLE tenant_config TO {APP_ROLE}"))
            # Memberships in the three worker roles (NOINHERIT means the
            # app role doesn't inherit privileges; it must SET ROLE).
            conn.execute(text(f"GRANT audit_writer TO {APP_ROLE}"))
            conn.execute(text(f"GRANT retention_cleanup_worker TO {APP_ROLE}"))
    finally:
        admin.dispose()


@pytest.fixture
def db_fixture(
    postgres_container: PostgresContainer,
) -> Iterator[tuple[AsyncEngine, str]]:
    sync_admin_dsn = _sync_dsn(postgres_container)
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", sync_admin_dsn)
    command.upgrade(cfg, "head")
    _provision_app_role(sync_admin_dsn)

    # The session-scoped ``postgres_container`` is shared across tests
    # in this module, so a prior test's leftover rows would leak into
    # later tests' ``audit_skipped_unacked`` / row counts. TRUNCATE
    # the three tables this job touches as the bootstrap superuser
    # before every case.
    admin = create_engine(sync_admin_dsn, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text("TRUNCATE TABLE audit_log RESTART IDENTITY"))
            conn.execute(text("TRUNCATE TABLE event_log RESTART IDENTITY"))
            conn.execute(text("TRUNCATE TABLE jwt_blacklist"))
            conn.execute(text("TRUNCATE TABLE tenant_config CASCADE"))
    finally:
        admin.dispose()

    app_dsn = _rewrite(_async_dsn(postgres_container), APP_ROLE, APP_PASSWORD)
    engine = create_async_engine_from_config(DatabaseConfig(dsn=app_dsn))
    yield engine, sync_admin_dsn


def _seed_tenant_config(
    sync_admin_dsn: str,
    *,
    tenant_id: UUID,
    audit_days: int,
    event_days: int,
) -> None:
    """Direct INSERT as the bootstrap superuser.

    Bypasses ``SqlTenantConfigStore.upsert`` because that path opens a
    fresh session without setting ``app.tenant_id``, which the
    ``tenant_config`` RLS policy needs. Production goes through
    ``TenantConfigService`` (admin endpoint middleware sets the GUC);
    this test exercises the cleanup job, not the seeding path.
    """
    admin = create_engine(sync_admin_dsn, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO tenant_config "
                    "(tenant_id, display_name, plan, "
                    " audit_retention_days, event_log_retention_days, updated_by) "
                    "VALUES (:t, 'acme', 'free', :a, :e, 'test')"
                ),
                {"t": str(tenant_id), "a": audit_days, "e": event_days},
            )
    finally:
        admin.dispose()


def _audit_entry(tenant_id: UUID, *, suffix: str) -> AuditEntry:
    return AuditEntry(
        tenant_id=tenant_id,
        actor_type="user",
        actor_id=f"alice-{suffix}",
        action=AuditAction.AUTH_LOGIN,
        resource_type="user",
        resource_id=f"alice-{suffix}",
        result=AuditResult.SUCCESS,
        details={},
    )


def _set_audit_row_age(
    sync_admin_dsn: str, *, audit_id: int, days_ago: int, backup_acked: bool
) -> None:
    """Time-travel one audit_log row + flip its backup_acked marker.

    Both UPDATE columns require privileged grants the app role
    doesn't have under the D.1a regime: ``occurred_at`` was never
    re-granted to anyone, and ``backup_acked`` belongs to
    ``audit_backup_worker``. The test fixture cheats by running the
    UPDATE as the bootstrap superuser via a direct sync connection.
    """
    admin = create_engine(sync_admin_dsn, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(
                text(
                    "UPDATE audit_log SET "
                    "occurred_at = now() - (:days || ' days')::interval, "
                    "backup_acked = :acked, "
                    "backup_acked_at = CASE WHEN :acked THEN now() ELSE NULL END "
                    "WHERE id = :id"
                ),
                {"days": days_ago, "acked": backup_acked, "id": audit_id},
            )
    finally:
        admin.dispose()


@pytest.mark.asyncio
async def test_audit_log_acked_old_rows_deleted_unacked_skipped(
    db_fixture: tuple[AsyncEngine, str],
) -> None:
    """Acked + past-retention → deleted. Unacked + past-retention → preserved + counted."""
    engine, sync_admin = db_fixture
    try:
        tenant = uuid4()
        _seed_tenant_config(sync_admin, tenant_id=tenant, audit_days=30, event_days=30)

        sf = create_async_session_factory(engine)
        store = SqlAuditLogStore(sf)
        ids: list[int] = []
        for i in range(3):
            written = await store.append(_audit_entry(tenant, suffix=str(i)))
            assert written.id is not None
            ids.append(written.id)

        # Row 0: acked + old → should be deleted
        _set_audit_row_age(sync_admin, audit_id=ids[0], days_ago=60, backup_acked=True)
        # Row 1: unacked + old → must be preserved + counted
        _set_audit_row_age(sync_admin, audit_id=ids[1], days_ago=60, backup_acked=False)
        # Row 2: acked + recent → not eligible
        _set_audit_row_age(sync_admin, audit_id=ids[2], days_ago=1, backup_acked=True)

        job = RetentionCleanupJob(db_session_factory=sf, batch_size=10000)
        report = await job.run_once()

        assert report.audit_deleted == 1
        assert report.audit_skipped_unacked == 1
        assert report.audit_deleted_by_tenant == {str(tenant): 1}

        # Spot-check the surviving rows.
        async with engine.begin() as conn:
            await conn.execute(text("SET LOCAL ROLE audit_writer"))
            still_there = (
                await conn.execute(
                    text("SELECT id FROM audit_log WHERE tenant_id = :t ORDER BY id"),
                    {"t": tenant},
                )
            ).all()
        surviving = {r[0] for r in still_there}
        assert ids[0] not in surviving  # deleted
        assert ids[1] in surviving  # unacked → kept
        assert ids[2] in surviving  # recent → kept
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_per_tenant_retention_isolated(
    db_fixture: tuple[AsyncEngine, str],
) -> None:
    """Tenant A's 7-day TTL deletes its acked-old rows; tenant B's 90-day keeps theirs."""
    engine, sync_admin = db_fixture
    try:
        tenant_a = uuid4()
        tenant_b = uuid4()
        _seed_tenant_config(sync_admin, tenant_id=tenant_a, audit_days=7, event_days=30)
        _seed_tenant_config(sync_admin, tenant_id=tenant_b, audit_days=90, event_days=30)

        sf = create_async_session_factory(engine)
        store = SqlAuditLogStore(sf)
        a_row = await store.append(_audit_entry(tenant_a, suffix="A"))
        b_row = await store.append(_audit_entry(tenant_b, suffix="B"))
        assert a_row.id is not None and b_row.id is not None

        # Both 30 days old, both acked.
        _set_audit_row_age(sync_admin, audit_id=a_row.id, days_ago=30, backup_acked=True)
        _set_audit_row_age(sync_admin, audit_id=b_row.id, days_ago=30, backup_acked=True)

        job = RetentionCleanupJob(db_session_factory=sf, batch_size=10000)
        report = await job.run_once()

        assert report.audit_deleted == 1
        assert report.audit_deleted_by_tenant == {str(tenant_a): 1}

        async with engine.begin() as conn:
            await conn.execute(text("SET LOCAL ROLE audit_writer"))
            survivors = {r[0] for r in (await conn.execute(text("SELECT id FROM audit_log"))).all()}
        assert a_row.id not in survivors
        assert b_row.id in survivors
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_event_log_retention_deletes_old_rows(
    db_fixture: tuple[AsyncEngine, str],
) -> None:
    """event_log uses ``event_log_retention_days``; no backup gate."""
    engine, sync_admin = db_fixture
    try:
        tenant = uuid4()
        _seed_tenant_config(sync_admin, tenant_id=tenant, audit_days=90, event_days=7)

        admin = create_engine(sync_admin, isolation_level="AUTOCOMMIT")
        try:
            with admin.connect() as conn:
                # ``event_log`` schema (migration 0001): id BigInt
                # autoincrement; columns are ``event_type`` /
                # ``payload`` etc. ``thread_id`` is UUID, not a
                # generated string.
                insert_old = text(
                    "INSERT INTO event_log "
                    "(thread_id, tenant_id, seq, event_type, payload, created_at) "
                    "VALUES (gen_random_uuid(), :t, 1, "
                    "'tick', '{}'::jsonb, now() - interval '30 days')"
                )
                insert_recent = text(
                    "INSERT INTO event_log "
                    "(thread_id, tenant_id, seq, event_type, payload, created_at) "
                    "VALUES (gen_random_uuid(), :t, 2, 'tick', '{}'::jsonb, now())"
                )
                conn.execute(insert_old, {"t": str(tenant)})
                conn.execute(insert_recent, {"t": str(tenant)})
        finally:
            admin.dispose()

        sf = create_async_session_factory(engine)
        job = RetentionCleanupJob(db_session_factory=sf, batch_size=10000)
        report = await job.run_once()
        assert report.event_deleted == 1

        async with engine.begin() as conn:
            await conn.execute(text("SET LOCAL ROLE audit_writer"))
            remaining = (
                await conn.execute(
                    text("SELECT count(*) FROM event_log WHERE tenant_id = :t"),
                    {"t": tenant},
                )
            ).scalar()
        assert remaining == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_jwt_blacklist_expired_rows_deleted(
    db_fixture: tuple[AsyncEngine, str],
) -> None:
    """jwt_blacklist is global; ``expires_at < now()`` rows are pruned."""
    engine, sync_admin = db_fixture
    try:
        # Seed one expired + one future row directly.
        expired_jti = "jti-expired"
        future_jti = "jti-future"
        admin = create_engine(sync_admin, isolation_level="AUTOCOMMIT")
        try:
            past = datetime.now(tz=UTC) - timedelta(days=1)
            future = datetime.now(tz=UTC) + timedelta(days=30)
            with admin.connect() as conn:
                conn.execute(
                    text(
                        "INSERT INTO jwt_blacklist (jti, reason, expires_at) "
                        "VALUES (:j, 'test', :e)"
                    ),
                    {"j": expired_jti, "e": past},
                )
                conn.execute(
                    text(
                        "INSERT INTO jwt_blacklist (jti, reason, expires_at) "
                        "VALUES (:j, 'test', :e)"
                    ),
                    {"j": future_jti, "e": future},
                )
        finally:
            admin.dispose()

        sf = create_async_session_factory(engine)
        job = RetentionCleanupJob(db_session_factory=sf, batch_size=10000)
        report = await job.run_once()
        assert report.jwt_blacklist_deleted == 1

        async with engine.begin() as conn:
            await conn.execute(text("SET LOCAL ROLE audit_writer"))
            remaining_jtis = {
                r[0] for r in (await conn.execute(text("SELECT jti FROM jwt_blacklist"))).all()
            }
        assert expired_jti not in remaining_jtis
        assert future_jti in remaining_jtis
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_run_once_idempotent_on_empty_state(
    db_fixture: tuple[AsyncEngine, str],
) -> None:
    """No old rows → all-zero report; safe to run repeatedly."""
    engine, sync_admin = db_fixture
    try:
        tenant = uuid4()
        _seed_tenant_config(sync_admin, tenant_id=tenant, audit_days=90, event_days=30)

        sf = create_async_session_factory(engine)
        job = RetentionCleanupJob(db_session_factory=sf, batch_size=10000)
        report = await job.run_once()
        assert report.audit_deleted == 0
        assert report.event_deleted == 0
        assert report.audit_skipped_unacked == 0
    finally:
        await engine.dispose()
