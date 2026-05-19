"""Persistence for ``sandbox_instance`` — the supervisor's DB access layer.

A :class:`SandboxStore` Protocol keeps the :class:`SandboxSupervisor`
logic testable with an in-memory fake; :class:`DbSandboxStore` is the
SQL-backed production implementation. The per-tenant sandbox limit also
lives here — it reads the ``tenant_quota`` row for the ``sandboxes``
dimension (Stream C.5).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from helix_agent.persistence.models import SandboxInstanceRow, TenantQuotaRow
from helix_agent.protocol.quota import QuotaDimension
from sandbox_supervisor.domain import SandboxRecord, SandboxState

#: ``sandbox_instance`` states that count against a tenant's quota.
_ACTIVE_STATES = (SandboxState.CREATING, SandboxState.IN_USE)


class SandboxStore(Protocol):
    """The persistence operations the supervisor needs."""

    async def insert(self, record: SandboxRecord) -> None:
        """Persist a freshly-created ``CREATING`` sandbox row."""

    async def update(self, record: SandboxRecord) -> None:
        """Overwrite the mutable columns of an existing row."""

    async def get(self, sandbox_id: UUID) -> SandboxRecord | None:
        """Return one sandbox by id, or ``None``."""

    async def count_active_for_tenant(self, tenant_id: UUID) -> int:
        """Count the tenant's non-terminal sandboxes (``CREATING`` + ``IN_USE``)."""

    async def list_orphans(self, *, now: datetime, grace_s: int) -> list[SandboxRecord]:
        """Return ``IN_USE`` sandboxes past ``acquired_at + timeout_s + grace_s``."""

    async def sandbox_limit_for_tenant(self, tenant_id: UUID) -> int | None:
        """Return the tenant's ``sandboxes`` quota, or ``None`` if unset."""


class DbSandboxStore:
    """SQL-backed :class:`SandboxStore` over the ``sandbox_instance`` table."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def insert(self, record: SandboxRecord) -> None:
        async with self._sf() as session:
            session.add(_to_row(record))
            await session.commit()

    async def update(self, record: SandboxRecord) -> None:
        async with self._sf() as session:
            await session.execute(
                update(SandboxInstanceRow)
                .where(SandboxInstanceRow.id == record.id)
                .values(
                    container_id=record.container_id,
                    state=record.state.value,
                    acquired_at=record.acquired_at,
                    released_at=record.released_at,
                    destroyed_at=record.destroyed_at,
                    destroy_reason=record.destroy_reason,
                )
            )
            await session.commit()

    async def get(self, sandbox_id: UUID) -> SandboxRecord | None:
        async with self._sf() as session:
            row = await session.get(SandboxInstanceRow, sandbox_id)
            return _to_record(row) if row is not None else None

    async def count_active_for_tenant(self, tenant_id: UUID) -> int:
        async with self._sf() as session:
            result = await session.execute(
                select(SandboxInstanceRow.id).where(
                    SandboxInstanceRow.tenant_id == tenant_id,
                    SandboxInstanceRow.state.in_([s.value for s in _ACTIVE_STATES]),
                )
            )
            return len(result.fetchall())

    async def list_orphans(self, *, now: datetime, grace_s: int) -> list[SandboxRecord]:
        # Per-row deadline = acquired_at + timeout_s + grace_s. M0 sandbox
        # counts are small, so fetching IN_USE rows and filtering in Python
        # is simpler than a SQL per-row interval expression.
        async with self._sf() as session:
            result = await session.execute(
                select(SandboxInstanceRow).where(
                    SandboxInstanceRow.state == SandboxState.IN_USE.value
                )
            )
            rows = result.scalars().all()
        return [
            record
            for row in rows
            if (record := _to_record(row)).acquired_at is not None
            and record.acquired_at + timedelta(seconds=record.timeout_s + grace_s) < now
        ]

    async def sandbox_limit_for_tenant(self, tenant_id: UUID) -> int | None:
        async with self._sf() as session:
            result = await session.execute(
                select(TenantQuotaRow.limit_value).where(
                    TenantQuotaRow.tenant_id == tenant_id,
                    TenantQuotaRow.dimension == QuotaDimension.SANDBOXES.value,
                )
            )
            return result.scalar_one_or_none()


def _to_row(record: SandboxRecord) -> SandboxInstanceRow:
    return SandboxInstanceRow(
        id=record.id,
        tenant_id=record.tenant_id,
        user_id=record.user_id,
        workspace_id=record.workspace_id,
        image_ref=record.image_ref,
        node=record.node,
        container_id=record.container_id,
        state=record.state.value,
        thread_id=record.thread_id,
        cpu_quota=record.cpu_quota,
        memory_mb=record.memory_mb,
        pids_limit=record.pids_limit,
        timeout_s=record.timeout_s,
        created_at=record.created_at,
        acquired_at=record.acquired_at,
        released_at=record.released_at,
        destroyed_at=record.destroyed_at,
        destroy_reason=record.destroy_reason,
    )


def _to_record(row: SandboxInstanceRow) -> SandboxRecord:
    return SandboxRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        workspace_id=row.workspace_id,
        image_ref=row.image_ref,
        node=row.node,
        container_id=row.container_id,
        state=SandboxState(row.state),
        thread_id=row.thread_id,
        cpu_quota=float(row.cpu_quota),
        memory_mb=row.memory_mb,
        pids_limit=row.pids_limit,
        timeout_s=row.timeout_s,
        created_at=row.created_at,
        acquired_at=row.acquired_at,
        released_at=row.released_at,
        destroyed_at=row.destroyed_at,
        destroy_reason=row.destroy_reason,
    )
