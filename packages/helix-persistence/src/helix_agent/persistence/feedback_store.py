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
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from helix_agent.persistence.models.feedback import FeedbackRow

#: Stream HX-2 — cross-tenant scan role (ledger/audit precedent).
#: ``SET LOCAL`` lifts on commit/rollback; the role is SELECT-only.
_SET_AUDIT_READER_ROLE = text("SET LOCAL ROLE audit_reader")


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
    #: Stream HX-2 (Mini-ADR HX-B1) -- FeedbackConsumerWorker stamp.
    processed_at: datetime | None = None


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

    @abc.abstractmethod
    async def down_rated_threads(self, *, thread_ids: Sequence[UUID]) -> set[UUID]:
        """The subset of ``thread_ids`` that carry at least one 👎 row.

        Stream HX-2 (Mini-ADR HX-B2) — the rollback gate joins user
        disapproval into the per-version outcome window by thread.
        Tenant scoping is the caller's RLS context, same as
        :meth:`list_for_thread` (the ``feedback`` table is FORCE-RLS, so
        the caller must hold a tenant scope — an owner bypass reads zero
        rows silently).
        """

    @abc.abstractmethod
    async def list_unprocessed_down_all_tenants(self, *, limit: int) -> list[FeedbackRecord]:
        """Cross-tenant scan: 👎 rows not yet consumed, oldest first.

        Stream HX-2 (Mini-ADR HX-B1) — the FeedbackConsumerWorker's
        enumeration. ``feedback`` is FORCE-RLS, so the SQL implementation
        assumes the ``audit_reader`` BYPASSRLS role for this read (the
        ledger / audit cross-tenant precedent); the caller must be inside
        a ``bypass`` scope so no tenant GUC is emitted.
        """

    @abc.abstractmethod
    async def mark_processed(self, *, feedback_id: int, processed_at: datetime) -> bool:
        """Stamp ``processed_at`` on one row; ``False`` if missing.

        A *write* — must run under the row's own tenant RLS scope (the
        BYPASSRLS role is read-only by grant). Idempotent: re-stamping a
        processed row just overwrites the timestamp.
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

    async def down_rated_threads(self, *, thread_ids: Sequence[UUID]) -> set[UUID]:
        wanted = set(thread_ids)
        return {r.thread_id for r in self._rows if r.thread_id in wanted and r.rating == "down"}

    async def list_unprocessed_down_all_tenants(self, *, limit: int) -> list[FeedbackRecord]:
        rows = [r for r in self._rows if r.rating == "down" and r.processed_at is None]
        return sorted(rows, key=lambda r: r.id or 0)[:limit]

    async def mark_processed(self, *, feedback_id: int, processed_at: datetime) -> bool:
        for i, r in enumerate(self._rows):
            if r.id == feedback_id:
                self._rows[i] = replace(r, processed_at=processed_at)
                return True
        return False


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

    async def down_rated_threads(self, *, thread_ids: Sequence[UUID]) -> set[UUID]:
        if not thread_ids:
            return set()
        async with self._sf() as session:
            result = await session.execute(
                select(FeedbackRow.thread_id)
                .where(FeedbackRow.thread_id.in_(list(thread_ids)))
                .where(FeedbackRow.rating == "down")
                .distinct()
            )
            return set(result.scalars().all())

    async def list_unprocessed_down_all_tenants(self, *, limit: int) -> list[FeedbackRecord]:
        stmt = (
            select(FeedbackRow)
            .where(FeedbackRow.rating == "down", FeedbackRow.processed_at.is_(None))
            .order_by(FeedbackRow.id.asc())
            .limit(limit)
        )
        async with self._sf() as session:
            # First statement: opens the txn AND assumes the BYPASSRLS role
            # (``SET LOCAL`` lifts on commit/rollback). Without it the
            # FORCE-RLS policy collapses to ``tenant_id = NULL`` → zero rows.
            await session.execute(_SET_AUDIT_READER_ROLE)
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_record(row) for row in rows]

    async def mark_processed(self, *, feedback_id: int, processed_at: datetime) -> bool:
        stmt = (
            update(FeedbackRow)
            .where(FeedbackRow.id == feedback_id)
            .values(processed_at=processed_at)
            .returning(FeedbackRow.id)
        )
        async with self._sf() as session:
            updated = (await session.execute(stmt)).scalars().all()
            await session.commit()
        return bool(updated)


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
        processed_at=row.processed_at,
    )
