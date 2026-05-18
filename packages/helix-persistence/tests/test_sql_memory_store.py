"""Integration tests for SqlMemoryStore against Postgres + pgvector — J.3."""

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
    SqlMemoryStore,
    create_async_engine_from_config,
    create_async_session_factory,
)
from helix_agent.persistence.embedding import EMBEDDING_DIM
from helix_agent.protocol import MemoryItem

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

SqlStoreFixture = tuple[SqlMemoryStore, AsyncEngine]


def _sync_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+psycopg").replace("postgresql://", "postgresql+psycopg://", 1)


def _async_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://", 1)


def _vec(*head: float) -> tuple[float, ...]:
    """An ``EMBEDDING_DIM``-wide vector with ``head`` as its leading values."""
    return tuple(head) + (0.0,) * (EMBEDDING_DIM - len(head))


def _item(
    *, tenant: object, user: object, embedding: tuple[float, ...], kind: str = "fact", content: str
) -> MemoryItem:
    return MemoryItem(
        id=uuid4(),
        tenant_id=tenant,  # type: ignore[arg-type]
        user_id=user,  # type: ignore[arg-type]
        kind=kind,  # type: ignore[arg-type]
        content=content,
        embedding=embedding,
    )


@pytest.fixture
def sql_store(postgres_container: PostgresContainer) -> Iterator[SqlStoreFixture]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")

    engine = create_async_engine_from_config(DatabaseConfig(dsn=_async_dsn(postgres_container)))
    session_factory = create_async_session_factory(engine)
    yield SqlMemoryStore(session_factory), engine


@pytest.mark.asyncio
async def test_write_and_retrieve_orders_by_cosine(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        tenant, user = uuid4(), uuid4()
        await store.write(
            [
                _item(tenant=tenant, user=user, embedding=_vec(1.0, 0.0), content="east"),
                _item(tenant=tenant, user=user, embedding=_vec(0.0, 1.0), content="north"),
                _item(tenant=tenant, user=user, embedding=_vec(0.7, 0.7), content="ne"),
            ]
        )
        hits = await store.retrieve(
            tenant_id=tenant, user_id=user, query_embedding=_vec(1.0, 0.0), limit=3
        )
        assert [h.content for h in hits] == ["east", "ne", "north"]
        # The embedding round-trips at full width.
        assert len(hits[0].embedding) == EMBEDDING_DIM
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_retrieve_scopes_to_tenant_and_user(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        tenant, user, other_user = uuid4(), uuid4(), uuid4()
        await store.write(
            [
                _item(tenant=tenant, user=user, embedding=_vec(1.0), content="mine"),
                _item(tenant=tenant, user=other_user, embedding=_vec(1.0), content="peer"),
            ]
        )
        hits = await store.retrieve(tenant_id=tenant, user_id=user, query_embedding=_vec(1.0))
        assert [h.content for h in hits] == ["mine"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_retrieve_kind_filter(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        tenant, user = uuid4(), uuid4()
        await store.write(
            [
                _item(tenant=tenant, user=user, embedding=_vec(1.0), kind="fact", content="f"),
                _item(
                    tenant=tenant,
                    user=user,
                    embedding=_vec(1.0),
                    kind="episodic",
                    content="e",
                ),
            ]
        )
        facts = await store.retrieve(
            tenant_id=tenant, user_id=user, query_embedding=_vec(1.0), kind="fact"
        )
        assert [h.content for h in facts] == ["f"]
    finally:
        await engine.dispose()
