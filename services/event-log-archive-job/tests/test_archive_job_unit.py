"""Unit tests for the G.8 archive job's pure helpers — no DB, no I/O."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from event_log_archive_job.job import (
    _json_default,
    _normalise_row,
    _object_key,
    _to_jsonl,
)


def test_object_key_is_deterministic_and_partitioned() -> None:
    key = _object_key("tenant-1", "thread-9", datetime(2026, 1, 5, tzinfo=UTC))
    assert key == "event-log/tenant-1/2026/01/thread-9.jsonl"


def test_to_jsonl_one_compact_line_per_row_with_iso_datetimes() -> None:
    rows = [
        {"id": 1, "created_at": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC), "payload": {"k": 1}},
        {"id": 2, "created_at": datetime(2026, 1, 2, 3, 4, 6, tzinfo=UTC), "payload": {"k": 2}},
    ]
    blob = _to_jsonl(rows)
    lines = blob.decode("utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["id"] == 1
    assert first["created_at"] == "2026-01-02T03:04:05+00:00"
    assert first["payload"] == {"k": 1}


def test_normalise_row_reparses_str_payload() -> None:
    # JSONB can surface as a str under a raw text() query — re-parsed.
    assert _normalise_row({"payload": '{"k": 7}'})["payload"] == {"k": 7}
    # An already-dict payload is left untouched.
    assert _normalise_row({"payload": {"k": 7}})["payload"] == {"k": 7}


def test_json_default_serialises_datetime_rejects_others() -> None:
    assert _json_default(datetime(2026, 1, 1, tzinfo=UTC)) == "2026-01-01T00:00:00+00:00"
    with pytest.raises(TypeError, match="not JSON-serialisable"):
        _json_default(object())
