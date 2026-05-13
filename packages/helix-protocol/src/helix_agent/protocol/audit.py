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
    # sandbox
    SANDBOX_FORCE_DESTROY = "sandbox:force_destroy"
    SANDBOX_QUOTA_DENIED = "sandbox:quota_denied"
    # api_key
    API_KEY_CREATE = "api_key:create"
    API_KEY_REVOKE = "api_key:revoke"
    # service_account
    SERVICE_ACCOUNT_CREATE = "service_account:create"
    SERVICE_ACCOUNT_DELETE = "service_account:delete"
    SERVICE_ACCOUNT_READ = "service_account:read"
    # role_binding
    ROLE_BINDING_CREATE = "role_binding:create"
    ROLE_BINDING_DELETE = "role_binding:delete"
    ROLE_BINDING_READ = "role_binding:read"
    # audit (meta)
    AUDIT_READ = "audit:read"
    AUDIT_EXPORT = "audit:export"


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
