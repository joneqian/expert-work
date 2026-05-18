"""SQLAlchemy + pgvector ``MemoryStore`` (Postgres / asyncpg)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from helix_agent.persistence.memory.base import MemoryStore
from helix_agent.persistence.models import MemoryItemRow
from helix_agent.protocol import MemoryItem


def _row_to_item(row: MemoryItemRow) -> MemoryItem:
    return MemoryItem(
        id=row.id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        kind=row.kind,  # type: ignore[arg-type]
        content=row.content,
        embedding=tuple(float(value) for value in row.embedding),
        source_thread_id=row.source_thread_id,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
    )


class SqlMemoryStore(MemoryStore):
    """Postgres-backed long-term memory repository (pgvector)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def write(self, items: Sequence[MemoryItem]) -> None:
        if not items:
            return
        rows = [
            MemoryItemRow(
                id=item.id,
                tenant_id=item.tenant_id,
                user_id=item.user_id,
                kind=item.kind,
                content=item.content,
                embedding=list(item.embedding),
                source_thread_id=item.source_thread_id,
            )
            for item in items
        ]
        async with self._sf() as session:
            session.add_all(rows)
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
