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

from collections.abc import Collection
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
        agent_name: str | None = None,
        agent_version: str | None = None,
        nonempty: bool = False,
        q: str | None = None,
        include_archived: bool = False,
        thread_ids: Collection[UUID] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ThreadMeta]:
        # ``nonempty`` is a no-op here: the in-memory backend has no run store
        # to correlate against. The SQL backend does the real filter.
        del nonempty
        rows = [r for r in self._rows.values() if r.tenant_id == tenant_id]
        if thread_ids is not None:
            wanted = set(thread_ids)
            rows = [r for r in rows if r.thread_id in wanted]
        if status is not None:
            rows = [r for r in rows if r.status == status]
        elif not include_archived:
            rows = [r for r in rows if r.status != ThreadStatus.ARCHIVED]
        if user_id is not None:
            rows = [r for r in rows if r.user_id == user_id]
        if agent_name is not None:
            rows = [r for r in rows if r.agent_name == agent_name]
        if agent_version is not None:
            rows = [r for r in rows if r.agent_version == agent_version]
        if q:
            needle = q.lower()
            rows = [r for r in rows if r.title is not None and needle in r.title.lower()]
        rows.sort(key=lambda r: r.created_at or datetime.min.replace(tzinfo=UTC), reverse=True)
        return rows[offset : offset + limit]

    async def list_all_tenants(
        self,
        *,
        status: ThreadStatus | None = None,
        agent_name: str | None = None,
        agent_version: str | None = None,
        nonempty: bool = False,
        q: str | None = None,
        include_archived: bool = False,
        thread_ids: Collection[UUID] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ThreadMeta]:
        del nonempty  # no-op in-memory (no run store) — see ``list_by_tenant``.
        rows = list(self._rows.values())
        if thread_ids is not None:
            wanted = set(thread_ids)
            rows = [r for r in rows if r.thread_id in wanted]
        if status is not None:
            rows = [r for r in rows if r.status == status]
        elif not include_archived:
            rows = [r for r in rows if r.status != ThreadStatus.ARCHIVED]
        if agent_name is not None:
            rows = [r for r in rows if r.agent_name == agent_name]
        if agent_version is not None:
            rows = [r for r in rows if r.agent_version == agent_version]
        if q:
            needle = q.lower()
            rows = [r for r in rows if r.title is not None and needle in r.title.lower()]
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

    async def update_title(
        self,
        thread_id: UUID,
        title: str,
        *,
        tenant_id: UUID,
    ) -> bool:
        row = self._rows.get(thread_id)
        if row is None or row.tenant_id != tenant_id:
            return False
        self._rows[thread_id] = row.model_copy(
            update={"title": title, "updated_at": datetime.now(UTC)}
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
