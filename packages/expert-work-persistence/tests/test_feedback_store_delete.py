"""Tests for ``FeedbackStore.delete_for_threads`` — deletion hygiene PR1, Task 2.

Purge-user cascade (Task 8) hard-deletes a tenant's 👍/👎 feedback rows
scoped to the set of thread ids being purged. Covers both store
implementations against the same scenario:

* :class:`InMemoryFeedbackStore` — unit-level, in-process.
* :class:`DbFeedbackStore` — Postgres integration (chunked ``IN`` DELETE
  + ``rowcount`` aggregation), mirroring ``test_sql_memory_store.py``'s
  container-fixture style.
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

from expert_work.persistence import (
    DatabaseConfig,
    create_async_engine_from_config,
    create_async_session_factory,
)
from expert_work.persistence.feedback_store import (
    DbFeedbackStore,
    FeedbackRecord,
    FeedbackStore,
    InMemoryFeedbackStore,
)
from expert_work.persistence.rls import build_rls_sessionmaker, current_tenant_id_var

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

# Distinct role name so this fixture's schema-wide grants don't collide
# with the other RLS integration tests sharing the session-scoped
# ``postgres_container`` (mirrors test_billing_ledger_rls_integration.py).
APP_ROLE = "expert_work_app_feedback_delete"
APP_PASSWORD = "expert_work_app_feedback_delete_pw"  # test-only fixture password

SqlStoreFixture = tuple[DbFeedbackStore, AsyncEngine]


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
    """Create the non-superuser ``expert_work_app_feedback_delete`` role and grant CRUD.

    Idempotent — the same session-scoped container may host multiple
    fixtures. Mirrors ``test_rls_integration.py``'s ``_provision_app_role``:
    the testcontainers bootstrap user is a superuser and would silently
    bypass every RLS policy, so we connect as a normal role instead.
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


async def _seed(
    store: FeedbackStore,
    *,
    tenant_a: UUID,
    thread_a: UUID,
    thread_b: UUID,
    tenant_b: UUID,
) -> None:
    """t1/threadA x2, t1/threadB x1, plus one t2 row sharing threadA's id."""
    await store.insert(
        FeedbackRecord(tenant_id=tenant_a, thread_id=thread_a, rating="up", actor_id="u1")
    )
    await store.insert(
        FeedbackRecord(tenant_id=tenant_a, thread_id=thread_a, rating="down", actor_id="u1")
    )
    await store.insert(
        FeedbackRecord(tenant_id=tenant_a, thread_id=thread_b, rating="up", actor_id="u2")
    )
    await store.insert(
        FeedbackRecord(tenant_id=tenant_b, thread_id=thread_a, rating="up", actor_id="u3")
    )


# --------------------------------------------------------------------------- #
# InMemoryFeedbackStore
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_in_memory_delete_for_threads_scopes_by_tenant_and_thread() -> None:
    store = InMemoryFeedbackStore()
    tenant_a, tenant_b = uuid4(), uuid4()
    thread_a, thread_b = uuid4(), uuid4()
    await _seed(store, tenant_a=tenant_a, thread_a=thread_a, thread_b=thread_b, tenant_b=tenant_b)

    deleted = await store.delete_for_threads(tenant_id=tenant_a, thread_ids=[thread_a])

    assert deleted == 2
    # threadB (tenant_a) survives.
    remaining_thread_b = await store.list_for_thread(thread_id=thread_b)
    assert len(remaining_thread_b) == 1
    assert remaining_thread_b[0].tenant_id == tenant_a
    # the other tenant's row on the *same* thread_id survives.
    remaining_thread_a = await store.list_for_thread(thread_id=thread_a)
    assert len(remaining_thread_a) == 1
    assert remaining_thread_a[0].tenant_id == tenant_b


@pytest.mark.asyncio
async def test_in_memory_delete_for_threads_empty_list_returns_zero() -> None:
    store = InMemoryFeedbackStore()
    deleted = await store.delete_for_threads(tenant_id=uuid4(), thread_ids=[])
    assert deleted == 0


