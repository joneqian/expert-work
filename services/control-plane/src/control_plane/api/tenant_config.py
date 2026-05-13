"""``/v1/tenants/{tenant_id}/config`` admin endpoints — Stream C.7.

GET (operator + admin) returns the full :class:`TenantConfigRecord`;
PUT (admin only) accepts a :class:`TenantConfigPatch` (partial
update). Both go through :class:`TenantConfigService` so the 60s LRU
cache stays warm and the audit log captures the access.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from control_plane.api._authz import require
from control_plane.tenancy import TenantConfigNotConfiguredError, TenantConfigService
from helix_agent.protocol import Principal, TenantConfigPatch

logger = logging.getLogger("helix.control_plane.api.tenant_config")


def _get_service(request: Request) -> TenantConfigService:
    return request.app.state.tenant_config_service  # type: ignore[no-any-return]


def build_tenant_config_router() -> APIRouter:
    router = APIRouter(prefix="/v1/tenants", tags=["tenant_config"])

    @router.get("/{tenant_id}/config")
    async def get_tenant_config(
        tenant_id: UUID,
        principal: Annotated[Principal, Depends(require("tenant_config", "read"))],
        svc: Annotated[TenantConfigService, Depends(_get_service)],
    ) -> dict[str, object]:
        _ensure_tenant_match(principal, tenant_id)
        try:
            record = await svc.get(tenant_id=tenant_id, actor_id=principal.subject_id)
        except TenantConfigNotConfiguredError as exc:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "TENANT_CONFIG_NOT_FOUND",
                    "message": "no tenant_config row exists for this tenant",
                },
            ) from exc
        return {"success": True, "data": record.model_dump(mode="json"), "error": None}

    @router.put("/{tenant_id}/config")
    async def upsert_tenant_config(
        tenant_id: UUID,
        payload: TenantConfigPatch,
        principal: Annotated[Principal, Depends(require("tenant_config", "write"))],
        svc: Annotated[TenantConfigService, Depends(_get_service)],
    ) -> dict[str, object]:
        _ensure_tenant_match(principal, tenant_id)
        try:
            record = await svc.upsert(
                tenant_id=tenant_id, patch=payload, actor_id=principal.subject_id
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "TENANT_CONFIG_FIRST_UPSERT_REQUIRES_DISPLAY_NAME",
                    "message": str(exc),
                },
            ) from exc
        return {"success": True, "data": record.model_dump(mode="json"), "error": None}

    return router


def _ensure_tenant_match(principal: Principal, tenant_id: UUID) -> None:
    """Block cross-tenant edits (same rule as ``/v1/tenants/{t}/quotas``)."""
    if principal.tenant_id == tenant_id:
        return
    if tenant_id in principal.allowed_tenants:
        return
    raise HTTPException(
        status_code=403,
        detail={
            "code": "TENANT_MISMATCH",
            "message": "principal cannot edit config for this tenant",
        },
    )
