"""``/v1/agents`` CRUD — Stream B.5.

Wraps :class:`AgentSpecStore` plus :class:`ManifestLoader` (B.4) and
emits ``manifest:{read,write,delete}`` audit records on every mutation
via the per-request :class:`AuditLogger`.

Body shape: the create / update endpoints accept ``{"manifest_yaml":
"...", "template_vars": {...}}``. The control-plane never accepts a
pre-parsed AgentSpec — round-tripping YAML keeps lint enforcement at
the boundary.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from control_plane.audit import emit
from control_plane.manifest import (
    ManifestError,
    ManifestLoader,
    ManifestSyntaxError,
    ManifestTemplateError,
    ManifestValidationError,
)
from helix_agent.common.observability import current_trace_id_hex
from helix_agent.persistence.agent_spec import AgentSpecStore, DuplicateAgentSpecError
from helix_agent.protocol import AgentSpecRecord, AgentSpecStatus, AuditAction, AuditResult
from helix_agent.runtime.audit.logger import AuditLogger

logger = logging.getLogger("helix.control_plane.agents")


class ManifestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_yaml: str = Field(min_length=1)
    template_vars: dict[str, Any] | None = None


class AgentDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    record: AgentSpecRecord


class AgentList(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[AgentSpecRecord]
    total: int


# ---------------------------------------------------------------------------
# Dependency injection — pulls everything from request.app.state
# ---------------------------------------------------------------------------


def _get_repo(request: Request) -> AgentSpecStore:
    return request.app.state.agent_spec_repo  # type: ignore[no-any-return]


def _get_audit(request: Request) -> AuditLogger:
    return request.app.state.audit_logger  # type: ignore[no-any-return]


def _get_loader(request: Request) -> ManifestLoader:
    return request.app.state.manifest_loader  # type: ignore[no-any-return]


def _envelope_error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "data": None,
            "error": {"code": code, "message": message},
        },
    )


def _spec_sha256(spec_json: Mapping[str, Any]) -> str:
    import json

    canonical = json.dumps(spec_json, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _load_manifest(
    payload: ManifestPayload,
    loader: ManifestLoader,
) -> tuple[Any, str]:
    """Parse the request body into an ``AgentSpec`` + canonical sha256."""
    spec = loader.load_from_string(
        payload.manifest_yaml,
        template_vars=payload.template_vars,
    )
    spec_json = spec.model_dump(by_alias=True, mode="json")
    return spec, _spec_sha256(spec_json)


def _manifest_error_to_response(exc: ManifestError) -> JSONResponse:
    """Map a parse / lint error to the public envelope.

    The raw exception text is logged server-side but **never** echoed to
    the API caller (CodeQL ``py/stack-trace-exposure``). The structured
    ``exc.errors`` list from :class:`ManifestValidationError` is field-
    level info we have already produced ourselves, so it's safe to
    surface.
    """
    logger.info("manifest.load_failed exc_type=%s", type(exc).__name__, exc_info=exc)

    if isinstance(exc, ManifestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "data": None,
                "error": {
                    "code": "MANIFEST_INVALID",
                    "message": "manifest failed validation",
                    "errors": exc.errors,
                },
            },
        )
    if isinstance(exc, ManifestTemplateError):
        return _envelope_error(
            "MANIFEST_TEMPLATE",
            "manifest template rendering failed",
            400,
        )
    if isinstance(exc, ManifestSyntaxError):
        return _envelope_error("MANIFEST_SYNTAX", "manifest is not valid YAML", 400)
    return _envelope_error("MANIFEST_ERROR", "manifest could not be parsed", 400)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def build_agents_router() -> APIRouter:
    router = APIRouter(prefix="/v1/agents", tags=["agents"])

    @router.post("", status_code=201)
    async def create_agent(
        payload: ManifestPayload,
        request: Request,
        repo: Annotated[AgentSpecStore, Depends(_get_repo)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
        loader: Annotated[ManifestLoader, Depends(_get_loader)],
    ) -> JSONResponse:
        tenant_id = request.state.tenant_id
        actor_id = request.state.actor_id
        trace_id = current_trace_id_hex()

        try:
            spec, sha = await _load_manifest(payload, loader)
        except ManifestError as exc:
            await emit(
                audit,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action=AuditAction.MANIFEST_WRITE,
                resource_type="manifest",
                resource_id=None,
                result=AuditResult.ERROR,
                reason=type(exc).__name__,
                trace_id=trace_id,
            )
            return _manifest_error_to_response(exc)

        try:
            record = await repo.create(
                tenant_id=tenant_id,
                spec=spec,
                spec_sha256=sha,
                created_by=actor_id,
            )
        except DuplicateAgentSpecError:
            logger.info(
                "manifest.create_duplicate name=%s version=%s",
                spec.metadata.name,
                spec.metadata.version,
            )
            await emit(
                audit,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action=AuditAction.MANIFEST_WRITE,
                resource_type="manifest",
                resource_id=f"{spec.metadata.name}/{spec.metadata.version}",
                result=AuditResult.ERROR,
                reason="duplicate",
                trace_id=trace_id,
            )
            return _envelope_error(
                "MANIFEST_DUPLICATE",
                "an agent with this name and version already exists",
                409,
            )

        await emit(
            audit,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=AuditAction.MANIFEST_WRITE,
            resource_type="manifest",
            resource_id=f"{record.name}/{record.version}",
            trace_id=trace_id,
            details={"spec_sha256": sha},
        )
        return JSONResponse(
            status_code=201,
            content={"success": True, "data": AgentDetail(record=record).model_dump(mode="json")},
        )

    @router.get("")
    async def list_agents(
        request: Request,
        repo: Annotated[AgentSpecStore, Depends(_get_repo)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
        status: AgentSpecStatus | None = None,
        name: str | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> JSONResponse:
        tenant_id = request.state.tenant_id
        items = await repo.list_by_tenant(
            tenant_id=tenant_id,
            status=status,
            name=name,
            limit=limit,
            offset=offset,
        )
        await emit(
            audit,
            tenant_id=tenant_id,
            actor_id=request.state.actor_id,
            action=AuditAction.MANIFEST_READ,
            resource_type="manifest",
            trace_id=current_trace_id_hex(),
            details={"count": len(items)},
        )
        payload = AgentList(items=items, total=len(items))
        return JSONResponse({"success": True, "data": payload.model_dump(mode="json")})

    @router.get("/{name}/{version}")
    async def get_agent(
        name: str,
        version: str,
        request: Request,
        repo: Annotated[AgentSpecStore, Depends(_get_repo)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
    ) -> JSONResponse:
        tenant_id = request.state.tenant_id
        record = await repo.get(tenant_id=tenant_id, name=name, version=version)
        if record is None:
            raise HTTPException(status_code=404, detail="agent not found")
        await emit(
            audit,
            tenant_id=tenant_id,
            actor_id=request.state.actor_id,
            action=AuditAction.MANIFEST_READ,
            resource_type="manifest",
            resource_id=f"{name}/{version}",
            trace_id=current_trace_id_hex(),
        )
        return JSONResponse(
            {"success": True, "data": AgentDetail(record=record).model_dump(mode="json")}
        )

    @router.put("/{name}/{version}")
    async def update_agent(
        name: str,
        version: str,
        payload: ManifestPayload,
        request: Request,
        repo: Annotated[AgentSpecStore, Depends(_get_repo)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
        loader: Annotated[ManifestLoader, Depends(_get_loader)],
    ) -> JSONResponse:
        tenant_id = request.state.tenant_id
        actor_id = request.state.actor_id
        trace_id = current_trace_id_hex()
        try:
            spec, sha = await _load_manifest(payload, loader)
        except ManifestError as exc:
            return _manifest_error_to_response(exc)

        if spec.metadata.name != name or spec.metadata.version != version:
            return _envelope_error(
                "MANIFEST_PATH_MISMATCH",
                f"path is {name}/{version} but manifest metadata is "
                f"{spec.metadata.name}/{spec.metadata.version}",
                422,
            )

        record = await repo.update_spec(
            tenant_id=tenant_id,
            name=name,
            version=version,
            spec=spec,
            spec_sha256=sha,
            updated_by=actor_id,
        )
        if record is None:
            raise HTTPException(status_code=404, detail="agent not found")
        await emit(
            audit,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=AuditAction.MANIFEST_WRITE,
            resource_type="manifest",
            resource_id=f"{name}/{version}",
            trace_id=trace_id,
            details={"spec_sha256": sha},
        )
        return JSONResponse(
            {"success": True, "data": AgentDetail(record=record).model_dump(mode="json")}
        )

    @router.delete("/{name}/{version}", status_code=204)
    async def delete_agent(
        name: str,
        version: str,
        request: Request,
        repo: Annotated[AgentSpecStore, Depends(_get_repo)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
    ) -> JSONResponse:
        tenant_id = request.state.tenant_id
        record = await repo.update_status(
            tenant_id=tenant_id,
            name=name,
            version=version,
            status=AgentSpecStatus.DELETED,
        )
        if record is None:
            raise HTTPException(status_code=404, detail="agent not found")
        await emit(
            audit,
            tenant_id=tenant_id,
            actor_id=request.state.actor_id,
            action=AuditAction.MANIFEST_DELETE,
            resource_type="manifest",
            resource_id=f"{name}/{version}",
            trace_id=current_trace_id_hex(),
        )
        return JSONResponse(status_code=204, content=None)

    return router
