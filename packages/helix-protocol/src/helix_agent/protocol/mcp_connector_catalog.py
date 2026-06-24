"""``mcp_connector_catalog`` records — Stream W (Mini-ADR W-1, W-5).

A **platform-curated** catalog of MCP connector *types* (e.g. an "official
GitHub connector"). A tenant *instantiates* a catalog entry by supplying its own
credentials, producing a per-tenant :class:`TenantMcpServerRecord` bound to the
entry (``catalog_id``). The catalog defines the type; the tenant instance holds
the per-company credentials — connections are inherently per-company.

The catalog table is platform-global (``tenant_id`` is ``None`` for now; the
column is kept so future per-tenant private catalogs are a non-migration change,
mirroring ``encrypted_secret``).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from helix_agent.protocol.tenant_config import TenantPlan

__all__ = [
    "McpConnectorAuthField",
    "McpConnectorAuthSchema",
    "McpConnectorCatalogPatch",
    "McpConnectorCatalogRecord",
    "McpConnectorCatalogUpsert",
]

# Catalog entry name doubles as the default instance name + secret path slug,
# so it must satisfy the same slug rule as a tenant MCP server name.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

CatalogTransport = Literal["sse", "streamable_http"]
# ``oauth2`` (Stream MCP-OAUTH) instantiates into a per-user ``mcp_oauth_connection``
# via the OAuth 2.1 + PKCE flow, not into a tenant_mcp_server row. The token comes
# from the flow, so an oauth2 entry declares no user-supplied secret fields.
CatalogAuthType = Literal["none", "bearer", "oauth2"]
AuthFieldKind = Literal["secret", "param"]


class McpConnectorAuthField(BaseModel):
    """One credential / parameter the tenant must supply at instantiation.

    ``kind="secret"`` values (e.g. an API token) are written to the encrypted
    secret store and bound to the instance's ``token_secret_ref``;
    ``kind="param"`` values (e.g. an org slug) fill ``url_template`` placeholders.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=128)
    kind: AuthFieldKind
    required: bool = True


class McpConnectorAuthSchema(BaseModel):
    """Declarative list of fields a tenant supplies when instantiating an entry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fields: list[McpConnectorAuthField] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_duplicate_keys(self) -> McpConnectorAuthSchema:
        keys = [f.key for f in self.fields]
        if len(keys) != len(set(keys)):
            raise ValueError("auth_schema field keys must be unique")
        return self

    def secret_fields(self) -> list[McpConnectorAuthField]:
        """The ``kind="secret"`` fields (each backs an encrypted secret_ref)."""
        return [f for f in self.fields if f.kind == "secret"]


def _validate_catalog_name(value: str) -> str:
    if not _NAME_RE.match(value):
        msg = (
            f"invalid catalog name {value!r}: must match "
            r"^[a-z0-9][a-z0-9_-]{0,63}$"
        )
        raise ValueError(msg)
    return value


def _validate_auth_consistency(
    *,
    auth_type: CatalogAuthType,
    auth_schema: McpConnectorAuthSchema,
    oauth_client_id: str | None = None,
    bearer_token_ref: str | None = None,
    bearer_token: SecretStr | None = None,
) -> None:
    """Cross-field auth invariants.

    ``bearer`` has a token source = a **platform-supplied** bearer token (the new
    shared-server model A: ``bearer_token`` plaintext on input, or its persisted
    ``bearer_token_ref``) **or** exactly one ``auth_schema`` secret field (the
    legacy tenant-fills model, removed in a later phase). The two are mutually
    exclusive. ``none`` carries no secret of either kind. ``oauth2`` carries no
    bearer/secret and requires ``oauth_client_id`` (token comes from the flow).
    """
    secrets = auth_schema.secret_fields()
    has_platform_token = bearer_token_ref is not None or bearer_token is not None
    if auth_type == "bearer":
        if has_platform_token and secrets:
            raise ValueError(
                "bearer auth: a platform bearer token cannot combine with auth_schema secret fields"
            )
        if not has_platform_token and len(secrets) != 1:
            raise ValueError(
                "bearer auth requires a platform bearer token or exactly one secret field"
            )
    if auth_type == "none":
        if secrets:
            raise ValueError("auth_type='none' must not declare secret fields")
        if has_platform_token:
            raise ValueError("auth_type='none' must not carry a bearer token")
    if auth_type == "oauth2":
        if secrets:
            raise ValueError("auth_type='oauth2' must not declare secret fields")
        if has_platform_token:
            raise ValueError("auth_type='oauth2' must not carry a bearer token")
        if not oauth_client_id:
            raise ValueError("auth_type='oauth2' requires oauth_client_id")


class McpConnectorCatalogRecord(BaseModel):
    """One row of ``mcp_connector_catalog`` as exposed across layers.

    No extra="forbid": materialized from a trusted DB row, not untrusted input.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    # NULL = platform-global (the only shape today). Kept so future per-tenant
    # private catalogs are a non-migration change.
    tenant_id: UUID | None = None
    name: str
    display_name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)
    category: str = Field(default="general", max_length=64)
    icon: str | None = None
    transport: CatalogTransport
    url_template: str = Field(min_length=1)
    auth_type: CatalogAuthType = "none"
    auth_schema: McpConnectorAuthSchema = Field(default_factory=McpConnectorAuthSchema)
    # Stream MCP-OAUTH — platform-registered OAuth app for an ``oauth2`` entry.
    # NULL for none/bearer. ``oauth_scopes`` is space-separated (OAuth convention).
    oauth_client_id: str | None = None
    oauth_scopes: str | None = None
    # Platform-supplied bearer token (shared server A) — ``secret://`` ref only.
    bearer_token_ref: str | None = None
    required_tier: TenantPlan = TenantPlan.FREE
    enabled: bool = True
    created_at: datetime
    updated_at: datetime
    updated_by: str

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return _validate_catalog_name(value)

    @model_validator(mode="after")
    def _validate(self) -> McpConnectorCatalogRecord:
        _validate_auth_consistency(
            auth_type=self.auth_type,
            auth_schema=self.auth_schema,
            oauth_client_id=self.oauth_client_id,
            bearer_token_ref=self.bearer_token_ref,
        )
        return self


