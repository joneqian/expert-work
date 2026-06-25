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
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from control_plane.api._authz import ensure_resource_access, require
from control_plane.api._quota_admission import check_admission
from control_plane.api._user_scope import get_user_repo
from control_plane.api.runs import RunRequest, spawn_run
from control_plane.audit import emit
from control_plane.auth.abac import ResourceAttrs
from control_plane.manifest import (
    ManifestError,
    ManifestLoader,
    ManifestSyntaxError,
    ManifestTemplateError,
    ManifestValidationError,
)
from control_plane.quota.base import QuotaService
from control_plane.runtime import AgentRuntime
from control_plane.tenancy import TenantConfigNotConfiguredError
from control_plane.tenant_scope import (
    CrossTenant,
    applied_scope,
    bypass_rls_session,
    cross_tenant_query_enabled,
    ensure_tenant_scope,
)
from helix_agent.common.observability import current_trace_id_hex
from helix_agent.common.uplift_metrics import record_manifest_provider_rejected
from helix_agent.persistence import ApprovalStore
from helix_agent.persistence.agent_instance import AgentInstanceStore
from helix_agent.persistence.agent_spec import AgentSpecStore, DuplicateAgentSpecError
from helix_agent.persistence.tenant_user import TenantUserStore
from helix_agent.persistence.thread_meta import ThreadMetaStore
from helix_agent.protocol import (
    AgentSpec,
    AgentSpecRecord,
    AgentSpecRevisionRecord,
    AgentSpecStatus,
    AuditAction,
    AuditResult,
    PlatformAgentTemplateStatus,
    Principal,
    Provider,
    TenantPlan,
    tier_satisfies,
)
from helix_agent.runtime.audit.logger import AuditLogger
from orchestrator import AgentFactoryError

logger = logging.getLogger("helix.control_plane.agents")


class ManifestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_yaml: str = Field(min_length=1)
    template_vars: dict[str, Any] | None = None


def _record_attrs(record: AgentSpecRecord) -> ResourceAttrs:
    """Stream 8.5 — ABAC attributes for a stored manifest instance."""
    return ResourceAttrs(
        resource_id=record.name,
        labels=record.spec.metadata.labels,
        owner_id=record.created_by,
    )


def _spec_attrs(spec: AgentSpec, *, owner_id: str) -> ResourceAttrs:
    """Stream 8.5 — ABAC attributes for a manifest being created (no record yet)."""
    return ResourceAttrs(
        resource_id=spec.metadata.name,
        labels=spec.metadata.labels,
        owner_id=owner_id,
    )


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


class ForkTemplateRequest(BaseModel):
    """Body for ``POST /v1/agents/fork`` — Stream Agent-Templates (M1-4).

    Forks a published platform template into a tenant-owned agent. ``name`` is the
    new agent's identifier (its ``agent_code``), unique within the tenant.
    ``template_version`` may be the literal ``"latest"`` (resolved to the newest
    published version and **pinned** in the fork's ``extends``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    template_name: str = Field(min_length=1)
    template_version: str = Field(default="latest", min_length=1)
    name: str = Field(min_length=1, max_length=128)


async def _resolve_plan(tenant_config_service: object, tenant_id: UUID) -> TenantPlan:
    """Tenant plan tier for template entitlement (FREE when unwired / unseeded)."""
    if tenant_config_service is None:
        return TenantPlan.FREE
    try:
        cfg = await tenant_config_service.get(tenant_id=tenant_id)  # type: ignore[attr-defined]
    except TenantConfigNotConfiguredError:
        return TenantPlan.FREE
    return cfg.plan  # type: ignore[no-any-return]


def _get_thread_repo(request: Request) -> ThreadMetaStore:
    return request.app.state.thread_meta_repo  # type: ignore[no-any-return]


def _get_instance_store(request: Request) -> AgentInstanceStore:
    return request.app.state.agent_instance_store  # type: ignore[no-any-return]


def _get_runtime(request: Request) -> AgentRuntime:
    return request.app.state.agent_runtime  # type: ignore[no-any-return]


def _get_approvals(request: Request) -> ApprovalStore:
    return request.app.state.approval_store  # type: ignore[no-any-return]


def _get_quota(request: Request) -> QuotaService:
    return request.app.state.quota_service  # type: ignore[no-any-return]


class _SessionError(Exception):
    """Internal control-flow signal for the external session/run helpers; the
    endpoints convert it to an envelope error."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


