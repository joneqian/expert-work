"""Integration test for :class:`SqlPlatformSecretStore` — Stream P (P-7).

Platform secret rows are tenant-less / RLS-exempt, so this test uses the
container's default connection directly (no app-role / RLS dance needed).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from helix_agent.persistence import (
    DatabaseConfig,
    SqlPlatformSecretStore,
    create_async_engine_from_config,
    create_async_session_factory,
)

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _sync_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+psycopg").replace("postgresql://", "postgresql+psycopg://", 1)


def _async_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture
def platform_secret_store(
    postgres_container: PostgresContainer,
) -> Iterator[tuple[SqlPlatformSecretStore, AsyncEngine]]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")

    engine = create_async_engine_from_config(DatabaseConfig(dsn=_async_dsn(postgres_container)))
    sf = create_async_session_factory(engine)
    yield SqlPlatformSecretStore(sf), engine


@pytest.mark.asyncio
async def test_provider_round_trip(
    platform_secret_store: tuple[SqlPlatformSecretStore, AsyncEngine],
) -> None:
    store, engine = platform_secret_store
    try:
        created = await store.upsert_provider(
            provider="anthropic",
            secret_ref="kms://platform/anthropic",
            enabled=True,
            actor_id="admin",
        )
        assert created.provider == "anthropic"
        assert created.enabled is True

        fetched = await store.get_provider("anthropic")
        assert fetched is not None
        assert fetched.secret_ref == "kms://platform/anthropic"

        # Upsert preserves created_at, can disable.
        updated = await store.upsert_provider(
            provider="anthropic",
            secret_ref="secret://anthropic-rotated",
            enabled=False,
            actor_id="admin2",
        )
        assert updated.created_at == created.created_at
        assert updated.enabled is False
        assert [p.provider for p in await store.list_providers()] == ["anthropic"]

        assert await store.delete_provider("anthropic") is True
        assert await store.delete_provider("anthropic") is False
        assert await store.get_provider("anthropic") is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_tool_round_trip(
    platform_secret_store: tuple[SqlPlatformSecretStore, AsyncEngine],
) -> None:
    store, engine = platform_secret_store
    try:
        await store.upsert_tool(
            tool="web_search", secret_ref="kms://tavily", enabled=True, actor_id="a"
        )
        fetched = await store.get_tool("web_search")
        assert fetched is not None
        assert fetched.secret_ref == "kms://tavily"
        assert await store.delete_tool("web_search") is True
        assert await store.get_tool("web_search") is None
    finally:
        await engine.dispose()
