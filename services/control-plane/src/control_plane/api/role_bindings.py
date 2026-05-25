"""``/v1/role_bindings`` admin endpoints — Stream C.3."""

from __future__ import annotations

import logging
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from control_plane.api._authz import require
from control_plane.audit import emit
from control_plane.tenant_scope import CrossTenant, applied_scope, ensure_tenant_scope
from helix_agent.common.observability import current_trace_id_hex
from helix_agent.persistence.auth import (
    DuplicateRoleBindingError,
    RoleBindingStore,
)
from helix_agent.protocol import AuditAction, Principal, Role
from helix_agent.runtime.audit.logger import AuditLogger

logger = logging.getLogger("helix.control_plane.api.role_bindings")


class CreateRoleBindingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_type: Literal["user", "service_account"]
    subject_id: UUID
    role: Role


def _get_repo(request: Request) -> RoleBindingStore:
    return request.app.state.role_binding_repo  # type: ignore[no-any-return]


def _get_audit(request: Request) -> AuditLogger:
    return request.app.state.audit_logger  # type: ignore[no-any-return]


def build_role_bindings_router() -> APIRouter:
    router = APIRouter(prefix="/v1/role_bindings", tags=["role_bindings"])

    @router.post("", status_code=201)
    async def create_role_binding(
        payload: CreateRoleBindingRequest,
        principal: Annotated[Principal, Depends(require("role_binding", "write"))],
        repo: Annotated[RoleBindingStore, Depends(_get_repo)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
    ) -> dict[str, object]:
        try:
            binding = await repo.create(
                subject_type=payload.subject_type,
                subject_id=payload.subject_id,
                tenant_id=principal.tenant_id,
                role=payload.role,
                granted_by=principal.subject_id,
            )
        except DuplicateRoleBindingError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "ROLE_BINDING_DUPLICATE",
                    "message": "the subject already has this role for this tenant",
                },
            ) from exc
        await emit(
            audit,
            tenant_id=principal.tenant_id,
            actor_id=principal.subject_id,
            action=AuditAction.ROLE_BINDING_CREATE,
            resource_type="role_binding",
            resource_id=str(binding.id),
            trace_id=current_trace_id_hex(),
            details={
                "subject_type": payload.subject_type,
                "subject_id": str(payload.subject_id),
                "role": payload.role.value,
            },
        )
        return {"success": True, "data": binding.model_dump(mode="json"), "error": None}

    @router.get("")
    async def list_role_bindings(
        principal: Annotated[Principal, Depends(require("role_binding", "read"))],
        repo: Annotated[RoleBindingStore, Depends(_get_repo)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
        tenant_id: Annotated[UUID | Literal["*"] | None, Query()] = None,  # Stream N
    ) -> dict[str, object]:
        scope = await ensure_tenant_scope(
            principal,
            tenant_id,
            audit,
            trace_id=current_trace_id_hex(),
            endpoint="GET /v1/role_bindings",
        )
        async with applied_scope(scope):
            if isinstance(scope, CrossTenant):
                items = await repo.list_all_tenants()
            else:
                items = await repo.list_for_tenant(tenant_id=scope.tenant_id)
        return {
            "success": True,
            "data": {
                "items": [b.model_dump(mode="json") for b in items],
                "total": len(items),
                "cross_tenant": isinstance(scope, CrossTenant),
            },
            "error": None,
        }

    @router.delete("/{binding_id}", status_code=204)
    async def delete_role_binding(
        binding_id: UUID,
        principal: Annotated[Principal, Depends(require("role_binding", "delete"))],
        repo: Annotated[RoleBindingStore, Depends(_get_repo)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
    ) -> None:
        ok = await repo.delete(tenant_id=principal.tenant_id, role_binding_id=binding_id)
        if not ok:
            raise HTTPException(status_code=404, detail="role_binding not found")
        await emit(
            audit,
            tenant_id=principal.tenant_id,
            actor_id=principal.subject_id,
            action=AuditAction.ROLE_BINDING_DELETE,
            resource_type="role_binding",
            resource_id=str(binding_id),
            trace_id=current_trace_id_hex(),
        )

    return router