class McpConnectorCatalogUpsert(BaseModel):
    """Create payload (system_admin ``POST /v1/platform/mcp-catalog``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    display_name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)
    category: str = Field(default="general", max_length=64)
    icon: str | None = None
    transport: CatalogTransport
    url_template: str = Field(min_length=1)
    auth_type: CatalogAuthType = "none"
    auth_schema: McpConnectorAuthSchema = Field(default_factory=McpConnectorAuthSchema)
    oauth_client_id: str | None = None
    oauth_scopes: str | None = None
    # Write-only platform bearer token (plaintext, shared server A). The API
    # writes it to the SecretStore and sets ``bearer_token_ref``; the persistence
    # layer reads only the ref, never this value. ``model_dump`` masks it.
    bearer_token: SecretStr | None = None
    bearer_token_ref: str | None = None
    required_tier: TenantPlan = TenantPlan.FREE
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return _validate_catalog_name(value)

    @model_validator(mode="after")
    def _validate(self) -> McpConnectorCatalogUpsert:
        _validate_auth_consistency(
            auth_type=self.auth_type,
            auth_schema=self.auth_schema,
            oauth_client_id=self.oauth_client_id,
            bearer_token_ref=self.bearer_token_ref,
            bearer_token=self.bearer_token,
        )
        return self


class McpConnectorCatalogPatch(BaseModel):
    """Partial update payload (``PATCH``). ``None`` = leave unchanged.

    ``name``/``transport`` are immutable post-create (they shape instance names
    and the runtime namespace) — re-create to change them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    category: str | None = Field(default=None, max_length=64)
    icon: str | None = None
    url_template: str | None = Field(default=None, min_length=1)
    auth_schema: McpConnectorAuthSchema | None = None
    # Re-paste the platform bearer token (write-only plaintext); ``None`` = keep
    # the existing one. The API writes it and sets ``bearer_token_ref``.
    bearer_token: SecretStr | None = None
    bearer_token_ref: str | None = None
    required_tier: TenantPlan | None = None
    enabled: bool | None = None
