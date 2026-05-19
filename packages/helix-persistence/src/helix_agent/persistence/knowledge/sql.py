"""SQLAlchemy + pgvector ``KnowledgeStore`` (Postgres / asyncpg)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from helix_agent.persistence.knowledge.base import DuplicateKnowledgeBaseError, KnowledgeStore
from helix_agent.persistence.models import (
    KnowledgeBaseRow,
    KnowledgeChunkRow,
    KnowledgeDocumentRow,
)
from helix_agent.protocol import DocumentStatus, KnowledgeBase, KnowledgeChunk, KnowledgeDocument


def _to_base(row: KnowledgeBaseRow) -> KnowledgeBase:
    return KnowledgeBase(
        id=row.id, tenant_id=row.tenant_id, name=row.name, created_at=row.created_at
    )


def _to_document(row: KnowledgeDocumentRow) -> KnowledgeDocument:
    return KnowledgeDocument(
        id=row.id,
        tenant_id=row.tenant_id,
        kb_id=row.kb_id,
        filename=row.filename,
        status=DocumentStatus(row.status),
        error=row.error,
        chunk_count=row.chunk_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_chunk(row: KnowledgeChunkRow) -> KnowledgeChunk:
    return KnowledgeChunk(
        id=row.id,
        tenant_id=row.tenant_id,
        kb_id=row.kb_id,
        document_id=row.document_id,
        chunk_index=row.chunk_index,
        content=row.content,
        embedding=tuple(float(value) for value in row.embedding),
        created_at=row.created_at,
    )


class SqlKnowledgeStore(KnowledgeStore):
    """Postgres-backed knowledge repository (pgvector)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    # -- knowledge bases ----------------------------------------------------

    async def create_base(self, *, tenant_id: UUID, name: str) -> KnowledgeBase:
        row = KnowledgeBaseRow(tenant_id=tenant_id, name=name)
        try:
            async with self._sf() as session:
                session.add(row)
                await session.commit()
                await session.refresh(row)
        except IntegrityError as exc:
            raise DuplicateKnowledgeBaseError(tenant_id=tenant_id, name=name) from exc
        return _to_base(row)

    async def get_base(self, *, tenant_id: UUID, name: str) -> KnowledgeBase | None:
        stmt = select(KnowledgeBaseRow).where(
            KnowledgeBaseRow.tenant_id == tenant_id,
            KnowledgeBaseRow.name == name,
        )
        async with self._sf() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
        return _to_base(row) if row is not None else None

    async def list_bases(self, *, tenant_id: UUID) -> list[KnowledgeBase]:
        stmt = (
            select(KnowledgeBaseRow)
            .where(KnowledgeBaseRow.tenant_id == tenant_id)
            .order_by(KnowledgeBaseRow.created_at.desc())
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_to_base(row) for row in rows]

    async def delete_base(self, *, tenant_id: UUID, kb_id: UUID) -> bool:
        async with self._sf() as session:
            exists = (
                await session.execute(
                    select(KnowledgeBaseRow.id).where(
                        KnowledgeBaseRow.tenant_id == tenant_id,
                        KnowledgeBaseRow.id == kb_id,
                    )
                )
            ).scalar_one_or_none()
            if exists is None:
                return False
            await session.execute(
                delete(KnowledgeChunkRow).where(
                    KnowledgeChunkRow.tenant_id == tenant_id,
                    KnowledgeChunkRow.kb_id == kb_id,
                )
            )
            await session.execute(
                delete(KnowledgeDocumentRow).where(
                    KnowledgeDocumentRow.tenant_id == tenant_id,
                    KnowledgeDocumentRow.kb_id == kb_id,
                )
            )
            await session.execute(
                delete(KnowledgeBaseRow).where(
                    KnowledgeBaseRow.tenant_id == tenant_id,
                    KnowledgeBaseRow.id == kb_id,
                )
            )
            await session.commit()
        return True

    # -- documents ----------------------------------------------------------

    async def upsert_document(
        self, *, tenant_id: UUID, kb_id: UUID, filename: str
    ) -> KnowledgeDocument:
        async with self._sf() as session:
            existing = (
                await session.execute(
                    select(KnowledgeDocumentRow).where(
                        KnowledgeDocumentRow.tenant_id == tenant_id,
                        KnowledgeDocumentRow.kb_id == kb_id,
                        KnowledgeDocumentRow.filename == filename,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                existing.status = DocumentStatus.PENDING.value
                existing.error = None
                existing.chunk_count = 0
                existing.updated_at = datetime.now(UTC)
                await session.execute(
                    delete(KnowledgeChunkRow).where(
                        KnowledgeChunkRow.tenant_id == tenant_id,
                        KnowledgeChunkRow.document_id == existing.id,
                    )
                )
                await session.commit()
                await session.refresh(existing)
                return _to_document(existing)
            row = KnowledgeDocumentRow(
                tenant_id=tenant_id,
                kb_id=kb_id,
                filename=filename,
                status=DocumentStatus.PENDING.value,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _to_document(row)

    async def get_document(self, *, tenant_id: UUID, document_id: UUID) -> KnowledgeDocument | None:
        stmt = select(KnowledgeDocumentRow).where(
            KnowledgeDocumentRow.tenant_id == tenant_id,
            KnowledgeDocumentRow.id == document_id,
        )
        async with self._sf() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
        return _to_document(row) if row is not None else None

    async def list_documents(self, *, tenant_id: UUID, kb_id: UUID) -> list[KnowledgeDocument]:
        stmt = (
            select(KnowledgeDocumentRow)
            .where(
                KnowledgeDocumentRow.tenant_id == tenant_id,
                KnowledgeDocumentRow.kb_id == kb_id,
            )
            .order_by(KnowledgeDocumentRow.created_at.desc())
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_to_document(row) for row in rows]

    async def set_document_status(
        self,
        *,
        tenant_id: UUID,
        document_id: UUID,
        status: DocumentStatus,
        error: str | None = None,
        chunk_count: int | None = None,
    ) -> None:
        values: dict[str, object] = {
            "status": status.value,
            "error": error,
            "updated_at": datetime.now(UTC),
        }
        if chunk_count is not None:
            values["chunk_count"] = chunk_count
        async with self._sf() as session:
            await session.execute(
                update(KnowledgeDocumentRow)
                .where(
                    KnowledgeDocumentRow.tenant_id == tenant_id,
                    KnowledgeDocumentRow.id == document_id,
                )
                .values(**values)
            )
            await session.commit()

    async def delete_document(self, *, tenant_id: UUID, document_id: UUID) -> bool:
        async with self._sf() as session:
            exists = (
                await session.execute(
                    select(KnowledgeDocumentRow.id).where(
                        KnowledgeDocumentRow.tenant_id == tenant_id,
                        KnowledgeDocumentRow.id == document_id,
                    )
                )
            ).scalar_one_or_none()
            if exists is None:
                return False
            await session.execute(
                delete(KnowledgeChunkRow).where(
                    KnowledgeChunkRow.tenant_id == tenant_id,
                    KnowledgeChunkRow.document_id == document_id,
                )
            )
            await session.execute(
                delete(KnowledgeDocumentRow).where(
                    KnowledgeDocumentRow.tenant_id == tenant_id,
                    KnowledgeDocumentRow.id == document_id,
                )
            )
            await session.commit()
        return True

    # -- chunks -------------------------------------------------------------

    async def replace_chunks(
        self, *, tenant_id: UUID, document_id: UUID, chunks: Sequence[KnowledgeChunk]
    ) -> None:
        async with self._sf() as session:
            await session.execute(
                delete(KnowledgeChunkRow).where(
                    KnowledgeChunkRow.tenant_id == tenant_id,
                    KnowledgeChunkRow.document_id == document_id,
                )
            )
            session.add_all(
                [
                    KnowledgeChunkRow(
                        id=chunk.id,
                        tenant_id=chunk.tenant_id,
                        kb_id=chunk.kb_id,
                        document_id=chunk.document_id,
                        chunk_index=chunk.chunk_index,
                        content=chunk.content,
                        embedding=list(chunk.embedding),
                    )
                    for chunk in chunks
                ]
            )
            await session.commit()

    async def search(
        self,
        *,
        tenant_id: UUID,
        kb_ids: Sequence[UUID],
        query_embedding: Sequence[float],
        limit: int = 5,
    ) -> list[KnowledgeChunk]:
        if not kb_ids:
            return []
        stmt = (
            select(KnowledgeChunkRow)
            .where(
                KnowledgeChunkRow.tenant_id == tenant_id,
                KnowledgeChunkRow.kb_id.in_(list(kb_ids)),
            )
            # pgvector cosine distance (``<=>``); HNSW index backs the sort.
            .order_by(KnowledgeChunkRow.embedding.cosine_distance(list(query_embedding)))
            .limit(limit)
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_to_chunk(row) for row in rows]
