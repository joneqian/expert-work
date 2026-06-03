"""Unit tests for TenantMcpServerRecord validation."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from helix_agent.protocol import TenantMcpServerRecord


def _record(**overrides: object) -> TenantMcpServerRecord:
    base: dict[str, object] = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "name": "github",
        "transport": "streamable_http",
        "url": "https://mcp.example.com/mcp",
        "auth_type": "none",
        "token_secret_ref": None,
        "timeout_s": 30.0,
        "enabled": True,
        "created_at": datetime.now(tz=UTC),
        "updated_at": datetime.now(tz=UTC),
        "created_by": "admin@acme",
    }
    base.update(overrides)
    return TenantMcpServerRecord(**base)  # type: ignore[arg-type]


def test_valid_none_auth_record() -> None:
    rec = _record()
    assert rec.name == "github"
    assert rec.auth_type == "none"


def test_valid_bearer_record_with_token_ref() -> None:
    rec = _record(auth_type="bearer", token_secret_ref="secret://helix-agent/t/mcp/github/token")
    assert rec.auth_type == "bearer"


def test_bearer_without_token_ref_rejected() -> None:
    with pytest.raises(ValueError, match="bearer auth requires token_secret_ref"):
        _record(auth_type="bearer", token_secret_ref=None)


def test_none_auth_with_token_ref_rejected() -> None:
    with pytest.raises(ValueError, match="token_secret_ref must be empty"):
        _record(auth_type="none", token_secret_ref="secret://x")


def test_none_auth_with_empty_string_token_ref_rejected() -> None:
    """Empty string must not bypass the auth guard (falsy trap)."""
    with pytest.raises(ValueError, match="token_secret_ref must be empty"):
        _record(auth_type="none", token_secret_ref="")


def test_bearer_with_plaintext_token_rejected() -> None:
    """Plaintext token values (no secret:// / kms:// prefix) must be rejected."""
    with pytest.raises(ValueError, match="secret:// or kms://"):
        _record(auth_type="bearer", token_secret_ref="ghp_plaintext_token")


@pytest.mark.parametrize("bad_name", ["", "Has Space", "UPPER", "a/b", "x" * 65, "-leading"])
def test_invalid_server_name_rejected(bad_name: str) -> None:
    with pytest.raises(ValueError):
        _record(name=bad_name)


@pytest.mark.parametrize("good_name", ["a", "github", "linear-prod", "pg_main", "a1"])
def test_valid_server_name_accepted(good_name: str) -> None:
    assert _record(name=good_name).name == good_name


def test_frozen() -> None:
    rec = _record()
    with pytest.raises(ValidationError):
        rec.name = "other"  # type: ignore[misc]
