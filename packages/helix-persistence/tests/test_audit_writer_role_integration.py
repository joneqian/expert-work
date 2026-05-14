"""Integration: ``audit_writer`` role + REVOKE UPDATE/DELETE/TRUNCATE.

Pins the D.1a contract (STREAM-D-DESIGN § 2.2 + Mini-ADR D-1):

1.  An application role that has been ``GRANT``ed ``audit_writer`` can
    insert into ``audit_log`` via :class:`SqlAuditLogStore.append` —
    because ``append`` does ``SET LOCAL ROLE audit_writer`` inside the
    write transaction.

2.  Without ``SET LOCAL ROLE`` the same application role can NOT issue
    a raw ``INSERT`` either (defense-in-depth: even if a future caller
    bypasses ``SqlAuditLogStore`` it cannot quietly write rows from the
    default role).

3.  ``UPDATE`` / ``DELETE`` / ``TRUNCATE`` are blocked at the DB layer
    for the application role no matter which role is active — those
    grants were never given to anyone but the table owner. Even
    ``audit_writer`` itself doesn't have them.

4.  The new ``backup_acked`` column defaults to ``false`` and is
    nullable for ``backup_acked_at``; the partial index ``audit_log_
    backup_pending_idx`` exists.

The bootstrap user that ``testcontainers`` ships is a superuser and
would silently bypass every check above, so we provision a fresh
``helix_app`` LOGIN role for this test, narrow its grants on
``audit_log`` to just ``SELECT``, and then ``GRANT audit_writer TO
helix_app`` so it can assume the writer role.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from helix_agent.persistence import (
    DatabaseConfig,
    SqlAuditLogStore,
    create_async_engine_from_config,
    create_async_session_factory,
)
from helix_agent.protocol import AuditAction, AuditEntry, AuditResult

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

# Test-only role, distinct from the RLS test fixture so the two
# integration tests don't fight over schema-wide grants.
APP_ROLE = "helix_app_audit_d1a"
APP_PASSWORD = "helix_app_audit_d1a_pw"  # test-only fixture password


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
    """Create the non-superuser app role + give it the minimum surface for D.1a.

    Steps (idempotent — the same Postgres container is reused across
    integration tests in one session):

    * CREATE ROLE LOGIN if missing
    * GRANT USAGE on schema
    * GRANT SELECT on ``audit_log`` (so reads / verification still work)
    * GRANT INSERT on the other tables it might write through other
      D-stream paths (none in this PR; placeholder kept narrow)
    * GRANT ``audit_writer`` TO the app role — the membership is what
      lets ``SET LOCAL ROLE audit_writer`` succeed.
    """
    admin = create_engine(sync_dsn, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :r"),
                {"r": APP_ROLE},
            ).first()
            if exists is None:
                # ``APP_ROLE`` and ``APP_PASSWORD`` are local constants
                # under test-author control — safe to interpolate.
                conn.execute(text(f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}'"))
            conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}"))
            conn.execute(text(f"GRANT SELECT ON TABLE audit_log TO {APP_ROLE}"))
            conn.execute(text(f"GRANT audit_writer TO {APP_ROLE}"))
    finally:
        admin.dispose()


@pytest.fixture
def app_role_store(
    postgres_container: PostgresContainer,
) -> Iterator[tuple[SqlAuditLogStore, AsyncEngine]]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")

    _provision_app_role(_sync_dsn(postgres_container))

    app_dsn = _rewrite_credentials(_async_dsn(postgres_container), APP_ROLE, APP_PASSWORD)
    engine = create_async_engine_from_config(DatabaseConfig(dsn=app_dsn))
    session_factory = create_async_session_factory(engine)
    yield SqlAuditLogStore(session_factory), engine


def _entry(tenant_id: UUID) -> AuditEntry:
    return AuditEntry(
        tenant_id=tenant_id,
        actor_type="user",
        actor_id="alice",
        action=AuditAction.AUTH_LOGIN,
        resource_type="user",
        resource_id="alice",
        result=AuditResult.SUCCESS,
        details={"k": "v"},
    )


@pytest.mark.asyncio
async def test_app_role_can_append_via_set_role(
    app_role_store: tuple[SqlAuditLogStore, AsyncEngine],
) -> None:
    """SqlAuditLogStore.append uses ``SET LOCAL ROLE audit_writer`` and succeeds."""
    store, engine = app_role_store
    try:
        written = await store.append(_entry(uuid4()))
        assert written.id is not None
        # backup_acked defaults to false; the WORM-backup worker (D.1c)
        # is the only thing that flips it.
        assert written is not None  # smoke: no exception thrown
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_app_role_cannot_raw_insert_without_set_role(
    app_role_store: tuple[SqlAuditLogStore, AsyncEngine],
) -> None:
    """Without ``SET LOCAL ROLE``, raw INSERT from the app role is denied."""
    _, engine = app_role_store
    try:
        async with engine.begin() as conn:
            with pytest.raises(ProgrammingError) as excinfo:
                await conn.execute(
                    text(
                        "INSERT INTO audit_log "
                        "(tenant_id, actor_type, actor_id, action, "
                        " resource_type, result, details) "
                        "VALUES (:tid, 'user', 'alice', 'auth:login', "
                        "        'user', 'success', '{}'::jsonb)"
                    ),
                    {"tid": uuid4()},
                )
        # asyncpg surfaces this as InsufficientPrivilegeError wrapped
        # by SQLAlchemy as ProgrammingError; the SQLSTATE is 42501.
        assert "permission denied" in str(excinfo.value).lower()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_app_role_cannot_update_audit_log(
    app_role_store: tuple[SqlAuditLogStore, AsyncEngine],
) -> None:
    """UPDATE on audit_log is REVOKEd from PUBLIC, never re-granted."""
    store, engine = app_role_store
    try:
        written = await store.append(_entry(uuid4()))
        assert written.id is not None
        async with engine.begin() as conn:
            with pytest.raises(ProgrammingError) as excinfo:
                await conn.execute(
                    text("UPDATE audit_log SET reason='tampered' WHERE id=:id"),
                    {"id": written.id},
                )
        assert "permission denied" in str(excinfo.value).lower()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_app_role_cannot_delete_audit_log(
    app_role_store: tuple[SqlAuditLogStore, AsyncEngine],
) -> None:
    store, engine = app_role_store
    try:
        written = await store.append(_entry(uuid4()))
        assert written.id is not None
        async with engine.begin() as conn:
            with pytest.raises(ProgrammingError) as excinfo:
                await conn.execute(
                    text("DELETE FROM audit_log WHERE id=:id"),
                    {"id": written.id},
                )
        assert "permission denied" in str(excinfo.value).lower()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_app_role_cannot_truncate_audit_log(
    app_role_store: tuple[SqlAuditLogStore, AsyncEngine],
) -> None:
    _, engine = app_role_store
    try:
        async with engine.begin() as conn:
            with pytest.raises(ProgrammingError) as excinfo:
                await conn.execute(text("TRUNCATE TABLE audit_log"))
        assert "permission denied" in str(excinfo.value).lower()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_backup_acked_defaults_false(
    app_role_store: tuple[SqlAuditLogStore, AsyncEngine],
) -> None:
    """New rows land with backup_acked=false; backup_acked_at NULL.

    Verified by direct SELECT (the column isn't exposed through the
    public AuditEntry contract — it's an internal worker marker).
    """
    store, engine = app_role_store
    try:
        written = await store.append(_entry(uuid4()))
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT backup_acked, backup_acked_at FROM audit_log WHERE id = :id"),
                    {"id": written.id},
                )
            ).one()
        assert row.backup_acked is False
        assert row.backup_acked_at is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_partial_index_present(
    app_role_store: tuple[SqlAuditLogStore, AsyncEngine],
) -> None:
    """The backup-pending partial index exists and has the right predicate.

    Catches a migration drift where someone re-creates the index
    without ``WHERE backup_acked = false`` — that would silently
    convert it back to a full-table index and explode in size as
    audit_log grows.
    """
    _, engine = app_role_store
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT indexdef FROM pg_indexes "
                        "WHERE indexname = 'audit_log_backup_pending_idx'"
                    )
                )
            ).first()
        assert row is not None
        indexdef = row.indexdef.lower()
        assert "where" in indexdef
        assert "backup_acked" in indexdef
        # Must filter unacked, not acked.
        assert "false" in indexdef or "= false" in indexdef
    finally:
        await engine.dispose()
