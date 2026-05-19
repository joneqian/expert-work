"""Unit tests for ``InMemoryKnowledgeStore`` — Stream J.5."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from helix_agent.persistence.knowledge import (
    DuplicateKnowledgeBaseError,
    InMemoryKnowledgeStore,
)
from helix_agent.protocol import DocumentStatus, KnowledgeChunk


def _chunk(
    *,
    tenant_id: UUID,
    kb_id: UUID,
    document_id: UUID,
    index: int,
    content: str,
    embedding: tuple[float, ...],
) -> KnowledgeChunk:
    return KnowledgeChunk(
        id=uuid4(),
        tenant_id=tenant_id,
        kb_id=kb_id,
        document_id=document_id,
        chunk_index=index,
        content=content,
        embedding=embedding,
    )


# ---------------------------------------------------------------------------
# knowledge bases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_get_base() -> None:
    store = InMemoryKnowledgeStore()
    tenant = uuid4()
    created = await store.create_base(tenant_id=tenant, name="hr-policies")
    fetched = await store.get_base(tenant_id=tenant, name="hr-policies")
    assert fetched is not None
    assert fetched.id == created.id


@pytest.mark.asyncio
async def test_create_base_rejects_duplicate() -> None:
    store = InMemoryKnowledgeStore()
    tenant = uuid4()
    await store.create_base(tenant_id=tenant, name="kb")
    with pytest.raises(DuplicateKnowledgeBaseError):
        await store.create_base(tenant_id=tenant, name="kb")


@pytest.mark.asyncio
async def test_get_base_is_tenant_scoped() -> None:
    store = InMemoryKnowledgeStore()
    tenant_a, tenant_b = uuid4(), uuid4()
    await store.create_base(tenant_id=tenant_a, name="kb")
    assert await store.get_base(tenant_id=tenant_b, name="kb") is None
    # Same name under a different tenant is allowed.
    await store.create_base(tenant_id=tenant_b, name="kb")


@pytest.mark.asyncio
async def test_list_bases_scoped_to_tenant() -> None:
    store = InMemoryKnowledgeStore()
    tenant, other = uuid4(), uuid4()
    await store.create_base(tenant_id=tenant, name="a")
    await store.create_base(tenant_id=tenant, name="b")
    await store.create_base(tenant_id=other, name="c")
    assert {b.name for b in await store.list_bases(tenant_id=tenant)} == {"a", "b"}


@pytest.mark.asyncio
async def test_delete_base_cascades_documents_and_chunks() -> None:
    store = InMemoryKnowledgeStore()
    tenant = uuid4()
    base = await store.create_base(tenant_id=tenant, name="kb")
    doc = await store.upsert_document(tenant_id=tenant, kb_id=base.id, filename="d.pdf")
    await store.replace_chunks(
        tenant_id=tenant,
        document_id=doc.id,
        chunks=[
            _chunk(
                tenant_id=tenant,
                kb_id=base.id,
                document_id=doc.id,
                index=0,
                content="c",
                embedding=(1.0,),
            )
        ],
    )

    assert await store.delete_base(tenant_id=tenant, kb_id=base.id) is True
    assert await store.get_base(tenant_id=tenant, name="kb") is None
    assert await store.list_documents(tenant_id=tenant, kb_id=base.id) == []
    assert await store.search(tenant_id=tenant, kb_ids=[base.id], query_embedding=(1.0,)) == []


@pytest.mark.asyncio
async def test_delete_missing_base_returns_false() -> None:
    store = InMemoryKnowledgeStore()
    assert await store.delete_base(tenant_id=uuid4(), kb_id=uuid4()) is False


# ---------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_document_creates_pending() -> None:
    store = InMemoryKnowledgeStore()
    tenant, kb_id = uuid4(), uuid4()
    doc = await store.upsert_document(tenant_id=tenant, kb_id=kb_id, filename="d.pdf")
    assert doc.status is DocumentStatus.PENDING
    assert doc.chunk_count == 0


@pytest.mark.asyncio
async def test_upsert_existing_document_resets_and_clears_chunks() -> None:
    store = InMemoryKnowledgeStore()
    tenant, kb_id = uuid4(), uuid4()
    doc = await store.upsert_document(tenant_id=tenant, kb_id=kb_id, filename="d.pdf")
    await store.set_document_status(
        tenant_id=tenant, document_id=doc.id, status=DocumentStatus.READY, chunk_count=3
    )
    await store.replace_chunks(
        tenant_id=tenant,
        document_id=doc.id,
        chunks=[
            _chunk(
                tenant_id=tenant,
                kb_id=kb_id,
                document_id=doc.id,
                index=0,
                content="c",
                embedding=(1.0,),
            )
        ],
    )

    reset = await store.upsert_document(tenant_id=tenant, kb_id=kb_id, filename="d.pdf")
    assert reset.id == doc.id  # same logical document
    assert reset.status is DocumentStatus.PENDING
    assert reset.chunk_count == 0
    assert await store.search(tenant_id=tenant, kb_ids=[kb_id], query_embedding=(1.0,)) == []


@pytest.mark.asyncio
async def test_set_document_status_failed_records_error() -> None:
    store = InMemoryKnowledgeStore()
    tenant, kb_id = uuid4(), uuid4()
    doc = await store.upsert_document(tenant_id=tenant, kb_id=kb_id, filename="d.pdf")
    await store.set_document_status(
        tenant_id=tenant,
        document_id=doc.id,
        status=DocumentStatus.FAILED,
        error="parse error",
    )
    fetched = await store.get_document(tenant_id=tenant, document_id=doc.id)
    assert fetched is not None
    assert fetched.status is DocumentStatus.FAILED
    assert fetched.error == "parse error"


@pytest.mark.asyncio
async def test_list_documents_scoped_to_base() -> None:
    store = InMemoryKnowledgeStore()
    tenant, kb_a, kb_b = uuid4(), uuid4(), uuid4()
    await store.upsert_document(tenant_id=tenant, kb_id=kb_a, filename="a.pdf")
    await store.upsert_document(tenant_id=tenant, kb_id=kb_b, filename="b.pdf")
    docs = await store.list_documents(tenant_id=tenant, kb_id=kb_a)
    assert [d.filename for d in docs] == ["a.pdf"]


@pytest.mark.asyncio
async def test_delete_document_cascades_chunks() -> None:
    store = InMemoryKnowledgeStore()
    tenant, kb_id = uuid4(), uuid4()
    doc = await store.upsert_document(tenant_id=tenant, kb_id=kb_id, filename="d.pdf")
    await store.replace_chunks(
        tenant_id=tenant,
        document_id=doc.id,
        chunks=[
            _chunk(
                tenant_id=tenant,
                kb_id=kb_id,
                document_id=doc.id,
                index=0,
                content="c",
                embedding=(1.0,),
            )
        ],
    )
    assert await store.delete_document(tenant_id=tenant, document_id=doc.id) is True
    assert await store.get_document(tenant_id=tenant, document_id=doc.id) is None
    assert await store.search(tenant_id=tenant, kb_ids=[kb_id], query_embedding=(1.0,)) == []


# ---------------------------------------------------------------------------
# chunk search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_orders_by_cosine_distance() -> None:
    store = InMemoryKnowledgeStore()
    tenant, kb_id, doc_id = uuid4(), uuid4(), uuid4()
    await store.replace_chunks(
        tenant_id=tenant,
        document_id=doc_id,
        chunks=[
            _chunk(
                tenant_id=tenant,
                kb_id=kb_id,
                document_id=doc_id,
                index=0,
                content="east",
                embedding=(1.0, 0.0),
            ),
            _chunk(
                tenant_id=tenant,
                kb_id=kb_id,
                document_id=doc_id,
                index=1,
                content="north",
                embedding=(0.0, 1.0),
            ),
            _chunk(
                tenant_id=tenant,
                kb_id=kb_id,
                document_id=doc_id,
                index=2,
                content="ne",
                embedding=(0.7, 0.7),
            ),
        ],
    )
    hits = await store.search(tenant_id=tenant, kb_ids=[kb_id], query_embedding=(1.0, 0.0), limit=3)
    assert [h.content for h in hits] == ["east", "ne", "north"]


@pytest.mark.asyncio
async def test_search_filters_by_kb_ids() -> None:
    store = InMemoryKnowledgeStore()
    tenant, kb_a, kb_b, doc = uuid4(), uuid4(), uuid4(), uuid4()
    await store.replace_chunks(
        tenant_id=tenant,
        document_id=doc,
        chunks=[
            _chunk(
                tenant_id=tenant,
                kb_id=kb_a,
                document_id=doc,
                index=0,
                content="in-a",
                embedding=(1.0,),
            ),
            _chunk(
                tenant_id=tenant,
                kb_id=kb_b,
                document_id=doc,
                index=1,
                content="in-b",
                embedding=(1.0,),
            ),
        ],
    )
    hits = await store.search(tenant_id=tenant, kb_ids=[kb_a], query_embedding=(1.0,))
    assert [h.content for h in hits] == ["in-a"]


@pytest.mark.asyncio
async def test_search_is_tenant_scoped() -> None:
    store = InMemoryKnowledgeStore()
    tenant, other, kb_id, doc = uuid4(), uuid4(), uuid4(), uuid4()
    await store.replace_chunks(
        tenant_id=tenant,
        document_id=doc,
        chunks=[
            _chunk(
                tenant_id=tenant,
                kb_id=kb_id,
                document_id=doc,
                index=0,
                content="mine",
                embedding=(1.0,),
            )
        ],
    )
    assert await store.search(tenant_id=other, kb_ids=[kb_id], query_embedding=(1.0,)) == []


@pytest.mark.asyncio
async def test_search_empty_kb_ids_returns_empty() -> None:
    store = InMemoryKnowledgeStore()
    assert await store.search(tenant_id=uuid4(), kb_ids=[], query_embedding=(1.0,)) == []
