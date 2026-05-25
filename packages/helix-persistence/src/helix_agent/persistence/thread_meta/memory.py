# ============================================================
# Adapted from bytedance/deer-flow @ 813d3c94efa7fdea6aafcb4f459304db91fcaed0
# Source: backend/packages/harness/deerflow/persistence/thread_meta/memory.py
# License: MIT (see vendor LICENSE)
# Modifications:
#   - Aligned to ThreadMetaStore (helix_agent.persistence.thread_meta.base)
#   - dict[str, ThreadMeta] keyed by thread_id; tenant filter happens at read
# Last sync: 2026-05-11
# ============================================================

"""In-memory ``ThreadMetaStore`` for unit tests."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from helix_agent.persistence.thread_meta.base import ThreadMetaStore
from helix_agent.protocol import ThreadMeta, ThreadStatus


class InMemoryThreadMetaStore(ThreadMetaStore):
    def __init__(self) -> None:
        self._rows: dict[UUID, ThreadMeta] = {}

    async def create(
        self,
        *,
        thread_id: UUID,
        tenant_id: UUID,
        created_by: str,
        user_id: UUID | None = None,
        agent_name: str | None = None,
        agent_version: str | None = None,
    ) -> ThreadMeta:
        if thread_id in self._rows:
            msg = f"thread_meta already exists for thread_id={thread_id}"
            raise ValueError(msg)
        now = datetime.now(UTC)
        meta = ThreadMeta(
            thread_id=thread_id,
            tenant_id=tenant_id,
            user_id=user_id,
            created_by=created_by,
            status=ThreadStatus.ACTIVE,
            agent_name=agent_name,
            agent_version=agent_version,
            created_at=now,
            updated_at=now,
        )
        self._rows[thread_id] = meta
        return meta

    async def get(self, thread_id: UUID, *, tenant_id: UUID) -> ThreadMeta | None:
        row = self._rows.get(thread_id)
        if row is None or row.tenant_id != tenant_id:
            return None
        return row

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        status: ThreadStatus | None = None,
        user_id: UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ThreadMeta]:
        rows = [r for r in self._rows.values() if r.tenant_id == tenant_id]
        if status is not None:
            rows = [r for r in rows if r.status == status]
        if user_id is not None:
            rows = [r for r in rows if r.user_id == user_id]
        rows.sort(key=lambda r: r.created_at or datetime.min.replace(tzinfo=UTC), reverse=True)
        return rows[offset : offset + limit]

    async def list_all_tenants(
        self,
        *,
        status: ThreadStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ThreadMeta]:
        rows = list(self._rows.values())
        if status is not None:
            rows = [r for r in rows if r.status == status]
        rows.sort(key=lambda r: r.created_at or datetime.min.replace(tzinfo=UTC), reverse=True)
        return rows[offset : offset + limit]

    async def update_status(
        self,
        thread_id: UUID,
        status: ThreadStatus,
        *,
        tenant_id: UUID,
    ) -> bool:
        row = self._rows.get(thread_id)
        if row is None or row.tenant_id != tenant_id:
            return False
        self._rows[thread_id] = row.model_copy(
            update={"status": status, "updated_at": datetime.now(UTC)}
        )
        return True

    async def check_access(self, thread_id: UUID, tenant_id: UUID) -> bool:
        row = self._rows.get(thread_id)
        return row is not None and row.tenant_id == tenant_id

    async def delete(self, thread_id: UUID, *, tenant_id: UUID) -> bool:
        row = self._rows.get(thread_id)
        if row is None or row.tenant_id != tenant_id:
            return False
        del self._rows[thread_id]
        return True
