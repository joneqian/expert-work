"""Integration tests for SqlArtifactStore against a real Postgres — J.9."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from helix_agent.persistence import (
    DatabaseConfig,
    SqlArtifactStore,
    create_async_engine_from_config,
    create_async_session_factory,
)

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

SqlStoreFixture = tuple[SqlArtifactStore, AsyncEngine]


def _sync_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+psycopg").replace("postgresql://", "postgresql+psycopg://", 1)


def _async_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture
def sql_store(postgres_container: PostgresContainer) -> Iterator[SqlStoreFixture]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")

    engine = create_async_engine_from_config(DatabaseConfig(dsn=_async_dsn(postgres_container)))
    session_factory = create_async_session_factory(engine)
    yield SqlArtifactStore(session_factory), engine


@pytest.mark.asyncio
async def test_save_version_round_trip_and_bump(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        tenant_id, user_id = uuid4(), uuid4()
        v1 = await store.save_version(
            tenant_id=tenant_id,
            user_id=user_id,
            name="report.md",
            kind="document",
            path_in_workspace="report.md",
            created_in_thread="t-1",
        )
        assert v1.version == 1

        # ON CONFLICT path: same name → next version, same artifact id.
        v2 = await store.save_version(
            tenant_id=tenant_id,
            user_id=user_id,
            name="report.md",
            kind="document",
            path_in_workspace="report.md",
            created_in_thread="t-2",
        )
        assert v2.version == 2
        assert v2.artifact_id == v1.artifact_id

        artifacts = await store.list_for_user(tenant_id=tenant_id, user_id=user_id)
        assert len(artifacts) == 1
        assert artifacts[0].latest_version == 2
        assert artifacts[0].kind == "document"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_list_for_user_isolates_tenant_and_user(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        tenant_a, tenant_b = uuid4(), uuid4()
        user_x, user_y = uuid4(), uuid4()
        for tenant_id, user_id in ((tenant_a, user_x), (tenant_a, user_y), (tenant_b, user_x)):
            await store.save_version(
                tenant_id=tenant_id,
                user_id=user_id,
                name="shared-name",
                kind="data",
                path_in_workspace="shared-name",
                created_in_thread="t",
            )
        assert len(await store.list_for_user(tenant_id=tenant_a, user_id=user_x)) == 1
        assert len(await store.list_for_user(tenant_id=tenant_b, user_id=user_x)) == 1
        assert await store.list_for_user(tenant_id=uuid4(), user_id=user_x) == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_latest_version_and_digest_backfill(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        tenant_id, user_id = uuid4(), uuid4()
        await store.save_version(
            tenant_id=tenant_id,
            user_id=user_id,
            name="report.md",
            kind="document",
            path_in_workspace="v1.md",
            created_in_thread="t-1",
        )
        await store.save_version(
            tenant_id=tenant_id,
            user_id=user_id,
            name="report.md",
            kind="document",
            path_in_workspace="v2.md",
            created_in_thread="t-2",
        )
        latest = await store.get_latest_version(
            tenant_id=tenant_id, user_id=user_id, name="report.md"
        )
        assert latest is not None
        assert latest.version == 2
        assert latest.path_in_workspace == "v2.md"
        assert latest.size_bytes is None

        await store.set_version_digest(version_id=latest.id, size_bytes=4096, sha256="deadbeef")
        refreshed = await store.get_latest_version(
            tenant_id=tenant_id, user_id=user_id, name="report.md"
        )
        assert refreshed is not None
        assert refreshed.size_bytes == 4096
        assert refreshed.sha256 == "deadbeef"

        assert (
            await store.get_latest_version(tenant_id=tenant_id, user_id=user_id, name="nope")
            is None
        )
    finally:
        await engine.dispose()
