"""``platform_agent_template`` records — Stream Agent-Templates (M1).

A **platform-curated** catalog of official Agent *templates*. A template holds a
complete base :class:`AgentSpec` manifest; a tenant *instantiates* it by creating
its own per-user agent whose manifest declares ``extends: "<name>@<version>"`` and
carries only a tier③ override + tier② capability delta (see
``docs/design/platform-agent-templates.md``). The base flows in at build time via
``resolve_extends`` (a later PR), so a platform fix to the base reaches every
instance without copying.

The table mirrors ``mcp_connector_catalog``'s NULL-tenant platform-row pattern
(``tenant_id`` is NULL = platform-global, the only shape today; the column is kept
so future per-tenant private template libraries are a non-migration change) and
``agent_spec``'s ``(name, version)`` versioning so tenants can pin ``@1.2.0``.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from expert_work.protocol.agent_spec import AgentSpec
from expert_work.protocol.tenant_config import TenantPlan

__all__ = [
    "PlatformAgentTemplatePatch",
    "PlatformAgentTemplateRecord",
    "PlatformAgentTemplateStatus",
    "PlatformAgentTemplateUpsert",
]

# Template name doubles as the ``extends`` reference slug, so it shares the agent
# manifest name rule (lowercase slug).
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class PlatformAgentTemplateStatus(StrEnum):
    """Publication lifecycle. ``DRAFT`` templates are admin-only (not offered to
    tenants); ``PUBLISHED`` ones appear in the tenant template marketplace."""

    DRAFT = "draft"
    PUBLISHED = "published"


def _validate_template_name(value: str) -> str:
    if not _NAME_RE.match(value):
        msg = f"invalid template name {value!r}: must match ^[a-z0-9][a-z0-9_-]{{0,63}}$"
        raise ValueError(msg)
    return value


class PlatformAgentTemplateRecord(BaseModel):
    """One row of ``platform_agent_template`` as exposed across layers.

    No ``extra="forbid"``: materialized from a trusted DB row, not untrusted input.
    ``name``/``version`` mirror ``spec.metadata`` (the store derives them on write).
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    # NULL = platform-global (the only shape today). Kept so future per-tenant
    # private template libraries are a non-migration change.
    tenant_id: UUID | None = None
    name: str
    version: str
    spec: AgentSpec
    spec_sha256: str = Field(min_length=64, max_length=64)
    # Marketplace metadata (governance-level, distinct from ``spec.description``
    # which is the agent's runtime self-description).
    display_name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)
    category: str = Field(default="general", max_length=64)
    icon: str | None = None
    required_tier: TenantPlan = TenantPlan.FREE
    status: PlatformAgentTemplateStatus = PlatformAgentTemplateStatus.DRAFT
    enabled: bool = True
    created_by: str
    created_at: datetime
    updated_at: datetime

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return _validate_template_name(value)


class PlatformAgentTemplateUpsert(BaseModel):
    """Create payload (system_admin ``POST /v1/platform/agent-templates``).

    ``name``/``version`` are derived from ``spec.metadata`` by the store, not
    supplied here (single source of truth = the manifest)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    spec: AgentSpec
    display_name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)
    category: str = Field(default="general", max_length=64)
    icon: str | None = None
    required_tier: TenantPlan = TenantPlan.FREE
    status: PlatformAgentTemplateStatus = PlatformAgentTemplateStatus.DRAFT
    enabled: bool = True


class PlatformAgentTemplatePatch(BaseModel):
    """Partial metadata update (``PATCH``). ``None`` = leave unchanged.

    Spec changes go through a separate ``update_spec`` (publish a new version or
    edit the current one in place), so the manifest stays the single source of
    ``name``/``version`` — neither is patchable here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    category: str | None = Field(default=None, max_length=64)
    icon: str | None = None
    required_tier: TenantPlan | None = None
    status: PlatformAgentTemplateStatus | None = None
    enabled: bool | None = None
