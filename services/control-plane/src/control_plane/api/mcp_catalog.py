"""Platform MCP connector catalog CRUD API — Stream W (Mini-ADR W-6).

system_admin-only CRUD over the platform-curated MCP connector catalog. Every
handler:

* first gates on the RBAC matrix via ``require("mcp_catalog", <action>)``
  (system_admin auto-gets tenant-ADMIN authority there), then re-checks
  ``principal.is_system_admin`` inline — defense in depth: this is a *platform*
  surface (NULL-tenant rows), same precedent as ``platform_config.py``;
* drives every store call inside ``bypass_rls_session()`` because the catalog
  rows are tenant-less and the RLS policy would otherwise hide them from a
  normally-scoped session (the W-8 trap);
* audits ``name``/``category``/``required_tier``/``transport`` only — never any
  secret value (the catalog declares auth-field *names*, not tenant secrets).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from control_plane.api._authz import require
from control_plane.audit import emit
from control_plane.tenant_scope import bypass_rls_session
from helix_agent.common.observability import current_trace_id_hex
from helix_agent.persistence import (
    McpConnectorCatalogAlreadyExistsError,
    McpConnectorCatalogInUseError,
    McpConnectorCatalogNotFoundError,
    McpConnectorCatalogStore,
)
from helix_agent.protocol import (
    AuditAction,
    McpConnectorCatalogPatch,
    McpConnectorCatalogUpsert,
    Principal,
)
from helix_agent.runtime.audit.logger import AuditLogger


def _get_catalog_store(request: Request) -> McpConnectorCatalogStore:
    return request.app.state.mcp_connector_catalog_store  # type: ignore[no-any-return]


def _get_audit(request: Request) -> AuditLogger:
    return request.app.state.audit_logger  # type: ignore[no-any-return]


def _require_system_admin(principal: Principal) -> None:
    if not principal.is_system_admin:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "PLATFORM_SCOPE_FORBIDDEN",
                "message": "only a system admin may manage the MCP connector catalog",
            },
        )


def build_mcp_catalog_router() -> APIRouter:
    router = APIRouter(prefix="/v1/platform/mcp-catalog", tags=["mcp_catalog"])

    @router.post("", status_code=201)
    async def create_catalog_entry(
        payload: McpConnectorCatalogUpsert,
        principal: Annotated[Principal, Depends(require("mcp_catalog", "write"))],
        store: Annotated[McpConnectorCatalogStore, Depends(_get_catalog_store)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
    ) -> dict[str, object]:
        _require_system_admin(principal)
        try:
            async with bypass_rls_session():
                record = await store.create(upsert=payload, actor_id=principal.subject_id)
        except McpConnectorCatalogAlreadyExistsError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "CATALOG_DUPLICATE", "message": "name already registered"},
            ) from exc
        await emit(
            audit,
            tenant_id=principal.tenant_id,
            actor_id=principal.subject_id,
            action=AuditAction.MCP_CATALOG_CREATE,
            resource_type="mcp_connector_catalog",
            resource_id=record.name,
            trace_id=current_trace_id_hex(),
            details={
                "name": record.name,
                "category": record.category,
                "required_tier": record.required_tier.value,
                "transport": record.transport,
            },  # NEVER include any secret value
        )
        return {"success": True, "data": record.model_dump(mode="json"), "error": None}

    @router.get("")
    async def list_catalog_entries(
        principal: Annotated[Principal, Depends(require("mcp_catalog", "read"))],
        store: Annotated[McpConnectorCatalogStore, Depends(_get_catalog_store)],
        category: Annotated[str | None, Query()] = None,
    ) -> dict[str, object]:
        _require_system_admin(principal)
        async with bypass_rls_session():
            rows = await store.list(category=category)
        return {
            "success": True,
            "data": [r.model_dump(mode="json") for r in rows],
            "error": None,
        }

    @router.get("/{catalog_id}")
    async def get_catalog_entry(
        catalog_id: Annotated[UUID, Path()],
        principal: Annotated[Principal, Depends(require("mcp_catalog", "read"))],
        store: Annotated[McpConnectorCatalogStore, Depends(_get_catalog_store)],
    ) -> dict[str, object]:
        _require_system_admin(principal)
        async with bypass_rls_session():
            record = await store.get_by_id(catalog_id)
        if record is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "CATALOG_NOT_FOUND", "message": "not found"},
            )
        return {"success": True, "data": record.model_dump(mode="json"), "error": None}

    @router.patch("/{catalog_id}")
    async def update_catalog_entry(
        catalog_id: Annotated[UUID, Path()],
        payload: McpConnectorCatalogPatch,
        principal: Annotated[Principal, Depends(require("mcp_catalog", "write"))],
        store: Annotated[McpConnectorCatalogStore, Depends(_get_catalog_store)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
    ) -> dict[str, object]:
        _require_system_admin(principal)
        try:
            async with bypass_rls_session():
                record = await store.update(catalog_id=catalog_id, patch=payload)
        except McpConnectorCatalogNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "CATALOG_NOT_FOUND", "message": "not found"},
            ) from exc
        except ValueError as exc:
            # The merged record violated a cross-field invariant (e.g. the
            # bearer/secret rule) — the record validator raised on re-validation.
            raise HTTPException(
                status_code=422,
                detail={"code": "CATALOG_INVALID", "message": str(exc)},
            ) from exc
        await emit(
            audit,
            tenant_id=principal.tenant_id,
            actor_id=principal.subject_id,
            action=AuditAction.MCP_CATALOG_UPDATE,
            resource_type="mcp_connector_catalog",
            resource_id=record.name,
            trace_id=current_trace_id_hex(),
            details={
                "name": record.name,
                "category": record.category,
                "required_tier": record.required_tier.value,
                "enabled": record.enabled,
            },
        )
        return {"success": True, "data": record.model_dump(mode="json"), "error": None}

    @router.delete("/{catalog_id}", status_code=204)
    async def delete_catalog_entry(
        catalog_id: Annotated[UUID, Path()],
        principal: Annotated[Principal, Depends(require("mcp_catalog", "delete"))],
        store: Annotated[McpConnectorCatalogStore, Depends(_get_catalog_store)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
    ) -> None:
        _require_system_admin(principal)
        # Resolve the row first so the audit record carries the stable name.
        async with bypass_rls_session():
            existing = await store.get_by_id(catalog_id)
        if existing is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "CATALOG_NOT_FOUND", "message": "not found"},
            )
        try:
            async with bypass_rls_session():
                await store.delete(catalog_id)
        except McpConnectorCatalogNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "CATALOG_NOT_FOUND", "message": "not found"},
            ) from exc
        except McpConnectorCatalogInUseError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "CATALOG_IN_USE",
                    "message": "catalog entry is instantiated by one or more tenants",
                },
            ) from exc
        await emit(
            audit,
            tenant_id=principal.tenant_id,
            actor_id=principal.subject_id,
            action=AuditAction.MCP_CATALOG_DELETE,
            resource_type="mcp_connector_catalog",
            resource_id=existing.name,
            trace_id=current_trace_id_hex(),
            details={"name": existing.name},
        )

    return router
