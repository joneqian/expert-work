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

import base64
import re
from pathlib import PurePosixPath
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, File, HTTPException, Path, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from control_plane.api import _skill_github
from control_plane.api._skill_moderation import (
    ModerationError,
    moderate_prompt_fragment,
    moderate_required_models,
    moderate_tool_names,
)
from control_plane.api._skill_zip import SkillZipError, build_skill_zip, parse_skill_zip
from control_plane.api.skills import (
    _MAX_SUPPORTING_FILE_SIZE,
    _SUPPORTING_FILE_TEXT_EXTS,
    _get_audit,
    _get_skill_store,
    _PutSupportingFileBody,
    _skill_dict,
    _validate_supporting_file_path,
    _version_dict,
)
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
    SkillStore,
)
from helix_agent.protocol import (
    AuditAction,
    AuditResult,
    Principal,
    SkillStatus,
    TenantPlan,
)
from helix_agent.protocol.skill import (
    SkillPackageLayoutError,
    compute_content_hash,
    is_high_risk_skill_version,
    supporting_files_to_jsonable,
)
from helix_agent.runtime.audit.logger import AuditLogger


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


class _PutPlatformPromptBody(BaseModel):
    """``PUT /v1/platform/skills/{id}/versions/{v}/prompt`` body — edit
    ``SKILL.md`` in place; new version inherits all other fields
    (skill-authoring-ia Phase D-2)."""

    prompt_fragment: str = Field(min_length=1)


class _ImportFromGithubBody(BaseModel):
    """``POST /v1/platform/skills/import-from-github`` request body.

    ``source``: ``owner/repo`` shorthand, a ``github.com`` URL, or a
    ``skills.sh`` URL (the latter also supplies ``skill``). ``skill``: which
    skill folder to pick in a multi-skill repo (the ``npx skills --skill <name>``
    convention). ``ref``: branch/tag/SHA (default: the repo's default branch).
    """

    source: str = Field(min_length=1, max_length=512)
    skill: str | None = Field(default=None, max_length=128)
    ref: str | None = Field(default=None, max_length=256)


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


