"""``/v1/skills`` — Stream J.7a admin CRUD + ZIP import/export.

Mini-ADR J-23 § 15.5 endpoints:

* ``POST   /v1/skills``                                   create skill (draft)
* ``POST   /v1/skills/{id}/versions``                     append version
* ``PATCH  /v1/skills/{id}``                              draft|active|archived
* ``GET    /v1/skills?status=&category=&cursor=&limit=``  list (cursor paging)
* ``GET    /v1/skills/{id}``                              get one
* ``GET    /v1/skills/{id}/versions``                     list versions
* ``GET    /v1/skills/{id}/versions/{n}``                 get single version
* ``POST   /v1/skills/import``                            multipart .skill ZIP
* ``GET    /v1/skills/{id}/versions/{n}/export``          download ZIP

All write paths pass content through the regex deny-list moderation
(``_skill_moderation``); all ZIP paths go through the size + zip-slip
guards in ``_skill_zip``. Tenant scoping is at the request layer
(``request.state.tenant_id``); RLS at the SQL layer is the second
safety net.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from control_plane.api._skill_moderation import (
    ModerationError,
    moderate_prompt_fragment,
    moderate_required_models,
    moderate_tool_names,
)
from control_plane.api._skill_zip import (
    SkillZipError,
    build_skill_zip,
    parse_skill_zip,
)
from control_plane.audit import emit as audit_emit
from control_plane.tenant_scope import CrossTenant, applied_scope, ensure_tenant_scope
from helix_agent.common.observability import current_trace_id_hex
from helix_agent.persistence import (
    DuplicateSkillError,
    SkillNotFoundError,
    SkillStore,
)
from helix_agent.protocol import (
    SKILL_REF_PATTERN,
    AuditAction,
    AuditResult,
    Skill,
    SkillStatus,
    SkillVersion,
)
from helix_agent.runtime.audit.logger import AuditLogger

logger = logging.getLogger("helix.control_plane.skills")


class _CreateSkillBody(BaseModel):
    """``POST /v1/skills`` request body."""

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    description: str = Field(default="", max_length=1024)
    category: str | None = Field(default=None, max_length=64)


class _AddVersionBody(BaseModel):
    """``POST /v1/skills/{id}/versions`` request body."""

    prompt_fragment: str = Field(min_length=1)
    tool_names: list[str] = Field(default_factory=list)
    description: str = Field(default="", max_length=1024)
    category: str | None = Field(default=None, max_length=64)
    required_models: list[str] = Field(default_factory=list)
    authored_by: str = Field(default="human", pattern=r"^(human|agent)$")


class _PatchStatusBody(BaseModel):
    """``PATCH /v1/skills/{id}`` request body."""

    status: SkillStatus


def _get_skill_store(request: Request) -> SkillStore:
    return request.app.state.skill_store  # type: ignore[no-any-return]


def _get_audit(request: Request) -> AuditLogger:
    return request.app.state.audit_logger  # type: ignore[no-any-return]


def _skill_dict(skill: Skill) -> dict[str, Any]:
    return {
        "id": str(skill.id),
        "name": skill.name,
        "status": skill.status.value,
        "latest_version": skill.latest_version,
        "description": skill.description,
        "category": skill.category,
        "created_at": skill.created_at.isoformat(),
        "updated_at": skill.updated_at.isoformat(),
    }


def _version_dict(version: SkillVersion) -> dict[str, Any]:
    return {
        "id": str(version.id),
        "skill_id": str(version.skill_id),
        "version": version.version,
        "prompt_fragment": version.prompt_fragment,
        "tool_names": list(version.tool_names),
        "description": version.description,
        "category": version.category,
        "required_models": list(version.required_models),
        "authored_by": version.authored_by,
        "created_at": version.created_at.isoformat(),
    }


def build_skills_router() -> APIRouter:
    """Stream J.7a admin CRUD + ZIP import/export router."""
    router = APIRouter(prefix="/v1/skills", tags=["skills"])

    @router.post("", response_model=None)
    async def create_skill(
        body: _CreateSkillBody,
        request: Request,
        store: Annotated[SkillStore, Depends(_get_skill_store)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
    ) -> JSONResponse:
        tenant_id: UUID = request.state.tenant_id
        actor_id: str = getattr(request.state, "actor_id", "anonymous")
        try:
            skill = await store.create_skill(
                skill_id=uuid4(),
                tenant_id=tenant_id,
                name=body.name,
                description=body.description,
                category=body.category,
            )
        except DuplicateSkillError as exc:
            raise HTTPException(
                status_code=409,
                detail=f"skill {body.name!r} already exists for this tenant",
            ) from exc

        await audit_emit(
            audit,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=AuditAction.SKILL_CREATE,
            resource_type="skill",
            resource_id=str(skill.id),
            result=AuditResult.SUCCESS,
            trace_id=current_trace_id_hex(),
            details={"name": skill.name, "category": skill.category},
        )
        return JSONResponse(status_code=201, content=_skill_dict(skill))

    @router.post("/{skill_id}/versions", response_model=None)
    async def add_version(
        skill_id: UUID,
        body: _AddVersionBody,
        request: Request,
        store: Annotated[SkillStore, Depends(_get_skill_store)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
    ) -> JSONResponse:
        tenant_id: UUID = request.state.tenant_id
        actor_id: str = getattr(request.state, "actor_id", "anonymous")

        # Mini-ADR J-23 § 15.6 admin moderation.
        try:
            moderate_prompt_fragment(body.prompt_fragment)
            moderate_tool_names(body.tool_names)
            moderate_required_models(body.required_models)
        except ModerationError as exc:
            raise HTTPException(status_code=400, detail=exc.detail) from exc

        try:
            version = await store.add_version(
                version_id=uuid4(),
                skill_id=skill_id,
                tenant_id=tenant_id,
                prompt_fragment=body.prompt_fragment,
                tool_names=body.tool_names,
                description=body.description,
                category=body.category,
                required_models=body.required_models,
                authored_by=body.authored_by,
            )
        except SkillNotFoundError as exc:
            raise HTTPException(status_code=404, detail="skill not found") from exc

        await audit_emit(
            audit,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=AuditAction.SKILL_VERSION_CREATE,
            resource_type="skill",
            resource_id=str(skill_id),
            result=AuditResult.SUCCESS,
            trace_id=current_trace_id_hex(),
            details={
                "version": version.version,
                "tool_names": list(version.tool_names),
                "source": "json_api",
            },
        )
        return JSONResponse(status_code=201, content=_version_dict(version))

    @router.patch("/{skill_id}", response_model=None)
    async def patch_status(
        skill_id: UUID,
        body: _PatchStatusBody,
        request: Request,
        store: Annotated[SkillStore, Depends(_get_skill_store)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
    ) -> JSONResponse:
        tenant_id: UUID = request.state.tenant_id
        actor_id: str = getattr(request.state, "actor_id", "anonymous")
        prior = await store.get_skill(skill_id=skill_id, tenant_id=tenant_id)
        if prior is None:
            raise HTTPException(status_code=404, detail="skill not found")
        try:
            updated = await store.set_status(
                skill_id=skill_id, tenant_id=tenant_id, status=body.status
            )
        except SkillNotFoundError as exc:
            raise HTTPException(status_code=404, detail="skill not found") from exc

        await audit_emit(
            audit,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=AuditAction.SKILL_STATUS_CHANGE,
            resource_type="skill",
            resource_id=str(skill_id),
            result=AuditResult.SUCCESS,
            trace_id=current_trace_id_hex(),
            details={"from": prior.status.value, "to": updated.status.value},
        )
        return JSONResponse(status_code=200, content=_skill_dict(updated))

    @router.get("", response_model=None)
    async def list_skills(
        request: Request,
        store: Annotated[SkillStore, Depends(_get_skill_store)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
        status: Annotated[SkillStatus | None, Query()] = None,
        category: Annotated[str | None, Query()] = None,
        cursor: Annotated[UUID | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        tenant_id: Annotated[UUID | Literal["*"] | None, Query()] = None,  # Stream N
    ) -> JSONResponse:
        scope = await ensure_tenant_scope(
            request.state.principal,
            tenant_id,
            audit,
            trace_id=current_trace_id_hex(),
            endpoint="GET /v1/skills",
        )
        async with applied_scope(scope):
            if isinstance(scope, CrossTenant):
                rows, next_cursor = await store.list_skills_all_tenants(
                    status=status, category=category, cursor=cursor, limit=limit
                )
            else:
                rows, next_cursor = await store.list_skills(
                    tenant_id=scope.tenant_id,
                    status=status,
                    category=category,
                    cursor=cursor,
                    limit=limit,
                )
        return JSONResponse(
            status_code=200,
            content={
                "items": [_skill_dict(r) for r in rows],
                "next_cursor": str(next_cursor) if next_cursor is not None else None,
                "cross_tenant": isinstance(scope, CrossTenant),
            },
        )

    @router.get("/{skill_id}", response_model=None)
    async def get_skill(
        skill_id: UUID,
        request: Request,
        store: Annotated[SkillStore, Depends(_get_skill_store)],
    ) -> JSONResponse:
        tenant_id: UUID = request.state.tenant_id
        skill = await store.get_skill(skill_id=skill_id, tenant_id=tenant_id)
        if skill is None:
            raise HTTPException(status_code=404, detail="skill not found")
        return JSONResponse(status_code=200, content=_skill_dict(skill))

    @router.get("/{skill_id}/versions", response_model=None)
    async def list_versions(
        skill_id: UUID,
        request: Request,
        store: Annotated[SkillStore, Depends(_get_skill_store)],
    ) -> JSONResponse:
        tenant_id: UUID = request.state.tenant_id
        skill = await store.get_skill(skill_id=skill_id, tenant_id=tenant_id)
        if skill is None:
            raise HTTPException(status_code=404, detail="skill not found")
        versions = await store.list_versions(skill_id=skill_id, tenant_id=tenant_id)
        return JSONResponse(
            status_code=200, content={"items": [_version_dict(v) for v in versions]}
        )

    @router.get("/{skill_id}/versions/{version_number}", response_model=None)
    async def get_version(
        skill_id: UUID,
        version_number: int,
        request: Request,
        store: Annotated[SkillStore, Depends(_get_skill_store)],
    ) -> JSONResponse:
        tenant_id: UUID = request.state.tenant_id
        version = await store.get_version_by_number(
            skill_id=skill_id, tenant_id=tenant_id, version=version_number
        )
        if version is None:
            raise HTTPException(status_code=404, detail="skill version not found")
        return JSONResponse(status_code=200, content=_version_dict(version))

    @router.post("/import", response_model=None)
    async def import_skill(
        request: Request,
        file: Annotated[UploadFile, File()],
        store: Annotated[SkillStore, Depends(_get_skill_store)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
    ) -> JSONResponse:
        """Multipart ``.skill`` ZIP — create skill (if absent) + first
        version, OR add a version to an existing skill of the same name."""
        tenant_id: UUID = request.state.tenant_id
        actor_id: str = getattr(request.state, "actor_id", "anonymous")
        blob = await file.read()
        try:
            payload = parse_skill_zip(blob)
        except SkillZipError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        # Validate name against the same regex AgentSpec.skills uses,
        # so a bad-name ZIP cannot create an unreferenceable skill.
        import re

        if not re.fullmatch(r"^[a-z][a-z0-9_-]{0,63}$", payload.name):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"skill name {payload.name!r} fails validation "
                    f"({SKILL_REF_PATTERN} without @version suffix)"
                ),
            )

        # Moderation gate before any DB write.
        try:
            moderate_prompt_fragment(payload.prompt_fragment)
            moderate_tool_names(payload.tool_names)
            moderate_required_models(payload.required_models)
        except ModerationError as exc:
            raise HTTPException(status_code=400, detail=exc.detail) from exc

        existing = await store.get_skill_by_name(tenant_id=tenant_id, name=payload.name)
        if existing is None:
            try:
                existing = await store.create_skill(
                    skill_id=uuid4(),
                    tenant_id=tenant_id,
                    name=payload.name,
                    description=payload.description,
                    category=payload.category,
                )
            except DuplicateSkillError as exc:
                # Race — another import won the create; resolve + add version.
                logger.info("skills.import_race name=%s", payload.name)
                existing = await store.get_skill_by_name(tenant_id=tenant_id, name=payload.name)
                if existing is None:
                    raise HTTPException(status_code=409, detail=str(exc)) from exc
            await audit_emit(
                audit,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action=AuditAction.SKILL_CREATE,
                resource_type="skill",
                resource_id=str(existing.id),
                result=AuditResult.SUCCESS,
                trace_id=current_trace_id_hex(),
                details={
                    "name": existing.name,
                    "category": existing.category,
                    "source": "zip_import",
                },
            )

        version = await store.add_version(
            version_id=uuid4(),
            skill_id=existing.id,
            tenant_id=tenant_id,
            prompt_fragment=payload.prompt_fragment,
            tool_names=payload.tool_names,
            description=payload.description,
            category=payload.category,
            required_models=payload.required_models,
            authored_by="human",
        )
        await audit_emit(
            audit,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=AuditAction.SKILL_VERSION_CREATE,
            resource_type="skill",
            resource_id=str(existing.id),
            result=AuditResult.SUCCESS,
            trace_id=current_trace_id_hex(),
            details={
                "version": version.version,
                "tool_names": list(version.tool_names),
                "source": "zip_import",
            },
        )
        return JSONResponse(
            status_code=201,
            content={
                "skill": _skill_dict(existing),
                "version": _version_dict(version),
            },
        )

    @router.get("/{skill_id}/versions/{version_number}/export", response_model=None)
    async def export_version(
        skill_id: UUID,
        version_number: int,
        request: Request,
        store: Annotated[SkillStore, Depends(_get_skill_store)],
    ) -> Response:
        tenant_id: UUID = request.state.tenant_id
        version = await store.get_version_by_number(
            skill_id=skill_id, tenant_id=tenant_id, version=version_number
        )
        if version is None:
            raise HTTPException(status_code=404, detail="skill version not found")
        skill = await store.get_skill(skill_id=skill_id, tenant_id=tenant_id)
        if skill is None:
            raise HTTPException(status_code=404, detail="skill not found")
        blob = build_skill_zip(
            name=skill.name,
            description=version.description,
            category=version.category,
            required_models=version.required_models,
            prompt_fragment=version.prompt_fragment,
            tool_names=version.tool_names,
        )
        return Response(
            content=blob,
            media_type="application/zip",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{skill.name}-v{version.version}.skill"'
                )
            },
        )

    return router
