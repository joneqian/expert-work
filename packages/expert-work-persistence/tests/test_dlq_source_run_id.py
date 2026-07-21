"""P5b-2a — ``DLQRow.source_run_id`` field (DLQ 溯源补齐).

Direct-construction unit test only; ``enqueue`` plumbing is Task 3 and the
migration is exercised end-to-end by Task 4's integration test.
"""

from __future__ import annotations


def test_dlqrow_has_source_run_id_field() -> None:
    from datetime import UTC, datetime
    from uuid import uuid4

    from expert_work.persistence.memory.dlq import DLQRow

    row = DLQRow(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        source_thread_id="t",
        source_run_id="run-123",
        extracted=(("fact", "x"),),
        attempts=0,
        next_retry_at=datetime.now(UTC),
        last_error=None,
        created_at=datetime.now(UTC),
    )
    assert row.source_run_id == "run-123"


async def test_enqueue_persists_source_run_id() -> None:
    from uuid import uuid4

    from expert_work.persistence.memory.dlq import InMemoryMemoryWritebackDLQ

    dlq = InMemoryMemoryWritebackDLQ()
    run = uuid4()
    row = await dlq.enqueue(
        tenant_id=uuid4(), user_id=uuid4(),
        source_thread_id="t", source_run_id=str(run),
        extracted=[("fact", "x")], error="e",
    )
    assert row.source_run_id == str(run)
    ready = await dlq.take_ready(limit=10, now=row.next_retry_at)
    assert ready[0].source_run_id == str(run)
