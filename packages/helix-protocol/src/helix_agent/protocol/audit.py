"""audit_log row shape — see ADR-0002 §audit_log and subsystems/17-audit-log."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from ipaddress import IPv4Address, IPv6Address
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AuditResult(StrEnum):
    """Outcome of an audited action."""

    SUCCESS = "success"
    DENIED = "denied"
    ERROR = "error"


class AuditAction(StrEnum):
    """Canonical action word — strict whitelist (subsystems/17-audit-log §5.1).

    Format: ``<resource>:<verb>``. New actions land via PR + word-list update
    + CI lint (``audit.write(action=...)`` must be in this enum).
    """

    # auth
    AUTH_LOGIN = "auth:login"
    AUTH_LOGOUT = "auth:logout"
    AUTH_LOGIN_FAILED = "auth:login_failed"
    AUTH_TOKEN_REFRESH = "auth:token_refresh"
    # manifest
    MANIFEST_READ = "manifest:read"
    MANIFEST_WRITE = "manifest:write"
    MANIFEST_DELETE = "manifest:delete"
    MANIFEST_SIGN = "manifest:sign"
    MANIFEST_PUBLISH = "manifest:publish"
    # session
    SESSION_READ = "session:read"
    SESSION_WRITE = "session:write"
    SESSION_CANCEL = "session:cancel"
    # run lifecycle — emitted by the orchestrator worker at run end
    RUN_COMPLETED = "run:completed"
    RUN_FAILED = "run:failed"
    # H.3 PR 1 — emitted by ``GET /v1/runs`` cross-thread index (Stream H Mini-ADR H-6)
    RUN_LIST_READ = "run:list_read"
    # secret
    SECRET_READ = "secret:read"
    SECRET_WRITE = "secret:write"
    SECRET_ROTATE = "secret:rotate"
    SECRET_DELETE = "secret:delete"
    # quota
    QUOTA_RATE_LIMIT_DENIED = "quota:rate_limit_denied"
    QUOTA_BUDGET_EXCEEDED = "quota:budget_exceeded"
    QUOTA_CONFIG_READ = "quota:config_read"
    QUOTA_CONFIG_WRITE = "quota:config_write"
    QUOTA_CONFIG_DELETE = "quota:config_delete"
    QUOTA_RESERVATION_EXPIRED = "quota:reservation_expired"
    # tenant_config (C.7)
    TENANT_CONFIG_READ = "tenant_config:read"
    TENANT_CONFIG_WRITE = "tenant_config:write"
    # tenant lifecycle — Stream P Mini-ADR P-1 (POST /v1/tenants creates the
    # first tenant_config row; system_admin-gated).
    TENANT_CREATE = "tenant:create"
    # tenant member onboarding — Stream R (Mini-ADR R-3/R-6). MEMBER_INVITE
    # on invite; RESEND on the idempotent compensation path; REVOKE/SUSPEND
    # on DELETE (invited→revoked / active→suspended); ACTIVATE on the W3
    # first-run hook (invited→active). KEYCLOAK_USER_CREATE(_FAILED) record
    # the cross-system account-provisioning side of the DB-first compensation.
    MEMBER_INVITE = "member:invite"
    MEMBER_RESEND = "member:resend"
    MEMBER_REVOKE = "member:revoke"
    MEMBER_SUSPEND = "member:suspend"
    MEMBER_ACTIVATE = "member:activate"
    KEYCLOAK_USER_CREATE = "keycloak_user:create"
    KEYCLOAK_USER_CREATE_FAILED = "keycloak_user:create_failed"
    # tenant credentials — Stream O Mini-ADR O-8.
    # MODE_CHANGED is emitted by the credentials-mode switch endpoint
    # (audit details ``from`` / ``to``); PROVIDER_CREDENTIALS_UPDATED
    # and TOOL_CREDENTIALS_UPDATED are emitted whenever the corresponding
    # dict is mutated via PUT; RESOLVE_FAILED is emitted by the
    # CredentialsResolver when a tenant in ``tenant`` mode looks up a
    # provider / tool with no configured credential (a 401 fail-fast
    # signal — not an attack, but a misconfiguration the runbook
    # walks through).
    CREDENTIALS_MODE_CHANGED = "credentials:mode_changed"
    PROVIDER_CREDENTIALS_UPDATED = "credentials:provider_updated"
    TOOL_CREDENTIALS_UPDATED = "credentials:tool_updated"
    CREDENTIALS_RESOLVE_FAILED = "credentials:resolve_failed"
    # sandbox
    SANDBOX_ACQUIRED = "sandbox:acquired"
    SANDBOX_FORCE_DESTROY = "sandbox:force_destroy"
    SANDBOX_QUOTA_DENIED = "sandbox:quota_denied"
    # workspace (Stream J.15-补强-1 — Mini-ADR J-29 第 1 项 + J-36)
    WORKSPACE_QUOTA_DENIED = "workspace:quota_denied"
    WORKSPACE_SOFT_DELETE = "workspace:soft_delete"
    WORKSPACE_ARCHIVE = "workspace:archive"
    # workspace (Stream J.15-补强-2 — Mini-ADR J-29 第 2 项 backup pipeline)
    WORKSPACE_BACKUP = "workspace:backup"
    # image upload (Stream J.6.补强-2 — Mini-ADR J-31)
    IMAGE_UPLOAD = "image:upload"
    # skill (Stream J.7a — Mini-ADR J-23)
    SKILL_CREATE = "skill:create"
    SKILL_VERSION_CREATE = "skill_version:create"
    SKILL_STATUS_CHANGE = "skill:status_change"
    # skill — Capability Uplift Sprint #3 supporting files (Mini-ADR U-17)
    SKILL_SUPPORTING_FILE_UPLOADED = "skill_supporting_file:uploaded"
    SKILL_SUPPORTING_FILE_REMOVED = "skill_supporting_file:removed"
    # skill — Capability Uplift Sprint #3 threat scan + drift (Mini-ADR U-21).
    # PROMPT_INJECTION_BLOCKED fires on write-time strict-scope match;
    # DRIFT_DETECTED fires when skill_view recomputes content_hash and
    # finds a mismatch (DB row tampered past the strict scan — almost
    # certainly SQL injection or internal actor).
    SKILL_PROMPT_INJECTION_BLOCKED = "skill:prompt_injection_blocked"
    SKILL_DRIFT_DETECTED = "skill:drift_detected"
    # skill — Capability Uplift Sprint #3 high-risk publish gate (Mini-ADR
    # U-24). High-risk = tool_names ∩ {exec_python, http, exec_shell} ≠ ∅
    # or any supporting_files path starts with "scripts/". The gate
    # blocks DRAFT → ACTIVE for non-admin actors. M0 transparent (all
    # writes are admin); M1-K J.7b-1 self-authored skills get gated.
    SKILL_HIGH_RISK_ACTIVATION_BLOCKED = "skill:high_risk_activation_blocked"
    SKILL_HIGH_RISK_ACTIVATED = "skill:high_risk_activated"
    # skill — Capability Uplift Sprint #4 Curator (Mini-ADRs U-26 / U-29 / U-30).
    # Sweep summary (per-tenant per-day); per-skill auto-revival from
    # ``stale`` on activity; skill_view blocked because the skill was
    # auto-archived (cold path); admin pin / unpin events.
    SKILL_CURATOR_RUN = "skill:curator_run"
    SKILL_AUTO_REVIVED = "skill:auto_revived"
    SKILL_VIEW_BLOCKED_ARCHIVED = "skill:view_blocked_archived"
    SKILL_PINNED = "skill:pinned"
    SKILL_UNPINNED = "skill:unpinned"
    # artifact (Stream J.9-step3 — Mini-ADR J-25). ``ARTIFACT_SAVE`` is
    # reserved for the orchestrator-side save-artifact tool emit; that
    # wiring lands when ToolEnv gains an :class:`AuditLogger` handle.
    ARTIFACT_DELETE = "artifact:delete"
    ARTIFACT_UPDATE = "artifact:update"
    # approval / HITL (Stream J.8 — Mini-ADR J-24)
    APPROVAL_REQUESTED = "approval:requested"
    APPROVAL_DECIDED = "approval:decided"
    # triggers (Stream J.10 — Mini-ADR J-26 / J-42)
    TRIGGER_CREATE = "trigger:create"
    TRIGGER_UPDATE = "trigger:update"
    TRIGGER_DELETE = "trigger:delete"
    TRIGGER_FIRE = "trigger:fire"
    # triggers — Capability Uplift Sprint #1 (Mini-ADR U-2)
    TRIGGER_PROMPT_INJECTION_BLOCKED = "trigger:prompt_injection_blocked"
    TRIGGER_PROMPT_INJECTION_WARN = "trigger:prompt_injection_warn"
    # curation / eval-dataset (Stream J.12 — Mini-ADR J-43)
    EVAL_DATASET_CREATE = "eval_dataset:create"
    EVAL_DATASET_UPDATE = "eval_dataset:update"
    EVAL_DATASET_DELETE = "eval_dataset:delete"
    CURATION_PROMOTE = "curation_candidate:promote"
    CURATION_DISMISS = "curation_candidate:dismiss"
    # tools (Stream E.6 + E.8 + onwards)
    TOOL_CALL = "tool:call"
    TOOL_BLOCKED = "tool:blocked"
    # llm (Stream E.11 + E.13)
    LLM_CIRCUIT_OPENED = "llm:circuit_opened"
    LLM_FALLBACK_TRIGGERED = "llm:fallback_triggered"
    LLM_CACHE_HIT = "llm:cache_hit"
    # api_key
    API_KEY_CREATE = "api_key:create"
    API_KEY_REVOKE = "api_key:revoke"
    API_KEY_ROTATE = "api_key:rotate"  # Stream K.K1
    # memory (Stream K.K6)
    MEMORY_UPDATE = "memory:update"
    MEMORY_FORGET = "memory:forget"
    # memory — Capability Uplift Sprint #2 (Mini-ADR U-3 / U-4)
    MEMORY_INJECTION_BLOCKED = "memory:injection_blocked"
    MEMORY_INJECTION_REDACTED = "memory:injection_redacted"
    MEMORY_DRIFT_DETECTED = "memory:drift_detected"
    # memory — Capability Uplift Sprint #7 MemoryConsolidator (Mini-ADRs
    # U-34 / U-36 / U-37 / U-40 / U-42). Mirrors the control-plane
    # ResourceType/Action Literal in services/control-plane/src/control_plane/
    # audit_log.py (per [memory:audit-literal-drift] — both must stay in sync).
    # MEMORY_CONSOLIDATED       — cluster verified + summary written
    # MEMORY_CONSOLIDATION_REJECTED — cluster LLM returned keep=false
    #                              with anti_mislearn:* reason
    # MEMORY_PURGED_AS_NOISE    — lone-item review classified noise + soft-deleted
    # MEMORY_REVIEWED_DURABLE   — lone-item review classified durable + marked
    # MEMORY_DEMOTED            — explicit "consolidated_into set" event
    #                             (M1-K Admin UI; Sprint #7 reserves)
    # MEMORY_ARCHIVED           — M2-C archive pipeline (Sprint #7 reserves)
    # MEMORY_CONSOLIDATOR_RUN   — per-sweep summary row (single audit per tick)
    MEMORY_CONSOLIDATED = "memory:consolidated"
    MEMORY_CONSOLIDATION_REJECTED = "memory:consolidation_rejected"
    MEMORY_PURGED_AS_NOISE = "memory:purged_as_noise"
    MEMORY_REVIEWED_DURABLE = "memory:reviewed_durable"
    MEMORY_DEMOTED = "memory:demoted"
    MEMORY_ARCHIVED = "memory:archived"
    MEMORY_CONSOLIDATOR_RUN = "memory:consolidator_run"
    # service_account
    SERVICE_ACCOUNT_CREATE = "service_account:create"
    SERVICE_ACCOUNT_DELETE = "service_account:delete"
    SERVICE_ACCOUNT_READ = "service_account:read"
    # role_binding
    ROLE_BINDING_CREATE = "role_binding:create"
    ROLE_BINDING_DELETE = "role_binding:delete"
    ROLE_BINDING_READ = "role_binding:read"
    # feedback (Stream G.6)
    FEEDBACK_CREATE = "feedback:create"
    # audit (meta)
    AUDIT_READ = "audit:read"
    AUDIT_EXPORT = "audit:export"
    # system / cross-tenant (Stream N — Mini-ADR N-5)
    SYSTEM_CROSS_TENANT_QUERY = "system:cross_tenant_query"
    SYSTEM_TENANT_SWITCH = "system:tenant_switch"

    # platform credentials (the runtime DB overlay) — Stream P Mini-ADR P-11.
    # system_admin-only writes to the platform provider/tool secret-ref tables.
    PLATFORM_PROVIDER_CREDENTIAL_UPSERT = "platform_credential:provider_upsert"
    PLATFORM_PROVIDER_CREDENTIAL_DELETE = "platform_credential:provider_delete"
    PLATFORM_TOOL_CREDENTIAL_UPSERT = "platform_credential:tool_upsert"
    PLATFORM_TOOL_CREDENTIAL_DELETE = "platform_credential:tool_delete"


class AuditEntry(BaseModel):
    """One row of ``audit_log`` (pre-WORM, post-redactor).

    Reads / writes only via the AuditLogger Python API (see subsystems/17 §4);
    external POST is disallowed.
    """

    model_config = ConfigDict(frozen=True)

    id: int | None = Field(default=None, description="DB autoincrement; None pre-insert")
    tenant_id: UUID
    actor_type: Literal["user", "service_account", "system", "agent"]
    actor_id: str
    on_behalf_of: str | None = Field(default=None, description="Original user when sa-driven")
    action: AuditAction
    resource_type: Literal[
        "manifest",
        "session",
        "sandbox",
        "secret",
        "audit",
        "quota",
        "tenant_config",
        "user",
        "role_binding",
        "api_key",
        "service_account",
        "feedback",
        "memory_item",  # Stream K.K6 — long-term memory CRUD
        "user_workspace",  # Stream J.15-补强-1 — volume quota + lifecycle
        "image_upload",  # Stream J.6.补强-2 — Mini-ADR J-31
        "skill",  # Stream J.7a — Mini-ADR J-23
        # Capability Uplift Sprint #3 (Mini-ADR U-17) — supporting-files
        # subresource. Mirrors the control-plane ResourceType Literal in
        # services/control-plane/src/control_plane/audit.py (per
        # [memory:audit-literal-drift] — both must stay in sync).
        "skill_supporting_file",
        "artifact",  # Stream J.9-step3 — Mini-ADR J-25
        "approval",  # Stream J.8 — Mini-ADR J-24
        "trigger",  # Stream J.10 — Mini-ADR J-26 / J-42
        "eval_dataset",  # Stream J.12 — Mini-ADR J-43
        "curation_candidate",  # Stream J.12 — Mini-ADR J-43
        "system",  # Stream N — Mini-ADR N-5 (cross-tenant query / tenant switch)
        "run",  # Stream H.3 PR 1 — Mini-ADR H-6 (RUN_LIST_READ)
        "tenant",  # Stream P — Mini-ADR P-1 (POST /v1/tenants)
        "platform_credential",  # Stream P — Mini-ADR P-11 (/v1/platform/credentials)
        "tenant_member",  # Stream R — Mini-ADR R-3 (member onboarding)
        "keycloak_user",  # Stream R — Mini-ADR R-3 (Keycloak account provisioning)
    ]
    resource_id: str | None = None
    result: AuditResult
    reason: str | None = Field(default=None, description="Required when result != success")
    ip: IPv4Address | IPv6Address | None = None
    user_agent: str | None = None
    request_id: UUID | None = None
    trace_id: str | None = None
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Already PII/secret-redacted",
    )
    occurred_at: datetime | None = Field(default=None, description="DB default now() at insert")


class AuditQuery(BaseModel):
    """Read-side query model for ``GET /v1/audit`` (subsystems/17 §3.2)."""

    model_config = ConfigDict(frozen=True)

    tenant_id: UUID | Literal["*"] = Field(description="'*' requires admin role")
    actor_id: str | None = None
    action: AuditAction | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    result: AuditResult | None = None
    from_ts: datetime | None = None
    to_ts: datetime | None = None
    limit: int = Field(default=100, le=1000)
    cursor: str | None = Field(default=None, description="Opaque base64 cursor")


class AuditPage(BaseModel):
    """One page of ``AuditEntry`` results plus an opaque next-cursor.

    ``next_cursor`` is ``None`` when the result set is exhausted; otherwise
    pass it back as :attr:`AuditQuery.cursor` to fetch the next page.
    """

    model_config = ConfigDict(frozen=True)

    entries: list[AuditEntry]
    next_cursor: str | None = None
