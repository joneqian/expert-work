"""In-memory ``ApprovalStore`` for unit tests."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from helix_agent.persistence.approval.base import ApprovalStore
from helix_agent.protocol import ApprovalRecord, ApprovalStatus


class InMemoryApprovalStore(ApprovalStore):
    def __init__(self) -> None:
        # Keyed by run_id — a run pauses at most once at a time.
        self._rows: dict[UUID, ApprovalRecord] = {}

    async def create(self, record: ApprovalRecord) -> ApprovalRecord:
        if record.run_id in self._rows:
            msg = f"approval row already exists for run {record.run_id}"
            raise ValueError(msg)
        self._rows[record.run_id] = record
        return record

    async def get_by_run(self, *, run_id: UUID, tenant_id: UUID) -> ApprovalRecord | None:
        row = self._rows.get(run_id)
        if row is None or row.tenant_id != tenant_id:
            return None
        return row

    async def list_expired(
        self,
        *,
        before: datetime,
        limit: int = 1000,
    ) -> list[ApprovalRecord]:
        rows = [
            r
            for r in self._rows.values()
            if r.status == ApprovalStatus.PENDING and r.timeout_at < before
        ]
        rows.sort(key=lambda r: r.timeout_at)
        return rows[:limit]

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
        row = self._rows.get(run_id)
        if row is None or row.tenant_id != tenant_id or row.status != ApprovalStatus.PENDING:
            return False
        self._rows[run_id] = row.model_copy(
            update={
                "status": status,
                "decided_by": decided_by,
                "decided_at": decided_at,
                "modified_args": modified_args,
            }
        )
        return True
