# ============================================================
# Adapted from bytedance/deer-flow @ 813d3c94efa7fdea6aafcb4f459304db91fcaed0
# Source: backend/packages/harness/deerflow/runtime/events/store/db.py
# License: MIT (see vendor LICENSE)
# Modifications:
#   - Aligned to EventStore (expert_work.runtime.event_log.base) — see base.py header
#   - Backed by expert_work.persistence.models.EventLogRow (ADR-0002 schema):
#       fields = thread_id / session_id / tenant_id / seq / event_type / payload / trace_id
#       (no DeerFlow run_id / category / content / event_metadata / user_id)
#   - Preserved algorithms:
#       * SELECT max(seq) ... FOR UPDATE  — serialize seq alloc within a thread
#       * put_batch single-transaction lock + bulk insert
#       * payload size truncation (configurable max_payload_bytes)
#   - Dropped DeerFlow's content_is_json / content_is_dict signals (our JSONB
#     stores payloads natively; no string round-tripping)
#   - tenant_id is a required arg, not a contextvar (Stream C wires the
#     contextvar separately)
# Last sync: 2026-05-11
# ============================================================

"""SQLAlchemy-backed ``EventStore`` (Postgres / asyncpg).

Production implementation for the ``event_log`` table created by
Stream A.1 / ADR-0002. The :class:`InMemoryEventStore` is preferred
for unit tests; this implementation is for integration / production.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from expert_work.persistence.models import EventLogRow
from expert_work.protocol import EventRecord, EventType
from expert_work.runtime.event_log.base import EventStore

logger = logging.getLogger(__name__)


def _coerce_event_type(value: EventType | str) -> EventType:
    return value if isinstance(value, EventType) else EventType(value)


async def _acquire_thread_lock(session: AsyncSession, thread_id: UUID) -> None:
    """Serialize seq allocation for a given thread within the current transaction.

    DeerFlow's ``with_for_update()`` on a ``max(seq)`` aggregate is invalid on
    Postgres (``FeatureNotSupportedError``); it relied on SQLite's no-op
    behaviour. Postgres native ``pg_advisory_xact_lock(bigint)`` keyed on the
    thread_id hash gives us the same "one writer per thread at a time"
    semantics without locking aggregates.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:t, 0))"),
        {"t": str(thread_id)},
    )


class DbEventStore(EventStore):
    """Postgres-backed event store. Inject via dependency injection."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        max_payload_bytes: int = 10_240,
    ) -> None:
        self._sf = session_factory
        self._max_payload_bytes = max_payload_bytes

    # --- payload safety -----------------------------------------------------

    def _maybe_truncate(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        """Cap JSONB payload size to prevent runaway rows.

        Truncates the JSON string representation; the truncated payload becomes
        ``{"_truncated": true, "_original_bytes": N, "_excerpt": "<head>"}``.
        DeerFlow's category=="trace" gating is replaced by an always-on byte cap.
        """
        if not payload:
            return {}
        encoded = json.dumps(payload, default=str, ensure_ascii=False).encode("utf-8")
        if len(encoded) <= self._max_payload_bytes:
            return payload
        head = encoded[: self._max_payload_bytes].decode("utf-8", errors="ignore")
        logger.debug(
            "event_log.payload_truncated original_bytes=%d cap=%d",
            len(encoded),
            self._max_payload_bytes,
        )
        return {
            "_truncated": True,
            "_original_bytes": len(encoded),
            "_excerpt": head,
        }

    # --- row <-> record mapping --------------------------------------------

    @staticmethod
    def _row_to_record(row: EventLogRow) -> EventRecord:
        return EventRecord(
            id=row.id,
            thread_id=row.thread_id,
            session_id=row.session_id,
            tenant_id=row.tenant_id,
            seq=row.seq,
            event_type=_coerce_event_type(row.event_type),
            payload=row.payload,
            trace_id=row.trace_id,
            created_at=row.created_at,
        )

    # --- public API ---------------------------------------------------------

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
        """Single-event insert with FOR UPDATE seq alloc.

        Low-frequency path (session start/end events). High-volume callers
        should batch through :meth:`put_batch`.
        """
        safe_payload = self._maybe_truncate(payload)
        async with self._sf() as session:
            async with session.begin():
                await _acquire_thread_lock(session, thread_id)
                max_seq = await session.scalar(
                    select(func.max(EventLogRow.seq)).where(EventLogRow.thread_id == thread_id)
                )
                row = EventLogRow(
                    thread_id=thread_id,
                    session_id=session_id,
                    tenant_id=tenant_id,
                    seq=(max_seq or 0) + 1,
                    event_type=str(_coerce_event_type(event_type)),
                    payload=safe_payload,
                    trace_id=trace_id,
                    created_at=datetime.now(UTC),
                )
                session.add(row)
            return self._row_to_record(row)

    async def put_batch(self, events: Sequence[EventRecord]) -> list[EventRecord]:
        if not events:
            return []
        thread_ids = {e.thread_id for e in events}
        if len(thread_ids) != 1:
            msg = "put_batch requires all events to share the same thread_id"
            raise ValueError(msg)
        thread_id = next(iter(thread_ids))

        async with self._sf() as session:
            async with session.begin():
                await _acquire_thread_lock(session, thread_id)
                max_seq = await session.scalar(
                    select(func.max(EventLogRow.seq)).where(EventLogRow.thread_id == thread_id)
                )
                seq = max_seq or 0
                rows: list[EventLogRow] = []
                for e in events:
                    seq += 1
                    rows.append(
                        EventLogRow(
                            thread_id=e.thread_id,
                            session_id=e.session_id,
                            tenant_id=e.tenant_id,
                            seq=seq,
                            event_type=str(_coerce_event_type(e.event_type)),
                            payload=self._maybe_truncate(e.payload),
                            trace_id=e.trace_id,
                            created_at=datetime.now(UTC),
                        )
                    )
                session.add_all(rows)
            return [self._row_to_record(r) for r in rows]

    async def list_events(
        self,
        thread_id: UUID,
        *,
        event_types: Sequence[EventType | str] | None = None,
        before_seq: int | None = None,
        after_seq: int | None = None,
        limit: int = 500,
    ) -> list[EventRecord]:
        stmt = select(EventLogRow).where(EventLogRow.thread_id == thread_id)
        if event_types is not None:
            normalized = [str(_coerce_event_type(t)) for t in event_types]
            stmt = stmt.where(EventLogRow.event_type.in_(normalized))
        if before_seq is not None:
            stmt = stmt.where(EventLogRow.seq < before_seq)
        if after_seq is not None:
            stmt = stmt.where(EventLogRow.seq > after_seq)

        async with self._sf() as session:
            if after_seq is not None:
                stmt = stmt.order_by(EventLogRow.seq.asc()).limit(limit)
                rows = list((await session.execute(stmt)).scalars())
            else:
                stmt = stmt.order_by(EventLogRow.seq.desc()).limit(limit)
                rows = list(reversed(list((await session.execute(stmt)).scalars())))
        return [self._row_to_record(r) for r in rows]

    async def count(self, thread_id: UUID) -> int:
        async with self._sf() as session:
            value = await session.scalar(
                select(func.count())
                .select_from(EventLogRow)
                .where(EventLogRow.thread_id == thread_id)
            )
        return value or 0

    async def delete_by_thread(self, thread_id: UUID) -> int:
        async with self._sf() as session:
            result = await session.execute(
                delete(EventLogRow).where(EventLogRow.thread_id == thread_id)
            )
            await session.commit()
            # CursorResult exposes rowcount; the generic Result type does not.
            return int(getattr(result, "rowcount", 0) or 0)
