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
from collections.abc import Collection
from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, update
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

    @abc.abstractmethod
    async def delete_by_thread(self, *, thread_id: UUID, tenant_id: UUID) -> int:
        """Hard-delete every run row for ``thread_id`` under ``tenant_id``.

        Session purge (hard delete of a whole conversation). Returns the
        number of rows removed. Tenant-scoped — never touches another
        tenant's runs.
        """

    @abc.abstractmethod
    async def list_for_tenant(
        self,
        *,
        tenant_id: UUID,
        status: RunStatus | None = None,
        thread_ids: Collection[UUID] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RunInfo]:
        """Return runs for ``tenant_id``, newest first; paginated.

        Stream H.3 PR 1 — feeds the cross-thread ``GET /v1/runs`` index.
        ``limit`` is clamped to ``MAX_LIST_LIMIT`` (Mini-ADR H-7 D).
        ``thread_ids`` narrows to runs of those threads (Stream H.6
        Mini-ADR H-10 — the API layer resolves an agent to its thread
        window via ``ThreadMetaStore`` and passes the ids here; an empty
        collection returns no rows).
        """

    @abc.abstractmethod
    async def list_all_tenants(
        self,
        *,
        status: RunStatus | None = None,
        thread_ids: Collection[UUID] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RunInfo]:
        """Cross-tenant variant for ``system_admin`` aggregate views.

        Stream N: callers MUST wrap this in ``bypass_rls_session()`` so
        the SQL backend bypasses tenant RLS. ``limit`` is clamped to
        ``MAX_LIST_LIMIT`` (Mini-ADR H-7 D). ``thread_ids`` as in
        :meth:`list_for_tenant` (Mini-ADR H-10).
        """

    @abc.abstractmethod
    async def set_trace_id(
        self,
        *,
        run_id: UUID,
        tenant_id: UUID,
        trace_id: str,
    ) -> bool:
        """Persist the OTel ``trace_id`` for ``run_id``.

        Stream H.3 PR 2 (Mini-ADR H-9.5). Idempotent overwrite — if the
        worker observes its own trace_id (rare), the second call wins.
        Returns ``True`` iff the row exists; cross-tenant probes return
        ``False`` so callers can hide existence.
        """

    # --- Stream 9.4 (HA failover) — ownership lease ------------------------

    @abc.abstractmethod
    async def claim(
        self,
        *,
        run_id: UUID,
        tenant_id: UUID,
        claimed_by: str,
        lease_until: datetime,
        heartbeat_at: datetime,
    ) -> bool:
        """Stamp the run-ownership lease (at the → RUNNING transition).

        Sets ``claimed_by`` / ``lease_until`` / ``heartbeat_at`` so the orphan
        sweep can tell a live owner from a crashed one. Returns ``True`` iff the
        row exists.
        """

    @abc.abstractmethod
    async def heartbeat(
        self,
        *,
        run_id: UUID,
        claimed_by: str,
        lease_until: datetime,
        heartbeat_at: datetime,
    ) -> bool:
        """Renew the lease — only when ``claimed_by`` still owns the running run.

        CAS on ``(status='running' AND claimed_by=<owner>)`` so a worker whose
        run was reclaimed by a peer (its lease lapsed) sees ``False`` and stops.
        Returns ``True`` iff this owner still holds the running run.
        """

    @abc.abstractmethod
    async def list_orphans(self, *, now: datetime, limit: int) -> list[RunInfo]:
        """Cross-tenant running runs whose lease expired — the orphan candidates.

        ``status='running' AND lease_until < now``. Caller MUST wrap in
        ``bypass_rls_session()`` (cross-tenant sweep). The reclaim CAS
        (:meth:`reclaim`) is the authoritative guard against two sweepers
        racing on one orphan; this scan only nominates candidates.
        """

    @abc.abstractmethod
    async def reclaim(
        self,
        *,
        run_id: UUID,
        new_owner: str,
        lease_until: datetime,
        heartbeat_at: datetime,
        now: datetime,
    ) -> bool:
        """Atomically take over an expired-lease running run; ``True`` iff won.

        CAS on ``(status='running' AND lease_until < now)`` so exactly one
        sweeper wins when several race on the same orphan (the others get
        ``False`` — rowcount 0). Caller wraps in ``bypass_rls_session()``.
        """

    @abc.abstractmethod
    async def list_queued(self, *, limit: int) -> list[RunInfo]:
        """Cross-tenant ``status='queued'`` runs, oldest first (FIFO).

        Stream 9.5 — the run-queue worker's scan. Caller MUST wrap in
        ``bypass_rls_session()`` (cross-tenant). The claim CAS
        (:meth:`claim_queued`) is the authoritative exactly-once guard;
        this scan only nominates candidates.
        """

    @abc.abstractmethod
    async def claim_queued(
        self,
        *,
        run_id: UUID,
        new_owner: str,
        lease_until: datetime,
        heartbeat_at: datetime,
    ) -> RunInfo | None:
        """Atomically claim a queued run for execution; the run iff won.

        Stream 9.5 — CAS on ``status='queued'`` (→ ``running`` + stamp the
        ownership lease) so exactly one worker wins when several race on the
        same queued run. Returns the claimed :class:`RunInfo` (carrying the
        ``enqueued_input`` the worker needs) on a win, ``None`` on a loss
        (rowcount 0). Caller wraps in ``bypass_rls_session()``.
        """


