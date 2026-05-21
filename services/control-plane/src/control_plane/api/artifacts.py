"""``GET /v1/artifacts`` — Stream J.9 artifact list + content download.

Artifacts are per-``(tenant, user)`` (Mini-ADR J-1). The list endpoint
returns the caller's artifacts' metadata; the download endpoint streams
one artifact's latest-version content.

Content lives in the user's J.15 workspace volume — only the
sandbox-supervisor can read a docker volume, so the download endpoint
proxies to the supervisor's workspace-file API and backfills the
version's ``size_bytes`` / ``sha256`` on that first read.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from control_plane.api._quota_admission import check_admission
from control_plane.api._user_scope import get_user_repo, resolve_caller_user_id
from control_plane.quota.base import QuotaService
from helix_agent.persistence import ArtifactStore
from helix_agent.persistence.rls import current_user_id_var
from helix_agent.persistence.tenant_user import TenantUserStore
from helix_agent.runtime.audit.logger import AuditLogger
from orchestrator.tools import SandboxSupervisorError, SupervisorClient

logger = logging.getLogger("helix.control_plane.artifacts")


def _get_artifact_store(request: Request) -> ArtifactStore:
    return request.app.state.artifact_store  # type: ignore[no-any-return]


def _get_supervisor_client(request: Request) -> SupervisorClient | None:
    return request.app.state.supervisor_client  # type: ignore[no-any-return]


def _get_quota(request: Request) -> QuotaService:
    return request.app.state.quota_service  # type: ignore[no-any-return]


def _get_audit(request: Request) -> AuditLogger:
    return request.app.state.audit_logger  # type: ignore[no-any-return]


def build_artifacts_router() -> APIRouter:
    router = APIRouter(prefix="/v1/artifacts", tags=["artifacts"])

    @router.get("", response_model=None)
    async def list_artifacts(
        request: Request,
        store: Annotated[ArtifactStore, Depends(_get_artifact_store)],
        users: Annotated[TenantUserStore, Depends(get_user_repo)],
    ) -> JSONResponse:
        tenant_id: UUID = request.state.tenant_id
        caller_user_id = await resolve_caller_user_id(request, users)
        # Artifacts are per-user; a machine principal owns none.
        if caller_user_id is None:
            return JSONResponse(content={"artifacts": []})
        current_user_id_var.set(caller_user_id)
        artifacts = await store.list_for_user(tenant_id=tenant_id, user_id=caller_user_id)
        return JSONResponse(
            content={
                "artifacts": [
                    {"name": a.name, "kind": a.kind, "latest_version": a.latest_version}
                    for a in artifacts
                ]
            }
        )

    @router.get("/download", response_model=None)
    async def download_artifact(
        name: str,
        request: Request,
        store: Annotated[ArtifactStore, Depends(_get_artifact_store)],
        users: Annotated[TenantUserStore, Depends(get_user_repo)],
        supervisor: Annotated[SupervisorClient | None, Depends(_get_supervisor_client)],
        quota: Annotated[QuotaService, Depends(_get_quota)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
    ) -> Response:
        tenant_id: UUID = request.state.tenant_id
        caller_user_id = await resolve_caller_user_id(request, users)
        # 404 (not 403) so a cross-user / nonexistent name stays opaque.
        if caller_user_id is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        current_user_id_var.set(caller_user_id)
        version = await store.get_latest_version(
            tenant_id=tenant_id, user_id=caller_user_id, name=name
        )
        if version is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        # Mini-ADR J-25 (J.9-step2) — quota admission. Deducts ``cost=1``
        # from QPS + ``ARTIFACT_DOWNLOAD_COUNT_30D`` (only the dimensions
        # a tenant has rows for run; others are no-ops). 429 on denial.
        # Storage-bytes is not deducted here: it's a save-side concern
        # and ships with the orchestrator quota plumbing in a later step.
        actor_id: str = getattr(request.state, "actor_id", "anonymous")
        denial = await check_admission(
            quota=quota,
            audit=audit,
            tenant_id=tenant_id,
            actor_id=actor_id,
            agent=None,
            resource_kind="artifact_download",
            cost=1,
        )
        if denial is not None:
            return denial
        if supervisor is None:
            raise HTTPException(
                status_code=503,
                detail="artifact download unavailable: no sandbox supervisor configured",
            )
        try:
            data = await supervisor.read_workspace_file(
                tenant_id=tenant_id,
                user_id=caller_user_id,
                path=version.path_in_workspace,
            )
        except SandboxSupervisorError as exc:
            # The metadata row exists but the file is gone / unreadable —
            # log the supervisor detail, keep the client response opaque.
            logger.warning("artifact.content_unavailable version=%s reason=%s", version.id, exc)
            raise HTTPException(status_code=404, detail="artifact content not found") from exc

        # Backfill the digest on first read — unknown at save_artifact time.
        if version.size_bytes is None:
            await store.set_version_digest(
                version_id=version.id,
                size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
            )
        return Response(content=data, media_type="application/octet-stream")

    return router
