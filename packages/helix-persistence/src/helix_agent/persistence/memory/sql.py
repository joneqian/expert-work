"""SQLAlchemy + pgvector ``MemoryStore`` (Postgres / asyncpg)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from helix_agent.persistence.memory.base import MemoryStore
from helix_agent.persistence.memory.hash import hash_content
from helix_agent.persistence.models import MemoryItemRow
from helix_agent.protocol import MemoryItem


def _row_to_item(row: MemoryItemRow) -> MemoryItem:
    return MemoryItem(
        id=row.id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        kind=row.kind,  # type: ignore[arg-type]
        content=row.content,
        content_hash=row.content_hash,
        embedding=tuple(float(value) for value in row.embedding),
        source_thread_id=row.source_thread_id,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        deleted_at=row.deleted_at,
    )


class SqlMemoryStore(MemoryStore):
    """Postgres-backed long-term memory repository (pgvector)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def write(self, items: Sequence[MemoryItem]) -> None:
        if not items:
            return
        # Stream K.K7 — fill content_hash here so callers do not need
        # to import the hash helper, and use ON CONFLICT DO NOTHING
        # against the (tenant_id, user_id, content_hash) partial unique
        # index so a re-run that re-extracts the same memory is a no-op
        # instead of a duplicate row.
        payload = [
            {
                "id": item.id,
                "tenant_id": item.tenant_id,
                "user_id": item.user_id,
                "kind": item.kind,
                "content": item.content,
                "content_hash": item.content_hash or hash_content(item.content),
                "embedding": list(item.embedding),
                "source_thread_id": item.source_thread_id,
            }
            for item in items
        ]
        stmt = pg_insert(MemoryItemRow).values(payload).on_conflict_do_nothing()
        async with self._sf() as session:
            await session.execute(stmt)
            await session.commit()

    async def retrieve(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        query_embedding: Sequence[float],
        kind: Literal["fact", "episodic"] | None = None,
        limit: int = 5,
    ) -> list[MemoryItem]:
        stmt = select(MemoryItemRow).where(
            MemoryItemRow.tenant_id == tenant_id,
            MemoryItemRow.user_id == user_id,
            MemoryItemRow.deleted_at.is_(None),  # Stream K.K6 — exclude soft-deleted
        )
        if kind is not None:
            stmt = stmt.where(MemoryItemRow.kind == kind)
        # pgvector cosine distance (``<=>``); HNSW index backs the sort.
        stmt = stmt.order_by(MemoryItemRow.embedding.cosine_distance(list(query_embedding))).limit(
            limit
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_item(row) for row in rows]

    async def list_for_user(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        kind: Literal["fact", "episodic"] | None = None,
        limit: int = 50,
    ) -> list[MemoryItem]:
        stmt = select(MemoryItemRow).where(
            MemoryItemRow.tenant_id == tenant_id,
            MemoryItemRow.user_id == user_id,
            MemoryItemRow.deleted_at.is_(None),
        )
        if kind is not None:
            stmt = stmt.where(MemoryItemRow.kind == kind)
        # newest first; ``memory_item_live_user_idx`` (migration 0024) is
        # a partial index on (user_id, created_at DESC) WHERE
        # deleted_at IS NULL — query shape matches.
        stmt = stmt.order_by(MemoryItemRow.created_at.desc()).limit(limit)
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_item(row) for row in rows]

    async def list_all_tenants(
        self,
        *,
        kind: Literal["fact", "episodic"] | None = None,
        limit: int = 50,
    ) -> list[MemoryItem]:
        # Stream N — no tenant / user filter; caller must wrap in bypass_rls_session().
        stmt = select(MemoryItemRow).where(MemoryItemRow.deleted_at.is_(None))
        if kind is not None:
            stmt = stmt.where(MemoryItemRow.kind == kind)
        stmt = stmt.order_by(MemoryItemRow.created_at.desc()).limit(limit)
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_item(row) for row in rows]

    async def update_content(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        memory_id: UUID,
        content: str,
        embedding: Sequence[float],
        kind: Literal["fact", "episodic"] | None = None,
    ) -> MemoryItem | None:
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(MemoryItemRow).where(
                        MemoryItemRow.id == memory_id,
                        MemoryItemRow.tenant_id == tenant_id,
                        MemoryItemRow.user_id == user_id,
                        MemoryItemRow.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            row.content = content
            row.content_hash = hash_content(content)  # K.K7 — keep dedup hash in sync
            row.embedding = list(embedding)
            if kind is not None:
                row.kind = kind
            await session.commit()
            await session.refresh(row)
            return _row_to_item(row)

    async def soft_delete(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        memory_id: UUID,
    ) -> bool:
        now = datetime.now(UTC)
        stmt = (
            update(MemoryItemRow)
            .where(
                MemoryItemRow.id == memory_id,
                MemoryItemRow.tenant_id == tenant_id,
                MemoryItemRow.user_id == user_id,
                MemoryItemRow.deleted_at.is_(None),
            )
            .values(deleted_at=now)
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            await session.commit()
        if int(getattr(result, "rowcount", 0) or 0) > 0:
            return True
        # Either truly missing or already deleted. Differentiate by a
        # cheap existence check so the caller gets idempotent semantics
        # on a second forget but a clean 404 on an unknown id.
        async with self._sf() as session:
            exists = (
                await session.execute(
                    select(MemoryItemRow.id).where(
                        MemoryItemRow.id == memory_id,
                        MemoryItemRow.tenant_id == tenant_id,
                        MemoryItemRow.user_id == user_id,
                    )
                )
            ).first()
        return exists is not None
