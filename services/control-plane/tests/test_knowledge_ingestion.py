"""Tests for the J.5 ``KnowledgeIngestionRunner``."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from control_plane.knowledge.ingestion import KnowledgeIngestionRunner
from helix_agent.persistence import InMemoryKnowledgeStore
from helix_agent.protocol import DocumentStatus, KnowledgeBase, KnowledgeDocument
from orchestrator.llm import FakeEmbedder


async def _seed(
    store: InMemoryKnowledgeStore, filename: str
) -> tuple[UUID, KnowledgeBase, KnowledgeDocument]:
    tenant = uuid4()
    base = await store.create_base(tenant_id=tenant, name="kb")
    document = await store.upsert_document(tenant_id=tenant, kb_id=base.id, filename=filename)
    return tenant, base, document


async def _ingest(store: InMemoryKnowledgeStore, filename: str, raw: bytes) -> KnowledgeDocument:
    """Submit one document, await it, and return the refreshed document row."""
    tenant, base, document = await _seed(store, filename)
    runner = KnowledgeIngestionRunner(store=store, embedder=FakeEmbedder())
    await runner.submit(
        tenant_id=tenant,
        document_id=document.id,
        kb_id=base.id,
        filename=filename,
        raw=raw,
        chunk_max_tokens=512,
        chunk_overlap_tokens=64,
    )
    fetched = await store.get_document(tenant_id=tenant, document_id=document.id)
    assert fetched is not None
    return fetched


@pytest.mark.asyncio
async def test_ingest_marks_document_ready_with_chunks() -> None:
    store = InMemoryKnowledgeStore()
    document = await _ingest(
        store, "notes.md", b"# Handbook\n\nThe deductible is 500 dollars per year."
    )
    assert document.status is DocumentStatus.READY
    assert document.chunk_count >= 1
    assert document.error is None


@pytest.mark.asyncio
async def test_ingest_marks_document_failed_on_unparseable_file() -> None:
    store = InMemoryKnowledgeStore()
    document = await _ingest(store, "broken.pdf", b"this is definitely not a valid pdf")
    assert document.status is DocumentStatus.FAILED
    assert document.error


@pytest.mark.asyncio
async def test_ingest_empty_document_failed() -> None:
    store = InMemoryKnowledgeStore()
    document = await _ingest(store, "empty.md", b"   ")
    assert document.status is DocumentStatus.FAILED


@pytest.mark.asyncio
async def test_drain_awaits_outstanding_tasks() -> None:
    store = InMemoryKnowledgeStore()
    tenant, base, document = await _seed(store, "notes.md")
    runner = KnowledgeIngestionRunner(store=store, embedder=FakeEmbedder())
    runner.submit(
        tenant_id=tenant,
        document_id=document.id,
        kb_id=base.id,
        filename="notes.md",
        raw=b"# Doc\n\nbody text here.",
        chunk_max_tokens=512,
        chunk_overlap_tokens=64,
    )
    await runner.drain()
    fetched = await store.get_document(tenant_id=tenant, document_id=document.id)
    assert fetched is not None
    assert fetched.status is DocumentStatus.READY
