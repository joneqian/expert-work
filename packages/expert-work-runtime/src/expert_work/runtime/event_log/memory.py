# ============================================================
# Adapted from bytedance/deer-flow @ 813d3c94efa7fdea6aafcb4f459304db91fcaed0
# Source: backend/packages/harness/deerflow/runtime/events/store/memory.py
# License: MIT (see vendor LICENSE)
# Modifications:
#   - Aligned to EventStore (expert_work.runtime.event_log.base) — see base.py header
#   - In-memory state keyed by thread_id (no run_id/category split)
#   - Removed DeerFlow's category-based message filtering (we use event_type only)
# Last sync: 2026-05-11
# ============================================================

"""In-memory ``EventStore`` for tests and lightweight dev runs.

Single-process async usage only — no threading locks (all mutations happen
inside the same event loop).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from itertools import count
from typing import Any
from uuid import UUID

from expert_work.protocol import EventRecord, EventType
from expert_work.runtime.event_log.base import EventStore


class InMemoryEventStore(EventStore):
    def __init__(self) -> None:
        self._events: dict[UUID, list[EventRecord]] = {}
        self._seq_counters: dict[UUID, int] = {}
        self._id_counter = count(start=1)

    def _next_seq(self, thread_id: UUID) -> int:
        current = self._seq_counters.get(thread_id, 0)
        next_val = current + 1
        self._seq_counters[thread_id] = next_val
        return next_val

    def _coerce_event_type(self, value: EventType | str) -> EventType:
        return value if isinstance(value, EventType) else EventType(value)

    async def put(
        self,
        *,
        thread_id: UUID,
        tenant_id: UUID,
        event_type: EventType | str,
        payload: dict[str, Any] | None = None,
        session_id: UUID | None = None,
        trace_id: str | None = None,
    ) -> EventRecord:
        record = EventRecord(
            id=next(self._id_counter),
            thread_id=thread_id,
            session_id=session_id,
            tenant_id=tenant_id,
            seq=self._next_seq(thread_id),
            event_type=self._coerce_event_type(event_type),
            payload=payload or {},
            trace_id=trace_id,
            created_at=datetime.now(UTC),
        )
        self._events.setdefault(thread_id, []).append(record)
        return record

    async def put_batch(self, events: Sequence[EventRecord]) -> list[EventRecord]:
        if not events:
            return []
        thread_ids = {e.thread_id for e in events}
        if len(thread_ids) != 1:
            msg = "put_batch requires all events to share the same thread_id"
            raise ValueError(msg)

        results: list[EventRecord] = []
        for e in events:
            results.append(
                await self.put(
                    thread_id=e.thread_id,
                    tenant_id=e.tenant_id,
                    event_type=e.event_type,
                    payload=e.payload,
                    session_id=e.session_id,
                    trace_id=e.trace_id,
                )
            )
        return results

    async def list_events(
        self,
        thread_id: UUID,
        *,
        event_types: Sequence[EventType | str] | None = None,
        before_seq: int | None = None,
        after_seq: int | None = None,
        limit: int = 500,
    ) -> list[EventRecord]:
        rows = list(self._events.get(thread_id, []))
        if event_types is not None:
            normalized = {self._coerce_event_type(t) for t in event_types}
            rows = [r for r in rows if r.event_type in normalized]
        if before_seq is not None:
            rows = [r for r in rows if r.seq < before_seq]
        if after_seq is not None:
            rows = [r for r in rows if r.seq > after_seq]

        if after_seq is not None:
            return rows[:limit]
        return rows[-limit:]

    async def count(self, thread_id: UUID) -> int:
        return len(self._events.get(thread_id, []))

    async def delete_by_thread(self, thread_id: UUID) -> int:
        removed = self._events.pop(thread_id, [])
        self._seq_counters.pop(thread_id, None)
        return len(removed)
