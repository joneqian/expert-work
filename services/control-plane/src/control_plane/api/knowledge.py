"""``/v1/knowledge`` — Stream J.5 knowledge-base management + document ingest.

Knowledge bases are tenant-scoped (shared, not per-user). This router
manages bases and their documents:

* bases — create / list / delete;
* documents — upload (async ingest) / list (with status) / delete.

Upload is **off the request path**: the document is recorded ``pending``
and handed to the :class:`KnowledgeIngestionRunner`; the caller polls the
document list for its ``status``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from control_plane.knowledge.ingestion import KnowledgeIngestionRunner
from control_plane.knowledge.parsing import SUPPORTED_EXTENSIONS
from helix_agent.persistence import KnowledgeStore
from helix_agent.persistence.knowledge import DuplicateKnowledgeBaseError
from helix_agent.protocol import (
    DEFAULT_CHUNK_MAX_TOKENS,
    DEFAULT_CHUNK_OVERLAP_TOKENS,
    KnowledgeBase,
    KnowledgeDocument,
)

logger = logging.getLogger("helix.control_plane.knowledge")


class _CreateBaseBody(BaseModel):
    """Body of ``POST /v1/knowledge/bases``."""

    name: str = Field(min_length=1, max_length=128)
    chunk_max_tokens: int | None = Field(default=None, gt=0)
    chunk_overlap_tokens: int | None = Field(default=None, ge=0)


def _get_knowledge_store(request: Request) -> KnowledgeStore:
    return request.app.state.knowledge_store  # type: ignore[no-any-return]


def _get_ingestion_runner(request: Request) -> KnowledgeIngestionRunner | None:
    return request.app.state.knowledge_ingestion_runner  # type: ignore[no-any-return]


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _base_dict(base: KnowledgeBase) -> dict[str, Any]:
    return {
        "id": str(base.id),
        "name": base.name,
        "chunk_max_tokens": base.chunk_max_tokens,
        "chunk_overlap_tokens": base.chunk_overlap_tokens,
        "created_at": _iso(base.created_at),
    }


def _document_dict(document: KnowledgeDocument) -> dict[str, Any]:
    return {
        "id": str(document.id),
        "filename": document.filename,
        "status": document.status.value,
        "error": document.error,
        "chunk_count": document.chunk_count,
        "created_at": _iso(document.created_at),
        "updated_at": _iso(document.updated_at),
    }


async def _require_base(store: KnowledgeStore, tenant_id: UUID, name: str) -> KnowledgeBase:
    base = await store.get_base(tenant_id=tenant_id, name=name)
    if base is None:
        raise HTTPException(status_code=404, detail="knowledge base not found")
    return base


def build_knowledge_router() -> APIRouter:
    router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])

    @router.post("/bases", response_model=None)
    async def create_base(
        body: _CreateBaseBody,
        request: Request,
        store: Annotated[KnowledgeStore, Depends(_get_knowledge_store)],
    ) -> JSONResponse:
        tenant_id: UUID = request.state.tenant_id
        max_tokens = body.chunk_max_tokens or DEFAULT_CHUNK_MAX_TOKENS
        overlap_tokens = (
            body.chunk_overlap_tokens
            if body.chunk_overlap_tokens is not None
            else DEFAULT_CHUNK_OVERLAP_TOKENS
        )
        if overlap_tokens >= max_tokens:
            raise HTTPException(
                status_code=400,
                detail="chunk_overlap_tokens must be less than chunk_max_tokens",
            )
        try:
            base = await store.create_base(
                tenant_id=tenant_id,
                name=body.name,
                chunk_max_tokens=max_tokens,
                chunk_overlap_tokens=overlap_tokens,
            )
        except DuplicateKnowledgeBaseError as exc:
            raise HTTPException(status_code=409, detail="knowledge base already exists") from exc
        return JSONResponse(status_code=201, content=_base_dict(base))

    @router.get("/bases", response_model=None)
    async def list_bases(
        request: Request,
        store: Annotated[KnowledgeStore, Depends(_get_knowledge_store)],
    ) -> JSONResponse:
        tenant_id: UUID = request.state.tenant_id
        bases = await store.list_bases(tenant_id=tenant_id)
        return JSONResponse(content={"bases": [_base_dict(base) for base in bases]})

    @router.delete("/bases/{name}", status_code=204, response_model=None)
    async def delete_base(
        name: str,
        request: Request,
        store: Annotated[KnowledgeStore, Depends(_get_knowledge_store)],
    ) -> Response:
        tenant_id: UUID = request.state.tenant_id
        base = await _require_base(store, tenant_id, name)
        await store.delete_base(tenant_id=tenant_id, kb_id=base.id)
        return Response(status_code=204)

    @router.post("/bases/{name}/documents", response_model=None)
    async def upload_document(
        name: str,
        request: Request,
        file: Annotated[UploadFile, File()],
        store: Annotated[KnowledgeStore, Depends(_get_knowledge_store)],
        runner: Annotated[KnowledgeIngestionRunner | None, Depends(_get_ingestion_runner)],
    ) -> JSONResponse:
        tenant_id: UUID = request.state.tenant_id
        base = await _require_base(store, tenant_id, name)
        if runner is None:
            raise HTTPException(
                status_code=503,
                detail="knowledge ingestion unavailable: no embedding model configured",
            )
        filename = file.filename
        if not filename:
            raise HTTPException(status_code=400, detail="uploaded file has no filename")
        if Path(filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"unsupported document type: {filename!r}")
        raw = await file.read()
        document = await store.upsert_document(
            tenant_id=tenant_id, kb_id=base.id, filename=filename
        )
        runner.submit(
            tenant_id=tenant_id,
            document_id=document.id,
            kb_id=base.id,
            filename=filename,
            raw=raw,
            chunk_max_tokens=base.chunk_max_tokens,
            chunk_overlap_tokens=base.chunk_overlap_tokens,
        )
        # 202 Accepted — ingestion runs in the background; poll the
        # document list for its status.
        return JSONResponse(status_code=202, content=_document_dict(document))

    @router.get("/bases/{name}/documents", response_model=None)
    async def list_documents(
        name: str,
        request: Request,
        store: Annotated[KnowledgeStore, Depends(_get_knowledge_store)],
    ) -> JSONResponse:
        tenant_id: UUID = request.state.tenant_id
        base = await _require_base(store, tenant_id, name)
        documents = await store.list_documents(tenant_id=tenant_id, kb_id=base.id)
        return JSONResponse(content={"documents": [_document_dict(doc) for doc in documents]})

    @router.delete("/bases/{name}/documents/{document_id}", status_code=204, response_model=None)
    async def delete_document(
        name: str,
        document_id: UUID,
        request: Request,
        store: Annotated[KnowledgeStore, Depends(_get_knowledge_store)],
    ) -> Response:
        tenant_id: UUID = request.state.tenant_id
        await _require_base(store, tenant_id, name)
        deleted = await store.delete_document(tenant_id=tenant_id, document_id=document_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="document not found")
        return Response(status_code=204)

    return router
