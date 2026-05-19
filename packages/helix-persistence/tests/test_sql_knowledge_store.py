"""Integration tests for SqlKnowledgeStore against Postgres + pgvector — J.5."""

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
    SqlKnowledgeStore,
    create_async_engine_from_config,
    create_async_session_factory,
)
from helix_agent.persistence.embedding import EMBEDDING_DIM
from helix_agent.persistence.knowledge import DuplicateKnowledgeBaseError
from helix_agent.protocol import DocumentStatus, KnowledgeChunk

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

SqlStoreFixture = tuple[SqlKnowledgeStore, AsyncEngine]


def _sync_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+psycopg").replace("postgresql://", "postgresql+psycopg://", 1)


def _async_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://", 1)


def _vec(*head: float) -> tuple[float, ...]:
    """An ``EMBEDDING_DIM``-wide vector with ``head`` as its leading values."""
    return tuple(head) + (0.0,) * (EMBEDDING_DIM - len(head))


@pytest.fixture
def sql_store(postgres_container: PostgresContainer) -> Iterator[SqlStoreFixture]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")

    engine = create_async_engine_from_config(DatabaseConfig(dsn=_async_dsn(postgres_container)))
    session_factory = create_async_session_factory(engine)
    yield SqlKnowledgeStore(session_factory), engine


@pytest.mark.asyncio
async def test_base_crud(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        tenant = uuid4()
        created = await store.create_base(tenant_id=tenant, name="hr-policies")
        fetched = await store.get_base(tenant_id=tenant, name="hr-policies")
        assert fetched is not None
        assert fetched.id == created.id
        assert {b.name for b in await store.list_bases(tenant_id=tenant)} == {"hr-policies"}
        with pytest.raises(DuplicateKnowledgeBaseError):
            await store.create_base(tenant_id=tenant, name="hr-policies")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_document_upsert_resets_existing(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        tenant = uuid4()
        base = await store.create_base(tenant_id=tenant, name="kb")
        doc = await store.upsert_document(tenant_id=tenant, kb_id=base.id, filename="d.pdf")
        assert doc.status is DocumentStatus.PENDING

        await store.set_document_status(
            tenant_id=tenant,
            document_id=doc.id,
            status=DocumentStatus.READY,
            chunk_count=5,
        )
        reset = await store.upsert_document(tenant_id=tenant, kb_id=base.id, filename="d.pdf")
        assert reset.id == doc.id
        assert reset.status is DocumentStatus.PENDING
        assert reset.chunk_count == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_replace_chunks_and_search_orders_by_cosine(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        tenant = uuid4()
        base = await store.create_base(tenant_id=tenant, name="kb")
        doc = await store.upsert_document(tenant_id=tenant, kb_id=base.id, filename="d.pdf")
        await store.replace_chunks(
            tenant_id=tenant,
            document_id=doc.id,
            chunks=[
                _make_chunk(tenant, base.id, doc.id, 0, "east", _vec(1.0, 0.0)),
                _make_chunk(tenant, base.id, doc.id, 1, "north", _vec(0.0, 1.0)),
                _make_chunk(tenant, base.id, doc.id, 2, "ne", _vec(0.7, 0.7)),
            ],
        )
        hits = await store.search(
            tenant_id=tenant, kb_ids=[base.id], query_embedding=_vec(1.0, 0.0), limit=3
        )
        assert [h.content for h in hits] == ["east", "ne", "north"]
        assert len(hits[0].embedding) == EMBEDDING_DIM
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_delete_base_cascades(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        tenant = uuid4()
        base = await store.create_base(tenant_id=tenant, name="kb")
        doc = await store.upsert_document(tenant_id=tenant, kb_id=base.id, filename="d.pdf")
        await store.replace_chunks(
            tenant_id=tenant,
            document_id=doc.id,
            chunks=[_make_chunk(tenant, base.id, doc.id, 0, "c", _vec(1.0))],
        )
        assert await store.delete_base(tenant_id=tenant, kb_id=base.id) is True
        assert await store.get_base(tenant_id=tenant, name="kb") is None
        assert await store.list_documents(tenant_id=tenant, kb_id=base.id) == []
        assert (
            await store.search(tenant_id=tenant, kb_ids=[base.id], query_embedding=_vec(1.0)) == []
        )
    finally:
        await engine.dispose()


def _make_chunk(
    tenant_id: object,
    kb_id: object,
    document_id: object,
    index: int,
    content: str,
    embedding: tuple[float, ...],
) -> KnowledgeChunk:
    return KnowledgeChunk(
        id=uuid4(),
        tenant_id=tenant_id,  # type: ignore[arg-type]
        kb_id=kb_id,  # type: ignore[arg-type]
        document_id=document_id,  # type: ignore[arg-type]
        chunk_index=index,
        content=content,
        embedding=embedding,
    )
