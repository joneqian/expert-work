"""Unit tests for the ``mcp_oauth_connection`` record — Stream MCP-OAUTH (OA-1b)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from expert_work.protocol import (
    McpOAuthConnectionPatch,
    McpOAuthConnectionRecord,
)

_REF = "secret://expert-work/tenant/t/user/u/mcp/linear/access"


def _record(**over: object) -> McpOAuthConnectionRecord:
    base: dict[str, object] = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "user_id": "user-123",
        "catalog_id": uuid4(),
        "name": "linear",
        "status": "pending",
        "resolved_url": "https://mcp.linear.app/sse",
        "created_at": datetime.now(tz=UTC),
        "updated_at": datetime.now(tz=UTC),
    }
    base.update(over)
    return McpOAuthConnectionRecord(**base)  # type: ignore[arg-type]


def test_valid_pending_record() -> None:
    rec = _record()
    assert rec.status == "pending"
    assert rec.access_token_ref is None
    assert rec.scopes == ""


def test_connected_record_with_token() -> None:
    rec = _record(status="connected", access_token_ref=_REF, scopes="read write")
    assert rec.status == "connected"
    assert rec.access_token_ref == _REF


def test_connected_requires_access_token() -> None:
    with pytest.raises(ValueError, match=r"connected.*requires access_token_ref"):
        _record(status="connected")


def test_invalid_name_rejected() -> None:
    with pytest.raises(ValueError, match="invalid connection name"):
        _record(name="Bad Name")


def test_token_ref_must_be_valid_secret_ref() -> None:
    with pytest.raises(ValueError):
        _record(status="connected", access_token_ref="not-a-secret-ref")


def test_patch_clear_flow_state_flag() -> None:
    patch = McpOAuthConnectionPatch(status="connected", clear_flow_state=True)
    assert patch.clear_flow_state is True
    assert patch.status == "connected"


def test_patch_rejects_unknown_field() -> None:
    with pytest.raises(ValueError):
        McpOAuthConnectionPatch(bogus="x")  # type: ignore[call-arg]