#: Stream H.3 PR 1 — Mini-ADR H-7 (D) hard cap so a single page can never
#: return more than this many rows. Callers passing a larger ``limit``
#: are silently clamped; ``/v1/runs`` sets ``X-Limit-Capped: true`` in
#: that case (same envelope-header convention as agents / triggers
#: list endpoints).
MAX_LIST_LIMIT = 500


def _clamp_limit(limit: int) -> int:
    """Apply the :data:`MAX_LIST_LIMIT` ceiling — public so the API
    layer can set the response header when it actually clamped."""
    if limit < 1:
        return 1
    return min(limit, MAX_LIST_LIMIT)


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

    async def delete_by_thread(self, *, thread_id: UUID, tenant_id: UUID) -> int:
        victims = [
            rid
            for rid, r in self._rows.items()
            if r.thread_id == thread_id and r.tenant_id == tenant_id
        ]
        for rid in victims:
            del self._rows[rid]
        return len(victims)

    async def list_for_tenant(
        self,
        *,
        tenant_id: UUID,
        status: RunStatus | None = None,
        thread_ids: Collection[UUID] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RunInfo]:
        rows = [r for r in self._rows.values() if r.tenant_id == tenant_id]
        if status is not None:
            rows = [r for r in rows if r.status is status]
        if thread_ids is not None:
            wanted = set(thread_ids)
            rows = [r for r in rows if r.thread_id in wanted]
        rows.sort(key=lambda r: r.created_at, reverse=True)
        clamped = _clamp_limit(limit)
        return rows[offset : offset + clamped]

    async def list_all_tenants(
        self,
        *,
        status: RunStatus | None = None,
        thread_ids: Collection[UUID] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RunInfo]:
        rows = list(self._rows.values())
        if status is not None:
            rows = [r for r in rows if r.status is status]
        if thread_ids is not None:
            wanted = set(thread_ids)
            rows = [r for r in rows if r.thread_id in wanted]
        rows.sort(key=lambda r: r.created_at, reverse=True)
        clamped = _clamp_limit(limit)
        return rows[offset : offset + clamped]

    async def set_trace_id(
        self,
        *,
        run_id: UUID,
        tenant_id: UUID,
        trace_id: str,
    ) -> bool:
        row = self._rows.get(run_id)
        if row is None or row.tenant_id != tenant_id:
            return False
        self._rows[run_id] = replace(row, trace_id=trace_id)
        return True

    async def claim(
        self,
        *,
        run_id: UUID,
        tenant_id: UUID,
        claimed_by: str,
        lease_until: datetime,
        heartbeat_at: datetime,
    ) -> bool:
        row = self._rows.get(run_id)
        if row is None or row.tenant_id != tenant_id:
            return False
        self._rows[run_id] = replace(
            row, claimed_by=claimed_by, lease_until=lease_until, heartbeat_at=heartbeat_at
        )
        return True

    async def heartbeat(
        self,
        *,
        run_id: UUID,
        claimed_by: str,
        lease_until: datetime,
        heartbeat_at: datetime,
    ) -> bool:
        row = self._rows.get(run_id)
        if row is None or row.status is not RunStatus.RUNNING or row.claimed_by != claimed_by:
            return False
        self._rows[run_id] = replace(row, lease_until=lease_until, heartbeat_at=heartbeat_at)
        return True

    async def list_orphans(self, *, now: datetime, limit: int) -> list[RunInfo]:
        rows = [
            r
            for r in self._rows.values()
            if r.status is RunStatus.RUNNING and r.lease_until is not None and r.lease_until < now
        ]
        rows.sort(key=lambda r: r.lease_until or r.created_at)
        return rows[: max(1, limit)]

    async def reclaim(
        self,
        *,
        run_id: UUID,
        new_owner: str,
        lease_until: datetime,
        heartbeat_at: datetime,
        now: datetime,
    ) -> bool:
        row = self._rows.get(run_id)
        # CAS — only an expired-lease running run can be taken over.
        if (
            row is None
            or row.status is not RunStatus.RUNNING
            or row.lease_until is None
            or row.lease_until >= now
        ):
            return False
        self._rows[run_id] = replace(
            row,
            claimed_by=new_owner,
            lease_until=lease_until,
            heartbeat_at=heartbeat_at,
            reclaim_count=row.reclaim_count + 1,
        )
        return True

    async def list_queued(self, *, limit: int) -> list[RunInfo]:
        rows = [r for r in self._rows.values() if r.status is RunStatus.QUEUED]
        rows.sort(key=lambda r: r.created_at)
        return rows[: max(1, limit)]

    async def claim_queued(
        self,
        *,
        run_id: UUID,
        new_owner: str,
        lease_until: datetime,
        heartbeat_at: datetime,
    ) -> RunInfo | None:
        row = self._rows.get(run_id)
        if row is None or row.status is not RunStatus.QUEUED:
            return None
        claimed = replace(
            row,
            status=RunStatus.RUNNING,
            claimed_by=new_owner,
            lease_until=lease_until,
            heartbeat_at=heartbeat_at,
        )
        self._rows[run_id] = claimed
        return claimed


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
        trace_id=row.trace_id,
        claimed_by=row.claimed_by,
        lease_until=row.lease_until,
        heartbeat_at=row.heartbeat_at,
        reclaim_count=row.reclaim_count,
        enqueued_input=row.enqueued_input,
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
                    trace_id=info.trace_id,
                    claimed_by=info.claimed_by,
                    lease_until=info.lease_until,
                    heartbeat_at=info.heartbeat_at,
                    reclaim_count=info.reclaim_count,
                    enqueued_input=info.enqueued_input,
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

    async def delete_by_thread(self, *, thread_id: UUID, tenant_id: UUID) -> int:
        async with self._sf() as session:
            result = await session.execute(
                delete(AgentRunRow).where(
                    AgentRunRow.thread_id == thread_id,
                    AgentRunRow.tenant_id == tenant_id,
                )
            )
            await session.commit()
        return int(getattr(result, "rowcount", 0) or 0)

    async def list_for_tenant(
        self,
        *,
        tenant_id: UUID,
        status: RunStatus | None = None,
        thread_ids: Collection[UUID] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RunInfo]:
        if thread_ids is not None and not thread_ids:
            return []
        clamped = _clamp_limit(limit)
        stmt = (
            select(AgentRunRow)
            .where(AgentRunRow.tenant_id == tenant_id)
            .order_by(AgentRunRow.created_at.desc())
            .limit(clamped)
            .offset(max(0, offset))
        )
        if status is not None:
            stmt = stmt.where(AgentRunRow.status == status.value)
        if thread_ids is not None:
            stmt = stmt.where(AgentRunRow.thread_id.in_(list(thread_ids)))
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_dto(r) for r in rows]

    async def list_all_tenants(
        self,
        *,
        status: RunStatus | None = None,
        thread_ids: Collection[UUID] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RunInfo]:
        # Stream N — no tenant filter; caller MUST wrap in bypass_rls_session().
        if thread_ids is not None and not thread_ids:
            return []
        clamped = _clamp_limit(limit)
        stmt = (
            select(AgentRunRow)
            .order_by(AgentRunRow.created_at.desc())
            .limit(clamped)
            .offset(max(0, offset))
        )
        if status is not None:
            stmt = stmt.where(AgentRunRow.status == status.value)
        if thread_ids is not None:
            stmt = stmt.where(AgentRunRow.thread_id.in_(list(thread_ids)))
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_dto(r) for r in rows]

    async def set_trace_id(
        self,
        *,
        run_id: UUID,
        tenant_id: UUID,
        trace_id: str,
    ) -> bool:
        async with self._sf() as session:
            result = await session.execute(
                update(AgentRunRow)
                .where(AgentRunRow.id == run_id, AgentRunRow.tenant_id == tenant_id)
                .values({"trace_id": trace_id})
            )
            await session.commit()
        return int(getattr(result, "rowcount", 0) or 0) > 0

    async def claim(
        self,
        *,
        run_id: UUID,
        tenant_id: UUID,
        claimed_by: str,
        lease_until: datetime,
        heartbeat_at: datetime,
    ) -> bool:
        async with self._sf() as session:
            result = await session.execute(
                update(AgentRunRow)
                .where(AgentRunRow.id == run_id, AgentRunRow.tenant_id == tenant_id)
                .values(claimed_by=claimed_by, lease_until=lease_until, heartbeat_at=heartbeat_at)
            )
            await session.commit()
        return int(getattr(result, "rowcount", 0) or 0) > 0

    async def heartbeat(
        self,
        *,
        run_id: UUID,
        claimed_by: str,
        lease_until: datetime,
        heartbeat_at: datetime,
    ) -> bool:
        async with self._sf() as session:
            result = await session.execute(
                update(AgentRunRow)
                .where(
                    AgentRunRow.id == run_id,
                    AgentRunRow.claimed_by == claimed_by,
                    AgentRunRow.status == RunStatus.RUNNING.value,
                )
                .values(lease_until=lease_until, heartbeat_at=heartbeat_at)
            )
            await session.commit()
        return int(getattr(result, "rowcount", 0) or 0) > 0

    async def list_orphans(self, *, now: datetime, limit: int) -> list[RunInfo]:
        # Cross-tenant: no tenant filter — caller wraps in bypass_rls_session().
        stmt = (
            select(AgentRunRow)
            .where(
                AgentRunRow.status == RunStatus.RUNNING.value,
                AgentRunRow.lease_until.is_not(None),
                AgentRunRow.lease_until < now,
            )
            .order_by(AgentRunRow.lease_until.asc())
            .limit(max(1, limit))
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_dto(r) for r in rows]

    async def reclaim(
        self,
        *,
        run_id: UUID,
        new_owner: str,
        lease_until: datetime,
        heartbeat_at: datetime,
        now: datetime,
    ) -> bool:
        # Atomic CAS — exactly one sweeper wins the orphan. The
        # ``status='running' AND lease_until < now`` predicate is the guard;
        # the loser's UPDATE matches zero rows (lease already renewed).
        async with self._sf() as session:
            result = await session.execute(
                update(AgentRunRow)
                .where(
                    AgentRunRow.id == run_id,
                    AgentRunRow.status == RunStatus.RUNNING.value,
                    AgentRunRow.lease_until.is_not(None),
                    AgentRunRow.lease_until < now,
                )
                .values(
                    claimed_by=new_owner,
                    lease_until=lease_until,
                    heartbeat_at=heartbeat_at,
                    reclaim_count=AgentRunRow.reclaim_count + 1,
                )
            )
            await session.commit()
        return int(getattr(result, "rowcount", 0) or 0) > 0

    async def list_queued(self, *, limit: int) -> list[RunInfo]:
        # Cross-tenant: no tenant filter — caller wraps in bypass_rls_session().
        stmt = (
            select(AgentRunRow)
            .where(AgentRunRow.status == RunStatus.QUEUED.value)
            .order_by(AgentRunRow.created_at.asc())
            .limit(max(1, limit))
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_dto(r) for r in rows]

    async def claim_queued(
        self,
        *,
        run_id: UUID,
        new_owner: str,
        lease_until: datetime,
        heartbeat_at: datetime,
    ) -> RunInfo | None:
        # Atomic CAS — exactly one worker wins the queued run. ``RETURNING``
        # hands back the claimed row (with ``enqueued_input``) in one round
        # trip; the loser's UPDATE matches zero rows and returns ``None``.
        async with self._sf() as session:
            result = await session.execute(
                update(AgentRunRow)
                .where(
                    AgentRunRow.id == run_id,
                    AgentRunRow.status == RunStatus.QUEUED.value,
                )
                .values(
                    status=RunStatus.RUNNING.value,
                    claimed_by=new_owner,
                    lease_until=lease_until,
                    heartbeat_at=heartbeat_at,
                )
                .returning(AgentRunRow)
            )
            row = result.scalars().first()
            await session.commit()
        return _row_to_dto(row) if row is not None else None
