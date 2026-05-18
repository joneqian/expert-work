"""Integration test for the ADR B-6 SQL store cutover.

Boots the control-plane app with ``store_backend='sql'`` against a real
Postgres (testcontainers) carrying the Alembic schema, then drives a
round-trip through the wired ``SqlThreadMetaStore`` inside the app's
lifespan — proving the whole chain ``create_app`` builds: engine →
``build_rls_sessionmaker``-wrapped sessionmaker → Sql store, plus the
lifespan ``engine.dispose()`` on shutdown.

The individual ``Sql*Store`` classes have their own CRUD coverage in
``test_sql_*_store.py``; this test covers only the control-plane wiring.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

from control_plane.app import create_app
from control_plane.settings import Settings
from helix_agent.persistence.rls import current_tenant_id_var
from helix_agent.persistence.thread_meta import SqlThreadMetaStore
from helix_agent.protocol import ThreadStatus
from tests.auth_fixtures import TEST_AUDIENCE, TEST_ISSUER, build_test_jwt_verifier

pytestmark = pytest.mark.integration

# Migrations live in the helix-persistence package, not control-plane.
_ALEMBIC_INI = Path(__file__).resolve().parents[3] / "packages/helix-persistence/alembic.ini"


def _sync_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+psycopg").replace("postgresql://", "postgresql+psycopg://", 1)


def _async_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture
def sql_settings(postgres_container: PostgresContainer) -> Iterator[Settings]:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")
    yield Settings(
        service_name="control_plane_sql_test",
        store_backend="sql",
        db_dsn=_async_dsn(postgres_container),
        # testcontainers Postgres is a direct connection, not PgBouncer.
        db_pgbouncer_mode=False,
        oidc_issuer=TEST_ISSUER,
        oidc_audience=[TEST_AUDIENCE],
    )


@pytest.mark.asyncio
async def test_sql_backed_app_persists_through_wired_store(sql_settings: Settings) -> None:
    """A SQL-backed app round-trips a row through the store on
    ``app.state`` — and the lifespan disposes the engine on exit."""
    app = create_app(
        settings=sql_settings,
        jwt_verifier=build_test_jwt_verifier(),
        enable_reaper=False,
    )
    store = app.state.thread_meta_repo
    assert isinstance(store, SqlThreadMetaStore)

    tenant_id, thread_id = uuid4(), uuid4()
    token = current_tenant_id_var.set(tenant_id)
    try:
        async with app.router.lifespan_context(app):
            created = await store.create(
                thread_id=thread_id,
                tenant_id=tenant_id,
                created_by="wiring-test",
                agent_name="probe",
                agent_version="1.0.0",
            )
            assert created.status is ThreadStatus.ACTIVE

            fetched = await store.get(thread_id, tenant_id=tenant_id)
            assert fetched is not None
            assert fetched.thread_id == thread_id
            assert fetched.agent_version == "1.0.0"
    finally:
        current_tenant_id_var.reset(token)
