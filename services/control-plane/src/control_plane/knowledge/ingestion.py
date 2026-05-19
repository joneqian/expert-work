"""Async knowledge-base document ingestion — Stream J.5.

An uploaded document is processed off the request path: the upload
endpoint records the document as ``pending`` and submits it here; this
runner then parses → chunks → embeds → stores it as a background
``asyncio.Task``, advancing ``knowledge_document.status`` along the way
(``processing`` → ``ready`` / ``failed``) so the caller can poll it.

See ``docs/streams/STREAM-J-DESIGN.md`` § 12.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID, uuid4

from control_plane.knowledge.chunking import chunk_markdown_semantic
from control_plane.knowledge.parsing import parse_document
from helix_agent.persistence import KnowledgeStore
from helix_agent.persistence.rls import current_tenant_id_var
from helix_agent.protocol import DocumentStatus, KnowledgeChunk
from orchestrator.llm import Embedder

logger = logging.getLogger(__name__)

#: A failed document's ``error`` is truncated to this many characters.
_ERROR_CAP = 500


class KnowledgeIngestionRunner:
    """Runs the parse → chunk → embed → store pipeline off the request path.

    One per process, held on ``app.state``. :meth:`submit` is fire-and-
    forget — the upload endpoint returns immediately and the caller polls
    the document's status. RLS context is set explicitly per task (the
    background task is not inside an HTTP request).
    """

    def __init__(self, *, store: KnowledgeStore, embedder: Embedder) -> None:
        self._store = store
        self._embedder = embedder
        self._tasks: set[asyncio.Task[None]] = set()

    def submit(
        self,
        *,
        tenant_id: UUID,
        document_id: UUID,
        kb_id: UUID,
        filename: str,
        raw: bytes,
        chunk_max_tokens: int,
        chunk_overlap_tokens: int,
    ) -> asyncio.Task[None]:
        """Schedule ingestion of one document; return its background task."""
        task: asyncio.Task[None] = asyncio.create_task(
            self._run(
                tenant_id=tenant_id,
                document_id=document_id,
                kb_id=kb_id,
                filename=filename,
                raw=raw,
                chunk_max_tokens=chunk_max_tokens,
                chunk_overlap_tokens=chunk_overlap_tokens,
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self) -> None:
        """Await all outstanding ingestion tasks (used by tests / shutdown)."""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def aclose(self) -> None:
        """Cancel and drain outstanding tasks — bounded shutdown."""
        for task in list(self._tasks):
            task.cancel()
        await self.drain()

    async def _run(
        self,
        *,
        tenant_id: UUID,
        document_id: UUID,
        kb_id: UUID,
        filename: str,
        raw: bytes,
        chunk_max_tokens: int,
        chunk_overlap_tokens: int,
    ) -> None:
        # The background task is not inside an HTTP request — set the RLS
        # tenant context explicitly so the store's SQL passes row-level
        # security (the reaper does the same, quota/reaper.py).
        token = current_tenant_id_var.set(tenant_id)
        try:
            await self._store.set_document_status(
                tenant_id=tenant_id,
                document_id=document_id,
                status=DocumentStatus.PROCESSING,
            )
            chunk_count = await self._ingest(
                tenant_id=tenant_id,
                document_id=document_id,
                kb_id=kb_id,
                filename=filename,
                raw=raw,
                chunk_max_tokens=chunk_max_tokens,
                chunk_overlap_tokens=chunk_overlap_tokens,
            )
            await self._store.set_document_status(
                tenant_id=tenant_id,
                document_id=document_id,
                status=DocumentStatus.READY,
                chunk_count=chunk_count,
            )
            logger.info("knowledge.ingest_ready document=%s chunks=%d", document_id, chunk_count)
        except Exception as exc:
            # Any failure marks the document FAILED with a truncated error.
            logger.warning("knowledge.ingest_failed document=%s", document_id, exc_info=True)
            await self._store.set_document_status(
                tenant_id=tenant_id,
                document_id=document_id,
                status=DocumentStatus.FAILED,
                error=str(exc)[:_ERROR_CAP],
            )
        finally:
            current_tenant_id_var.reset(token)

    async def _ingest(
        self,
        *,
        tenant_id: UUID,
        document_id: UUID,
        kb_id: UUID,
        filename: str,
        raw: bytes,
        chunk_max_tokens: int,
        chunk_overlap_tokens: int,
    ) -> int:
        # Parsing is CPU-bound — run it off the event loop.
        markdown = await asyncio.to_thread(parse_document, filename, raw)
        chunk_texts = await chunk_markdown_semantic(
            markdown,
            max_tokens=chunk_max_tokens,
            overlap_tokens=chunk_overlap_tokens,
            embedder=self._embedder,
        )
        if not chunk_texts:
            await self._store.replace_chunks(
                tenant_id=tenant_id, document_id=document_id, chunks=[]
            )
            return 0
        embeddings = await self._embedder.embed(chunk_texts)
        chunks = [
            KnowledgeChunk(
                id=uuid4(),
                tenant_id=tenant_id,
                kb_id=kb_id,
                document_id=document_id,
                chunk_index=index,
                content=text,
                embedding=embedding,
            )
            for index, (text, embedding) in enumerate(zip(chunk_texts, embeddings, strict=True))
        ]
        await self._store.replace_chunks(
            tenant_id=tenant_id, document_id=document_id, chunks=chunks
        )
        return len(chunks)
