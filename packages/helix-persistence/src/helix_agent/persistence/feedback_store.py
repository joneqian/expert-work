"""Feedback store — Stream G.6.

Persists user 👍/👎 feedback on agent sessions. Two implementations
behind one ABC, matching the audit-log store pattern:

* :class:`InMemoryFeedbackStore` — dev / unit tests (no durability).
* :class:`DbFeedbackStore` — Postgres-backed; RLS-scoped via the
  sessionmaker wrapper (the ``app.tenant_id`` GUC is set after BEGIN,
  so the ``feedback`` table's tenant-isolation policy applies).
"""

from __future__ import annotations

import abc
import itertools
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from helix_agent.persistence.models.feedback import FeedbackRow


@dataclass(frozen=True)
class FeedbackRecord:
    """One feedback row. ``id`` / ``created_at`` are ``None`` pre-insert."""

    tenant_id: UUID
    thread_id: UUID
    rating: str
    actor_id: str
    turn_seq: int | None = None
    trace_id: str | None = None
    comment: str | None = None
    id: int | None = None
    created_at: datetime | None = None


class FeedbackStore(abc.ABC):
    """Append + read-by-thread for user feedback."""

    @abc.abstractmethod
    async def insert(self, record: FeedbackRecord) -> FeedbackRecord:
        """Persist one feedback row; return it with ``id`` + ``created_at`` filled."""

    @abc.abstractmethod
    async def list_for_thread(self, *, thread_id: UUID) -> list[FeedbackRecord]:
        """Return feedback for one thread, newest first.

        Tenant scoping is the caller's RLS context (the GUC / contextvar),
        not a SQL filter — so this doubles as the cross-tenant isolation
        check (test #64).
        """


class InMemoryFeedbackStore(FeedbackStore):
    """In-memory :class:`FeedbackStore` — dev / unit tests."""

    def __init__(self) -> None:
        self._rows: list[FeedbackRecord] = []
        self._ids = itertools.count(1)

    async def insert(self, record: FeedbackRecord) -> FeedbackRecord:
        stored = replace(record, id=next(self._ids), created_at=datetime.now(UTC))
        self._rows.append(stored)
        return stored

    async def list_for_thread(self, *, thread_id: UUID) -> list[FeedbackRecord]:
        rows = [r for r in self._rows if r.thread_id == thread_id]
        return sorted(rows, key=lambda r: r.id or 0, reverse=True)


class DbFeedbackStore(FeedbackStore):
    """Postgres-backed :class:`FeedbackStore`.

    ``session_factory`` must be the RLS-wrapped sessionmaker
    (:func:`helix_agent.persistence.rls.build_rls_sessionmaker`): the
    tenant GUC is set after BEGIN, so the ``feedback`` RLS policy scopes
    every row to the calling tenant. An ``insert`` whose tenant context
    is unset fails the policy ``WITH CHECK`` — RLS, not trust.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def insert(self, record: FeedbackRecord) -> FeedbackRecord:
        async with self._sf() as session:
            row = FeedbackRow(
                tenant_id=record.tenant_id,
                thread_id=record.thread_id,
                turn_seq=record.turn_seq,
                trace_id=record.trace_id,
                rating=record.rating,
                comment=record.comment,
                actor_id=record.actor_id,
            )
            session.add(row)
            await session.flush()
            await session.refresh(row)
            stored = _row_to_record(row)
            await session.commit()
            return stored

    async def list_for_thread(self, *, thread_id: UUID) -> list[FeedbackRecord]:
        async with self._sf() as session:
            result = await session.execute(
                select(FeedbackRow)
                .where(FeedbackRow.thread_id == thread_id)
                .order_by(FeedbackRow.id.desc())
            )
            return [_row_to_record(row) for row in result.scalars().all()]


def _row_to_record(row: FeedbackRow) -> FeedbackRecord:
    return FeedbackRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        thread_id=row.thread_id,
        turn_seq=row.turn_seq,
        trace_id=row.trace_id,
        rating=row.rating,
        comment=row.comment,
        actor_id=row.actor_id,
        created_at=row.created_at,
    )