# --------------------------------------------------------------------------- #
# DbFeedbackStore
# --------------------------------------------------------------------------- #
@pytest.fixture
def sql_store(postgres_container: PostgresContainer) -> Iterator[SqlStoreFixture]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")

    engine = create_async_engine_from_config(DatabaseConfig(dsn=_async_dsn(postgres_container)))
    session_factory = create_async_session_factory(engine)
    yield DbFeedbackStore(session_factory), engine


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sql_delete_for_threads_scopes_by_tenant_and_thread(
    sql_store: SqlStoreFixture,
) -> None:
    store, engine = sql_store
    try:
        tenant_a, tenant_b = uuid4(), uuid4()
        thread_a, thread_b = uuid4(), uuid4()
        await _seed(
            store, tenant_a=tenant_a, thread_a=thread_a, thread_b=thread_b, tenant_b=tenant_b
        )

        deleted = await store.delete_for_threads(tenant_id=tenant_a, thread_ids=[thread_a])

        assert deleted == 2
        remaining_thread_b = await store.list_for_thread(thread_id=thread_b)
        assert len(remaining_thread_b) == 1
        assert remaining_thread_b[0].tenant_id == tenant_a
        remaining_thread_a = await store.list_for_thread(thread_id=thread_a)
        assert len(remaining_thread_a) == 1
        assert remaining_thread_a[0].tenant_id == tenant_b
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sql_delete_for_threads_empty_list_returns_zero(
    sql_store: SqlStoreFixture,
) -> None:
    store, engine = sql_store
    try:
        deleted = await store.delete_for_threads(tenant_id=uuid4(), thread_ids=[])
        assert deleted == 0
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------- #
# DbFeedbackStore — RLS-scoped (Important review finding: the fixture above
# connects as the testcontainers superuser bootstrap account, which bypasses
# ``feedback``'s FORCE RLS unconditionally. It stays as the rowcount /
# chunking regression lock; the tests below additionally pin the production
# contract that ``delete_for_threads``' explicit ``tenant_id`` argument must
# agree with the RLS session's GUC — mirrors ``feedback_rls_store`` in
# test_rls_integration.py:249-260.
# --------------------------------------------------------------------------- #
@pytest.fixture
def feedback_rls_store(postgres_container: PostgresContainer) -> Iterator[SqlStoreFixture]:
    """A :class:`DbFeedbackStore` on the unprivileged role — RLS enforced."""
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")
    _provision_app_role(_sync_dsn(postgres_container))
    app_async_dsn = _rewrite_credentials(_async_dsn(postgres_container), APP_ROLE, APP_PASSWORD)
    engine = create_async_engine_from_config(DatabaseConfig(dsn=app_async_dsn))
    session_factory = build_rls_sessionmaker(create_async_session_factory(engine))
    yield DbFeedbackStore(session_factory), engine


@pytest.fixture(autouse=True)
def reset_rls_context() -> Iterator[None]:
    token = current_tenant_id_var.set(None)
    try:
        yield
    finally:
        current_tenant_id_var.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sql_delete_for_threads_rls_scoped_to_session_guc(
    feedback_rls_store: SqlStoreFixture,
) -> None:
    """RLS GUC and the explicit ``tenant_id`` argument must agree.

    Regression lock for the review finding that the sibling test above
    uses the superuser session factory and never exercises FORCE RLS.
    Here, under a session scoped to tenant A: deleting with
    ``tenant_id=tenant_a`` removes A's own row; deleting with
    ``tenant_id=tenant_b`` while the GUC is still A matches zero rows —
    the RLS policy clamps the row set before the explicit ``WHERE
    tenant_id = ...`` even applies, so B's row is untouched (not merely
    invisible).
    """
    store, engine = feedback_rls_store
    try:
        tenant_a, tenant_b = uuid4(), uuid4()
        shared_thread = uuid4()

        current_tenant_id_var.set(tenant_a)
        await store.insert(
            FeedbackRecord(tenant_id=tenant_a, thread_id=shared_thread, rating="up", actor_id="u1")
        )

        current_tenant_id_var.set(tenant_b)
        await store.insert(
            FeedbackRecord(tenant_id=tenant_b, thread_id=shared_thread, rating="up", actor_id="u2")
        )

        # Session stays scoped to tenant A for both delete calls below.
        current_tenant_id_var.set(tenant_a)

        # tenant_id argument agrees with the GUC: deletes A's own row.
        deleted_own = await store.delete_for_threads(tenant_id=tenant_a, thread_ids=[shared_thread])
        assert deleted_own == 1

        # tenant_id argument names B, but the GUC is still A. RLS
        # restricts the visible/deletable row set to tenant A before the
        # WHERE clause runs, so this matches nothing even though B's row
        # exists.
        deleted_other = await store.delete_for_threads(
            tenant_id=tenant_b, thread_ids=[shared_thread]
        )
        assert deleted_other == 0

        # Prove B's row genuinely survived (not just invisible): switch
        # the GUC to B and read it back.
        current_tenant_id_var.set(tenant_b)
        b_rows = await store.list_for_thread(thread_id=shared_thread)
        assert len(b_rows) == 1
        assert b_rows[0].tenant_id == tenant_b
    finally:
        await engine.dispose()
