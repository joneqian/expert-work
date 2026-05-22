"""Durable run-lifecycle store — Stream J.8 closeout follow-up (Mini-ADR J-41).

:class:`~helix_agent.runtime.runs.manager.RunManager` is an in-memory
registry with a 5-minute TTL; this store is its durable mirror.
``RunManager`` writes every create / status transition here, and
``GET .../runs/{id}`` reads it when the in-memory record has expired —
so a run's status survives both the TTL sweep and a control-plane
restart.

Mini-ADR J-41 scopes this to the *bare run lifecycle layer*. Run
queueing / multitask strategy / retry / DLQ are J.10 work (Mini-ADR
J-26) and add their columns later via expand-contract.

Implementations:
- :class:`InMemoryRunStore` — unit tests.
- :class:`SqlRunStore` — Postgres-backed, the ``agent_run`` table.
"""

from __future__ import annotations

import abc
from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from helix_agent.persistence.models import AgentRunRow
from helix_agent.runtime.runs.schemas import DisconnectMode, RunInfo, RunStatus


class RunStore(abc.ABC):
    """Durable, tenant-scoped run-lifecycle registry."""

    @abc.abstractmethod
    async def create(self, info: RunInfo) -> None:
        """Persist a new run row.

        ``run_id`` is the primary key — a second ``create`` for the
        same id is a programming error and the SQL backend's primary
        key surfaces it.
        """

    @abc.abstractmethod
    async def set_status(
        self,
        *,
        run_id: UUID,
        tenant_id: UUID,
        status: RunStatus,
        updated_at: datetime,
        error: str | None = None,
        finished_at: datetime | None = None,
    ) -> bool:
        """Update a run's status; return ``True`` iff the row exists.

        ``error`` / ``finished_at`` are written only when not ``None``
        so a non-terminal transition (e.g. → RUNNING) never clears a
        verdict an earlier terminal write recorded.
        """

    @abc.abstractmethod
    async def get(self, *, run_id: UUID, tenant_id: UUID) -> RunInfo | None:
        """Return the run row, or ``None`` when unknown / cross-tenant.

        ``None`` (never raising) on a cross-tenant probe so callers can
        turn it straight into a 404 that hides existence.
        """

    @abc.abstractmethod
    async def list_by_thread(self, *, thread_id: UUID, tenant_id: UUID) -> list[RunInfo]:
        """Return all runs for ``thread_id`` under ``tenant_id``, oldest first."""


class InMemoryRunStore(RunStore):
    """In-memory ``RunStore`` for unit tests."""

    def __init__(self) -> None:
        # Keyed by run_id — the primary key.
        self._rows: dict[UUID, RunInfo] = {}

    async def create(self, info: RunInfo) -> None:
        if info.run_id in self._rows:
            msg = f"run row already exists for run {info.run_id}"
            raise ValueError(msg)
        self._rows[info.run_id] = info

    async def set_status(
        self,
        *,
        run_id: UUID,
        tenant_id: UUID,
        status: RunStatus,
        updated_at: datetime,
        error: str | None = None,
        finished_at: datetime | None = None,
    ) -> bool:
        row = self._rows.get(run_id)
        if row is None or row.tenant_id != tenant_id:
            return False
        self._rows[run_id] = replace(
            row,
            status=status,
            updated_at=updated_at,
            error=error if error is not None else row.error,
            finished_at=finished_at if finished_at is not None else row.finished_at,
        )
        return True

    async def get(self, *, run_id: UUID, tenant_id: UUID) -> RunInfo | None:
        row = self._rows.get(run_id)
        if row is None or row.tenant_id != tenant_id:
            return None
        return row

    async def list_by_thread(self, *, thread_id: UUID, tenant_id: UUID) -> list[RunInfo]:
        rows = [
            r for r in self._rows.values() if r.thread_id == thread_id and r.tenant_id == tenant_id
        ]
        rows.sort(key=lambda r: r.created_at)
        return rows


def _row_to_dto(row: AgentRunRow) -> RunInfo:
    return RunInfo(
        run_id=row.id,
        tenant_id=row.tenant_id,
        thread_id=row.thread_id,
        user_id=row.user_id,
        status=RunStatus(row.status),
        on_disconnect=DisconnectMode(row.on_disconnect),
        is_resume=row.is_resume,
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
        finished_at=row.finished_at,
    )


class SqlRunStore(RunStore):
    """Postgres-backed run-lifecycle registry — the ``agent_run`` table."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create(self, info: RunInfo) -> None:
        async with self._sf() as session:
            session.add(
                AgentRunRow(
                    id=info.run_id,
                    tenant_id=info.tenant_id,
                    user_id=info.user_id,
                    thread_id=info.thread_id,
                    status=info.status.value,
                    on_disconnect=info.on_disconnect.value,
                    is_resume=info.is_resume,
                    error=info.error,
                    created_at=info.created_at,
                    updated_at=info.updated_at,
                    finished_at=info.finished_at,
                )
            )
            await session.commit()

    async def set_status(
        self,
        *,
        run_id: UUID,
        tenant_id: UUID,
        status: RunStatus,
        updated_at: datetime,
        error: str | None = None,
        finished_at: datetime | None = None,
    ) -> bool:
        values: dict[str, Any] = {"status": status.value, "updated_at": updated_at}
        if error is not None:
            values["error"] = error
        if finished_at is not None:
            values["finished_at"] = finished_at
        async with self._sf() as session:
            result = await session.execute(
                update(AgentRunRow)
                .where(AgentRunRow.id == run_id, AgentRunRow.tenant_id == tenant_id)
                .values(values)
            )
            await session.commit()
        return int(getattr(result, "rowcount", 0) or 0) > 0

    async def get(self, *, run_id: UUID, tenant_id: UUID) -> RunInfo | None:
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(AgentRunRow).where(
                        AgentRunRow.id == run_id,
                        AgentRunRow.tenant_id == tenant_id,
                    )
                )
            ).scalar_one_or_none()
        return _row_to_dto(row) if row is not None else None

    async def list_by_thread(self, *, thread_id: UUID, tenant_id: UUID) -> list[RunInfo]:
        async with self._sf() as session:
            rows = (
                (
                    await session.execute(
                        select(AgentRunRow)
                        .where(
                            AgentRunRow.thread_id == thread_id,
                            AgentRunRow.tenant_id == tenant_id,
                        )
                        .order_by(AgentRunRow.created_at.asc())
                    )
                )
                .scalars()
                .all()
            )
        return [_row_to_dto(r) for r in rows]
