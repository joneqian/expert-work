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
    McpConnectorCatalogRecord,
    McpConnectorCatalogUpsert,
    Principal,
)
from helix_agent.runtime.audit.logger import AuditLogger
from helix_agent.runtime.secret_store import SecretStore


def _get_catalog_store(request: Request) -> McpConnectorCatalogStore:
    return request.app.state.mcp_connector_catalog_store  # type: ignore[no-any-return]


def _get_secret_store(request: Request) -> SecretStore:
    return request.app.state.secret_store  # type: ignore[no-any-return]


def _get_audit(request: Request) -> AuditLogger:
    return request.app.state.audit_logger  # type: ignore[no-any-return]


def _get_platform_mcp_pool_service(request: Request) -> object:  # type: ignore[no-untyped-def]
    return getattr(request.app.state, "platform_mcp_pool_service", None)


def _get_agent_runtime(request: Request) -> object:  # type: ignore[no-untyped-def]
    return getattr(request.app.state, "agent_runtime", None)


async def _invalidate_platform_mcp(pool_service: object, agent_runtime: object) -> None:
    """Rebuild the platform shared pool + evict every cached agent (P1b).

    A catalog create / update / delete changes the process-global platform
    shared MCP pool, which feeds every tenant's build — so the pool is dropped
    (next build rebuilds from the catalog) and every cached built-agent is
    invalidated across all tenants.
    """
    if pool_service is not None:
        await pool_service.invalidate()  # type: ignore[attr-defined]
    if agent_runtime is not None:
        agent_runtime.invalidate_all()  # type: ignore[attr-defined]


def _bearer_secret_name(name: str) -> str:
    """SecretStore key for a platform connector's shared bearer token (slug=name)."""
    return f"helix-agent/platform/mcp/{name}/token"


def _public(record: McpConnectorCatalogRecord) -> dict[str, object]:
    """Response projection — never leak the ``secret://`` ref; expose only a flag."""
    data = record.model_dump(mode="json")
    data.pop("bearer_token_ref", None)
    data["has_bearer_token"] = record.bearer_token_ref is not None
    return data


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
        secret_store: Annotated[SecretStore, Depends(_get_secret_store)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
        pool_service: Annotated[object, Depends(_get_platform_mcp_pool_service)],
        agent_runtime: Annotated[object, Depends(_get_agent_runtime)],
    ) -> dict[str, object]:
        _require_system_admin(principal)
        upsert = payload
        if payload.bearer_token is not None:
            # Platform shared bearer (A): write the token to the SecretStore and
            # persist only the ref; the plaintext never reaches the DB / logs.
            sname = _bearer_secret_name(payload.name)
            await secret_store.put(sname, payload.bearer_token.get_secret_value())
            upsert = payload.model_copy(
                update={"bearer_token_ref": f"secret://{sname}", "bearer_token": None}
            )
        try:
            async with bypass_rls_session():
                record = await store.create(upsert=upsert, actor_id=principal.subject_id)
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
        await _invalidate_platform_mcp(pool_service, agent_runtime)
        return {"success": True, "data": _public(record), "error": None}

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
            "data": [_public(r) for r in rows],
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
        return {"success": True, "data": _public(record), "error": None}

    @router.patch("/{catalog_id}")
    async def update_catalog_entry(
        catalog_id: Annotated[UUID, Path()],
        payload: McpConnectorCatalogPatch,
        principal: Annotated[Principal, Depends(require("mcp_catalog", "write"))],
        store: Annotated[McpConnectorCatalogStore, Depends(_get_catalog_store)],
        secret_store: Annotated[SecretStore, Depends(_get_secret_store)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
        pool_service: Annotated[object, Depends(_get_platform_mcp_pool_service)],
        agent_runtime: Annotated[object, Depends(_get_agent_runtime)],
    ) -> dict[str, object]:
        _require_system_admin(principal)
        patch = payload
        if payload.bearer_token is not None:
            # Re-paste the platform shared token: resolve the entry's stable name
            # for the secret path, write the new value, persist only the ref.
            async with bypass_rls_session():
                existing = await store.get_by_id(catalog_id)
            if existing is None:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "CATALOG_NOT_FOUND", "message": "not found"},
                )
            sname = _bearer_secret_name(existing.name)
            await secret_store.put(sname, payload.bearer_token.get_secret_value())
            patch = payload.model_copy(
                update={"bearer_token_ref": f"secret://{sname}", "bearer_token": None}
            )
        try:
            async with bypass_rls_session():
                record = await store.update(catalog_id=catalog_id, patch=patch)
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
        await _invalidate_platform_mcp(pool_service, agent_runtime)
        return {"success": True, "data": _public(record), "error": None}

    @router.delete("/{catalog_id}", status_code=204)
    async def delete_catalog_entry(
        catalog_id: Annotated[UUID, Path()],
        principal: Annotated[Principal, Depends(require("mcp_catalog", "delete"))],
        store: Annotated[McpConnectorCatalogStore, Depends(_get_catalog_store)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
        pool_service: Annotated[object, Depends(_get_platform_mcp_pool_service)],
        agent_runtime: Annotated[object, Depends(_get_agent_runtime)],
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
        await _invalidate_platform_mcp(pool_service, agent_runtime)

    return router
