"""Abstract ``KnowledgeStore`` repository — Stream J.5 RAG.

Implementations:
- :class:`helix_agent.persistence.knowledge.memory.InMemoryKnowledgeStore`
- :class:`helix_agent.persistence.knowledge.sql.SqlKnowledgeStore`

The store deals in *vectors* — embedding text into a vector is the
caller's job (the J.5 ingestion runner / retriever wire the embedder).
This keeps the persistence layer free of any embedding-model dependency.
All methods are tenant-scoped; knowledge bases are tenant-shared.
"""

from __future__ import annotations

import abc
from collections.abc import Sequence
from uuid import UUID

from helix_agent.protocol import DocumentStatus, KnowledgeBase, KnowledgeChunk, KnowledgeDocument


class DuplicateKnowledgeBaseError(Exception):
    """Raised when :meth:`KnowledgeStore.create_base` hits the unique
    ``(tenant_id, name)`` index. The API layer maps it to ``HTTP 409``."""

    def __init__(self, *, tenant_id: UUID, name: str) -> None:
        super().__init__(f"knowledge base already exists: tenant_id={tenant_id} name={name!r}")
        self.tenant_id = tenant_id
        self.name = name


class KnowledgeStore(abc.ABC):
    """Tenant-scoped knowledge base / document / chunk repository."""

    # -- knowledge bases ----------------------------------------------------

    @abc.abstractmethod
    async def create_base(self, *, tenant_id: UUID, name: str) -> KnowledgeBase:
        """Create a new knowledge base; raise :class:`DuplicateKnowledgeBaseError`
        if ``(tenant_id, name)`` already exists."""

    @abc.abstractmethod
    async def get_base(self, *, tenant_id: UUID, name: str) -> KnowledgeBase | None:
        """Fetch a base by name, or ``None`` — never reveals another tenant's."""

    @abc.abstractmethod
    async def list_bases(self, *, tenant_id: UUID) -> list[KnowledgeBase]:
        """The tenant's knowledge bases, newest first."""

    @abc.abstractmethod
    async def delete_base(self, *, tenant_id: UUID, kb_id: UUID) -> bool:
        """Delete a base and cascade its documents + chunks. ``False`` if
        no base matched."""

    # -- documents ----------------------------------------------------------

    @abc.abstractmethod
    async def upsert_document(
        self, *, tenant_id: UUID, kb_id: UUID, filename: str
    ) -> KnowledgeDocument:
        """Create a document at ``PENDING``, or — if ``(kb_id, filename)``
        already exists — reset it to ``PENDING`` (clearing ``error`` /
        ``chunk_count``) for re-ingestion. Returns the document."""

    @abc.abstractmethod
    async def get_document(self, *, tenant_id: UUID, document_id: UUID) -> KnowledgeDocument | None:
        """Fetch a document by id, or ``None``."""

    @abc.abstractmethod
    async def list_documents(self, *, tenant_id: UUID, kb_id: UUID) -> list[KnowledgeDocument]:
        """The base's documents, newest first."""

    @abc.abstractmethod
    async def set_document_status(
        self,
        *,
        tenant_id: UUID,
        document_id: UUID,
        status: DocumentStatus,
        error: str | None = None,
        chunk_count: int | None = None,
    ) -> None:
        """Update a document's ingestion status. ``chunk_count`` is set
        only when supplied (on a successful ingest)."""

    @abc.abstractmethod
    async def delete_document(self, *, tenant_id: UUID, document_id: UUID) -> bool:
        """Delete a document and cascade its chunks. ``False`` if no
        document matched."""

    # -- chunks -------------------------------------------------------------

    @abc.abstractmethod
    async def replace_chunks(
        self, *, tenant_id: UUID, document_id: UUID, chunks: Sequence[KnowledgeChunk]
    ) -> None:
        """Replace all of a document's chunks with ``chunks`` — deletes
        the document's existing chunks, then inserts the new set."""

    @abc.abstractmethod
    async def search(
        self,
        *,
        tenant_id: UUID,
        kb_ids: Sequence[UUID],
        query_embedding: Sequence[float],
        limit: int = 5,
    ) -> list[KnowledgeChunk]:
        """Return the ``limit`` chunks across ``kb_ids`` nearest
        ``query_embedding`` by cosine distance, closest first. An empty
        ``kb_ids`` yields an empty list."""
