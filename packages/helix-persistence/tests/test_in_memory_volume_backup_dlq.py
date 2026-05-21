"""Unit tests for InMemoryVolumeBackupDLQ — Stream J.15-补强-2 contract."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from helix_agent.persistence import InMemoryVolumeBackupDLQ


@pytest.mark.asyncio
async def test_enqueue_creates_row_with_attempts_zero() -> None:
    dlq = InMemoryVolumeBackupDLQ()
    row = await dlq.enqueue(
        tenant_id=uuid4(),
        user_id=uuid4(),
        workspace_id=uuid4(),
        volume_name="helix-ws-x",
        op_kind="archive",
        error="boom",
    )
    assert row.attempts == 0
    assert row.last_error == "boom"
    assert row.op_kind == "archive"


@pytest.mark.asyncio
async def test_take_ready_returns_only_ready_rows_oldest_first() -> None:
    dlq = InMemoryVolumeBackupDLQ()
    older = await dlq.enqueue(
        tenant_id=uuid4(),
        user_id=uuid4(),
        workspace_id=uuid4(),
        volume_name="v1",
        op_kind="backup",
        error="e1",
    )
    newer = await dlq.enqueue(
        tenant_id=uuid4(),
        user_id=uuid4(),
        workspace_id=uuid4(),
        volume_name="v2",
        op_kind="backup",
        error="e2",
    )

    # ``now`` is sampled after both enqueues so each row's
    # ``next_retry_at`` (set at enqueue to "now()") is <= test now.
    poll_at = datetime.now(UTC) + timedelta(milliseconds=1)
    # Push the newer one's next_retry_at into the future.
    await dlq.record_failure(
        row_id=newer.id, error="fail", next_retry_at=poll_at + timedelta(hours=1)
    )

    ready = await dlq.take_ready(limit=10, now=poll_at)
    assert [r.id for r in ready] == [older.id]


@pytest.mark.asyncio
async def test_mark_done_removes_row() -> None:
    dlq = InMemoryVolumeBackupDLQ()
    row = await dlq.enqueue(
        tenant_id=uuid4(),
        user_id=uuid4(),
        workspace_id=uuid4(),
        volume_name="v",
        op_kind="archive",
        error="e",
    )
    assert await dlq.count() == 1
    await dlq.mark_done(row_id=row.id)
    assert await dlq.count() == 0


@pytest.mark.asyncio
async def test_record_failure_bumps_attempts_and_schedules() -> None:
    dlq = InMemoryVolumeBackupDLQ()
    row = await dlq.enqueue(
        tenant_id=uuid4(),
        user_id=uuid4(),
        workspace_id=uuid4(),
        volume_name="v",
        op_kind="archive",
        error="e1",
    )
    when = datetime.now(UTC) + timedelta(minutes=5)
    await dlq.record_failure(row_id=row.id, error="e2", next_retry_at=when)
    [updated] = await dlq.take_ready(limit=10, now=when + timedelta(seconds=1))
    assert updated.attempts == 1
    assert updated.last_error == "e2"
    assert updated.next_retry_at == when


@pytest.mark.asyncio
async def test_record_failure_on_missing_row_is_silent() -> None:
    dlq = InMemoryVolumeBackupDLQ()
    # mark_done first, then record_failure — the recorded entry is
    # gone, so this is a no-op (matches the SQL semantics where the
    # UPDATE matches zero rows).
    await dlq.record_failure(row_id=uuid4(), error="late", next_retry_at=datetime.now(UTC))
    assert await dlq.count() == 0
