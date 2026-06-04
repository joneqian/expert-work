"""``/v1/platform/skills`` — Stream X (X4) platform skill library CRUD.

system_admin-only CRUD over the platform-curated **NULL-tenant** skill
library that the X-6 merged ``GET /v1/skills`` view surfaces to tenants.
Every handler:

* resolves the principal from ``request.state.principal`` (skills have no
  RBAC resource, so there is no ``require(...)`` dependency to lean on) and
  re-checks ``principal.is_system_admin`` inline via
  ``_require_system_admin`` — same 403 ``PLATFORM_SCOPE_FORBIDDEN`` shape
  as ``platform_config.py`` / ``mcp_catalog.py``;
* drives every store call inside ``bypass_rls_session()`` because the rows
  are tenant-less and the RLS policy would otherwise hide them from a
  normally-scoped session (the W-8 trap);
* tags audit details with ``{"scope": "platform", ...}`` (Mini-ADR X-7),
  reusing the existing ``SKILL_*`` audit actions — never a new ``AuditAction``
  value (avoids the protocol+control-plane double-Literal drift), and
  **never** the skill ``prompt_fragment`` body (metadata only).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from control_plane.api._skill_moderation import (
    ModerationError,
    moderate_prompt_fragment,
    moderate_required_models,
    moderate_tool_names,
)
from control_plane.api.skills import _get_audit, _get_skill_store, _skill_dict, _version_dict
from control_plane.audit import emit as audit_emit
from control_plane.tenant_scope import bypass_rls_session
from helix_agent.common.observability import current_trace_id_hex
from helix_agent.common.threat_patterns import scan_for_threats
from helix_agent.common.uplift_metrics import (
    record_skill_blocked,
    record_threat_pattern_hits,
)
from helix_agent.persistence import (
    DuplicateSkillError,
    SkillNotFoundError,
)
from helix_agent.protocol import (
    AuditAction,
    AuditResult,
    Principal,
    SkillStatus,
    TenantPlan,
)
from helix_agent.protocol.skill import compute_content_hash, is_high_risk_skill_version


class _CreatePlatformSkillBody(BaseModel):
    """``POST /v1/platform/skills`` request body."""

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    description: str = Field(default="", max_length=1024)
    category: str | None = Field(default=None, max_length=64)
    required_tier: TenantPlan = TenantPlan.FREE


class _AddPlatformVersionBody(BaseModel):
    """``POST /v1/platform/skills/{id}/versions`` request body."""

    prompt_fragment: str = Field(min_length=1)
    tool_names: list[str] = Field(default_factory=list)
    description: str = Field(default="", max_length=1024)
    category: str | None = Field(default=None, max_length=64)
    required_models: list[str] = Field(default_factory=list)
    authored_by: str = Field(default="human", pattern=r"^(human|agent)$")


class _PatchPlatformSkillBody(BaseModel):
    """``PATCH /v1/platform/skills/{id}`` request body.

    X4 supports ``status`` + ``pinned`` only; ``required_tier`` is set at
    create time (mutating it later is a follow-up — this API-only PR adds
    no new store method).
    """

    status: SkillStatus | None = None
    pinned: bool | None = None


def _require_system_admin(principal: Principal) -> None:
    if not principal.is_system_admin:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "PLATFORM_SCOPE_FORBIDDEN",
                "message": "only a system admin may manage the platform skill library",
            },
        )


def _principal(request: Request) -> Principal:
    principal: Principal | None = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "PLATFORM_SCOPE_FORBIDDEN",
                "message": "only a system admin may manage the platform skill library",
            },
        )
    _require_system_admin(principal)
    return principal


def build_platform_skills_router() -> APIRouter:
    """Stream X (X4) platform skill library CRUD router."""
    router = APIRouter(prefix="/v1/platform/skills", tags=["platform_skills"])

    @router.post("", response_model=None)
    async def create_platform_skill(
        body: _CreatePlatformSkillBody,
        request: Request,
    ) -> JSONResponse:
        principal = _principal(request)
        store = _get_skill_store(request)
        audit = _get_audit(request)
        try:
            async with bypass_rls_session():
                skill = await store.create_platform_skill(
                    skill_id=uuid4(),
                    name=body.name,
                    description=body.description,
                    category=body.category,
                    required_tier=body.required_tier,
                )
        except DuplicateSkillError as exc:
            raise HTTPException(
                status_code=409,
                detail=f"platform skill {body.name!r} already exists",
            ) from exc

        await audit_emit(
            audit,
            tenant_id=principal.tenant_id,
            actor_id=principal.subject_id,
            action=AuditAction.SKILL_CREATE,
            resource_type="skill",
            resource_id=str(skill.id),
            result=AuditResult.SUCCESS,
            trace_id=current_trace_id_hex(),
            details={
                "scope": "platform",
                "name": skill.name,
                "category": skill.category,
                "required_tier": skill.required_tier.value,
            },
        )
        return JSONResponse(status_code=201, content=_skill_dict(skill))

    @router.post("/{skill_id}/versions", response_model=None)
    async def add_platform_version(
        skill_id: Annotated[UUID, Path()],
        body: _AddPlatformVersionBody,
        request: Request,
    ) -> JSONResponse:
        principal = _principal(request)
        store = _get_skill_store(request)
        audit = _get_audit(request)

        # Same moderation gate as the tenant add-version path.
        try:
            moderate_prompt_fragment(body.prompt_fragment)
            moderate_tool_names(body.tool_names)
            moderate_required_models(body.required_models)
        except ModerationError as exc:
            raise HTTPException(status_code=400, detail=exc.detail) from exc

        # Mini-ADR U-21 write-time strict scan on prompt_fragment.
        findings = scan_for_threats(body.prompt_fragment, scope="strict")
        if findings:
            record_threat_pattern_hits(findings, scope="strict")
            record_skill_blocked(phase="supporting_file_api")
            await audit_emit(
                audit,
                tenant_id=principal.tenant_id,
                actor_id=principal.subject_id,
                action=AuditAction.SKILL_PROMPT_INJECTION_BLOCKED,
                resource_type="skill",
                resource_id=str(skill_id),
                result=AuditResult.DENIED,
                trace_id=current_trace_id_hex(),
                details={
                    "scope": "platform",
                    "finding_count": len(findings),
                    "findings": [
                        {"pattern_id": f.pattern_id, "category": f.category} for f in findings
                    ],
                    "source": "json_api",
                },
            )
            raise HTTPException(status_code=400, detail="invalid skill content")

        # Mini-ADR U-21 / U-24 — content_hash + high_risk at write time.
        content_hash = compute_content_hash(body.prompt_fragment, {})
        high_risk = is_high_risk_skill_version(tool_names=body.tool_names, supporting_file_paths=[])

        try:
            async with bypass_rls_session():
                version = await store.add_platform_version(
                    version_id=uuid4(),
                    skill_id=skill_id,
                    prompt_fragment=body.prompt_fragment,
                    tool_names=body.tool_names,
                    description=body.description,
                    category=body.category,
                    required_models=body.required_models,
                    authored_by=body.authored_by,
                    content_hash=content_hash,
                    high_risk=high_risk,
                )
        except SkillNotFoundError as exc:
            raise HTTPException(status_code=404, detail="skill not found") from exc

        await audit_emit(
            audit,
            tenant_id=principal.tenant_id,
            actor_id=principal.subject_id,
            action=AuditAction.SKILL_VERSION_CREATE,
            resource_type="skill",
            resource_id=str(skill_id),
            result=AuditResult.SUCCESS,
            trace_id=current_trace_id_hex(),
            details={
                "scope": "platform",
                "version": version.version,
                "tool_names": list(version.tool_names),
                "source": "json_api",
            },
        )
        return JSONResponse(status_code=201, content=_version_dict(version))

    @router.patch("/{skill_id}", response_model=None)
    async def patch_platform_skill(
        skill_id: Annotated[UUID, Path()],
        body: _PatchPlatformSkillBody,
        request: Request,
    ) -> JSONResponse:
        principal = _principal(request)
        store = _get_skill_store(request)
        audit = _get_audit(request)
        if body.status is None and body.pinned is None:
            raise HTTPException(
                status_code=422,
                detail="patch body must set at least one of: status, pinned",
            )

        async with bypass_rls_session():
            prior = await store.get_platform_skill(skill_id=skill_id)
        if prior is None:
            raise HTTPException(status_code=404, detail="skill not found")

        updated = prior
        if body.status is not None:
            try:
                async with bypass_rls_session():
                    updated = await store.set_platform_status(skill_id=skill_id, status=body.status)
            except SkillNotFoundError as exc:
                raise HTTPException(status_code=404, detail="skill not found") from exc
            await audit_emit(
                audit,
                tenant_id=principal.tenant_id,
                actor_id=principal.subject_id,
                action=AuditAction.SKILL_STATUS_CHANGE,
                resource_type="skill",
                resource_id=str(skill_id),
                result=AuditResult.SUCCESS,
                trace_id=current_trace_id_hex(),
                details={
                    "scope": "platform",
                    "from": prior.status.value,
                    "to": updated.status.value,
                },
            )

        if body.pinned is not None and body.pinned != updated.pinned:
            try:
                async with bypass_rls_session():
                    updated = await store.set_platform_pinned(skill_id=skill_id, pinned=body.pinned)
            except SkillNotFoundError as exc:
                raise HTTPException(status_code=404, detail="skill not found") from exc
            await audit_emit(
                audit,
                tenant_id=principal.tenant_id,
                actor_id=principal.subject_id,
                action=(AuditAction.SKILL_PINNED if body.pinned else AuditAction.SKILL_UNPINNED),
                resource_type="skill",
                resource_id=str(skill_id),
                result=AuditResult.SUCCESS,
                trace_id=current_trace_id_hex(),
                details={"scope": "platform", "pinned": body.pinned},
            )

        return JSONResponse(status_code=200, content=_skill_dict(updated))

    @router.get("", response_model=None)
    async def list_platform_skills(
        request: Request,
        status: Annotated[SkillStatus | None, Query()] = None,
        category: Annotated[str | None, Query()] = None,
        cursor: Annotated[UUID | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> JSONResponse:
        _principal(request)
        store = _get_skill_store(request)
        async with bypass_rls_session():
            rows, next_cursor = await store.list_platform_skills(
                status=status, category=category, cursor=cursor, limit=limit
            )
        return JSONResponse(
            status_code=200,
            content={
                "items": [_skill_dict(r) for r in rows],
                "next_cursor": str(next_cursor) if next_cursor is not None else None,
            },
        )

    @router.get("/{skill_id}", response_model=None)
    async def get_platform_skill(
        skill_id: Annotated[UUID, Path()],
        request: Request,
    ) -> JSONResponse:
        _principal(request)
        store = _get_skill_store(request)
        async with bypass_rls_session():
            skill = await store.get_platform_skill(skill_id=skill_id)
        if skill is None:
            raise HTTPException(status_code=404, detail="skill not found")
        return JSONResponse(status_code=200, content=_skill_dict(skill))

    @router.get("/{skill_id}/versions", response_model=None)
    async def list_platform_versions(
        skill_id: Annotated[UUID, Path()],
        request: Request,
    ) -> JSONResponse:
        _principal(request)
        store = _get_skill_store(request)
        async with bypass_rls_session():
            skill = await store.get_platform_skill(skill_id=skill_id)
            if skill is None:
                raise HTTPException(status_code=404, detail="skill not found")
            versions = await store.list_platform_versions(skill_id=skill_id)
        return JSONResponse(
            status_code=200, content={"items": [_version_dict(v) for v in versions]}
        )

    @router.get("/{skill_id}/versions/{version_number}", response_model=None)
    async def get_platform_version(
        skill_id: Annotated[UUID, Path()],
        version_number: int,
        request: Request,
    ) -> JSONResponse:
        _principal(request)
        store = _get_skill_store(request)
        async with bypass_rls_session():
            version = await store.get_platform_version_by_number(
                skill_id=skill_id, version=version_number
            )
        if version is None:
            raise HTTPException(status_code=404, detail="skill version not found")
        return JSONResponse(status_code=200, content=_version_dict(version))

    return router
