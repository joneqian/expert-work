"""``mcp_oauth_connection`` record — Stream MCP-OAUTH (OA-1b).

A **per-user** OAuth 2.1 connection to a hosted MCP connector (Notion / Linear /
…). Distinct from ``tenant_mcp_server`` (tenant-level none/bearer instances):
oauth2 connections are keyed by ``(tenant_id, user_id, catalog_id)`` and carry
the access/refresh token *references* (values live in the encrypted secret store,
never here) plus the short-lived PKCE/state flow fields used to correlate the
authorize → callback round-trip.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from expert_work.protocol.platform_secret import validate_secret_ref

__all__ = [
    "McpOAuthConnectionPatch",
    "McpOAuthConnectionRecord",
    "OAuthConnectionStatus",
]

# pending: authorize started, awaiting callback. connected: usable token held.
# expired: access+refresh both unusable (re-auth needed). revoked: user/disconnect.
# error: last refresh/exchange failed.
OAuthConnectionStatus = Literal["pending", "connected", "expired", "revoked", "error"]

# Connection name is the runtime tool namespace (``mcp:<name>.<tool>``) — same
# slug rule as tenant_mcp_server / catalog.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class McpOAuthConnectionRecord(BaseModel):
    """One row of ``mcp_oauth_connection`` as exposed across layers.

    No extra="forbid": materialized from a trusted DB row, not untrusted input.
    Token *values* are never here — only ``secret://`` refs.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: UUID
    user_id: str = Field(min_length=1)
    catalog_id: UUID
    name: str
    status: OAuthConnectionStatus = "pending"
    resolved_url: str = Field(min_length=1)
    scopes: str = ""
    # Per-initiate redirect URI (multi-client OAuth). None = global default was
    # used. Reused at callback for the token exchange.
    redirect_uri: str | None = None
    access_token_ref: str | None = None
    refresh_token_ref: str | None = None
    token_expires_at: datetime | None = None
    # Short-lived flow correlation (cleared once status=connected). ``pkce_verifier``
    # is single-use and useless without the matching authorization code.
    oauth_state: str | None = None
    pkce_verifier: str | None = None
    last_refresh_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("access_token_ref", "refresh_token_ref")
    @classmethod
    def _check_token_ref(cls, value: str | None) -> str | None:
        if value:
            return validate_secret_ref(value)
        return value

    @model_validator(mode="after")
    def _validate(self) -> McpOAuthConnectionRecord:
        if not _NAME_RE.match(self.name):
            msg = f"invalid connection name {self.name!r}: must match ^[a-z0-9][a-z0-9_-]{{0,63}}$"
            raise ValueError(msg)
        if self.status == "connected" and not self.access_token_ref:
            raise ValueError("status='connected' requires access_token_ref")
        return self


class McpOAuthConnectionPatch(BaseModel):
    """Partial update for the connection lifecycle (``None`` = leave unchanged).

    ``clear_flow_state`` nulls ``oauth_state`` + ``pkce_verifier`` (used once the
    callback exchange succeeds) — Optional-means-unchanged can't express a clear.
    ``clear_last_error`` nulls ``last_error`` (a recovered connection — e.g. a
    successful OA-6 refresh — must drop the stale error) for the same reason.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: OAuthConnectionStatus | None = None
    access_token_ref: str | None = None
    refresh_token_ref: str | None = None
    token_expires_at: datetime | None = None
    scopes: str | None = None
    last_refresh_at: datetime | None = None
    last_error: str | None = None
    clear_flow_state: bool = False
    clear_last_error: bool = False

    @field_validator("access_token_ref", "refresh_token_ref")
    @classmethod
    def _check_token_ref(cls, value: str | None) -> str | None:
        if value:
            return validate_secret_ref(value)
        return value
