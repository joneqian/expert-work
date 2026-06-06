"""Unit tests for MCP connector catalog records (Stream W, Mini-ADR W-1/W-5)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from helix_agent.protocol import (
    McpConnectorAuthField,
    McpConnectorAuthSchema,
    McpConnectorCatalogPatch,
    McpConnectorCatalogRecord,
    McpConnectorCatalogUpsert,
    TenantPlan,
)


def _bearer_schema() -> McpConnectorAuthSchema:
    return McpConnectorAuthSchema(
        fields=[
            McpConnectorAuthField(key="token", label="API Token", kind="secret"),
            McpConnectorAuthField(key="org", label="Org", kind="param"),
        ]
    )


def _record(**overrides: object) -> McpConnectorCatalogRecord:
    base: dict[str, object] = {
        "id": uuid4(),
        "tenant_id": None,
        "name": "github",
        "display_name": "GitHub",
        "description": "Official GitHub connector",
        "category": "dev",
        "transport": "streamable_http",
        "url_template": "https://mcp.github.example/{org}/mcp",
        "auth_type": "bearer",
        "auth_schema": _bearer_schema(),
        "required_tier": TenantPlan.PRO,
        "enabled": True,
        "created_at": datetime.now(tz=UTC),
        "updated_at": datetime.now(tz=UTC),
        "updated_by": "root@platform",
    }
    base.update(overrides)
    return McpConnectorCatalogRecord(**base)  # type: ignore[arg-type]


def test_valid_bearer_record() -> None:
    rec = _record()
    assert rec.tenant_id is None  # platform-global
    assert rec.required_tier is TenantPlan.PRO
    assert len(rec.auth_schema.secret_fields()) == 1


def test_valid_none_auth_record() -> None:
    rec = _record(auth_type="none", auth_schema=McpConnectorAuthSchema())
    assert rec.auth_type == "none"


def test_default_required_tier_is_free() -> None:
    rec = _record(required_tier=TenantPlan.FREE)
    assert rec.required_tier is TenantPlan.FREE


def test_bearer_requires_exactly_one_secret_field() -> None:
    with pytest.raises(ValueError, match="exactly one secret field"):
        _record(auth_type="bearer", auth_schema=McpConnectorAuthSchema())


def test_bearer_with_two_secret_fields_rejected() -> None:
    two_secrets = McpConnectorAuthSchema(
        fields=[
            McpConnectorAuthField(key="a", label="A", kind="secret"),
            McpConnectorAuthField(key="b", label="B", kind="secret"),
        ]
    )
    with pytest.raises(ValueError, match="exactly one secret field"):
        _record(auth_type="bearer", auth_schema=two_secrets)


def test_none_auth_with_secret_field_rejected() -> None:
    one_secret = McpConnectorAuthSchema(
        fields=[McpConnectorAuthField(key="t", label="T", kind="secret")]
    )
    with pytest.raises(ValueError, match="must not declare secret fields"):
        _record(auth_type="none", auth_schema=one_secret)


# --- Stream MCP-OAUTH (OA-1a) — oauth2 catalog auth type --------------------


def test_valid_oauth2_record() -> None:
    rec = _record(
        auth_type="oauth2",
        auth_schema=McpConnectorAuthSchema(),
        oauth_client_id="helix-linear-app",
        oauth_scopes="read write",
    )
    assert rec.auth_type == "oauth2"
    assert rec.oauth_client_id == "helix-linear-app"
    assert rec.oauth_scopes == "read write"


def test_oauth2_requires_client_id() -> None:
    with pytest.raises(ValueError, match=r"oauth2.*requires oauth_client_id"):
        _record(auth_type="oauth2", auth_schema=McpConnectorAuthSchema())


def test_oauth2_with_secret_field_rejected() -> None:
    one_secret = McpConnectorAuthSchema(
        fields=[McpConnectorAuthField(key="t", label="T", kind="secret")]
    )
    with pytest.raises(ValueError, match=r"oauth2.*must not declare secret fields"):
        _record(
            auth_type="oauth2",
            auth_schema=one_secret,
            oauth_client_id="helix-linear-app",
        )


def test_upsert_oauth2_round_trips() -> None:
    up = McpConnectorCatalogUpsert(
        name="linear",
        display_name="Linear",
        transport="sse",
        url_template="https://mcp.linear.app/sse",
        auth_type="oauth2",
        oauth_client_id="helix-linear-app",
        oauth_scopes="read",
    )
    assert up.auth_type == "oauth2"
    assert up.oauth_client_id == "helix-linear-app"


def test_duplicate_auth_field_keys_rejected() -> None:
    with pytest.raises(ValueError, match="field keys must be unique"):
        McpConnectorAuthSchema(
            fields=[
                McpConnectorAuthField(key="dup", label="A", kind="param"),
                McpConnectorAuthField(key="dup", label="B", kind="param"),
            ]
        )


@pytest.mark.parametrize("bad_name", ["", "Has Space", "UPPER", "a/b", "x" * 65])
def test_invalid_catalog_name_rejected(bad_name: str) -> None:
    with pytest.raises(ValueError):
        _record(name=bad_name)


def test_frozen() -> None:
    rec = _record()
    with pytest.raises(ValidationError):
        rec.name = "other"


def test_upsert_validates_auth_consistency() -> None:
    with pytest.raises(ValueError, match="exactly one secret field"):
        McpConnectorCatalogUpsert(
            name="x",
            display_name="X",
            transport="sse",
            url_template="https://x.example/sse",
            auth_type="bearer",
            auth_schema=McpConnectorAuthSchema(),
        )


def test_upsert_defaults() -> None:
    up = McpConnectorCatalogUpsert(
        name="pg",
        display_name="Postgres",
        transport="sse",
        url_template="https://pg.example/sse",
    )
    assert up.auth_type == "none"
    assert up.required_tier is TenantPlan.FREE
    assert up.enabled is True


def test_patch_partial_leaves_unset() -> None:
    patch = McpConnectorCatalogPatch(required_tier=TenantPlan.ENTERPRISE)
    assert patch.required_tier is TenantPlan.ENTERPRISE
    assert patch.display_name is None
    assert patch.enabled is None


def test_patch_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        McpConnectorCatalogPatch(name="cannot-rename")  # type: ignore[call-arg]
