"""Unit tests for the transcript mirror sweep + shared extraction (IA M4).

Drives ``TranscriptMirrorSweep.run_once`` over a stubbed checkpointer and
an in-memory message store whose ``pending_thread_ids`` is overridden (the
real work-queue selection is SQL-only and covered by the persistence
integration tests).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from control_plane.transcript import read_turns
from control_plane.transcript_mirror_sweep import TranscriptMirrorSweep
from helix_agent.persistence import InMemoryThreadMessageStore


def _msg(mtype: str, content: Any) -> SimpleNamespace:
    return SimpleNamespace(type=mtype, content=content)


class _FakeCheckpointer:
    """aget_tuple stub — thread_id → message list (None = no checkpoint)."""

    def __init__(self, by_thread: dict[str, list[SimpleNamespace] | None]) -> None:
        self._by_thread = by_thread

    async def aget_tuple(self, config: dict[str, Any]) -> SimpleNamespace | None:
        thread_id = config["configurable"]["thread_id"]
        messages = self._by_thread.get(thread_id)
        if messages is None:
            return None
        return SimpleNamespace(checkpoint={"channel_values": {"messages": messages}})


class _QueueStore(InMemoryThreadMessageStore):
    """In-memory store with an injectable work queue."""

    def __init__(self, queue: list[tuple[UUID, UUID]]) -> None:
        super().__init__()
        self.queue = queue
        self.synced: list[tuple[UUID, datetime]] = []

    async def pending_thread_ids(self, *, limit: int) -> list[tuple[UUID, UUID]]:
        return self.queue[:limit]

    async def sync_thread(self, *, thread_id, tenant_id, turns, synced_at) -> None:  # type: ignore[no-untyped-def]
        self.synced.append((thread_id, synced_at))
        await super().sync_thread(
            thread_id=thread_id, tenant_id=tenant_id, turns=turns, synced_at=synced_at
        )


def _sweep(store: InMemoryThreadMessageStore, checkpointer: object | None) -> TranscriptMirrorSweep:
    runtime = SimpleNamespace(durable_checkpointer=checkpointer)
    return TranscriptMirrorSweep(message_store=store, runtime=runtime)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_read_turns_filters_to_text_turns_with_stable_seq() -> None:
    cp = _FakeCheckpointer(
        {
            "t1": [
                _msg("human", "I was charged twice"),
                _msg("ai", [{"type": "text", "text": "Refund opened"}]),
                _msg("tool", "lookup result"),  # skipped
                _msg("ai", "   "),  # empty text — skipped
                _msg("human", "thanks"),
            ]
        }
    )
    turns = await read_turns(cp, UUID(int=0))  # type: ignore[arg-type]
    assert turns == []  # unknown thread → no checkpoint → empty

    class _Fixed(_FakeCheckpointer):
        async def aget_tuple(self, config: dict[str, Any]) -> SimpleNamespace | None:
            return SimpleNamespace(
                checkpoint={"channel_values": {"messages": self._by_thread["t1"]}}
            )

    turns = await read_turns(_Fixed(cp._by_thread), uuid4())  # type: ignore[arg-type]
    # seq is the raw channel index — gaps where non-text turns were skipped.
    assert [(t.seq, t.role) for t in turns] == [(0, "user"), (1, "assistant"), (4, "user")]
    assert turns[1].content == "Refund opened"


@pytest.mark.asyncio
async def test_run_once_mirrors_pending_threads() -> None:
    tenant = uuid4()
    ok_thread, broken_thread = uuid4(), uuid4()
    store = _QueueStore([(ok_thread, tenant), (broken_thread, tenant)])

    class _PartiallyBroken(_FakeCheckpointer):
        async def aget_tuple(self, config: dict[str, Any]) -> SimpleNamespace | None:
            if config["configurable"]["thread_id"] == str(broken_thread):
                msg = "checkpoint read failed"
                raise RuntimeError(msg)
            return SimpleNamespace(
                checkpoint={"channel_values": {"messages": [_msg("human", "find me")]}}
            )

    sweep = _sweep(store, _PartiallyBroken({}))
    before = datetime.now(UTC)
    synced = await sweep.run_once()

    # The broken thread is skipped (retried next cycle), the healthy one lands.
    assert synced == 1
    assert await store.search_thread_ids(tenant_id=tenant, q="find me") == {ok_thread}
    # Watermark is taken BEFORE the checkpoint read — a run landing mid-read
    # bumps agent_run.updated_at past it, so the tail is re-selected.
    (thread_synced, mark) = store.synced[0]
    assert thread_synced == ok_thread
    assert mark >= before


@pytest.mark.asyncio
async def test_run_once_short_circuits_without_checkpointer() -> None:
    store = _QueueStore([(uuid4(), uuid4())])
    sweep = _sweep(store, None)
    assert await sweep.run_once() == 0
    assert store.synced == []
