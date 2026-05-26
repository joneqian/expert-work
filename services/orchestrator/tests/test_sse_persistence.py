"""Tests for ``run_agent`` ``event_store`` dual-write — Stream H.3 PR 3 (Mini-ADR H-7).

Verifies that every frame the worker emits to ``StreamBridge.publish``
is also mirrored to the durable :class:`RunEventStore`, AND that a
store failure neither blocks the SSE stream nor changes its content.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest

from helix_agent.runtime.runs import (
    DisconnectMode,
    InMemoryRunEventStore,
    RunEventRecord,
    RunEventStore,
    RunManager,
    RunRecord,
)
from helix_agent.runtime.stream_bridge import END_SENTINEL, InMemoryStreamBridge
from orchestrator.sse import run_agent


@dataclass
class _ScriptedGraph:
    chunks: list[Any]
    chunk_delay_s: float = 0.0
    started: asyncio.Event = field(default_factory=asyncio.Event)
    final_state: dict[str, Any] = field(default_factory=dict)

    async def astream(
        self, _input: Any, _config: Any = None, *, stream_mode: str = "updates"
    ) -> AsyncIterator[Any]:
        for chunk in self.chunks:
            if self.chunk_delay_s:
                await asyncio.sleep(self.chunk_delay_s)
            self.started.set()
            yield chunk

    async def aget_state(self, _config: Any) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(values=self.final_state)


async def _new_record(rm: RunManager) -> RunRecord:
    return await rm.create(
        run_id=uuid4(),
        thread_id=uuid4(),
        tenant_id=uuid4(),
        on_disconnect=DisconnectMode.CANCEL,
    )


async def _drain(bridge: InMemoryStreamBridge, run_id: UUID) -> list[Any]:
    events: list[Any] = []
    async for entry in bridge.subscribe(run_id, heartbeat_interval=5.0):
        if entry is END_SENTINEL:
            break
        events.append(entry)
    return events


@pytest.mark.asyncio
async def test_run_agent_mirrors_metadata_and_updates_to_event_store() -> None:
    bridge = InMemoryStreamBridge()
    rm = RunManager()
    record = await _new_record(rm)
    store = InMemoryRunEventStore()
    graph = _ScriptedGraph(chunks=[{"agent": {"step_count": 1}}, {"agent": {"step_count": 2}}])

    await run_agent(
        bridge=bridge,
        run_manager=rm,
        record=record,
        graph=graph,
        graph_input={"messages": []},
        config={},
        event_store=store,
    )

    listed = await store.list(run_id=record.run_id)
    # Expect: 1 metadata frame + 2 updates frames = 3 persisted rows.
    assert [r.event_name for r in listed] == ["metadata", "updates", "updates"]
    # Monotonic seqs starting at 0.
    assert [r.seq for r in listed] == [0, 1, 2]
    # First row carries the metadata payload.
    assert listed[0].data["run_id"] == str(record.run_id)


@pytest.mark.asyncio
async def test_run_agent_mirrors_error_event_when_graph_raises() -> None:
    """A failed run still mirrors metadata + error frames to the store
    so RunDetail can replay the failure."""
    bridge = InMemoryStreamBridge()
    rm = RunManager()
    record = await _new_record(rm)
    store = InMemoryRunEventStore()

    @dataclass
    class _FailingGraph:
        async def astream(
            self, _input: Any, _config: Any = None, *, stream_mode: str = "updates"
        ) -> AsyncIterator[Any]:
            yield {"agent": {"step_count": 1}}
            raise RuntimeError("graph failed")

        async def aget_state(self, _config: Any) -> Any:
            from types import SimpleNamespace

            return SimpleNamespace(values={})

    await run_agent(
        bridge=bridge,
        run_manager=rm,
        record=record,
        graph=_FailingGraph(),
        graph_input={},
        config={},
        event_store=store,
    )

    listed = await store.list(run_id=record.run_id)
    assert [r.event_name for r in listed] == ["metadata", "updates", "error"]
    assert listed[-1].data["name"] == "RuntimeError"


@pytest.mark.asyncio
async def test_store_append_failure_does_not_block_sse() -> None:
    """The durable mirror is graceful-degradation — a store error must
    NEVER stop the live SSE stream."""

    class _FailingStore(RunEventStore):
        async def append(self, record: RunEventRecord) -> None:
            raise RuntimeError("simulated DB outage")

        async def list(
            self,
            *,
            run_id: UUID,
            since_seq: int | None = None,
            limit: int = 100,
        ) -> Sequence[RunEventRecord]:
            return []

    bridge = InMemoryStreamBridge()
    rm = RunManager()
    record = await _new_record(rm)
    store = _FailingStore()
    graph = _ScriptedGraph(chunks=[{"agent": {"step_count": 1}}])

    await run_agent(
        bridge=bridge,
        run_manager=rm,
        record=record,
        graph=graph,
        graph_input={"messages": []},
        config={},
        event_store=store,
    )

    # SSE stream still delivers metadata + updates + (graph terminates ok).
    events = await _drain(bridge, record.run_id)
    types = [e.event for e in events]
    assert "metadata" in types
    assert "updates" in types


@pytest.mark.asyncio
async def test_event_store_optional_keeps_sse_working_without_it() -> None:
    """Backwards-compat: ``event_store=None`` (default) behaves exactly as
    before this PR — no mirror, no errors."""
    bridge = InMemoryStreamBridge()
    rm = RunManager()
    record = await _new_record(rm)
    graph = _ScriptedGraph(chunks=[{"agent": {"step_count": 1}}])

    await run_agent(
        bridge=bridge,
        run_manager=rm,
        record=record,
        graph=graph,
        graph_input={"messages": []},
        config={},
    )
    events = await _drain(bridge, record.run_id)
    assert any(e.event == "metadata" for e in events)
