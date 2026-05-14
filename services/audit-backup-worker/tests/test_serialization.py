"""Unit tests for the audit-row JSON serializer."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from ipaddress import IPv4Address
from uuid import UUID

from audit_backup_worker.serialization import object_key_for, serialize_row
from helix_agent.persistence.models import AuditLogRow


def _row(**overrides: object) -> AuditLogRow:
    defaults: dict[str, object] = {
        "id": 42,
        "tenant_id": UUID("00000000-0000-0000-0000-000000000001"),
        "actor_type": "user",
        "actor_id": "alice",
        "on_behalf_of": None,
        "action": "auth:login",
        "resource_type": "user",
        "resource_id": "alice",
        "result": "success",
        "reason": None,
        "ip": IPv4Address("10.0.0.1"),
        "user_agent": "pytest",
        "request_id": UUID("11111111-1111-1111-1111-111111111111"),
        "trace_id": "abc123",
        "details": {"k": "v"},
        "occurred_at": datetime(2026, 5, 14, 12, 30, 45, tzinfo=UTC),
        "backup_acked": False,
        "backup_acked_at": None,
    }
    defaults.update(overrides)
    return AuditLogRow(**defaults)


def test_serialize_round_trip_basic_fields() -> None:
    row = _row()
    payload = json.loads(serialize_row(row))
    assert payload["id"] == 42
    assert payload["tenant_id"] == "00000000-0000-0000-0000-000000000001"
    assert payload["actor_id"] == "alice"
    assert payload["action"] == "auth:login"
    assert payload["details"] == {"k": "v"}
    assert payload["occurred_at"] == "2026-05-14T12:30:45+00:00"
    assert payload["ip"] == "10.0.0.1"
    assert payload["request_id"] == "11111111-1111-1111-1111-111111111111"


def test_serialize_omits_backup_acked_columns() -> None:
    """backup_acked / backup_acked_at are worker progress markers, not audit data."""
    row = _row(backup_acked=True, backup_acked_at=datetime.now(tz=UTC))
    payload = json.loads(serialize_row(row))
    assert "backup_acked" not in payload
    assert "backup_acked_at" not in payload


def test_serialize_handles_null_fields() -> None:
    row = _row(on_behalf_of=None, reason=None, request_id=None, trace_id=None, ip=None)
    payload = json.loads(serialize_row(row))
    assert payload["on_behalf_of"] is None
    assert payload["request_id"] is None
    assert payload["ip"] is None


def test_serialize_is_deterministic() -> None:
    """Keys sorted + compact separators → byte-identical output for same row."""
    row = _row()
    assert serialize_row(row) == serialize_row(row)


def test_object_key_for_uses_year_month_day_prefix() -> None:
    row = _row(
        id=7,
        tenant_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        occurred_at=datetime(2026, 5, 14, 0, 0, 0, tzinfo=UTC),
    )
    assert object_key_for(row) == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/2026/05/14/7.json"
