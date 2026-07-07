"""Tests for :class:`FeedbackConsumerWorker` — Stream HX-2 (§ 3.2-③).

All-in-memory: the worker is exercised against InMemory stores; the
cross-tenant SET-ROLE mechanics of the SQL path are integration-tested
in ``packages/expert-work-persistence/tests/test_rls_integration.py``.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from control_plane.feedback_consumer import FeedbackConsumerWorker
from expert_work.persistence.feedback_store import FeedbackRecord, InMemoryFeedbackStore
from expert_work.persistence.memory.memory import InMemoryMemoryStore
from expert_work.persistence.thread_meta import InMemoryThreadMetaStore
from expert_work.protocol import MemoryItem

_TENANT = UUID("55555555-5555-5555-5555-555555555555")
_USER = UUID("66666666-6666-6666-6666-666666666666")


def _item(thread_id: UUID, *, content: str = "fact") -> MemoryItem:
    return MemoryItem(
        id=uuid4(),
        tenant_id=_TENANT,
        user_id=_USER,
        kind="fact",
        content=content,
        embedding=(0.1, 0.2),
        source_thread_id=str(thread_id),
    )


async def _seed_thread(meta_store: InMemoryThreadMetaStore, thread_id: UUID) -> None:
    await meta_store.create(
        thread_id=thread_id,
        tenant_id=_TENANT,
        user_id=_USER,
        created_by="user-1",
    )


def _worker(
    feedback: InMemoryFeedbackStore,
    meta: InMemoryThreadMetaStore,
    memory: InMemoryMemoryStore,
) -> FeedbackConsumerWorker:
    return FeedbackConsumerWorker(
        feedback_store=feedback, thread_meta_store=meta, memory_store=memory
    )


async def _down(feedback: InMemoryFeedbackStore, thread_id: UUID) -> FeedbackRecord:
    return await feedback.insert(
        FeedbackRecord(tenant_id=_TENANT, thread_id=thread_id, rating="down", actor_id="u")
    )


@pytest.mark.asyncio
async def test_down_feedback_flags_thread_memories_and_stamps() -> None:
    feedback, meta, memory = (
        InMemoryFeedbackStore(),
        InMemoryThreadMetaStore(),
        InMemoryMemoryStore(),
    )
    thread_id = uuid4()
    await _seed_thread(meta, thread_id)
    await memory.write([_item(thread_id)])
    await memory.write([_item(thread_id, content="other fact")])
    await memory.write([_item(uuid4(), content="unrelated thread")])
    row = await _down(feedback, thread_id)

    tally = await _worker(feedback, meta, memory).run_once()

    assert tally.scanned == 1
    assert tally.memory_flagged == 1
    flagged = await memory.list_review_flagged(tenant_id=_TENANT, user_id=_USER, limit=10)
    assert len(flagged) == 2  # only this thread's two items
    stored = await feedback.list_for_thread(thread_id=thread_id)
    assert stored[0].id == row.id
    assert stored[0].processed_at is not None


@pytest.mark.asyncio
async def test_run_once_is_idempotent_and_skips_up_ratings() -> None:
    feedback, meta, memory = (
        InMemoryFeedbackStore(),
        InMemoryThreadMetaStore(),
        InMemoryMemoryStore(),
    )
    thread_id = uuid4()
    await _seed_thread(meta, thread_id)
    await memory.write([_item(thread_id)])
    await _down(feedback, thread_id)
    await feedback.insert(
        FeedbackRecord(tenant_id=_TENANT, thread_id=thread_id, rating="up", actor_id="u")
    )
    worker = _worker(feedback, meta, memory)

    first = await worker.run_once()
    second = await worker.run_once()

    assert first.scanned == 1  # the 👍 row is never enumerated
    assert second.scanned == 0  # processed stamp consumed the 👎


@pytest.mark.asyncio
async def test_missing_thread_meta_is_a_stamped_noop() -> None:
    feedback, meta, memory = (
        InMemoryFeedbackStore(),
        InMemoryThreadMetaStore(),
        InMemoryMemoryStore(),
    )
    thread_id = uuid4()  # no thread_meta row at all
    await memory.write([_item(thread_id)])
    await _down(feedback, thread_id)

    tally = await _worker(feedback, meta, memory).run_once()

    assert tally.noop == 1
    assert tally.memory_flagged == 0
    # The row is consumed anyway — no retry storm on unresolvable threads.
    assert await feedback.list_unprocessed_down_all_tenants(limit=10) == []


@pytest.mark.asyncio
async def test_row_failure_leaves_row_unprocessed_for_retry() -> None:
    class _ExplodingMeta(InMemoryThreadMetaStore):
        async def get(self, thread_id: UUID, *, tenant_id: UUID) -> None:
            raise RuntimeError("boom")

    feedback, memory = InMemoryFeedbackStore(), InMemoryMemoryStore()
    thread_id = uuid4()
    await _down(feedback, thread_id)

    tally = await _worker(feedback, _ExplodingMeta(), memory).run_once()

    assert tally.errors == 1
    assert len(await feedback.list_unprocessed_down_all_tenants(limit=10)) == 1
