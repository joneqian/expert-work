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
from typing import Annotated, Any, Literal
from uuid import UUID

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
from control_plane.tenant_scope import (
    CrossTenant,
    applied_scope,
    cross_tenant_query_enabled,
    ensure_tenant_scope,
)
from helix_agent.common.observability import current_trace_id_hex
from helix_agent.common.uplift_metrics import record_manifest_provider_rejected
from helix_agent.persistence.agent_spec import AgentSpecStore, DuplicateAgentSpecError
from helix_agent.protocol import (
    AgentSpec,
    AgentSpecRecord,
    AgentSpecRevisionRecord,
    AgentSpecStatus,
    AuditAction,
    AuditResult,
    Provider,
)
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
    cross_tenant: bool = False  # Stream N — true ⇔ ?tenant_id=* response


class RevisionSummary(BaseModel):
    """One history entry, without the full spec payload (Stream HX-5).

    The list view needs actor / time / sha; the diff view fetches the
    two full snapshots it compares via ``GET .../revisions/{n}``.
    """

    model_config = ConfigDict(frozen=True)

    revision: int
    spec_sha256: str
    actor_id: str
    created_at: str


class RevisionList(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[RevisionSummary]


class RevisionDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    record: AgentSpecRevisionRecord


# ---------------------------------------------------------------------------
# Dependency injection — pulls everything from request.app.state
# ---------------------------------------------------------------------------


def _get_repo(request: Request) -> AgentSpecStore:
    return request.app.state.agent_spec_repo  # type: ignore[no-any-return]


def _get_audit(request: Request) -> AuditLogger:
    return request.app.state.audit_logger  # type: ignore[no-any-return]


def _get_loader(request: Request) -> ManifestLoader:
    return request.app.state.manifest_loader  # type: ignore[no-any-return]


def _collect_manifest_providers(spec: AgentSpec) -> set[Provider]:
    """Stream O Mini-ADR O-4 — collect every provider this manifest
    transitively references for the publish-time whitelist gate.

    Mirrors :func:`control_plane.api.tenant_config._providers_referenced_by`
    but operates on a single :class:`AgentSpec` rather than an iterable
    of stored records. Includes the primary model + its fallback chain,
    vision model + its fallbacks, and the memory_consolidation aux
    model (Sprint #7).
    """
    referenced: set[Provider] = set()
    stack = [spec.spec.model]
    if spec.spec.vision is not None:
        stack.append(spec.spec.vision.model)
        stack.extend(spec.spec.vision.fallbacks)
    consolidation = spec.spec.policies.memory_consolidation
    if consolidation.aux_model is not None:
        stack.append(consolidation.aux_model)
    while stack:
        current = stack.pop()
        referenced.add(current.provider)  # type: ignore[arg-type]
        stack.extend(current.fallback)
    return referenced


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
    # ``exc_info`` is intentionally False: passing the raw exception makes
    # CodeQL flag this site as forwarding traceback info to log handlers
    # that the API response code also touches. The exception ``type`` /
    # ``message`` already captured below give operators what they need.
    logger.info(
        "manifest.load_failed exc_type=%s",
        type(exc).__name__,
    )

    if isinstance(exc, ManifestValidationError):
        # ``exc.errors`` came from a hand-curated whitelist built inside
        # ``loader._validate`` (loc / type / msg only); no traceback or
        # Pydantic-internal data reaches the response body.
        sanitized_errors = list(exc.errors)
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "data": None,
                "error": {
                    "code": "MANIFEST_INVALID",
                    "message": "manifest failed validation",
                    "errors": sanitized_errors,
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

        # Stream O Mini-ADR O-4 — manifest publish provider whitelist gate.
        # Reject if the spec references a provider the platform does not
        # support. Runtime LLMRouter would also reject (build_llm_router
        # uses these providers), but the manifest-time gate gives a
        # clean 403 with the offending provider list rather than a
        # late agent-build error.
        #
        # Empty ``supported_providers`` = deployment hasn't opted into
        # Stream O yet (legacy / dev mode); the gate is a no-op so
        # existing manifests keep working. Operators opt in by setting
        # ``HELIX_AGENT_SUPPORTED_PROVIDERS`` env, which activates the
        # whitelist enforcement.
        settings = request.app.state.settings
        supported = set(settings.supported_providers)
        referenced = _collect_manifest_providers(spec)
        invalid = sorted(referenced - supported) if supported else []
        if invalid:
            for provider in invalid:
                record_manifest_provider_rejected(provider=provider)
            await emit(
                audit,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action=AuditAction.MANIFEST_WRITE,
                resource_type="manifest",
                resource_id=f"{spec.metadata.name}/{spec.metadata.version}",
                result=AuditResult.DENIED,
                reason="provider_not_supported",
                trace_id=trace_id,
                details={"unsupported_providers": invalid},
            )
            return _envelope_error(
                "MANIFEST_PROVIDER_NOT_SUPPORTED",
                f"manifest references providers not in the platform's "
                f"supported_providers list: {invalid}",
                403,
            )

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
            details={"spec_sha256": sha, "revision": 1},
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
        tenant_id: Annotated[UUID | Literal["*"] | None, Query()] = None,  # Stream N
    ) -> JSONResponse:
        # Stream N — resolve ``?tenant_id=`` against the caller's scope.
        # ``"*"`` requires ``is_system_admin``; non-home UUID requires
        # ``allowed_tenants`` membership. See control_plane.tenant_scope.
        scope = await ensure_tenant_scope(
            request.state.principal,
            tenant_id,
            audit,
            trace_id=current_trace_id_hex(),
            endpoint="GET /v1/agents",
            cross_tenant_enabled=cross_tenant_query_enabled(request),
        )
        async with applied_scope(scope):
            if isinstance(scope, CrossTenant):
                items = await repo.list_all_tenants(
                    status=status, name=name, limit=limit, offset=offset
                )
            else:
                items = await repo.list_by_tenant(
                    tenant_id=scope.tenant_id,
                    status=status,
                    name=name,
                    limit=limit,
                    offset=offset,
                )
        # Manifest-read audit — recorded under the actual queried tenant for
        # SingleTenant; under principal's home for CrossTenant (the cross-tenant
        # audit was already emitted by ensure_tenant_scope).
        audit_tenant = (
            request.state.principal.tenant_id if isinstance(scope, CrossTenant) else scope.tenant_id
        )
        await emit(
            audit,
            tenant_id=audit_tenant,
            actor_id=request.state.actor_id,
            action=AuditAction.MANIFEST_READ,
            resource_type="manifest",
            trace_id=current_trace_id_hex(),
            details={"count": len(items)},
        )
        payload = AgentList(
            items=items, total=len(items), cross_tenant=isinstance(scope, CrossTenant)
        )
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

        result = await repo.update_spec(
            tenant_id=tenant_id,
            name=name,
            version=version,
            spec=spec,
            spec_sha256=sha,
            updated_by=actor_id,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="agent not found")
        await emit(
            audit,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=AuditAction.MANIFEST_WRITE,
            resource_type="manifest",
            resource_id=f"{name}/{version}",
            trace_id=trace_id,
            # Stream HX-5 -- before/after pair + the history row this
            # write appended (null = same-sha no-op, nothing recorded).
            details={
                "spec_sha256": sha,
                "prev_sha256": result.prev_sha256,
                "revision": result.revision,
            },
        )
        return JSONResponse(
            {"success": True, "data": AgentDetail(record=result.record).model_dump(mode="json")}
        )

    @router.get("/{name}/{version}/revisions")
    async def list_revisions(
        name: str,
        version: str,
        request: Request,
        repo: Annotated[AgentSpecStore, Depends(_get_repo)],
        limit: int = 50,
        offset: int = 0,
    ) -> JSONResponse:
        """Stream HX-5 — revision history, newest first (summaries only)."""
        tenant_id = request.state.tenant_id
        # 404 for an unknown manifest, [] for a known one with a short
        # history window — the UI distinguishes the two.
        record = await repo.get(tenant_id=tenant_id, name=name, version=version)
        if record is None:
            raise HTTPException(status_code=404, detail="agent not found")
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        revisions = await repo.list_revisions(
            tenant_id=tenant_id, name=name, version=version, limit=limit, offset=offset
        )
        items = [
            RevisionSummary(
                revision=r.revision,
                spec_sha256=r.spec_sha256,
                actor_id=r.actor_id,
                created_at=r.created_at.isoformat(),
            )
            for r in revisions
        ]
        return JSONResponse(
            {"success": True, "data": RevisionList(items=items).model_dump(mode="json")}
        )

    @router.get("/{name}/{version}/revisions/{revision}")
    async def get_revision(
        name: str,
        version: str,
        revision: int,
        request: Request,
        repo: Annotated[AgentSpecStore, Depends(_get_repo)],
    ) -> JSONResponse:
        """Stream HX-5 — one full revision snapshot (the diff view's input)."""
        tenant_id = request.state.tenant_id
        snapshot = await repo.get_revision(
            tenant_id=tenant_id, name=name, version=version, revision=revision
        )
        if snapshot is None:
            raise HTTPException(status_code=404, detail="revision not found")
        return JSONResponse(
            {"success": True, "data": RevisionDetail(record=snapshot).model_dump(mode="json")}
        )

    @router.post("/{name}/{version}/revisions/{revision}/rollback")
    async def rollback_to_revision(
        name: str,
        version: str,
        revision: int,
        request: Request,
        repo: Annotated[AgentSpecStore, Depends(_get_repo)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
    ) -> JSONResponse:
        """Stream HX-5 (Mini-ADR HX-E2) — roll the manifest back to an
        older snapshot by *appending* a new revision with its content.

        Same write path as PUT (``update_spec``): the snapshot was
        schema-validated at write time and re-validates on read; a
        rollback to the current content is a recorded no-op.
        """
        tenant_id = request.state.tenant_id
        actor_id = request.state.actor_id
        trace_id = current_trace_id_hex()
        snapshot = await repo.get_revision(
            tenant_id=tenant_id, name=name, version=version, revision=revision
        )
        if snapshot is None:
            raise HTTPException(status_code=404, detail="revision not found")
        result = await repo.update_spec(
            tenant_id=tenant_id,
            name=name,
            version=version,
            spec=snapshot.spec,
            spec_sha256=snapshot.spec_sha256,
            updated_by=actor_id,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="agent not found")
        await emit(
            audit,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=AuditAction.MANIFEST_WRITE,
            resource_type="manifest",
            resource_id=f"{name}/{version}",
            trace_id=trace_id,
            details={
                "spec_sha256": snapshot.spec_sha256,
                "prev_sha256": result.prev_sha256,
                "revision": result.revision,
                "rolled_back_to": revision,
            },
        )
        return JSONResponse(
            {
                "success": True,
                "data": {
                    "record": AgentDetail(record=result.record).model_dump(mode="json")["record"],
                    "revision": result.revision,
                    "rolled_back_to": revision,
                },
            }
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