async def _ingest_platform_skill_blob(
    *,
    blob: bytes,
    store: SkillStore,
    audit: AuditLogger,
    principal: Principal,
    source: str,
    origin: str | None = None,
) -> JSONResponse:
    """Shared platform-import pipeline: parse a ``.skill`` zip → name check →
    moderation → Mini-ADR U-21 strict threat scan → idempotent create/version
    → audit. Both the multipart ZIP upload and the GitHub import feed this; only
    ``source`` (audit/metric label) and ``origin`` (locator) differ.
    """
    try:
        payload = parse_skill_zip(blob)
    except SkillZipError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not re.fullmatch(r"^[a-z][a-z0-9_-]{0,63}$", payload.name):
        raise HTTPException(
            status_code=400,
            detail=f"skill name {payload.name!r} fails validation",
        )

    # Moderation gate before any DB write.
    try:
        moderate_prompt_fragment(payload.prompt_fragment)
        moderate_tool_names(payload.tool_names)
        moderate_required_models(payload.required_models)
    except ModerationError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc

    # Mini-ADR U-21 write-time strict scan (same as add_platform_version).
    findings = scan_for_threats(payload.prompt_fragment, scope="strict")
    if findings:
        record_threat_pattern_hits(findings, scope="strict")
        record_skill_blocked(phase=f"platform_{source}")
        await audit_emit(
            audit,
            tenant_id=principal.tenant_id,
            actor_id=principal.subject_id,
            action=AuditAction.SKILL_PROMPT_INJECTION_BLOCKED,
            resource_type="skill",
            resource_id=payload.name,
            result=AuditResult.DENIED,
            trace_id=current_trace_id_hex(),
            details={
                "scope": "platform",
                "finding_count": len(findings),
                "findings": [
                    {"pattern_id": f.pattern_id, "category": f.category} for f in findings
                ],
                "source": source,
                **({"origin": origin} if origin else {}),
            },
        )
        raise HTTPException(status_code=400, detail="invalid skill content")

    created_skill = False
    async with bypass_rls_session():
        existing = await store.get_platform_skill_by_name(name=payload.name)

        # Idempotency (mirrors the tenant import path): if the latest version
        # already carries this exact content_hash, the re-import is a no-op —
        # return it (200, created=False).
        if existing is not None and existing.latest_version > 0:
            latest = await store.get_platform_version_by_number(
                skill_id=existing.id, version=existing.latest_version
            )
            if latest is not None and latest.content_hash == payload.content_hash:
                return JSONResponse(
                    status_code=200,
                    content={
                        "skill": _skill_dict(existing),
                        "version": _version_dict(latest),
                        "created": False,
                    },
                )

        if existing is None:
            try:
                existing = await store.create_platform_skill(
                    skill_id=uuid4(),
                    name=payload.name,
                    description=payload.description,
                    category=payload.category,
                    required_tier=TenantPlan.FREE,
                )
                created_skill = True
            except DuplicateSkillError as exc:
                # Race — another import won the create; resolve + add.
                existing = await store.get_platform_skill_by_name(name=payload.name)
                if existing is None:
                    raise HTTPException(status_code=409, detail=str(exc)) from exc
        version = await store.add_platform_version(
            version_id=uuid4(),
            skill_id=existing.id,
            prompt_fragment=payload.prompt_fragment,
            tool_names=payload.tool_names,
            description=payload.description,
            category=payload.category,
            required_models=payload.required_models,
            authored_by="human",
            supporting_files=supporting_files_to_jsonable(payload.supporting_files),
            lazy_load=payload.lazy_load,
            content_hash=payload.content_hash,
            high_risk=payload.high_risk,
        )

    if created_skill:
        await audit_emit(
            audit,
            tenant_id=principal.tenant_id,
            actor_id=principal.subject_id,
            action=AuditAction.SKILL_CREATE,
            resource_type="skill",
            resource_id=str(existing.id),
            result=AuditResult.SUCCESS,
            trace_id=current_trace_id_hex(),
            details={
                "scope": "platform",
                "name": existing.name,
                "category": existing.category,
                "source": source,
                **({"origin": origin} if origin else {}),
            },
        )
    await audit_emit(
        audit,
        tenant_id=principal.tenant_id,
        actor_id=principal.subject_id,
        action=AuditAction.SKILL_VERSION_CREATE,
        resource_type="skill",
        resource_id=str(existing.id),
        result=AuditResult.SUCCESS,
        trace_id=current_trace_id_hex(),
        details={
            "scope": "platform",
            "version": version.version,
            "tool_names": list(version.tool_names),
            "source": source,
            **({"origin": origin} if origin else {}),
        },
    )
    return JSONResponse(
        status_code=201,
        content={
            "skill": _skill_dict(existing),
            "version": _version_dict(version),
            "created": True,
        },
    )


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

    @router.post("/import", response_model=None)
    async def import_platform_skill(
        file: Annotated[UploadFile, File()],
        request: Request,
    ) -> JSONResponse:
        """OFFICE-3: multipart ``.skill`` ZIP → platform (NULL-tenant) skill.

        system_admin only. Same ZIP parse + moderation + Mini-ADR U-21 strict
        threat scan as the tenant ``POST /v1/skills/import``, but writes a
        tenant-less row via ``bypass_rls_session()``. content_hash idempotent
        (same semantics as the tenant path): re-importing identical content
        returns ``200`` + ``created: false`` with the existing latest version,
        instead of churning a duplicate version.
        """
        principal = _principal(request)
        store = _get_skill_store(request)
        audit = _get_audit(request)
        blob = await file.read()
        return await _ingest_platform_skill_blob(
            blob=blob,
            store=store,
            audit=audit,
            principal=principal,
            source="zip_import",
        )

    @router.post("/import-from-github", response_model=None)
    async def import_platform_skill_from_github(
        body: _ImportFromGithubBody,
        request: Request,
    ) -> JSONResponse:
        """方案 A: fetch a skill from a public GitHub repo → platform library.

        system_admin only. Resolves ``source`` (owner/repo · github.com URL ·
        skills.sh URL) + optional ``skill`` selector, downloads the repo archive
        from the fixed ``codeload.github.com`` host (SSRF面=单域名), scans it for
        the matching ``SKILL.md`` folder, repacks it, then runs the SAME
        moderation + strict threat scan + idempotent create/version pipeline as
        the ZIP upload. GitHub-only / platform-only / public repos (M0).
        """
        principal = _principal(request)
        store = _get_skill_store(request)
        audit = _get_audit(request)

        try:
            src = _skill_github.resolve_github_source(body.source, skill=body.skill, ref=body.ref)
            archive = await _skill_github.download_github_archive(src)
            blob = _skill_github.select_skill_zip(archive, skill=src.skill)
        except _skill_github.GithubImportError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc

        origin = f"{src.owner}/{src.repo}@{src.ref}" + (f"#{src.skill}" if src.skill else "")
        return await _ingest_platform_skill_blob(
            blob=blob,
            store=store,
            audit=audit,
            principal=principal,
            source="github_import",
            origin=origin,
        )

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

    # ── Supporting files (Stream X / skill-authoring-ia Phase A) ──────────
    # Mirror the tenant ``/v1/skills`` supporting-file surface so the platform
    # admin can iterate a curated skill's bundled scripts/references in-UI
    # instead of re-importing a ZIP for every edit. NULL-tenant rows ⇒ every
    # store call inside ``bypass_rls_session()`` + ``is_system_admin`` gate.

    @router.get(
        "/{skill_id}/versions/{version}/supporting-files/{file_path:path}",
        response_model=None,
    )
    async def get_platform_supporting_file(
        skill_id: Annotated[UUID, Path()],
        version: int,
        file_path: str,
        request: Request,
    ) -> JSONResponse:
        """Single-file content fetch (base64). Mirrors the tenant path; skips
        the U-21 re-scan on purpose so operators see the literal stored bytes
        for audit/triage."""
        _principal(request)
        store = _get_skill_store(request)
        try:
            _validate_supporting_file_path(file_path)
        except SkillPackageLayoutError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        async with bypass_rls_session():
            row = await store.get_platform_version_by_number(skill_id=skill_id, version=version)
        if row is None:
            raise HTTPException(status_code=404, detail="skill version not found")
        entry = row.supporting_files.get(file_path)
        if entry is None:
            raise HTTPException(status_code=404, detail="supporting file not found")
        return JSONResponse(
            status_code=200,
            content={"content": entry.content, "size": entry.size, "mime": entry.mime},
        )

    @router.put(
        "/{skill_id}/versions/{version}/supporting-files/{file_path:path}",
        response_model=None,
    )
    async def put_platform_supporting_file(
        skill_id: Annotated[UUID, Path()],
        version: int,
        file_path: str,
        body: _PutSupportingFileBody,
        request: Request,
    ) -> JSONResponse:
        """Add or replace one supporting file → new platform version (U-17).
        U-18 path validation + U-21 write-time strict scan + U-24 high_risk
        recompute, exactly as the tenant path."""
        principal = _principal(request)
        store = _get_skill_store(request)
        audit = _get_audit(request)

        try:
            _validate_supporting_file_path(file_path)
        except SkillPackageLayoutError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if body.size > _MAX_SUPPORTING_FILE_SIZE:
            raise HTTPException(status_code=400, detail="invalid supporting file path")

        async with bypass_rls_session():
            prior = await store.get_platform_version_by_number(skill_id=skill_id, version=version)
        if prior is None:
            raise HTTPException(status_code=404, detail="skill version not found")

        try:
            raw = base64.b64decode(body.content, validate=True)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail="invalid supporting file path") from exc
        if len(raw) != body.size:
            raise HTTPException(status_code=400, detail="invalid supporting file path")

        ext = PurePosixPath(file_path).suffix.lower()
        if ext in _SUPPORTING_FILE_TEXT_EXTS:
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                record_skill_blocked(phase="supporting_file_api")
                await audit_emit(
                    audit,
                    tenant_id=principal.tenant_id,
                    actor_id=principal.subject_id,
                    action=AuditAction.SKILL_PROMPT_INJECTION_BLOCKED,
                    resource_type="skill_supporting_file",
                    resource_id=f"{skill_id}/{version}/{file_path}",
                    result=AuditResult.DENIED,
                    trace_id=current_trace_id_hex(),
                    details={"scope": "platform", "reason": "text_extension_binary_content"},
                )
                raise HTTPException(status_code=400, detail="invalid supporting file path") from exc
            findings = scan_for_threats(text, scope="strict")
            if findings:
                record_threat_pattern_hits(findings, scope="strict")
                record_skill_blocked(phase="supporting_file_api")
                await audit_emit(
                    audit,
                    tenant_id=principal.tenant_id,
                    actor_id=principal.subject_id,
                    action=AuditAction.SKILL_PROMPT_INJECTION_BLOCKED,
                    resource_type="skill_supporting_file",
                    resource_id=f"{skill_id}/{version}/{file_path}",
                    result=AuditResult.DENIED,
                    trace_id=current_trace_id_hex(),
                    details={
                        "scope": "platform",
                        "finding_count": len(findings),
                        "findings": [
                            {"pattern_id": f.pattern_id, "category": f.category} for f in findings
                        ],
                    },
                )
                raise HTTPException(status_code=400, detail="invalid supporting file path")

        merged = supporting_files_to_jsonable(prior.supporting_files)
        merged[file_path] = {"content": body.content, "size": body.size, "mime": body.mime}
        new_paths = list(merged.keys())
        new_high_risk = is_high_risk_skill_version(
            tool_names=prior.tool_names, supporting_file_paths=new_paths
        )
        new_hash = compute_content_hash(prior.prompt_fragment, merged)

        async with bypass_rls_session():
            new_version = await store.add_platform_version(
                version_id=uuid4(),
                skill_id=skill_id,
                prompt_fragment=prior.prompt_fragment,
                tool_names=list(prior.tool_names),
                description=prior.description,
                category=prior.category,
                required_models=list(prior.required_models),
                authored_by=prior.authored_by,
                supporting_files=merged,
                lazy_load=prior.lazy_load,
                content_hash=new_hash,
                high_risk=new_high_risk,
            )

        await audit_emit(
            audit,
            tenant_id=principal.tenant_id,
            actor_id=principal.subject_id,
            action=AuditAction.SKILL_SUPPORTING_FILE_UPLOADED,
            resource_type="skill_supporting_file",
            resource_id=f"{skill_id}/{new_version.version}/{file_path}",
            result=AuditResult.SUCCESS,
            trace_id=current_trace_id_hex(),
            details={
                "scope": "platform",
                "from_version": prior.version,
                "to_version": new_version.version,
                "path": file_path,
                "size": body.size,
                "high_risk_after": new_high_risk,
            },
        )
        return JSONResponse(status_code=201, content=_version_dict(new_version))

    @router.delete(
        "/{skill_id}/versions/{version}/supporting-files/{file_path:path}",
        response_model=None,
    )
    async def delete_platform_supporting_file(
        skill_id: Annotated[UUID, Path()],
        version: int,
        file_path: str,
        request: Request,
    ) -> JSONResponse:
        """Remove one supporting file → new platform version (U-17)."""
        principal = _principal(request)
        store = _get_skill_store(request)
        audit = _get_audit(request)

        try:
            _validate_supporting_file_path(file_path)
        except SkillPackageLayoutError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        async with bypass_rls_session():
            prior = await store.get_platform_version_by_number(skill_id=skill_id, version=version)
        if prior is None:
            raise HTTPException(status_code=404, detail="skill version not found")
        if file_path not in prior.supporting_files:
            raise HTTPException(status_code=404, detail="supporting file not found")

        merged = supporting_files_to_jsonable(prior.supporting_files)
        merged.pop(file_path)
        new_paths = list(merged.keys())
        new_high_risk = is_high_risk_skill_version(
            tool_names=prior.tool_names, supporting_file_paths=new_paths
        )
        new_hash = compute_content_hash(prior.prompt_fragment, merged)

        async with bypass_rls_session():
            new_version = await store.add_platform_version(
                version_id=uuid4(),
                skill_id=skill_id,
                prompt_fragment=prior.prompt_fragment,
                tool_names=list(prior.tool_names),
                description=prior.description,
                category=prior.category,
                required_models=list(prior.required_models),
                authored_by=prior.authored_by,
                supporting_files=merged,
                lazy_load=prior.lazy_load,
                content_hash=new_hash,
                high_risk=new_high_risk,
            )

        await audit_emit(
            audit,
            tenant_id=principal.tenant_id,
            actor_id=principal.subject_id,
            action=AuditAction.SKILL_SUPPORTING_FILE_REMOVED,
            resource_type="skill_supporting_file",
            resource_id=f"{skill_id}/{new_version.version}/{file_path}",
            result=AuditResult.SUCCESS,
            trace_id=current_trace_id_hex(),
            details={
                "scope": "platform",
                "from_version": prior.version,
                "to_version": new_version.version,
                "path": file_path,
                "high_risk_after": new_high_risk,
            },
        )
        return JSONResponse(status_code=200, content=_version_dict(new_version))

    @router.get("/{skill_id}/versions/{version_number}/export", response_model=None)
    async def export_platform_version(
        skill_id: Annotated[UUID, Path()],
        version_number: int,
        request: Request,
    ) -> Response:
        """Export a platform version as a ``.skill`` ZIP. Unlike the tenant
        export, the bundle includes ``supporting_files`` (scripts/references)
        so a round-trip is lossless — that bundle is the whole point of the
        platform editor."""
        _principal(request)
        store = _get_skill_store(request)
        async with bypass_rls_session():
            version = await store.get_platform_version_by_number(
                skill_id=skill_id, version=version_number
            )
            skill = await store.get_platform_skill(skill_id=skill_id)
        if version is None:
            raise HTTPException(status_code=404, detail="skill version not found")
        if skill is None:
            raise HTTPException(status_code=404, detail="skill not found")
        blob = build_skill_zip(
            name=skill.name,
            description=version.description,
            category=version.category,
            required_models=version.required_models,
            prompt_fragment=version.prompt_fragment,
            tool_names=version.tool_names,
            supporting_files=version.supporting_files,
            lazy=version.lazy_load,
            version=version.version,
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

    @router.put("/{skill_id}/versions/{version}/prompt", response_model=None)
    async def put_platform_prompt(
        skill_id: Annotated[UUID, Path()],
        version: int,
        body: _PutPlatformPromptBody,
        request: Request,
    ) -> JSONResponse:
        """Edit ``SKILL.md`` (prompt_fragment) → new platform version
        (Phase D-2). Inherits all other fields incl. supporting_files, so
        editing the prompt never drops bundled files."""
        principal = _principal(request)
        store = _get_skill_store(request)
        audit = _get_audit(request)

        try:
            moderate_prompt_fragment(body.prompt_fragment)
        except ModerationError as exc:
            raise HTTPException(status_code=400, detail=exc.detail) from exc

        async with bypass_rls_session():
            prior = await store.get_platform_version_by_number(skill_id=skill_id, version=version)
        if prior is None:
            raise HTTPException(status_code=404, detail="skill version not found")

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
                    "source": "prompt_edit",
                },
            )
            raise HTTPException(status_code=400, detail="invalid skill content")

        merged = supporting_files_to_jsonable(prior.supporting_files)
        new_high_risk = is_high_risk_skill_version(
            tool_names=prior.tool_names, supporting_file_paths=list(merged.keys())
        )
        new_hash = compute_content_hash(body.prompt_fragment, merged)

        async with bypass_rls_session():
            new_version = await store.add_platform_version(
                version_id=uuid4(),
                skill_id=skill_id,
                prompt_fragment=body.prompt_fragment,
                tool_names=list(prior.tool_names),
                description=prior.description,
                category=prior.category,
                required_models=list(prior.required_models),
                authored_by=prior.authored_by,
                supporting_files=merged,
                lazy_load=prior.lazy_load,
                content_hash=new_hash,
                high_risk=new_high_risk,
            )

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
                "from_version": prior.version,
                "to_version": new_version.version,
                "source": "prompt_edit",
            },
        )
        return JSONResponse(status_code=201, content=_version_dict(new_version))

    return router
