"""Per-tenant runtime configuration schemas — Stream C.7.

Distinct from :class:`helix_agent.protocol.agent_spec.TenantConfig`
(which is an *agent manifest* fragment). The schemas here back the
``tenant_config`` table from migration 0007 and the C.7 admin
``GET / PUT /v1/tenants/{tid}/config`` endpoints.

Subsystems/15 § 6 + STREAM-C-DESIGN § 2.8 set the contract:

* ``model_credentials_ref`` is ``{<provider>: "kms://..."}``. The
  control plane stores only the URI; F.6 ``SecretStore`` resolves it
  on the LLM-call hot path.
* ``mcp_allowlist`` controls which MCP servers the tenant may
  connect to (Stream E).
* ``rate_limit_override`` is consumed by :class:`TenantRateLimitMiddleware`
  in M1; in M0 it lands here as forward-compatible storage.
* ``pii_fields`` feeds the Stream D PII redactor.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "TenantConfigPatch",
    "TenantConfigRecord",
    "TenantPlan",
]


class TenantPlan(StrEnum):
    """Pricing tier label. M0 informational only; M1 derives default quotas."""

    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


_RETENTION_MIN_DAYS = 1
_RETENTION_MAX_DAYS = 3650


class TenantConfigRecord(BaseModel):
    """One row of ``tenant_config`` as exposed by the admin API."""

    model_config = ConfigDict(frozen=True)

    tenant_id: UUID
    display_name: str
    plan: TenantPlan = TenantPlan.FREE
    model_credentials_ref: dict[str, str] = Field(default_factory=dict)
    mcp_allowlist: list[str] = Field(default_factory=list)
    rate_limit_override: dict[str, Any] = Field(default_factory=dict)
    pii_fields: list[str] = Field(default_factory=list)
    # D.3: per-tenant retention. Bounded ranges mirror the DB CHECK
    # constraints in migration 0010 so admin clients fail fast rather
    # than tripping a 23514 at write time.
    audit_retention_days: int = Field(default=90, ge=_RETENTION_MIN_DAYS, le=_RETENTION_MAX_DAYS)
    event_log_retention_days: int = Field(
        default=30, ge=_RETENTION_MIN_DAYS, le=_RETENTION_MAX_DAYS
    )
    created_at: datetime
    updated_at: datetime
    updated_by: str


class TenantConfigPatch(BaseModel):
    """Admin ``PUT /v1/tenants/{tid}/config`` payload.

    All fields are optional so admins can update one knob at a time.
    Empty containers are *meaningful* values (e.g. clear the
    allowlist); to leave a field untouched, omit the key entirely.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    display_name: str | None = None
    plan: TenantPlan | None = None
    model_credentials_ref: dict[str, str] | None = None
    mcp_allowlist: list[str] | None = None
    rate_limit_override: dict[str, Any] | None = None
    pii_fields: list[str] | None = None
    audit_retention_days: int | None = Field(
        default=None, ge=_RETENTION_MIN_DAYS, le=_RETENTION_MAX_DAYS
    )
    event_log_retention_days: int | None = Field(
        default=None, ge=_RETENTION_MIN_DAYS, le=_RETENTION_MAX_DAYS
    )
