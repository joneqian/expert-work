"""Tenant MCP server registration API — Stream V-C."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from control_plane.api._authz import require
from control_plane.audit import emit
from control_plane.mcp_probe import McpProbeError, probe_remote_mcp
from helix_agent.common.observability import current_trace_id_hex
from helix_agent.common.url_validation import RemoteURLError, validate_remote_url
from helix_agent.persistence import (
    TenantMcpServerAlreadyExistsError,
    TenantMcpServerNotFoundError,
    TenantMcpServerStore,
)
from helix_agent.protocol import (
    AuditAction,
    McpServerAuthType,
    McpServerTransport,
    Principal,
    TenantMcpServerPatch,
)
from helix_agent.runtime.audit.logger import AuditLogger
from helix_agent.runtime.secret_store import SecretStore, parse_secret_ref

logger = logging.getLogger("helix.control_plane.api.mcp_servers")

_DEFAULT_TIMEOUT_S = 30.0


def manifest_references_server(spec_json: Mapping[str, Any], server_name: str) -> bool:
    """Return whether an agent manifest references the named MCP server.

    Reads ``spec.tools[].servers`` from the raw manifest dict (the
    ``MCPToolSpec.servers`` field is added in V-E; pre-V-E manifests have no
    ``servers`` key, so this is dormant — and forward-compatible — until then).
    """
    spec = spec_json.get("spec")
    if not isinstance(spec, Mapping):
        return False
    tools = spec.get("tools")
    if not isinstance(tools, list):
        return False
    for tool in tools:
        if not isinstance(tool, Mapping) or tool.get("type") != "mcp":
            continue
        servers = tool.get("servers")
        if isinstance(servers, list) and server_name in servers:
            return True
    return False


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CreateMcpServerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    transport: McpServerTransport
    url: str = Field(min_length=1)
    auth_type: McpServerAuthType = "none"
    token: SecretStr | None = None
    timeout_s: float = Field(default=_DEFAULT_TIMEOUT_S, gt=0, le=300)


class UpdateMcpServerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str | None = Field(default=None, min_length=1)
    token: SecretStr | None = None
    timeout_s: float | None = Field(default=None, gt=0, le=300)
    enabled: bool | None = None


# ---------------------------------------------------------------------------
# DI accessors
# ---------------------------------------------------------------------------


def _get_store(request: Request) -> TenantMcpServerStore:
    return request.app.state.tenant_mcp_server_store  # type: ignore[no-any-return]


def _get_secret_store(request: Request) -> SecretStore:
    return request.app.state.secret_store  # type: ignore[no-any-return]


def _get_audit(request: Request) -> AuditLogger:
    return request.app.state.audit_logger  # type: ignore[no-any-return]


def _get_agent_spec_store(request: Request) -> object:
    # Wired as ``app.state.agent_spec_repo`` in create_app (Stream B.5).
    return getattr(request.app.state, "agent_spec_repo", None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _token_secret_name(tenant_id: UUID, name: str) -> str:
    return f"helix-agent/tenant/{tenant_id}/mcp/{name}/token"


def _public(record: object) -> dict[str, object]:
    # Serialize the record WITHOUT exposing the token_secret_ref — a ref
    # (not a secret value) but dropped from the public payload to keep the
    # API surface minimal.
    data: dict[str, object] = record.model_dump(mode="json")  # type: ignore[attr-defined]
    data.pop("token_secret_ref", None)
    return data


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def build_mcp_servers_router() -> APIRouter:
    router = APIRouter(prefix="/v1/mcp-servers", tags=["mcp-servers"])

    @router.post("", status_code=201)
    async def create_mcp_server(
        payload: CreateMcpServerRequest,
        principal: Annotated[Principal, Depends(require("mcp_server", "write"))],
        store: Annotated[TenantMcpServerStore, Depends(_get_store)],
        secret_store: Annotated[SecretStore, Depends(_get_secret_store)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
    ) -> dict[str, object]:
        tenant_id = principal.tenant_id
        # 1) SSRF check — fail fast with a clear error code before any I/O.
        try:
            validate_remote_url(payload.url)
        except RemoteURLError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "MCP_SERVER_INVALID_URL", "message": str(exc)},
            ) from exc
        # 2) auth_type / token consistency — reject BEFORE any I/O.
        raw_token = payload.token.get_secret_value() if payload.token is not None else None
        if payload.auth_type == "bearer" and not (raw_token and raw_token.strip()):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "MCP_SERVER_TOKEN_REQUIRED",
                    "message": "bearer auth requires a non-empty token",
                },
            )
        if payload.auth_type == "none" and payload.token is not None:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "MCP_SERVER_TOKEN_NOT_ALLOWED",
                    "message": "token must not be set when auth_type='none'",
                },
            )
        # 3) reject duplicate BEFORE probe / secret write (avoid orphan secret version).
        if await store.get(tenant_id=tenant_id, name=payload.name) is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "MCP_SERVER_DUPLICATE",
                    "message": "name already registered",
                },
            )
        # 4) connect-probe (connect + list_tools) with the raw token in memory.
        try:
            tools = await probe_remote_mcp(
                name=payload.name,
                transport=payload.transport,
                url=payload.url,
                bearer_token=raw_token,
                timeout_s=payload.timeout_s,
            )
        except McpProbeError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": exc.code, "message": exc.message},
            ) from exc
        # 5) persist token as a secret ref — only after probe success.
        # Orphan-secret note: if store.create fails after this put, the secret version is
        # orphaned — acceptable (no plaintext leak; same pattern as platform_config).
        token_secret_ref: str | None = None
        if raw_token is not None:
            sname = _token_secret_name(tenant_id, payload.name)
            await secret_store.put(sname, raw_token)
            token_secret_ref = f"secret://{sname}"
        # 6) create the DB row.
        try:
            record = await store.create(
                tenant_id=tenant_id,
                name=payload.name,
                transport=payload.transport,
                url=payload.url,
                auth_type=payload.auth_type,
                token_secret_ref=token_secret_ref,
                timeout_s=payload.timeout_s,
                created_by=principal.subject_id,
            )
        except TenantMcpServerAlreadyExistsError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "MCP_SERVER_DUPLICATE",
                    "message": "name already registered",
                },
            ) from exc
        logger.info("mcp_server.registered server=%s transport=%s", record.name, record.transport)
        await emit(
            audit,
            tenant_id=tenant_id,
            actor_id=principal.subject_id,
            action=AuditAction.MCP_SERVER_CREATE,
            resource_type="tenant_mcp_server",
            resource_id=str(record.id),
            trace_id=current_trace_id_hex(),
            details={
                "name": record.name,
                "transport": record.transport,
                "url": record.url,
                "tool_count": len(tools),
            },  # NEVER include the token
        )
        return {
            "success": True,
            "data": {**_public(record), "tool_count": len(tools)},
            "error": None,
        }

    @router.get("")
    async def list_mcp_servers(
        principal: Annotated[Principal, Depends(require("mcp_server", "read"))],
        store: Annotated[TenantMcpServerStore, Depends(_get_store)],
    ) -> dict[str, object]:
        rows = await store.list_for_tenant(tenant_id=principal.tenant_id)
        return {"success": True, "data": [_public(r) for r in rows], "error": None}

    @router.patch("/{name}")
    async def update_mcp_server(
        name: Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")],
        payload: UpdateMcpServerRequest,
        principal: Annotated[Principal, Depends(require("mcp_server", "write"))],
        store: Annotated[TenantMcpServerStore, Depends(_get_store)],
        secret_store: Annotated[SecretStore, Depends(_get_secret_store)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
    ) -> dict[str, object]:
        tenant_id = principal.tenant_id
        existing = await store.get(tenant_id=tenant_id, name=name)
        if existing is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "MCP_SERVER_NOT_FOUND", "message": "not found"},
            )
        new_url = payload.url if payload.url is not None else existing.url
        if payload.url is not None:
            try:
                validate_remote_url(new_url)
            except RemoteURLError as exc:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "MCP_SERVER_INVALID_URL", "message": str(exc)},
                ) from exc
        # Reject an explicitly-empty token rotation before any I/O.
        if payload.token is not None and not payload.token.get_secret_value().strip():
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "MCP_SERVER_TOKEN_REQUIRED",
                    "message": "token must be non-empty",
                },
            )
        # Re-probe when connectivity-affecting fields change (url or token).
        # next_token_secret_ref: will be set to the new ref only when rotating; else None
        # (TenantMcpServerPatch treats None as "leave unchanged").
        next_token_secret_ref: str | None = None
        if payload.url is not None or payload.token is not None:
            raw_token: str | None
            if payload.token is not None:
                raw_token = payload.token.get_secret_value()
            elif existing.token_secret_ref is not None:
                raw_token = await secret_store.get(parse_secret_ref(existing.token_secret_ref))
            else:
                raw_token = None
            try:
                await probe_remote_mcp(
                    name=name,
                    transport=existing.transport,
                    url=new_url,
                    bearer_token=raw_token,
                    timeout_s=(
                        payload.timeout_s if payload.timeout_s is not None else existing.timeout_s
                    ),
                )
            except McpProbeError as exc:
                raise HTTPException(
                    status_code=422,
                    detail={"code": exc.code, "message": exc.message},
                ) from exc
            if payload.token is not None and raw_token is not None:
                sname = _token_secret_name(tenant_id, name)
                await secret_store.put(sname, raw_token)  # already resolved above
                next_token_secret_ref = f"secret://{sname}"
        patch = TenantMcpServerPatch(
            url=payload.url,
            token_secret_ref=(next_token_secret_ref if payload.token is not None else None),
            timeout_s=payload.timeout_s,
            enabled=payload.enabled,
        )
        try:
            record = await store.update(tenant_id=tenant_id, name=name, patch=patch)
        except TenantMcpServerNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "MCP_SERVER_NOT_FOUND", "message": "not found"},
            ) from exc
        await emit(
            audit,
            tenant_id=tenant_id,
            actor_id=principal.subject_id,
            action=AuditAction.MCP_SERVER_UPDATE,
            resource_type="tenant_mcp_server",
            resource_id=str(record.id),
            trace_id=current_trace_id_hex(),
            details={"name": record.name, "url": record.url, "enabled": record.enabled},
        )
        return {"success": True, "data": _public(record), "error": None}

    @router.delete("/{name}", status_code=204)
    async def delete_mcp_server(
        name: Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")],
        principal: Annotated[Principal, Depends(require("mcp_server", "delete"))],
        store: Annotated[TenantMcpServerStore, Depends(_get_store)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
        agent_spec_store: Annotated[object, Depends(_get_agent_spec_store)],
    ) -> None:
        tenant_id = principal.tenant_id
        # (a) Resolve row first so we have the UUID for the audit record and a clean 404.
        record = await store.get(tenant_id=tenant_id, name=name)
        if record is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "MCP_SERVER_NOT_FOUND", "message": "not found"},
            )
        # (b) Reference check: refuse if any active agent manifest references this server.
        # agent_spec_repo may be None in minimal deployments (no spec store wired).
        if agent_spec_store is not None:
            specs = await agent_spec_store.list_by_tenant(  # type: ignore[attr-defined]
                tenant_id=tenant_id, limit=1000
            )
            # AgentSpecRecord.spec is an AgentSpec object — convert to dict for the
            # manifest_references_server helper which reads raw manifest dicts.
            referencing = [
                s.name
                for s in specs
                if manifest_references_server(s.spec.model_dump(mode="json"), name)
            ]
            if referencing:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "MCP_SERVER_IN_USE",
                        "message": (
                            f"referenced by agent(s): {', '.join(sorted(set(referencing)))}"
                        ),
                    },
                )
        # (c) Delete the row.
        await store.delete(tenant_id=tenant_id, name=name)
        # (d) Audit with the row UUID (consistent with POST/PATCH), name in details.
        await emit(
            audit,
            tenant_id=tenant_id,
            actor_id=principal.subject_id,
            action=AuditAction.MCP_SERVER_DELETE,
            resource_type="tenant_mcp_server",
            resource_id=str(record.id),
            trace_id=current_trace_id_hex(),
            details={"name": name},
        )

    return router
