"""Platform Agent template catalog CRUD API — Stream Agent-Templates (M1-3).

system_admin-only CRUD over the platform-curated Agent template catalog (the base
manifests tenants ``fork`` via ``extends``). Mirrors ``mcp_catalog.py``: every
handler

* gates on the RBAC matrix via ``require("agent_template", <action>)`` (system_admin
  auto-gets tenant-ADMIN there), then re-checks ``principal.is_system_admin`` inline
  — defense in depth for a *platform* (NULL-tenant) surface;
* drives every store call inside ``bypass_rls_session()`` (NULL-tenant rows would
  otherwise be hidden by RLS — the W-8 trap);
* on any change, invalidates every cached built-agent so inheriting forks re-resolve
  against the updated base on their next build (the security floor re-applies).

Templates are versioned by ``(name, version)`` (from ``spec.metadata``), so a tenant
can pin ``extends: name@1.2.0``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from control_plane.api._authz import require
from control_plane.audit import emit
from control_plane.tenant_scope import bypass_rls_session
from helix_agent.common.observability import current_trace_id_hex
from helix_agent.persistence import (
    PlatformAgentTemplateAlreadyExistsError,
    PlatformAgentTemplateNotFoundError,
    PlatformAgentTemplateStore,
)
from helix_agent.protocol import (
    AgentSpec,
    AuditAction,
    PlatformAgentTemplatePatch,
    PlatformAgentTemplateRecord,
    PlatformAgentTemplateStatus,
    PlatformAgentTemplateUpsert,
    Principal,
)
from helix_agent.runtime.audit.logger import AuditLogger


def _get_template_store(request: Request) -> PlatformAgentTemplateStore:
    return request.app.state.platform_agent_template_store  # type: ignore[no-any-return]


def _get_audit(request: Request) -> AuditLogger:
    return request.app.state.audit_logger  # type: ignore[no-any-return]


def _get_agent_runtime(request: Request) -> object:
    return getattr(request.app.state, "agent_runtime", None)


def _invalidate_agents(agent_runtime: object) -> None:
    """Evict every cached built-agent so inheriting forks re-resolve against the
    updated template base (and re-apply the security floor) on next build."""
    if agent_runtime is not None:
        agent_runtime.invalidate_all()  # type: ignore[attr-defined]


def _public(record: PlatformAgentTemplateRecord) -> dict[str, object]:
    """Response projection — the full base manifest + marketplace metadata."""
    return record.model_dump(mode="json")


def _require_system_admin(principal: Principal) -> None:
    if not principal.is_system_admin:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "PLATFORM_SCOPE_FORBIDDEN",
                "message": "only a system admin may manage the Agent template catalog",
            },
        )


def _reject_extends(spec: AgentSpec) -> None:
    """A platform template IS a base — it cannot itself ``extends`` another."""
    if spec.spec.extends is not None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "TEMPLATE_CANNOT_EXTEND",
                "message": "a platform template is a base manifest and cannot declare extends",
            },
        )


def build_agent_templates_router() -> APIRouter:
    router = APIRouter(prefix="/v1/platform/agent-templates", tags=["agent_templates"])

    @router.post("", status_code=201)
    async def create_template(
        payload: PlatformAgentTemplateUpsert,
        principal: Annotated[Principal, Depends(require("agent_template", "write"))],
        store: Annotated[PlatformAgentTemplateStore, Depends(_get_template_store)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
        agent_runtime: Annotated[object, Depends(_get_agent_runtime)],
    ) -> dict[str, object]:
        _require_system_admin(principal)
        _reject_extends(payload.spec)
        try:
            async with bypass_rls_session():
                record = await store.create(upsert=payload, created_by=principal.subject_id)
        except PlatformAgentTemplateAlreadyExistsError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "TEMPLATE_DUPLICATE",
                    "message": "name@version already registered",
                },
            ) from exc
        await _emit(audit, principal, AuditAction.AGENT_TEMPLATE_CREATE, record)
        _invalidate_agents(agent_runtime)
        return {"success": True, "data": _public(record), "error": None}

    @router.get("")
    async def list_templates(
        principal: Annotated[Principal, Depends(require("agent_template", "read"))],
        store: Annotated[PlatformAgentTemplateStore, Depends(_get_template_store)],
        category: Annotated[str | None, Query()] = None,
        status: Annotated[PlatformAgentTemplateStatus | None, Query()] = None,
    ) -> dict[str, object]:
        _require_system_admin(principal)
        async with bypass_rls_session():
            rows = await store.list(category=category, status=status)
        return {"success": True, "data": [_public(r) for r in rows], "error": None}

    @router.get("/{name}/{version}")
    async def get_template(
        name: Annotated[str, Path()],
        version: Annotated[str, Path()],
        principal: Annotated[Principal, Depends(require("agent_template", "read"))],
        store: Annotated[PlatformAgentTemplateStore, Depends(_get_template_store)],
    ) -> dict[str, object]:
        _require_system_admin(principal)
        async with bypass_rls_session():
            record = await store.get(name=name, version=version)
        if record is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "TEMPLATE_NOT_FOUND", "message": "not found"},
            )
        return {"success": True, "data": _public(record), "error": None}

    @router.put("/{name}/{version}")
    async def update_template_spec(
        name: Annotated[str, Path()],
        version: Annotated[str, Path()],
        payload: AgentSpec,
        principal: Annotated[Principal, Depends(require("agent_template", "write"))],
        store: Annotated[PlatformAgentTemplateStore, Depends(_get_template_store)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
        agent_runtime: Annotated[object, Depends(_get_agent_runtime)],
    ) -> dict[str, object]:
        _require_system_admin(principal)
        _reject_extends(payload)
        if payload.metadata.name != name or payload.metadata.version != version:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "TEMPLATE_IDENTITY_MISMATCH",
                    "message": "manifest metadata name/version must match the path",
                },
            )
        async with bypass_rls_session():
            record = await store.update_spec(
                name=name, version=version, spec=payload, updated_by=principal.subject_id
            )
        if record is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "TEMPLATE_NOT_FOUND", "message": "not found"},
            )
        await _emit(audit, principal, AuditAction.AGENT_TEMPLATE_UPDATE, record)
        _invalidate_agents(agent_runtime)
        return {"success": True, "data": _public(record), "error": None}

    @router.patch("/{name}/{version}")
    async def patch_template_meta(
        name: Annotated[str, Path()],
        version: Annotated[str, Path()],
        patch: PlatformAgentTemplatePatch,
        principal: Annotated[Principal, Depends(require("agent_template", "write"))],
        store: Annotated[PlatformAgentTemplateStore, Depends(_get_template_store)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
        agent_runtime: Annotated[object, Depends(_get_agent_runtime)],
    ) -> dict[str, object]:
        _require_system_admin(principal)
        async with bypass_rls_session():
            record = await store.update_meta(name=name, version=version, patch=patch)
        if record is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "TEMPLATE_NOT_FOUND", "message": "not found"},
            )
        await _emit(audit, principal, AuditAction.AGENT_TEMPLATE_UPDATE, record)
        # A status flip (publish/unpublish) changes @latest resolution → invalidate.
        _invalidate_agents(agent_runtime)
        return {"success": True, "data": _public(record), "error": None}

    @router.delete("/{name}/{version}", status_code=204)
    async def delete_template(
        name: Annotated[str, Path()],
        version: Annotated[str, Path()],
        principal: Annotated[Principal, Depends(require("agent_template", "delete"))],
        store: Annotated[PlatformAgentTemplateStore, Depends(_get_template_store)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
        agent_runtime: Annotated[object, Depends(_get_agent_runtime)],
    ) -> None:
        _require_system_admin(principal)
        try:
            async with bypass_rls_session():
                await store.delete(name=name, version=version)
        except PlatformAgentTemplateNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "TEMPLATE_NOT_FOUND", "message": "not found"},
            ) from exc
        await emit(
            audit,
            tenant_id=principal.tenant_id,
            actor_id=principal.subject_id,
            action=AuditAction.AGENT_TEMPLATE_DELETE,
            resource_type="platform_agent_template",
            resource_id=f"{name}@{version}",
            trace_id=current_trace_id_hex(),
            details={"name": name, "version": version},
        )
        _invalidate_agents(agent_runtime)

    return router


async def _emit(
    audit: AuditLogger,
    principal: Principal,
    action: AuditAction,
    record: PlatformAgentTemplateRecord,
) -> None:
    await emit(
        audit,
        tenant_id=principal.tenant_id,
        actor_id=principal.subject_id,
        action=action,
        resource_type="platform_agent_template",
        resource_id=f"{record.name}@{record.version}",
        trace_id=current_trace_id_hex(),
        details={
            "name": record.name,
            "version": record.version,
            "category": record.category,
            "required_tier": record.required_tier.value,
            "status": record.status.value,
        },
    )
