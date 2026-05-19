"""In-memory ``KnowledgeStore`` for unit tests."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from helix_agent.persistence.knowledge.base import DuplicateKnowledgeBaseError, KnowledgeStore
from helix_agent.protocol import DocumentStatus, KnowledgeBase, KnowledgeChunk, KnowledgeDocument


def _cosine_distance(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine distance (0 = identical) — mirrors pgvector's ``<=>``."""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 1.0
    return 1.0 - dot / (norm_a * norm_b)


class InMemoryKnowledgeStore(KnowledgeStore):
    """In-memory knowledge repository — semantics mirror :class:`SqlKnowledgeStore`."""

    def __init__(self) -> None:
        self._bases: list[KnowledgeBase] = []
        self._documents: list[KnowledgeDocument] = []
        self._chunks: list[KnowledgeChunk] = []

    # -- knowledge bases ----------------------------------------------------

    async def create_base(self, *, tenant_id: UUID, name: str) -> KnowledgeBase:
        if any(b.tenant_id == tenant_id and b.name == name for b in self._bases):
            raise DuplicateKnowledgeBaseError(tenant_id=tenant_id, name=name)
        base = KnowledgeBase(
            id=uuid4(), tenant_id=tenant_id, name=name, created_at=datetime.now(UTC)
        )
        self._bases.append(base)
        return base

    async def get_base(self, *, tenant_id: UUID, name: str) -> KnowledgeBase | None:
        return next((b for b in self._bases if b.tenant_id == tenant_id and b.name == name), None)

    async def list_bases(self, *, tenant_id: UUID) -> list[KnowledgeBase]:
        bases = [b for b in self._bases if b.tenant_id == tenant_id]
        return sorted(bases, key=_created_key, reverse=True)

    async def delete_base(self, *, tenant_id: UUID, kb_id: UUID) -> bool:
        base = next((b for b in self._bases if b.tenant_id == tenant_id and b.id == kb_id), None)
        if base is None:
            return False
        self._bases.remove(base)
        self._documents = [d for d in self._documents if d.kb_id != kb_id]
        self._chunks = [c for c in self._chunks if c.kb_id != kb_id]
        return True

    # -- documents ----------------------------------------------------------

    async def upsert_document(
        self, *, tenant_id: UUID, kb_id: UUID, filename: str
    ) -> KnowledgeDocument:
        now = datetime.now(UTC)
        existing = next(
            (
                d
                for d in self._documents
                if d.tenant_id == tenant_id and d.kb_id == kb_id and d.filename == filename
            ),
            None,
        )
        if existing is not None:
            reset = existing.model_copy(
                update={
                    "status": DocumentStatus.PENDING,
                    "error": None,
                    "chunk_count": 0,
                    "updated_at": now,
                }
            )
            self._documents[self._documents.index(existing)] = reset
            self._chunks = [c for c in self._chunks if c.document_id != existing.id]
            return reset
        document = KnowledgeDocument(
            id=uuid4(),
            tenant_id=tenant_id,
            kb_id=kb_id,
            filename=filename,
            status=DocumentStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
        self._documents.append(document)
        return document

    async def get_document(self, *, tenant_id: UUID, document_id: UUID) -> KnowledgeDocument | None:
        return next(
            (d for d in self._documents if d.tenant_id == tenant_id and d.id == document_id),
            None,
        )

    async def list_documents(self, *, tenant_id: UUID, kb_id: UUID) -> list[KnowledgeDocument]:
        docs = [d for d in self._documents if d.tenant_id == tenant_id and d.kb_id == kb_id]
        return sorted(docs, key=_created_key, reverse=True)

    async def set_document_status(
        self,
        *,
        tenant_id: UUID,
        document_id: UUID,
        status: DocumentStatus,
        error: str | None = None,
        chunk_count: int | None = None,
    ) -> None:
        document = await self.get_document(tenant_id=tenant_id, document_id=document_id)
        if document is None:
            return
        update: dict[str, object] = {
            "status": status,
            "error": error,
            "updated_at": datetime.now(UTC),
        }
        if chunk_count is not None:
            update["chunk_count"] = chunk_count
        self._documents[self._documents.index(document)] = document.model_copy(update=update)

    async def delete_document(self, *, tenant_id: UUID, document_id: UUID) -> bool:
        document = await self.get_document(tenant_id=tenant_id, document_id=document_id)
        if document is None:
            return False
        self._documents.remove(document)
        self._chunks = [c for c in self._chunks if c.document_id != document_id]
        return True

    # -- chunks -------------------------------------------------------------

    async def replace_chunks(
        self, *, tenant_id: UUID, document_id: UUID, chunks: Sequence[KnowledgeChunk]
    ) -> None:
        self._chunks = [
            c
            for c in self._chunks
            if not (c.tenant_id == tenant_id and c.document_id == document_id)
        ]
        self._chunks.extend(chunks)

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
        kb_set = set(kb_ids)
        candidates = [c for c in self._chunks if c.tenant_id == tenant_id and c.kb_id in kb_set]
        candidates.sort(key=lambda c: _cosine_distance(query_embedding, c.embedding))
        return candidates[:limit]


def _created_key(record: KnowledgeBase | KnowledgeDocument) -> datetime:
    return record.created_at or datetime.min.replace(tzinfo=UTC)
