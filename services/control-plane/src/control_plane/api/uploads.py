"""``POST /v1/sessions/{thread_id}/uploads`` — Stream J.6 image upload.

Multipart entry point for the multimodal input path: the user uploads an
image alongside a session, the bytes land in the object store, and the
endpoint returns the ``helix://image/...`` reference the run request's
``image_refs`` field carries.

Image references — not inline base64 — ride in the run message so each
checkpoint snapshot stays small; the provider adapter / ``ask_image``
tool resolves the ref to bytes only at LLM-call time.

Auth follows the same J.14 pattern as ``POST .../runs`` — the caller
must own the thread, and 404 hides cross-user existence.
"""

from __future__ import annotations

from typing import Annotated, Final
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from control_plane.api._user_scope import (
    caller_owns_thread,
    get_user_repo,
    resolve_caller_user_id,
)
from control_plane.settings import Settings
from helix_agent.persistence.tenant_user import TenantUserStore
from helix_agent.protocol.multimodal import ImageRef
from helix_agent.runtime.storage import ObjectStore

#: File extension per accepted image content type. The reverse direction
#: (ext → media_type) lives in the orchestrator's image resolver.
_EXT_BY_CONTENT_TYPE: Final[dict[str, str]] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def _get_object_store(request: Request) -> ObjectStore | None:
    store: ObjectStore | None = getattr(request.app.state, "object_store", None)
    return store


def _get_thread_repo(request: Request) -> object:
    return request.app.state.thread_meta_repo


def _get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def build_uploads_router() -> APIRouter:
    router = APIRouter(prefix="/v1/sessions", tags=["sessions"])

    @router.post("/{thread_id}/uploads", response_model=None)
    async def upload_image(
        thread_id: UUID,
        request: Request,
        file: Annotated[UploadFile, File()],
        threads: Annotated[object, Depends(_get_thread_repo)],
        users: Annotated[TenantUserStore, Depends(get_user_repo)],
        store: Annotated[ObjectStore | None, Depends(_get_object_store)],
        settings: Annotated[Settings, Depends(_get_settings)],
    ) -> JSONResponse:
        if store is None:
            raise HTTPException(status_code=503, detail="object store unavailable")

        tenant_id: UUID = request.state.tenant_id

        # Thread ownership — Stream J.14 (404 hides cross-user existence).
        meta = await threads.get(thread_id, tenant_id=tenant_id)  # type: ignore[attr-defined]
        if meta is None:
            raise HTTPException(status_code=404, detail="session not found")
        caller_user_id = await resolve_caller_user_id(request, users)
        if not caller_owns_thread(
            meta=meta,
            caller_user_id=caller_user_id,
            principal=request.state.principal,
        ):
            raise HTTPException(status_code=404, detail="session not found")

        # Validate the upload at the boundary.
        if not file.filename:
            raise HTTPException(status_code=400, detail="uploaded file has no filename")
        content_type = (file.content_type or "").lower()
        if content_type not in settings.multimodal_allowed_content_types:
            raise HTTPException(
                status_code=400,
                detail=f"unsupported image content type: {content_type or 'missing'!r}",
            )
        ext = _EXT_BY_CONTENT_TYPE.get(content_type)
        if ext is None:
            # Config drift: the allowlist admitted a type the canonical
            # extension table doesn't know. Refuse rather than forge a key.
            raise HTTPException(
                status_code=400,
                detail=f"no extension known for content type {content_type!r}",
            )

        max_bytes = settings.multimodal_max_image_bytes
        if file.size is not None and file.size > max_bytes:
            raise HTTPException(status_code=413, detail=f"image exceeds {max_bytes}-byte limit")
        raw = await file.read()
        if len(raw) > max_bytes:
            raise HTTPException(status_code=413, detail=f"image exceeds {max_bytes}-byte limit")
        if not raw:
            raise HTTPException(status_code=400, detail="uploaded file is empty")

        image_ref = ImageRef(
            tenant_id=tenant_id,
            thread_id=thread_id,
            image_id=uuid4(),
            ext=ext,
        )
        await store.put(image_ref.storage_key, raw, content_type=content_type)
        # No app-level INFO log of the upload — request-context logging
        # is the audit middleware's job. Logging request-derived values
        # here is both redundant and trips CodeQL py/log-injection.
        return JSONResponse(status_code=201, content={"image_ref": image_ref.to_uri()})

    return router
