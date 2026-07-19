"""B2 — run_agent 注入 worker sink:发布 + 持久化 + 并发 seq."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest

from expert_work.runtime.runs import DisconnectMode, RunManager, RunRecord
from expert_work.runtime.runs.event_store import InMemoryRunEventStore
from expert_work.runtime.stream_bridge import InMemoryStreamBridge
from orchestrator.sse import run_agent
from orchestrator.tools._worker_events import WORKER_EVENT_SINK_KEY


async def _new_record(rm: RunManager) -> RunRecord:
    return await rm.create(
        run_id=uuid4(), thread_id=uuid4(), tenant_id=uuid4(),
        on_disconnect=DisconnectMode.CANCEL,
    )


class _WorkerGraph:
    """astream 期间经注入的 sink 发 worker 帧(模拟 child run 桥接)."""

    def __init__(self, frames: list[dict[str, Any]], *, concurrent: bool = False) -> None:
        self.frames = frames
        self.concurrent = concurrent

    async def astream(
        self, input: Any, config: Any = None, *, stream_mode: Any = None
    ) -> AsyncIterator[Any]:
        del input, stream_mode
        sink = config["configurable"][WORKER_EVENT_SINK_KEY]
        if self.concurrent:
            await asyncio.gather(*(sink(f) for f in self.frames))
        else:
            for frame in self.frames:
                await sink(frame)
        yield {"agent": {"step_count": 1}}


@pytest.mark.asyncio
async def test_worker_frames_published_and_persisted_with_monotonic_seq() -> None:
    bridge = InMemoryStreamBridge()
    rm = RunManager()
    record = await _new_record(rm)
    store = InMemoryRunEventStore()
    frames = [
        {"worker_id": "w1", "kind": "start", "wseq": 0},
        {"worker_id": "w1", "kind": "end", "wseq": 1},
    ]

    await run_agent(
        bridge=bridge, run_manager=rm, record=record,
        graph=_WorkerGraph(frames), graph_input={},
        config={"configurable": {"thread_id": str(record.thread_id)}},
        event_store=store,
    )

    events = await store.list(run_id=record.run_id, limit=500)
    worker_rows = [e for e in events if e.event_name == "worker"]
    assert [r.data["kind"] for r in worker_rows] == ["start", "end"]
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)  # 无重复


@pytest.mark.asyncio
async def test_concurrent_worker_frames_do_not_collide_on_seq() -> None:
    bridge = InMemoryStreamBridge()
    rm = RunManager()
    record = await _new_record(rm)
    store = InMemoryRunEventStore()  # append 对重复 (run_id, seq) 直接 raise
    pairs = [("a", "start"), ("b", "start"), ("a", "end"), ("b", "end")]
    frames = [{"worker_id": w, "kind": k, "wseq": i} for i, (w, k) in enumerate(pairs)]

    await run_agent(
        bridge=bridge, run_manager=rm, record=record,
        graph=_WorkerGraph(frames, concurrent=True), graph_input={},
        config={"configurable": {"thread_id": str(record.thread_id)}},
        event_store=store,
    )

    events = await store.list(run_id=record.run_id, limit=500)
    worker_rows = [e for e in events if e.event_name == "worker"]
    assert len(worker_rows) == 4
    assert len({e.seq for e in events}) == len(events)