async def _resolve_session(
    *,
    tenant_id: UUID,
    agent_code: str,
    actor_id: str,
    user_id: str,
    session_id: UUID | None,
    repo: AgentSpecStore,
    threads: ThreadMetaStore,
    users: TenantUserStore,
    instances: AgentInstanceStore,
) -> tuple[AgentSpecRecord, UUID, UUID]:
    """Resolve agent_code → active record, mint the end-user, create / continue
    the session thread, and touch the per-user instance binding. Returns
    ``(record, thread_id, end_user_id)``. Raises :class:`_SessionError`.

    Shared by the external session-bind and run endpoints (M1-5b)."""
    active = await repo.list_by_tenant(
        tenant_id=tenant_id, status=AgentSpecStatus.ACTIVE, name=agent_code, limit=1
    )
    if not active:
        raise _SessionError(
            "AGENT_NOT_FOUND", f"no active agent {agent_code!r} for this tenant", 404
        )
    record = active[0]

    # Mint-on-use the end-user. The app owns its user_id namespace; subject type
    # "user" + the app's id is unique per tenant. (Any valid tenant key may act
    # for any of its users — network-layer hardening is a later addition; every
    # call is audited with on_behalf_of.)
    end_user = await users.resolve(tenant_id=tenant_id, subject_type="user", subject_id=user_id)

    if session_id is not None:
        meta = await threads.get(session_id, tenant_id=tenant_id)
        if meta is None or meta.user_id != end_user.id or meta.agent_name != agent_code:
            raise _SessionError("SESSION_NOT_FOUND", "session not found for this user / agent", 404)
        thread_id = session_id
    else:
        thread_id = uuid4()
        await threads.create(
            thread_id=thread_id,
            tenant_id=tenant_id,
            created_by=actor_id,
            user_id=end_user.id,
            agent_name=agent_code,
            agent_version=record.version,
        )

    await instances.touch(tenant_id=tenant_id, agent_code=agent_code, user_id=end_user.id)
    return record, thread_id, end_user.id


