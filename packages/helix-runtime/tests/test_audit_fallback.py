"""Unit tests for the audit fallback queues."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from helix_agent.protocol import AuditAction, AuditEntry, AuditResult
from helix_agent.runtime.audit import (
    InMemoryAuditFallbackQueue,
    JsonlFileAuditFallbackQueue,
)


def _entry() -> AuditEntry:
    return AuditEntry(
        tenant_id=uuid4(),
        actor_type="user",
        actor_id="alice",
        action=AuditAction.MANIFEST_WRITE,
        resource_type="manifest",
        resource_id="demo@1",
        result=AuditResult.SUCCESS,
        details={"k": "v"},
    )


@pytest.mark.asyncio
async def test_in_memory_enqueue_preserves_entry_and_reason() -> None:
    queue = InMemoryAuditFallbackQueue()
    entry = _entry()

    await queue.enqueue(entry, reason="ConnectionResetError")
    snapshot = queue.snapshot()

    assert len(snapshot) == 1
    assert snapshot[0].entry == entry
    assert snapshot[0].reason == "ConnectionResetError"


@pytest.mark.asyncio
async def test_in_memory_records_queued_at() -> None:
    queue = InMemoryAuditFallbackQueue()
    before = datetime.now(UTC)
    await queue.enqueue(_entry(), reason="x")
    after = datetime.now(UTC)

    record = queue.snapshot()[0]
    assert before <= record.queued_at <= after


@pytest.mark.asyncio
async def test_jsonl_file_round_trip(tmp_path: Path) -> None:
    queue = JsonlFileAuditFallbackQueue(tmp_path)
    entry = _entry()

    await queue.enqueue(entry, reason="OperationalError: timeout")

    records = list(queue.read_records(datetime.now(UTC)))
    assert len(records) == 1
    # Re-parsed AuditEntry must equal the original; ``model_dump_json`` ↔
    # ``model_validate`` round-trips losslessly for our schema.
    assert records[0].entry == entry
    assert records[0].reason == "OperationalError: timeout"


@pytest.mark.asyncio
async def test_jsonl_file_day_partitioned(tmp_path: Path) -> None:
    queue = JsonlFileAuditFallbackQueue(tmp_path)
    await queue.enqueue(_entry(), reason="r1")
    await queue.enqueue(_entry(), reason="r2")

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    expected_file = tmp_path / f"{today}.jsonl"
    assert expected_file.exists()
    # Two records, one line each.
    assert len(expected_file.read_text().splitlines()) == 2


@pytest.mark.asyncio
async def test_jsonl_file_missing_day_returns_empty(tmp_path: Path) -> None:
    queue = JsonlFileAuditFallbackQueue(tmp_path)
    # No writes — file shouldn't exist.
    other_day = datetime(2024, 1, 1, tzinfo=UTC)
    assert list(queue.read_records(other_day)) == []


@pytest.mark.asyncio
async def test_jsonl_file_creates_parent_dir(tmp_path: Path) -> None:
    # Use a deeper path that does not exist yet.
    nested = tmp_path / "var" / "audit-fallback"
    queue = JsonlFileAuditFallbackQueue(nested)

    await queue.enqueue(_entry(), reason="r")
    assert nested.exists()
