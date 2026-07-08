"""``/v1/tenants/{tenant_id}/quotas`` admin endpoints — Stream C.5.

CRUD on ``tenant_quota`` rows. All write paths require the admin
role; read returns the per-tenant config including currently active
limit values.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from control_plane.api._authz import require
from control_plane.audit import emit
from expert_work.common.observability import current_trace_id_hex
from expert_work.persistence.quota import TenantQuotaStore
from expert_work.protocol import ALL_TENANTS, AuditAction, Principal, TenantQuotaPatch
from expert_work.runtime.audit.logger import AuditLogger

logger = logging.getLogger("expert_work.control_plane.api.tenant_quotas")


def _get_repo(request: Request) -> TenantQuotaStore:
    return request.app.state.tenant_quota_repo  # type: ignore[no-any-return]


def _get_audit(request: Request) -> AuditLogger:
    return request.app.state.audit_logger  # type: ignore[no-any-return]


def build_tenant_quotas_router() -> APIRouter:
    router = APIRouter(prefix="/v1/tenants", tags=["tenant_quotas"])

    @router.get("/{tenant_id}/quotas")
    async def list_tenant_quotas(
        tenant_id: UUID,
        principal: Annotated[Principal, Depends(require("quota", "read"))],
        repo: Annotated[TenantQuotaStore, Depends(_get_repo)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
    ) -> dict[str, object]:
        _ensure_tenant_match(principal, tenant_id)
        rows = await repo.list_by_tenant(tenant_id=tenant_id)
        await emit(
            audit,
            tenant_id=tenant_id,
            actor_id=principal.subject_id,
            action=AuditAction.QUOTA_CONFIG_READ,
            resource_type="quota",
            resource_id=None,
            trace_id=current_trace_id_hex(),
            details={"count": len(rows)},
        )
        return {
            "success": True,
            "data": [r.model_dump(mode="json") for r in rows],
            "error": None,
        }

    @router.post("/{tenant_id}/quotas", status_code=201)
    async def upsert_tenant_quota(
        tenant_id: UUID,
        payload: TenantQuotaPatch,
        principal: Annotated[Principal, Depends(require("quota", "write"))],
        repo: Annotated[TenantQuotaStore, Depends(_get_repo)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
    ) -> dict[str, object]:
        _ensure_tenant_match(principal, tenant_id)
        row = await repo.upsert(
            tenant_id=tenant_id,
            patch=payload,
            updated_by=principal.subject_id,
        )
        await emit(
            audit,
            tenant_id=tenant_id,
            actor_id=principal.subject_id,
            action=AuditAction.QUOTA_CONFIG_WRITE,
            resource_type="quota",
            resource_id=str(row.id),
            trace_id=current_trace_id_hex(),
            details={
                "dimension": payload.dimension.value,
                "scope": dict(payload.scope),
                "limit_value": payload.limit_value,
                "burst": payload.burst,
            },
        )
        return {"success": True, "data": row.model_dump(mode="json"), "error": None}

    @router.delete("/{tenant_id}/quotas/{quota_id}", status_code=204)
    async def delete_tenant_quota(
        tenant_id: UUID,
        quota_id: UUID,
        principal: Annotated[Principal, Depends(require("quota", "delete"))],
        repo: Annotated[TenantQuotaStore, Depends(_get_repo)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
    ) -> None:
        _ensure_tenant_match(principal, tenant_id)
        if not await repo.delete(quota_id=quota_id, tenant_id=tenant_id):
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "QUOTA_NOT_FOUND",
                    "message": "tenant_quota row not found for this tenant",
                },
            )
        await emit(
            audit,
            tenant_id=tenant_id,
            actor_id=principal.subject_id,
            action=AuditAction.QUOTA_CONFIG_DELETE,
            resource_type="quota",
            resource_id=str(quota_id),
            trace_id=current_trace_id_hex(),
        )

    return router


def _ensure_tenant_match(principal: Principal, tenant_id: UUID) -> None:
    """Block admins from one tenant editing another tenant's quotas.

    JWT principals always carry their own tenant; mTLS service
    principals carry the system tenant and may be allowed to operate
    on any (via ``allowed_tenants``) — but for admin endpoints we want
    the principal's tenant to match the path. Subsystems/15 § 5
    documents this exact rule for cross-tenant guard.
    """
    if principal.tenant_id == tenant_id:
        return
    # A principal authorized for every tenant carries the ``ALL_TENANTS`` ("*")
    # sentinel — system_admin (Stream N) and certain mTLS service principals.
    # Guard it before the ``in`` test: ``UUID in "*"`` raises TypeError, not a
    # membership check (mirrors control_plane.tenant_scope's ``!= "*"`` guard).
    if principal.allowed_tenants == ALL_TENANTS:
        return
    if tenant_id in principal.allowed_tenants:
        return
    raise HTTPException(
        status_code=403,
        detail={
            "code": "TENANT_MISMATCH",
            "message": "principal cannot edit quotas for this tenant",
        },
    )
