# ============================================================
# Adapted from bytedance/deer-flow @ 813d3c94efa7fdea6aafcb4f459304db91fcaed0
# Source: backend/packages/harness/deerflow/persistence/thread_meta/sql.py
# License: MIT (see vendor LICENSE)
# Modifications:
#   - Aligned to ThreadMetaStore (helix_agent.persistence.thread_meta.base)
#   - Backed by helix_agent.persistence.models.ThreadMetaRow (ADR-0002 schema)
#   - tenant_id (UUID) is a required arg, no AUTO sentinel / contextvar
#   - Returns Pydantic ThreadMeta (helix-agent-protocol) instead of dict
# Last sync: 2026-05-11
# ============================================================

"""SQLAlchemy-backed ``ThreadMetaStore`` (Postgres / asyncpg)."""

from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from helix_agent.persistence.models import ThreadMetaRow
from helix_agent.persistence.models.agent_run import AgentRunRow
from helix_agent.persistence.thread_meta.base import ThreadMetaStore
from helix_agent.protocol import ThreadMeta, ThreadStatus


def _row_to_meta(row: ThreadMetaRow) -> ThreadMeta:
    return ThreadMeta(
        thread_id=row.thread_id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        created_by=row.created_by,
        status=ThreadStatus(row.status),
        title=row.title,
        agent_name=row.agent_name,
        agent_version=row.agent_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _title_ilike(q: str) -> str:
    """Escape LIKE wildcards so a search term matches literally (used with
    ``ilike(..., escape="\\")``)."""
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class SqlThreadMetaStore(ThreadMetaStore):
    """Postgres-backed thread metadata repository."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

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
        now = datetime.now(UTC)
        row = ThreadMetaRow(
            thread_id=thread_id,
            tenant_id=tenant_id,
            user_id=user_id,
            created_by=created_by,
            status=ThreadStatus.ACTIVE.value,
            agent_name=agent_name,
            agent_version=agent_version,
            created_at=now,
            updated_at=now,
        )
        async with self._sf() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                msg = f"thread_meta already exists for thread_id={thread_id}"
                raise ValueError(msg) from exc
            await session.refresh(row)
            return _row_to_meta(row)

    async def get(self, thread_id: UUID, *, tenant_id: UUID) -> ThreadMeta | None:
        async with self._sf() as session:
            row = await session.get(ThreadMetaRow, thread_id)
            if row is None or row.tenant_id != tenant_id:
                return None
            return _row_to_meta(row)

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
        if thread_ids is not None and not thread_ids:
            return []
        stmt = select(ThreadMetaRow).where(ThreadMetaRow.tenant_id == tenant_id)
        if thread_ids is not None:
            stmt = stmt.where(ThreadMetaRow.thread_id.in_(list(thread_ids)))
        if status is not None:
            stmt = stmt.where(ThreadMetaRow.status == status.value)
        elif not include_archived:
            stmt = stmt.where(ThreadMetaRow.status != ThreadStatus.ARCHIVED.value)
        if user_id is not None:
            stmt = stmt.where(ThreadMetaRow.user_id == user_id)
        if agent_name is not None:
            stmt = stmt.where(ThreadMetaRow.agent_name == agent_name)
        if agent_version is not None:
            stmt = stmt.where(ThreadMetaRow.agent_version == agent_version)
        if nonempty:
            stmt = stmt.where(exists().where(AgentRunRow.thread_id == ThreadMetaRow.thread_id))
        if q:
            stmt = stmt.where(ThreadMetaRow.title.ilike(_title_ilike(q), escape="\\"))
        stmt = stmt.order_by(ThreadMetaRow.created_at.desc()).limit(limit).offset(offset)
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_meta(r) for r in rows]

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
        # Stream N — no tenant filter; caller must wrap in bypass_rls_session().
        if thread_ids is not None and not thread_ids:
            return []
        stmt = select(ThreadMetaRow)
        if thread_ids is not None:
            stmt = stmt.where(ThreadMetaRow.thread_id.in_(list(thread_ids)))
        if status is not None:
            stmt = stmt.where(ThreadMetaRow.status == status.value)
        elif not include_archived:
            stmt = stmt.where(ThreadMetaRow.status != ThreadStatus.ARCHIVED.value)
        if agent_name is not None:
            stmt = stmt.where(ThreadMetaRow.agent_name == agent_name)
        if agent_version is not None:
            stmt = stmt.where(ThreadMetaRow.agent_version == agent_version)
        if nonempty:
            stmt = stmt.where(exists().where(AgentRunRow.thread_id == ThreadMetaRow.thread_id))
        if q:
            stmt = stmt.where(ThreadMetaRow.title.ilike(_title_ilike(q), escape="\\"))
        stmt = stmt.order_by(ThreadMetaRow.created_at.desc()).limit(limit).offset(offset)
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_meta(r) for r in rows]

    async def update_status(
        self,
        thread_id: UUID,
        status: ThreadStatus,
        *,
        tenant_id: UUID,
    ) -> bool:
        stmt = (
            update(ThreadMetaRow)
            .where(
                ThreadMetaRow.thread_id == thread_id,
                ThreadMetaRow.tenant_id == tenant_id,
            )
            .values(status=status.value, updated_at=datetime.now(UTC))
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            await session.commit()
            return int(getattr(result, "rowcount", 0) or 0) > 0

    async def update_title(
        self,
        thread_id: UUID,
        title: str,
        *,
        tenant_id: UUID,
    ) -> bool:
        stmt = (
            update(ThreadMetaRow)
            .where(
                ThreadMetaRow.thread_id == thread_id,
                ThreadMetaRow.tenant_id == tenant_id,
            )
            .values(title=title, updated_at=datetime.now(UTC))
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            await session.commit()
            return int(getattr(result, "rowcount", 0) or 0) > 0

    async def check_access(self, thread_id: UUID, tenant_id: UUID) -> bool:
        async with self._sf() as session:
            row = await session.get(ThreadMetaRow, thread_id)
            return row is not None and row.tenant_id == tenant_id

    async def delete(self, thread_id: UUID, *, tenant_id: UUID) -> bool:
        stmt = delete(ThreadMetaRow).where(
            ThreadMetaRow.thread_id == thread_id,
            ThreadMetaRow.tenant_id == tenant_id,
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            await session.commit()
            return int(getattr(result, "rowcount", 0) or 0) > 0
