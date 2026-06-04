"""Stream O — TenantConfigRecord credentials_mode + tool_credentials.

Tests that the 2 new credentials fields land with correct defaults +
validate via the Pydantic schema. The all-or-nothing API gate lives
in the control-plane and is tested separately.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from helix_agent.protocol import (
    PROVIDER_CATALOG,
    TOOL_CATALOG,
    TenantConfigPatch,
    TenantConfigRecord,
    TenantPlan,
)

_NOW = datetime.now(UTC)
_TENANT = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")


def _make(**overrides: object) -> TenantConfigRecord:
    base: dict[str, object] = {
        "tenant_id": _TENANT,
        "display_name": "Acme",
        "plan": TenantPlan.FREE,
        "created_at": _NOW,
        "updated_at": _NOW,
        "updated_by": "tester",
    }
    base.update(overrides)
    return TenantConfigRecord(**base)  # type: ignore[arg-type]


def test_default_credentials_mode_is_platform() -> None:
    record = _make()
    assert record.credentials_mode == "platform"
    assert record.tool_credentials == {}


def test_credentials_mode_tenant_rejected() -> None:
    # Stream Y-1 — LLM platform-exclusive: 'tenant' BYOK mode removed.
    with pytest.raises(ValidationError):
        _make(credentials_mode="tenant")


def test_credentials_mode_invalid_rejected() -> None:
    with pytest.raises(ValidationError):
        _make(credentials_mode="other")


def test_tool_credentials_accepts_supported_tool() -> None:
    record = _make(tool_credentials={"web_search": "kms://acme/tavily"})
    assert record.tool_credentials["web_search"] == "kms://acme/tavily"


def test_patch_credentials_mode_field() -> None:
    patch = TenantConfigPatch(credentials_mode="platform")
    assert patch.credentials_mode == "platform"
    assert patch.tool_credentials is None


def test_patch_credentials_mode_tenant_rejected() -> None:
    # Stream Y-1 — PATCH carrying the removed 'tenant' mode is rejected by
    # the narrowed Literal (Pydantic 422 at the API boundary).
    with pytest.raises(ValidationError):
        TenantConfigPatch(credentials_mode="tenant")  # type: ignore[arg-type]


def test_patch_tool_credentials_field() -> None:
    patch = TenantConfigPatch(tool_credentials={"web_search": "kms://acme/tavily"})
    assert patch.tool_credentials == {"web_search": "kms://acme/tavily"}


def test_provider_catalog_export() -> None:
    """Stream O — PROVIDER_CATALOG tuple is the runtime mirror of the
    Provider Literal; deployments use this for startup validation."""
    assert "anthropic" in PROVIDER_CATALOG
    assert "qwen" in PROVIDER_CATALOG


def test_tool_catalog_export() -> None:
    assert "web_search" in TOOL_CATALOG
