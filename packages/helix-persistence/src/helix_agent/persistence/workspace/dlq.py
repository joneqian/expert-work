"""Stream J.15-补强-2 — dead-letter queue for volume backup / archive.

The sandbox-supervisor's volume lifecycle worker pushes failed
operations here on each error; the same worker (or a retention sweep)
drains the ready rows on schedule. Same backoff envelope as K7
(``MemoryWritebackDLQ``); the difference is the ``op_kind`` column —
one queue serves both archive (J-36 第 2 → 第 3 档) and the daily
backup (J-29 第 2 项 DR pipeline).

The repository is deliberately thin (no priority, no per-key fairness)
— J.15-补强-2 just needs "don't drop the work".
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from helix_agent.persistence.models import VolumeBackupDLQRow

#: The two operation kinds the queue carries. The supervisor reuses one
#: backoff envelope for both; only the destination ObjectStore prefix
#: at backup time differs.
VolumeOpKind = Literal["archive", "backup"]


@dataclass(frozen=True)
class VolumeDLQRow:
    """One pending volume archive / backup that failed to land."""

    id: UUID
    tenant_id: UUID
    user_id: UUID
    workspace_id: UUID
    volume_name: str
    op_kind: VolumeOpKind
    attempts: int
    next_retry_at: datetime
    last_error: str | None
    created_at: datetime


def _row_to_dlq(row: VolumeBackupDLQRow) -> VolumeDLQRow:
    op_kind: VolumeOpKind = "archive" if row.op_kind == "archive" else "backup"
    return VolumeDLQRow(
        id=row.id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        workspace_id=row.workspace_id,
        volume_name=row.volume_name,
        op_kind=op_kind,
        attempts=int(row.attempts),
        next_retry_at=row.next_retry_at,
        last_error=row.last_error,
        created_at=row.created_at,
    )


class VolumeBackupDLQ(abc.ABC):
    """Repository for failed volume backup / archive operations."""

    @abc.abstractmethod
    async def enqueue(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        workspace_id: UUID,
        volume_name: str,
        op_kind: VolumeOpKind,
        error: str,
    ) -> VolumeDLQRow:
        """Add one pending op. ``next_retry_at`` starts at ``now()`` so
        the next sweep picks it up immediately; :meth:`record_failure`
        schedules subsequent retries further out."""

    @abc.abstractmethod
    async def take_ready(self, *, limit: int, now: datetime) -> list[VolumeDLQRow]:
        """Return up to ``limit`` rows whose ``next_retry_at <= now``,
        oldest first."""

    @abc.abstractmethod
    async def mark_done(self, *, row_id: UUID) -> None:
        """Delete the row — the retry succeeded."""

    @abc.abstractmethod
    async def record_failure(
        self,
        *,
        row_id: UUID,
        error: str,
        next_retry_at: datetime,
    ) -> None:
        """Bump ``attempts``, store ``error``, schedule ``next_retry_at``."""

    @abc.abstractmethod
    async def count(self) -> int:
        """Total queue depth — used by the worker for a metric."""


# ---------------------------------------------------------------------------
# In-memory implementation — unit tests
# ---------------------------------------------------------------------------


@dataclass
class InMemoryVolumeBackupDLQ(VolumeBackupDLQ):
    """Process-local DLQ for tests. Not safe across processes."""

    _rows: dict[UUID, VolumeDLQRow] = field(default_factory=dict)

    async def enqueue(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        workspace_id: UUID,
        volume_name: str,
        op_kind: VolumeOpKind,
        error: str,
    ) -> VolumeDLQRow:
        now = datetime.now(UTC)
        row = VolumeDLQRow(
            id=uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            workspace_id=workspace_id,
            volume_name=volume_name,
            op_kind=op_kind,
            attempts=0,
            next_retry_at=now,
            last_error=error,
            created_at=now,
        )
        self._rows[row.id] = row
        return row

    async def take_ready(self, *, limit: int, now: datetime) -> list[VolumeDLQRow]:
        ready = [r for r in self._rows.values() if r.next_retry_at <= now]
        ready.sort(key=lambda r: r.next_retry_at)
        return ready[:limit]

    async def mark_done(self, *, row_id: UUID) -> None:
        self._rows.pop(row_id, None)

    async def record_failure(
        self,
        *,
        row_id: UUID,
        error: str,
        next_retry_at: datetime,
    ) -> None:
        existing = self._rows.get(row_id)
        if existing is None:
            return
        self._rows[row_id] = VolumeDLQRow(
            id=existing.id,
            tenant_id=existing.tenant_id,
            user_id=existing.user_id,
            workspace_id=existing.workspace_id,
            volume_name=existing.volume_name,
            op_kind=existing.op_kind,
            attempts=existing.attempts + 1,
            next_retry_at=next_retry_at,
            last_error=error,
            created_at=existing.created_at,
        )

    async def count(self) -> int:
        return len(self._rows)


# ---------------------------------------------------------------------------
# SQLAlchemy implementation — prod
# ---------------------------------------------------------------------------


class SqlVolumeBackupDLQ(VolumeBackupDLQ):
    """Postgres-backed DLQ. One short transaction per call."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def enqueue(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        workspace_id: UUID,
        volume_name: str,
        op_kind: VolumeOpKind,
        error: str,
    ) -> VolumeDLQRow:
        row = VolumeBackupDLQRow(
            tenant_id=tenant_id,
            user_id=user_id,
            workspace_id=workspace_id,
            volume_name=volume_name,
            op_kind=op_kind,
            last_error=error,
        )
        async with self._sf() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return _row_to_dlq(row)

    async def take_ready(self, *, limit: int, now: datetime) -> list[VolumeDLQRow]:
        stmt = (
            select(VolumeBackupDLQRow)
            .where(VolumeBackupDLQRow.next_retry_at <= now)
            .order_by(VolumeBackupDLQRow.next_retry_at.asc())
            .limit(limit)
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_dlq(r) for r in rows]

    async def mark_done(self, *, row_id: UUID) -> None:
        stmt = delete(VolumeBackupDLQRow).where(VolumeBackupDLQRow.id == row_id)
        async with self._sf() as session:
            await session.execute(stmt)
            await session.commit()

    async def record_failure(
        self,
        *,
        row_id: UUID,
        error: str,
        next_retry_at: datetime,
    ) -> None:
        stmt = (
            update(VolumeBackupDLQRow)
            .where(VolumeBackupDLQRow.id == row_id)
            .values(
                attempts=VolumeBackupDLQRow.attempts + 1,
                last_error=error,
                next_retry_at=next_retry_at,
            )
        )
        async with self._sf() as session:
            await session.execute(stmt)
            await session.commit()

    async def count(self) -> int:
        async with self._sf() as session:
            result = await session.execute(select(VolumeBackupDLQRow.id).limit(10_000))
            return len(result.all())


__all__ = [
    "InMemoryVolumeBackupDLQ",
    "SqlVolumeBackupDLQ",
    "VolumeBackupDLQ",
    "VolumeDLQRow",
    "VolumeOpKind",
]
