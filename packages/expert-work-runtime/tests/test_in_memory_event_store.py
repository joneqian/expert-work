"""Unit tests for InMemoryEventStore — pure in-process behaviour."""

from __future__ import annotations

from uuid import uuid4

import pytest

from expert_work.protocol import EventRecord, EventType
from expert_work.runtime.event_log import InMemoryEventStore


@pytest.mark.asyncio
async def test_put_assigns_monotonic_seq_per_thread() -> None:
    store = InMemoryEventStore()
    thread_a, thread_b, tenant = uuid4(), uuid4(), uuid4()

    a1 = await store.put(thread_id=thread_a, tenant_id=tenant, event_type=EventType.SESSION_START)
    a2 = await store.put(thread_id=thread_a, tenant_id=tenant, event_type=EventType.LLM_CALL)
    b1 = await store.put(thread_id=thread_b, tenant_id=tenant, event_type=EventType.SESSION_START)

    assert (a1.seq, a2.seq) == (1, 2)
    assert b1.seq == 1  # separate per-thread counter


@pytest.mark.asyncio
async def test_put_batch_same_thread() -> None:
    store = InMemoryEventStore()
    thread, tenant = uuid4(), uuid4()

    events = [
        EventRecord(thread_id=thread, tenant_id=tenant, seq=0, event_type=EventType.TOOL_CALL),
        EventRecord(thread_id=thread, tenant_id=tenant, seq=0, event_type=EventType.TOOL_RESULT),
    ]
    out = await store.put_batch(events)

    assert [r.seq for r in out] == [1, 2]
    assert await store.count(thread) == 2


@pytest.mark.asyncio
async def test_put_batch_rejects_mixed_thread() -> None:
    store = InMemoryEventStore()
    tenant = uuid4()
    events = [
        EventRecord(thread_id=uuid4(), tenant_id=tenant, seq=0, event_type=EventType.LLM_CALL),
        EventRecord(thread_id=uuid4(), tenant_id=tenant, seq=0, event_type=EventType.LLM_CALL),
    ]
    with pytest.raises(ValueError, match="same thread_id"):
        await store.put_batch(events)


@pytest.mark.asyncio
async def test_list_events_cursor_pagination() -> None:
    store = InMemoryEventStore()
    thread, tenant = uuid4(), uuid4()
    for _ in range(5):
        await store.put(thread_id=thread, tenant_id=tenant, event_type=EventType.STATE)

    page1 = await store.list_events(thread, limit=2)
    assert len(page1) == 2
    assert [r.seq for r in page1] == [4, 5]  # latest 2

    forward = await store.list_events(thread, after_seq=2, limit=2)
    assert [r.seq for r in forward] == [3, 4]

    backward = await store.list_events(thread, before_seq=4, limit=2)
    assert [r.seq for r in backward] == [2, 3]


@pytest.mark.asyncio
async def test_list_events_filter_by_type() -> None:
    store = InMemoryEventStore()
    thread, tenant = uuid4(), uuid4()
    await store.put(thread_id=thread, tenant_id=tenant, event_type=EventType.LLM_CALL)
    await store.put(thread_id=thread, tenant_id=tenant, event_type=EventType.TOOL_CALL)
    await store.put(thread_id=thread, tenant_id=tenant, event_type=EventType.LLM_RESULT)

    only_llm = await store.list_events(
        thread, event_types=[EventType.LLM_CALL, EventType.LLM_RESULT]
    )
    assert {r.event_type for r in only_llm} == {EventType.LLM_CALL, EventType.LLM_RESULT}
    assert len(only_llm) == 2


@pytest.mark.asyncio
async def test_delete_by_thread_resets_seq_counter() -> None:
    store = InMemoryEventStore()
    thread, tenant = uuid4(), uuid4()
    await store.put(thread_id=thread, tenant_id=tenant, event_type=EventType.STATE)
    await store.put(thread_id=thread, tenant_id=tenant, event_type=EventType.STATE)

    removed = await store.delete_by_thread(thread)
    assert removed == 2
    assert await store.count(thread) == 0

    # After delete, seq restarts from 1 (new "thread lifetime")
    fresh = await store.put(thread_id=thread, tenant_id=tenant, event_type=EventType.SESSION_START)
    assert fresh.seq == 1
