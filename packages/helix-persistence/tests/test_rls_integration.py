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
from helix_agent.persistence.feedback_store import DbFeedbackStore, FeedbackRecord
from helix_agent.persistence.rls import (
    build_rls_sessionmaker,
    bypass_rls_var,
    current_tenant_id_var,
)
from helix_agent.persistence.thread_meta import SqlThreadMetaStore

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

# Non-superuser application role created per fixture call. Postgres
# superusers — which is what the testcontainers bootstrap user
# defaults to — bypass RLS unconditionally regardless of FORCE ROW
# LEVEL SECURITY. We must connect as a normal role so the policies
# actually run.
APP_ROLE = "helix_app"
APP_PASSWORD = "helix_app_test_pw"  # test-only fixture password


def _sync_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+psycopg").replace("postgresql://", "postgresql+psycopg://", 1)


def _async_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://", 1)


def _rewrite_credentials(dsn: str, user: str, password: str) -> str:
    """Return ``dsn`` with userinfo replaced by ``user:password``."""
    parsed = urlparse(dsn)
    new_netloc = f"{user}:{password}@{parsed.hostname}"
    if parsed.port is not None:
        new_netloc = f"{new_netloc}:{parsed.port}"
    return urlunparse(parsed._replace(netloc=new_netloc))


def _provision_app_role(sync_dsn: str) -> None:
    """Create the non-superuser ``helix_app`` role and grant it CRUD on the schema.

    Idempotent: the existence check short-circuits if the same role
    has been provisioned earlier in the test session (session-scoped
    ``postgres_container`` may host multiple fixtures).
    """
    admin_engine = create_engine(sync_dsn, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
                {"role": APP_ROLE},
            ).first()
            if exists is None:
                # ``APP_ROLE`` / ``APP_PASSWORD`` are module-level
                # constants under our control, not external input —
                # safe to interpolate.
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
def rls_store(
    postgres_container: PostgresContainer,
) -> Iterator[tuple[SqlThreadMetaStore, AsyncEngine]]:
    # 1. Run migrations as the bootstrap (superuser) account so DDL —
    #    including ``CREATE POLICY`` / ``ALTER TABLE ... FORCE`` /
    #    ``CREATE ROLE audit_reader`` — is allowed.
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")

    # 2. Provision a non-superuser application role. RLS only enforces
    #    against non-superuser, non-BYPASSRLS roles; the testcontainers
    #    bootstrap user is superuser and would silently skip the
    #    policies.
    _provision_app_role(_sync_dsn(postgres_container))

    # 3. Build the application engine using the unprivileged role.
    app_async_dsn = _rewrite_credentials(_async_dsn(postgres_container), APP_ROLE, APP_PASSWORD)
    engine = create_async_engine_from_config(DatabaseConfig(dsn=app_async_dsn))
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


# ---------------------------------------------------------------------------
# feedback table — Stream G.6 (#64)
# ---------------------------------------------------------------------------


@pytest.fixture
def feedback_rls_store(
    postgres_container: PostgresContainer,
) -> Iterator[tuple[DbFeedbackStore, AsyncEngine]]:
    """A :class:`DbFeedbackStore` on the unprivileged role — RLS enforced."""
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")
    _provision_app_role(_sync_dsn(postgres_container))
    app_async_dsn = _rewrite_credentials(_async_dsn(postgres_container), APP_ROLE, APP_PASSWORD)
    engine = create_async_engine_from_config(DatabaseConfig(dsn=app_async_dsn))
    session_factory = build_rls_sessionmaker(create_async_session_factory(engine))
    yield DbFeedbackStore(session_factory), engine


@pytest.mark.asyncio
async def test_feedback_tenants_cannot_see_each_other(
    feedback_rls_store: tuple[DbFeedbackStore, AsyncEngine],
) -> None:
    """#64 — two tenants' feedback on the *same* thread id stays isolated.

    Using one shared ``thread_id`` proves it is RLS, not the thread
    filter, that isolates: ``list_for_thread`` carries no tenant
    predicate, so a leak would surface both rows.
    """
    store, engine = feedback_rls_store
    try:
        tenant_a, tenant_b = uuid4(), uuid4()
        thread_id = uuid4()

        current_tenant_id_var.set(tenant_a)
        await store.insert(
            FeedbackRecord(tenant_id=tenant_a, thread_id=thread_id, rating="up", actor_id="user-a")
        )

        current_tenant_id_var.set(tenant_b)
        await store.insert(
            FeedbackRecord(
                tenant_id=tenant_b, thread_id=thread_id, rating="down", actor_id="user-b"
            )
        )

        current_tenant_id_var.set(tenant_a)
        a_rows = await store.list_for_thread(thread_id=thread_id)
        assert [r.rating for r in a_rows] == ["up"]
        assert all(r.tenant_id == tenant_a for r in a_rows)

        current_tenant_id_var.set(tenant_b)
        b_rows = await store.list_for_thread(thread_id=thread_id)
        assert [r.rating for r in b_rows] == ["down"]
        assert all(r.tenant_id == tenant_b for r in b_rows)
    finally:
        await engine.dispose()