class BindSessionRequest(BaseModel):
    """Body for ``POST /v1/agents/{agent_code}/sessions`` — Stream Agent-Templates
    (M1-5b). An external app (tenant API-key) binds / continues a per-user session.

    ``user_id`` is the app's own identifier for its end-user; it is minted into a
    ``tenant_user`` on first use (the app does not pre-onboard users). ``session_id``
    continues an existing conversation; omit it to start a new one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str = Field(min_length=1, max_length=255)
    session_id: UUID | None = None


class ExternalRunRequest(BaseModel):
    """Body for ``POST /v1/agents/{agent_code}/runs`` — Stream Agent-Templates
    (M1-5b-2). Binds / continues a per-user session and runs in one call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str = Field(min_length=1, max_length=255)
    session_id: UUID | None = None
    input: str | None = Field(default=None, max_length=8192)
    mode: Literal["stream", "queue"] = "stream"
    image_refs: list[str] = Field(default_factory=list, max_length=64)
    untrusted_content: list[str] = Field(default_factory=list, max_length=16)


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

        # Stream 8.5 — instance-level RBAC + ABAC on the create. A conditioned
        # binding (e.g. operator restricted to resource_ids / a label) may only
        # create matching manifests; the creator is the owner for owner_only.
        await ensure_resource_access(
            request,
            resource="manifest",
            action="write",
            attrs=_spec_attrs(spec, owner_id=actor_id),
        )

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

    @router.post("/fork", status_code=201)
    async def fork_template(
        payload: ForkTemplateRequest,
        request: Request,
        repo: Annotated[AgentSpecStore, Depends(_get_repo)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
    ) -> JSONResponse:
        """Fork a published platform template into a tenant-owned agent (M1-4).

        Materializes a copy of the template base manifest, pins ``extends`` to the
        resolved template version (so the tier① security floor re-applies at build),
        renames it to the tenant's ``agent_code``, and persists it as an ordinary
        tenant ``agent_spec`` — editable thereafter via the normal agent CRUD.
        """
        tenant_id = request.state.tenant_id
        actor_id = request.state.actor_id
        trace_id = current_trace_id_hex()
        template_store = request.app.state.platform_agent_template_store
        tcs = getattr(request.app.state, "tenant_config_service", None)

        # 1. Load the platform base template (NULL-tenant rows → bypass_rls).
        async with bypass_rls_session():
            if payload.template_version == "latest":
                base = await template_store.get_latest(
                    name=payload.template_name,
                    status=PlatformAgentTemplateStatus.PUBLISHED,
                )
            else:
                base = await template_store.get(
                    name=payload.template_name, version=payload.template_version
                )
        if (
            base is None
            or base.status is not PlatformAgentTemplateStatus.PUBLISHED
            or not base.enabled
        ):
            return _envelope_error(
                "TEMPLATE_NOT_AVAILABLE",
                "template not found, not published, or disabled",
                404,
            )

        # 2. Entitlement — the tenant's plan must satisfy the template's tier.
        plan = await _resolve_plan(tcs, tenant_id)
        if not tier_satisfies(plan, base.required_tier):
            return _envelope_error(
                "TEMPLATE_TIER_FORBIDDEN",
                f"forking this template requires the {base.required_tier.value} plan",
                403,
            )

        # 3. Materialize the fork: copy the base manifest, pin extends to the
        #    resolved concrete version, rename to the tenant's agent_code.
        pinned = f"{base.name}@{base.version}"
        doc = base.spec.model_dump(by_alias=True, mode="json")
        doc["metadata"]["name"] = payload.name
        doc["metadata"]["tenant"] = str(tenant_id)
        doc["spec"]["extends"] = pinned
        try:
            fork_spec = AgentSpec.model_validate(doc)
        except ValidationError as exc:
            return _envelope_error("FORK_INVALID", str(exc), 422)

        # 4. ABAC + provider whitelist gate (parity with create_agent).
        await ensure_resource_access(
            request,
            resource="manifest",
            action="write",
            attrs=_spec_attrs(fork_spec, owner_id=actor_id),
        )
        settings = request.app.state.settings
        supported = set(settings.supported_providers)
        referenced = _collect_manifest_providers(fork_spec)
        invalid = sorted(referenced - supported) if supported else []
        if invalid:
            return _envelope_error(
                "MANIFEST_PROVIDER_NOT_SUPPORTED",
                f"template references providers not in the platform's "
                f"supported_providers list: {invalid}",
                403,
            )

        # 5. Persist as an ordinary tenant agent_spec.
        sha = _spec_sha256(doc)
        try:
            record = await repo.create(
                tenant_id=tenant_id, spec=fork_spec, spec_sha256=sha, created_by=actor_id
            )
        except DuplicateAgentSpecError:
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
            details={"forked_from": pinned, "revision": 1},
        )
        return JSONResponse(
            status_code=201,
            content={"success": True, "data": AgentDetail(record=record).model_dump(mode="json")},
        )

    @router.post("/{agent_code}/sessions", status_code=201)
    async def bind_session(
        agent_code: str,
        payload: BindSessionRequest,
        request: Request,
        principal: Annotated[Principal, Depends(require("session", "write"))],
        repo: Annotated[AgentSpecStore, Depends(_get_repo)],
        threads: Annotated[ThreadMetaStore, Depends(_get_thread_repo)],
        users: Annotated[TenantUserStore, Depends(get_user_repo)],
        instances: Annotated[AgentInstanceStore, Depends(_get_instance_store)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
    ) -> JSONResponse:
        """Bind / continue a per-user session for an external-app end-user (M1-5b).

        Mints the end-user (``tenant_user``) from the app's ``user_id`` on first
        use, resolves ``agent_code`` to its latest active version, and creates a
        new conversation thread (or continues ``session_id``). Records the per-user
        ``agent_instance`` binding + an ``on_behalf_of`` audit. The agent
        *definition* is shared; per-user memory / workspace / threads provide
        isolation. (The run itself is M1-5b-2.)
        """
        tenant_id = request.state.tenant_id
        actor_id = request.state.actor_id
        trace_id = current_trace_id_hex()
        try:
            record, thread_id, end_user_id = await _resolve_session(
                tenant_id=tenant_id,
                agent_code=agent_code,
                actor_id=actor_id,
                user_id=payload.user_id,
                session_id=payload.session_id,
                repo=repo,
                threads=threads,
                users=users,
                instances=instances,
            )
        except _SessionError as exc:
            return _envelope_error(exc.code, exc.message, exc.status_code)

        await emit(
            audit,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=AuditAction.SESSION_WRITE,
            resource_type="session",
            resource_id=str(thread_id),
            trace_id=trace_id,
            details={"agent_code": agent_code, "agent_version": record.version},
            on_behalf_of=str(end_user_id),
        )
        return JSONResponse(
            status_code=201,
            content={
                "success": True,
                "data": {
                    "session_id": str(thread_id),
                    "agent_code": agent_code,
                    "agent_version": record.version,
                    "user_id": str(end_user_id),
                },
            },
        )

    @router.post("/{agent_code}/runs", response_model=None)
    async def run_agent_for_user(
        agent_code: str,
        payload: ExternalRunRequest,
        request: Request,
        principal: Annotated[Principal, Depends(require("session", "write"))],
        repo: Annotated[AgentSpecStore, Depends(_get_repo)],
        threads: Annotated[ThreadMetaStore, Depends(_get_thread_repo)],
        users: Annotated[TenantUserStore, Depends(get_user_repo)],
        instances: Annotated[AgentInstanceStore, Depends(_get_instance_store)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
        runtime: Annotated[AgentRuntime, Depends(_get_runtime)],
        approvals: Annotated[ApprovalStore, Depends(_get_approvals)],
        quota: Annotated[QuotaService, Depends(_get_quota)],
    ) -> StreamingResponse | JSONResponse:
        """Run an agent on behalf of an external-app end-user (M1-5b-2).

        One call: mints the end-user, resolves ``agent_code``, binds / continues a
        session, then spawns the run **scoped to the end-user** — long-term memory,
        the workspace volume, and per-user token cost all key on the minted user, not
        the API-key caller. Returns the SSE stream (``X-Helix-Session-Id`` header) or
        202 for queue mode. The agent definition is shared across the tenant's users;
        per-user isolation is the user-scoped state.
        """
        tenant_id = request.state.tenant_id
        actor_id = request.state.actor_id
        trace_id = current_trace_id_hex()
        try:
            record, thread_id, end_user_id = await _resolve_session(
                tenant_id=tenant_id,
                agent_code=agent_code,
                actor_id=actor_id,
                user_id=payload.user_id,
                session_id=payload.session_id,
                repo=repo,
                threads=threads,
                users=users,
                instances=instances,
            )
        except _SessionError as exc:
            return _envelope_error(exc.code, exc.message, exc.status_code)

        # Admission (Stream C.5b) — bucket the run against the agent.
        denial = await check_admission(
            quota=quota,
            audit=audit,
            tenant_id=tenant_id,
            actor_id=actor_id,
            agent=agent_code,
            resource_kind="run",
        )
        if denial is not None:
            return denial

        # Build (cache-hit) the agent. The end-user has no OAuth subject of its
        # own (minted, not a Keycloak login), so the OAuth pool keys on its id and
        # resolves empty — the build stays shared.
        try:
            built = await runtime.get_agent(
                tenant_id=tenant_id,
                name=agent_code,
                version=record.version,
                spec=record.spec,
                user_id=str(end_user_id),
            )
        except AgentFactoryError as exc:
            return _envelope_error("AGENT_BUILD_FAILED", f"agent cannot be built: {exc}", 422)

        run_payload = RunRequest(
            input=payload.input,
            mode=payload.mode,
            image_refs=payload.image_refs,
            untrusted_content=payload.untrusted_content,
        )
        return await spawn_run(
            runtime=runtime,
            audit=audit,
            approvals=approvals,
            request=request,
            settings=request.app.state.settings,
            built=built,
            record_spec=record.spec,
            thread_id=thread_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
            effective_user_id=end_user_id,
            oauth_subject=str(end_user_id),
            payload=run_payload,
            trace_id=trace_id,
            extra_headers={"X-Helix-Session-Id": str(thread_id)},
            on_behalf_of=str(end_user_id),
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
        # Stream 8.5 — instance-level RBAC + ABAC (conditioned bindings may
        # restrict a member to specific agents by id / label / ownership).
        await ensure_resource_access(
            request, resource="manifest", action="read", attrs=_record_attrs(record)
        )
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

        # Stream 8.5 — authorize against the EXISTING instance's attributes
        # (owner / labels) before mutating it. 404 stays 404 for unknown names.
        existing = await repo.get(tenant_id=tenant_id, name=name, version=version)
        if existing is None:
            raise HTTPException(status_code=404, detail="agent not found")
        await ensure_resource_access(
            request, resource="manifest", action="write", attrs=_record_attrs(existing)
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
        # Stream 8.5 — authorize against the existing instance before deleting.
        existing = await repo.get(tenant_id=tenant_id, name=name, version=version)
        if existing is None:
            raise HTTPException(status_code=404, detail="agent not found")
        await ensure_resource_access(
            request, resource="manifest", action="delete", attrs=_record_attrs(existing)
        )
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
