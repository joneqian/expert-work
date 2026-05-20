"""Stream K.K7 — ``MemoryDLQWorker`` retry semantics.

Covers the worker's behaviour against an in-memory DLQ + memory store:

- happy retry → row removed
- transient failure → row stays, ``attempts`` bumps, ``next_retry_at``
  pushed by the configured backoff
- ``attempts >= max_attempts`` → row marked as dead letter (not
  retried, ``next_retry_at`` parked far in the future)
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from control_plane.memory import MemoryDLQWorker
from helix_agent.persistence import InMemoryMemoryStore
from helix_agent.persistence.memory import InMemoryMemoryWritebackDLQ

_TENANT = uuid4()
_USER = uuid4()


class _ScriptedEmbedder:
    """Each call returns the next scripted output. Failure rotation is
    handled by raising from a recorded exception."""

    def __init__(
        self,
        outputs: Sequence[list[tuple[float, ...]] | Exception],
    ) -> None:
        self._outputs = list(outputs)
        self.calls = 0

    async def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        idx = self.calls
        self.calls += 1
        if idx >= len(self._outputs):
            raise RuntimeError(f"scripted embedder ran out at call {idx}")
        out = self._outputs[idx]
        if isinstance(out, Exception):
            raise out
        # The recorded output has one vector per text; assert the
        # shape matches so a buggy script surfaces here, not in the
        # store.
        assert len(out) == len(texts), (out, texts)
        return out


async def _seed_dlq(dlq: InMemoryMemoryWritebackDLQ, *, error: str = "boom") -> None:
    await dlq.enqueue(
        tenant_id=_TENANT,
        user_id=_USER,
        source_thread_id="t-1",
        extracted=[("fact", "Likes coffee")],
        error=error,
    )


@pytest.mark.asyncio
async def test_happy_retry_writes_memory_and_clears_dlq() -> None:
    dlq = InMemoryMemoryWritebackDLQ()
    store = InMemoryMemoryStore()
    await _seed_dlq(dlq)
    embedder = _ScriptedEmbedder([[(0.1, 0.2, 0.3)]])

    worker = MemoryDLQWorker(dlq=dlq, memory_store=store, embedder=embedder)
    succeeded, retried, dead = await worker.run_once()

    assert (succeeded, retried, dead) == (1, 0, 0)
    assert await dlq.count() == 0
    items = await store.list_for_user(tenant_id=_TENANT, user_id=_USER)
    assert len(items) == 1
    assert items[0].content == "Likes coffee"


@pytest.mark.asyncio
async def test_transient_failure_schedules_backoff_retry() -> None:
    """An embedder failure leaves the row in the DLQ with attempts=1
    and ``next_retry_at`` pushed to the first-backoff slot (1 min)."""
    dlq = InMemoryMemoryWritebackDLQ()
    store = InMemoryMemoryStore()
    await _seed_dlq(dlq)
    # First call raises; the worker must catch and reschedule.
    embedder = _ScriptedEmbedder([RuntimeError("embed-fail")])

    worker = MemoryDLQWorker(dlq=dlq, memory_store=store, embedder=embedder)
    succeeded, retried, dead = await worker.run_once()
    assert (succeeded, retried, dead) == (0, 1, 0)

    # Still in the queue, attempts bumped, next_retry_at pushed >= 60s out.
    pending = list(dlq._rows.values())
    assert len(pending) == 1
    pending_row = pending[0]
    assert pending_row.attempts == 1
    assert pending_row.last_error is not None
    assert "embed-fail" in pending_row.last_error
    # Sanity on the schedule — first backoff is 60 s.
    delta = (pending_row.next_retry_at - datetime.now(UTC)).total_seconds()
    assert 30 <= delta <= 90, delta


@pytest.mark.asyncio
async def test_max_attempts_marks_dead_letter() -> None:
    """A row at ``attempts == max_attempts - 1`` whose retry fails
    becomes a dead letter; future ``take_ready`` calls inside the
    backoff window skip it."""
    dlq = InMemoryMemoryWritebackDLQ()
    store = InMemoryMemoryStore()
    await _seed_dlq(dlq)
    # Bring the row to attempts=4 — next failure crosses the threshold
    # (max_attempts default=5).
    row_id = next(iter(dlq._rows))
    for _ in range(4):
        await dlq.record_failure(
            row_id=row_id,
            error="prior",
            when=datetime.now(UTC),
            next_retry_at=datetime.now(UTC),
        )

    embedder = _ScriptedEmbedder([RuntimeError("still broken")])
    worker = MemoryDLQWorker(dlq=dlq, memory_store=store, embedder=embedder)
    succeeded, retried, dead = await worker.run_once()
    assert (succeeded, retried, dead) == (0, 0, 1)

    pending = list(dlq._rows.values())
    assert len(pending) == 1, "dead letter should remain in the queue for review"
    # next_retry_at parked far ahead so the cycle doesn't keep
    # re-trying it.
    far = (pending[0].next_retry_at - datetime.now(UTC)).total_seconds()
    assert far > 30 * 24 * 3600  # parked > 30 days out


@pytest.mark.asyncio
async def test_run_once_is_noop_when_queue_empty() -> None:
    """``take_ready`` returns nothing → counters all zero, no calls
    to the embedder."""
    dlq = InMemoryMemoryWritebackDLQ()
    store = InMemoryMemoryStore()
    embedder = _ScriptedEmbedder([])  # no outputs → would raise if called
    worker = MemoryDLQWorker(dlq=dlq, memory_store=store, embedder=embedder)

    result = await worker.run_once()

    assert result == (0, 0, 0)
    assert embedder.calls == 0


@pytest.mark.asyncio
async def test_take_ready_skips_rows_not_yet_due(monkeypatch: pytest.MonkeyPatch) -> None:
    """A row whose next_retry_at is later than ``now`` is left alone."""
    dlq = InMemoryMemoryWritebackDLQ()
    store = InMemoryMemoryStore()
    await _seed_dlq(dlq)
    row_id = next(iter(dlq._rows))
    # Push the row's retry into the future — worker.run_once with the
    # current clock must not pick it up.
    future = datetime.now(UTC).replace(year=datetime.now(UTC).year + 1)
    await dlq.record_failure(
        row_id=row_id, error="later", when=datetime.now(UTC), next_retry_at=future
    )

    embedder = _ScriptedEmbedder([])
    worker = MemoryDLQWorker(dlq=dlq, memory_store=store, embedder=embedder)
    succeeded, retried, dead = await worker.run_once()

    assert (succeeded, retried, dead) == (0, 0, 0)
    assert embedder.calls == 0


@pytest.mark.asyncio
async def test_rejects_non_positive_constructor_args() -> None:
    dlq = InMemoryMemoryWritebackDLQ()
    store = InMemoryMemoryStore()
    embedder = _ScriptedEmbedder([])
    with pytest.raises(ValueError, match="interval_s"):
        MemoryDLQWorker(dlq=dlq, memory_store=store, embedder=embedder, interval_s=0)
    with pytest.raises(ValueError, match="batch_size"):
        MemoryDLQWorker(dlq=dlq, memory_store=store, embedder=embedder, batch_size=0)
    with pytest.raises(ValueError, match="max_attempts"):
        MemoryDLQWorker(dlq=dlq, memory_store=store, embedder=embedder, max_attempts=0)


@pytest.mark.asyncio
async def test_dedup_skips_duplicate_writeback_via_on_conflict() -> None:
    """Stream K.K7 dedup — writing the same content twice yields one
    row, not two. (Mirrors what the SQL UNIQUE + ON CONFLICT does.)"""
    store = InMemoryMemoryStore()
    from helix_agent.protocol import MemoryItem

    same_text = "Loves espresso"
    items = [
        MemoryItem(
            id=uuid4(),
            tenant_id=_TENANT,
            user_id=_USER,
            kind="fact",
            content=same_text,
            embedding=(0.1, 0.2, 0.3),
        ),
        MemoryItem(
            id=uuid4(),
            tenant_id=_TENANT,
            user_id=_USER,
            kind="fact",
            content=same_text,  # identical
            embedding=(0.4, 0.5, 0.6),
        ),
    ]
    await store.write(items)
    out = await store.list_for_user(tenant_id=_TENANT, user_id=_USER)
    assert len(out) == 1
    # The first write wins — the dedup is "do nothing on conflict",
    # so the second item with the same content_hash is dropped.
    assert out[0].embedding == (0.1, 0.2, 0.3)
