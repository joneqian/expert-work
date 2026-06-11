"""SQLAlchemy-backed ``ApprovalStore`` — Stream J.8 (Mini-ADR J-24)."""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from helix_agent.persistence.approval.base import ApprovalStore
from helix_agent.persistence.models import AgentApprovalRow
from helix_agent.protocol import ApprovalReasonKind, ApprovalRecord, ApprovalStatus


def _row_to_dto(row: AgentApprovalRow) -> ApprovalRecord:
    return ApprovalRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        run_id=row.run_id,
        thread_id=row.thread_id,
        request_id=row.request_id,
        node=row.node,
        reason_kind=cast(ApprovalReasonKind, row.reason_kind),
        action_summary=row.action_summary,
        proposed_args=dict(row.proposed_args or {}),
        requested_at=row.requested_at,
        timeout_at=row.timeout_at,
        status=ApprovalStatus(row.status),
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        modified_args=dict(row.modified_args) if row.modified_args is not None else None,
    )


class SqlApprovalStore(ApprovalStore):
    """Postgres-backed paused-run approval registry."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create(self, record: ApprovalRecord) -> ApprovalRecord:
        async with self._sf() as session:
            session.add(
                AgentApprovalRow(
                    id=record.id,
                    tenant_id=record.tenant_id,
                    user_id=record.user_id,
                    run_id=record.run_id,
                    thread_id=record.thread_id,
                    request_id=record.request_id,
                    node=record.node,
                    reason_kind=record.reason_kind,
                    action_summary=record.action_summary,
                    proposed_args=dict(record.proposed_args),
                    requested_at=record.requested_at,
                    timeout_at=record.timeout_at,
                    status=record.status.value,
                    decided_by=record.decided_by,
                    decided_at=record.decided_at,
                    modified_args=record.modified_args,
                )
            )
            await session.commit()
        return record

    async def get_by_run(self, *, run_id: UUID, tenant_id: UUID) -> ApprovalRecord | None:
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(AgentApprovalRow).where(
                        AgentApprovalRow.run_id == run_id,
                        AgentApprovalRow.tenant_id == tenant_id,
                    )
                )
            ).scalar_one_or_none()
        return _row_to_dto(row) if row is not None else None

    async def list_expired(
        self,
        *,
        before: datetime,
        limit: int = 1000,
    ) -> list[ApprovalRecord]:
        async with self._sf() as session:
            rows = (
                (
                    await session.execute(
                        select(AgentApprovalRow)
                        .where(
                            AgentApprovalRow.status == ApprovalStatus.PENDING.value,
                            AgentApprovalRow.timeout_at < before,
                        )
                        .order_by(AgentApprovalRow.timeout_at.asc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
        return [_row_to_dto(r) for r in rows]

    async def count_pending(self) -> int:
        async with self._sf() as session:
            result = await session.execute(
                select(func.count())
                .select_from(AgentApprovalRow)
                .where(AgentApprovalRow.status == ApprovalStatus.PENDING.value)
            )
            return int(result.scalar_one())

    async def mark_decided(
        self,
        *,
        run_id: UUID,
        tenant_id: UUID,
        status: ApprovalStatus,
        decided_by: str,
        decided_at: datetime,
        modified_args: dict[str, object] | None = None,
    ) -> bool:
        async with self._sf() as session:
            result = await session.execute(
                update(AgentApprovalRow)
                .where(
                    AgentApprovalRow.run_id == run_id,
                    AgentApprovalRow.tenant_id == tenant_id,
                    AgentApprovalRow.status == ApprovalStatus.PENDING.value,
                )
                .values(
                    status=status.value,
                    decided_by=decided_by,
                    decided_at=decided_at,
                    modified_args=modified_args,
                )
            )
            await session.commit()
        return int(getattr(result, "rowcount", 0) or 0) > 0
