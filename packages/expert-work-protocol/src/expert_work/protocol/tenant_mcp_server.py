"""``tenant_mcp_server`` registry record — Stream V.

A tenant-registered **remote** MCP server (sse / streamable_http). stdio
servers are operator-only (subprocess RCE risk) and never live here. The
bearer token is stored in the encrypted secret store; this record holds only
its ``secret://`` reference.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from expert_work.protocol.platform_secret import validate_secret_ref

__all__ = [
    "McpServerAuthType",
    "McpServerProbeStatus",
    "McpServerTransport",
    "TenantMcpServerPatch",
    "TenantMcpServerRecord",
]

McpServerTransport = Literal["sse", "streamable_http"]
McpServerAuthType = Literal["none", "bearer"]
# Result of the most recent connectivity probe. ``None`` on the record means the
# server has never been probed (treat as "unknown" in the UI).
McpServerProbeStatus = Literal["ok", "error"]

# Server name is used in the runtime tool namespace (``mcp:<name>.<tool>``)
# and in the secret path — restrict to a safe slug.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class TenantMcpServerRecord(BaseModel):
    """One row of ``tenant_mcp_server`` as exposed across layers.

    No extra="forbid": materialized from a trusted DB row, not untrusted API input.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: UUID
    name: str
    transport: McpServerTransport
    url: str = Field(min_length=1)
    auth_type: McpServerAuthType = "none"
    token_secret_ref: str | None = None
    # Custom HTTP headers (M1). Values may be secrets, so the {name: value} map
    # lives in the SecretStore and the row holds only the ``secret://`` ref; the
    # header names are kept for display. ``custom_header_names`` is the only one
    # surfaced to the API (the ref is internal — resolved at connect-out).
    custom_headers_ref: str | None = None
    custom_header_names: list[str] | None = None
    # SSE read timeout override (M1) — None keeps the SDK default (300s).
    sse_read_timeout_s: float | None = Field(default=None, gt=0, le=3600)
    timeout_s: float = Field(default=30.0, gt=0, le=300)
    enabled: bool = True
    # Stream W (Mini-ADR W-2). NULL = off-catalog custom server (every
    # Stream V row); non-NULL = an instantiation of an ``mcp_connector_catalog``
    # entry. Resolved url/transport/auth are snapshotted onto this row at
    # instantiation, so the runtime pool reads this record unchanged.
    catalog_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
    created_by: str
    # Connectivity health (#2): the result of the most recent probe (registration,
    # on-demand tools listing, or update re-probe). All three None = never probed.
    # Health is observational — it never gates tool assembly.
    last_probe_at: datetime | None = None
    last_probe_status: McpServerProbeStatus | None = None
    last_probe_error: str | None = None

    @field_validator("token_secret_ref", "custom_headers_ref")
    @classmethod
    def _check_token_ref(cls, value: str | None) -> str | None:
        if value:  # non-None and non-empty: must be a valid ref
            return validate_secret_ref(value)
        return value  # None or "" flows through; model_validator handles auth rules

    @model_validator(mode="after")
    def _validate(self) -> TenantMcpServerRecord:
        if not _NAME_RE.match(self.name):
            msg = (
                f"invalid MCP server name {self.name!r}: must match "
                r"^[a-z0-9][a-z0-9_-]{0,63}$"
            )
            raise ValueError(msg)
        if self.auth_type == "bearer" and not self.token_secret_ref:
            raise ValueError("bearer auth requires token_secret_ref")
        if self.auth_type == "none" and self.token_secret_ref is not None:
            raise ValueError("token_secret_ref must be empty when auth_type='none'")
        if bool(self.custom_headers_ref) != bool(self.custom_header_names):
            raise ValueError(
                "custom_headers_ref and custom_header_names must both be set or both empty"
            )
        return self


class TenantMcpServerPatch(BaseModel):
    """Partial update payload (V-C ``PATCH``). ``None`` = leave unchanged.

    Auth-type changes are out of scope — to switch between none/bearer, delete
    and re-register. Rotating a bearer token sets a new ``token_secret_ref``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str | None = Field(default=None, min_length=1)
    token_secret_ref: str | None = None
    # Custom headers (M1): set both together to replace the header set; both
    # None = leave unchanged (clearing is via delete+recreate, like auth-type).
    custom_headers_ref: str | None = None
    custom_header_names: list[str] | None = None
    sse_read_timeout_s: float | None = Field(default=None, gt=0, le=3600)
    timeout_s: float | None = Field(default=None, gt=0, le=300)
    enabled: bool | None = None

    @field_validator("token_secret_ref", "custom_headers_ref")
    @classmethod
    def _check_token_ref(cls, value: str | None) -> str | None:
        if value:  # non-None and non-empty: must be a valid ref
            return validate_secret_ref(value)
        return value  # None or "" flows through
