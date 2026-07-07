"""Tests for the knowledge ingestion recovery worker — Stream KB durability.

In-memory logic tests: the worker claims stuck documents (``pending`` /
lease-expired ``processing``) and re-drives them from retained bytes. The
exactly-once CAS guarantee under concurrency is only meaningfully testable
against real Postgres (see ``test_sql_knowledge_store.py``)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from control_plane.knowledge.recovery import KnowledgeIngestRecoveryWorker
from expert_work.persistence import InMemoryKnowledgeStore
from expert_work.protocol import DocumentStatus
from orchestrator.llm import FakeEmbedder

_DOC = b"# Handbook\n\nThe deductible is 500 dollars per year."


def _worker(
    store: InMemoryKnowledgeStore, *, max_attempts: int = 5
) -> KnowledgeIngestRecoveryWorker:
    return KnowledgeIngestRecoveryWorker(
        store=store,
        embedder=FakeEmbedder(),
        interval_s=1,
        batch_size=10,
        lease_seconds=300,
        max_attempts=max_attempts,
    )


@pytest.mark.asyncio
async def test_recovers_pending_document_from_bytes() -> None:
    # A document uploaded (bytes persisted, status pending) but whose fast-path
    # task never ran (e.g. a crash) is drained by the worker.
    store = InMemoryKnowledgeStore()
    tenant = uuid4()
    base = await store.create_base(tenant_id=tenant, name="kb")
    doc = await store.upsert_document(
        tenant_id=tenant, kb_id=base.id, filename="h.md", content=_DOC
    )

    settled = await _worker(store).run_once()
    assert settled == 1
    fetched = await store.get_document(tenant_id=tenant, document_id=doc.id)
    assert fetched is not None
    assert fetched.status is DocumentStatus.READY
    assert fetched.chunk_count >= 1


@pytest.mark.asyncio
async def test_recovers_processing_document_with_expired_lease() -> None:
    store = InMemoryKnowledgeStore()
    tenant = uuid4()
    base = await store.create_base(tenant_id=tenant, name="kb")
    doc = await store.upsert_document(
        tenant_id=tenant, kb_id=base.id, filename="h.md", content=_DOC
    )
    # Simulate a crashed claim: processing with an already-expired (0s) lease.
    claimed = await store.claim_document(
        tenant_id=tenant,
        document_id=doc.id,
        now=datetime.now(UTC),
        lease_seconds=0,
        max_attempts=5,
    )
    assert claimed is not None

    settled = await _worker(store).run_once()
    assert settled == 1
    fetched = await store.get_document(tenant_id=tenant, document_id=doc.id)
    assert fetched is not None
    assert fetched.status is DocumentStatus.READY


@pytest.mark.asyncio
async def test_legacy_document_without_bytes_fails_terminally() -> None:
    store = InMemoryKnowledgeStore()
    tenant = uuid4()
    base = await store.create_base(tenant_id=tenant, name="kb")
    # No content retained (legacy row).
    doc = await store.upsert_document(tenant_id=tenant, kb_id=base.id, filename="h.md")

    settled = await _worker(store).run_once()
    assert settled == 1
    fetched = await store.get_document(tenant_id=tenant, document_id=doc.id)
    assert fetched is not None
    assert fetched.status is DocumentStatus.FAILED
    assert fetched.error is not None


@pytest.mark.asyncio
async def test_ready_document_is_not_reclaimed() -> None:
    store = InMemoryKnowledgeStore()
    tenant = uuid4()
    base = await store.create_base(tenant_id=tenant, name="kb")
    doc = await store.upsert_document(
        tenant_id=tenant, kb_id=base.id, filename="h.md", content=_DOC
    )
    await store.set_document_status(
        tenant_id=tenant, document_id=doc.id, status=DocumentStatus.READY, chunk_count=2
    )
    assert await _worker(store).run_once() == 0


@pytest.mark.asyncio
async def test_exhausted_attempts_not_reclaimed() -> None:
    # A document already at max attempts is past its retry budget — the worker
    # leaves it alone (a separate manual re-ingest is the path forward).
    store = InMemoryKnowledgeStore()
    tenant = uuid4()
    base = await store.create_base(tenant_id=tenant, name="kb")
    doc = await store.upsert_document(
        tenant_id=tenant, kb_id=base.id, filename="h.md", content=_DOC
    )
    # Burn the attempt budget with 0s leases so it stays claimable until max.
    for _ in range(3):
        await store.claim_document(
            tenant_id=tenant,
            document_id=doc.id,
            now=datetime.now(UTC),
            lease_seconds=0,
            max_attempts=3,
        )
    assert await _worker(store, max_attempts=3).run_once() == 0
