"""Stream W (Mini-ADR W-4): allow_custom_mcp_servers on tenant config."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from expert_work.protocol import TenantConfigPatch, TenantConfigRecord


def _record(**overrides: object) -> TenantConfigRecord:
    base: dict[str, object] = {
        "tenant_id": uuid4(),
        "display_name": "Acme",
        "created_at": datetime.now(tz=UTC),
        "updated_at": datetime.now(tz=UTC),
        "updated_by": "admin@acme",
    }
    base.update(overrides)
    return TenantConfigRecord(**base)  # type: ignore[arg-type]


def test_allow_custom_mcp_servers_defaults_true() -> None:
    """Preserves Stream V self-service behavior unless a platform admin opts out."""
    assert _record().allow_custom_mcp_servers is True


def test_allow_custom_mcp_servers_can_be_disabled() -> None:
    assert _record(allow_custom_mcp_servers=False).allow_custom_mcp_servers is False


def test_patch_default_leaves_unchanged() -> None:
    assert TenantConfigPatch().allow_custom_mcp_servers is None


def test_patch_can_set_flag() -> None:
    assert TenantConfigPatch(allow_custom_mcp_servers=False).allow_custom_mcp_servers is False
