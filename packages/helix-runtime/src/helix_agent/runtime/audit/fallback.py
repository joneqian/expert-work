"""Local-disk / in-memory fallback queue for audit writes.

When the primary store (Postgres) is unreachable or rejects a write, the
:class:`AuditLogger` enqueues the entry here so we never silently lose
audit records. A reaper (later batch) drains the queue back into the store
once Postgres recovers.

Design: subsystems/17 § 4.3 + § 5.6.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from helix_agent.protocol import AuditEntry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FallbackRecord:
    """One queued entry plus the reason it was queued.

    The reaper (batch 3+) uses ``reason`` for retry-budget decisions and
    operator alerting (e.g., persistent ``unique_violation`` should not
    keep retrying forever).
    """

    entry: AuditEntry
    reason: str
    queued_at: datetime


class AuditFallbackQueue(Protocol):
    """Append-only enqueue surface.

    The reaper interface (drain / ack / requeue) is intentionally **not**
    part of the M0 contract — it lands with the reaper batch.
    """

    async def enqueue(self, entry: AuditEntry, *, reason: str) -> None:
        """Append a failed entry to the queue."""


class InMemoryAuditFallbackQueue:
    """Process-local queue, useful for unit tests and dev environments.

    Records are accessible via :meth:`snapshot` for assertions; production
    code should not rely on this.
    """

    def __init__(self) -> None:
        self._records: list[FallbackRecord] = []
        self._lock = asyncio.Lock()

    async def enqueue(self, entry: AuditEntry, *, reason: str) -> None:
        record = FallbackRecord(entry=entry, reason=reason, queued_at=datetime.now(UTC))
        async with self._lock:
            self._records.append(record)

    def snapshot(self) -> list[FallbackRecord]:
        """Test-only: return a copy of the current queue contents."""
        return list(self._records)


class JsonlFileAuditFallbackQueue:
    """Append failed entries to a day-partitioned JSONL file on disk.

    Layout: ``<base_dir>/<YYYY-MM-DD>.jsonl``. One JSON object per line:

    .. code-block:: json

        {"queued_at": "...", "reason": "...", "entry": { ... AuditEntry ... }}

    Day partitioning matches the design's "log_min_duration"-style cron
    rotation, so a single ``find ... -mtime +N`` cleans archived shards
    once the reaper has acked them.
    """

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = Path(base_dir)
        self._lock = asyncio.Lock()

    async def enqueue(self, entry: AuditEntry, *, reason: str) -> None:
        record = FallbackRecord(entry=entry, reason=reason, queued_at=datetime.now(UTC))
        payload = json.dumps(_record_to_jsonable(record), ensure_ascii=False)
        async with self._lock:
            # File I/O off the event loop. ``mkdir`` + ``open(..., 'a')`` is
            # the simplest path; concurrent writers from other processes are
            # safe because POSIX appends < PIPE_BUF (4 KB) are atomic — and
            # audit entries are well under that bound.
            await asyncio.to_thread(self._append_line, payload, record.queued_at)

    def _append_line(self, payload: str, queued_at: datetime) -> None:
        self._base_dir.mkdir(parents=True, exist_ok=True)
        day = queued_at.strftime("%Y-%m-%d")
        path = self._base_dir / f"{day}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(payload)
            fh.write("\n")

    def read_records(self, day: datetime) -> Iterable[FallbackRecord]:
        """Test / reaper helper: yield records for a given day's shard.

        Returns an empty iterable when the shard does not exist (no fallback
        writes happened that day).
        """
        path = self._base_dir / f"{day.strftime('%Y-%m-%d')}.jsonl"
        if not path.exists():
            return ()
        return list(_iter_jsonl_records(path))


def _record_to_jsonable(record: FallbackRecord) -> dict[str, object]:
    return {
        "queued_at": record.queued_at.isoformat(),
        "reason": record.reason,
        "entry": json.loads(record.entry.model_dump_json()),
    }


def _iter_jsonl_records(path: Path) -> Iterable[FallbackRecord]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            yield FallbackRecord(
                entry=AuditEntry.model_validate(obj["entry"]),
                reason=obj["reason"],
                queued_at=datetime.fromisoformat(obj["queued_at"]),
            )
