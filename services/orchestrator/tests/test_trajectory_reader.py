"""Unit tests for TrajectoryReader — Stream J.12 (Mini-ADR J-43)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from expert_work.runtime.storage import InMemoryObjectStore
from orchestrator.trajectory import TrajectoryReader, TrajectoryRecord, TrajectoryRecorder

_BASE = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_read_round_trips_a_recorded_trajectory() -> None:
    store = InMemoryObjectStore()
    thread_id, tenant_id, user_id, run_id = uuid4(), uuid4(), uuid4(), uuid4()
    await TrajectoryRecorder(object_store=store).record(
        TrajectoryRecord(
            thread_id=thread_id,
            tenant_id=tenant_id,
            outcome="failed",
            messages=[HumanMessage(content="hi"), AIMessage(content="bye")],
            user_id=user_id,
            run_id=run_id,
            finished_at=_BASE,
            step_count=4,
        )
    )
    reader = TrajectoryReader(object_store=store)

    keys = await reader.list_keys(tenant_id=tenant_id)
    assert len(keys) == 1

    stored = await reader.read(keys[0])
    assert stored is not None
    assert stored.thread_id == thread_id
    assert stored.tenant_id == tenant_id
    assert stored.outcome == "failed"
    assert stored.user_id == user_id
    assert stored.run_id == run_id
    assert stored.step_count == 4
    assert [m["role"] for m in stored.messages] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_list_keys_filters_by_outcome() -> None:
    store = InMemoryObjectStore()
    tenant_id = uuid4()
    recorder = TrajectoryRecorder(object_store=store)
    await recorder.record(
        TrajectoryRecord(
            thread_id=uuid4(),
            tenant_id=tenant_id,
            outcome="success",
            messages=[HumanMessage(content="x")],
            finished_at=_BASE,
        )
    )
    await recorder.record(
        TrajectoryRecord(
            thread_id=uuid4(),
            tenant_id=tenant_id,
            outcome="failed",
            messages=[HumanMessage(content="x")],
            finished_at=_BASE,
        )
    )
    reader = TrajectoryReader(object_store=store)

    assert len(await reader.list_keys(tenant_id=tenant_id, outcome="failed")) == 1
    assert len(await reader.list_keys(tenant_id=tenant_id)) == 2


@pytest.mark.asyncio
async def test_list_keys_outcome_without_tenant_raises() -> None:
    reader = TrajectoryReader(object_store=InMemoryObjectStore())
    with pytest.raises(ValueError, match="requires tenant_id"):
        await reader.list_keys(outcome="failed")


@pytest.mark.asyncio
async def test_read_missing_key_returns_none() -> None:
    reader = TrajectoryReader(object_store=InMemoryObjectStore())
    assert await reader.read("trajectories/nope.jsonl") is None


@pytest.mark.asyncio
async def test_read_malformed_object_returns_none() -> None:
    store = InMemoryObjectStore()
    await store.put("trajectories/bad.jsonl", b"not json at all", content_type="application/jsonl")
    reader = TrajectoryReader(object_store=store)
    assert await reader.read("trajectories/bad.jsonl") is None


@pytest.mark.asyncio
async def test_read_object_missing_required_field_returns_none() -> None:
    store = InMemoryObjectStore()
    await store.put(
        "trajectories/partial.jsonl",
        b'{"thread_id": "x", "tenant_id": "y"}\n',
        content_type="application/jsonl",
    )
    reader = TrajectoryReader(object_store=store)
    assert await reader.read("trajectories/partial.jsonl") is None
