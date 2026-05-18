"""``EventLogArchiveJob`` — the G.8 cold-archival sweep.

Per STREAM-G-DESIGN § 2.4: select ``event_log`` rows older than the
archive age, grouped by ``(tenant_id, thread_id, calendar month)``;
serialise each group to JSONL, ``put`` it to object storage, then
``DELETE`` the group. Archive-then-delete + a deterministic object key
make the sweep crash-safe — a mid-run crash re-archives (overwriting the
same key) and re-deletes on the next run, never losing rows.

One ``run_once`` computes a single ``cutoff`` timestamp and threads it
through the select / fetch / delete statements, so the delete removes
exactly the rows the put archived (no clock-drift gap).

The DELETE uses a flat ``WHERE`` predicate, not a ``ctid IN (... LIMIT)``
subquery — the latter intermittently raises ``permission denied`` on
``event_log`` under asyncpg + SQLAlchemy 2.0 (see ``retention_cleanup_job``).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from helix_agent.runtime.storage.base import ObjectStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArchiveReport:
    """Tally produced by one ``run_once`` sweep."""

    archived_objects: int = 0
    archived_rows: int = 0
    duration_seconds: float = 0.0


class EventLogArchiveJob:
    """One-shot ``event_log`` cold-archival sweep."""

    def __init__(
        self,
        *,
        db_session_factory: async_sessionmaker[AsyncSession],
        object_store: ObjectStore,
        archive_age_days: int,
        batch_size: int,
    ) -> None:
        if archive_age_days <= 0:
            msg = "archive_age_days must be positive"
            raise ValueError(msg)
        if batch_size <= 0:
            msg = "batch_size must be positive"
            raise ValueError(msg)
        self._sf = db_session_factory
        self._store = object_store
        self._age_days = archive_age_days
        self._batch = batch_size

    async def run_once(self) -> ArchiveReport:
        """Archive + delete every aged ``(tenant, thread, month)`` group once."""
        started = time.monotonic()
        cutoff = datetime.now(UTC) - timedelta(days=self._age_days)

        groups = await self._archivable_groups(cutoff)
        objects = 0
        rows_total = 0
        for tenant_id, thread_id, month in groups:
            rows = await self._fetch_group(thread_id, month, cutoff)
            if not rows:
                continue
            key = _object_key(tenant_id, thread_id, month)
            await self._store.put(key, _to_jsonl(rows), content_type="application/x-ndjson")
            deleted = await self._delete_group(thread_id, month, cutoff)
            objects += 1
            rows_total += deleted
            logger.info("event_log_archive.group key=%s rows=%d", key, deleted)

        return ArchiveReport(
            archived_objects=objects,
            archived_rows=rows_total,
            duration_seconds=time.monotonic() - started,
        )

    async def _archivable_groups(self, cutoff: datetime) -> list[tuple[str, str, datetime]]:
        """Distinct ``(tenant_id, thread_id, month-start)`` past the cutoff."""
        async with self._sf() as session:
            result = await session.execute(
                text(
                    "SELECT DISTINCT tenant_id::text, thread_id::text, "
                    "       date_trunc('month', created_at) AS month "
                    "FROM event_log "
                    "WHERE created_at < :cutoff "
                    "ORDER BY month "
                    "LIMIT :batch"
                ),
                {"cutoff": cutoff, "batch": self._batch},
            )
            groups = [(str(r[0]), str(r[1]), r[2]) for r in result.fetchall()]
            await session.commit()
        return groups

    async def _fetch_group(
        self, thread_id: str, month: datetime, cutoff: datetime
    ) -> list[dict[str, Any]]:
        """All of one thread's rows in ``month`` that are past ``cutoff``."""
        async with self._sf() as session:
            result = await session.execute(
                text(
                    "SELECT id, thread_id::text AS thread_id, "
                    "       session_id::text AS session_id, "
                    "       tenant_id::text AS tenant_id, seq, event_type, "
                    "       payload, trace_id, created_at "
                    "FROM event_log "
                    "WHERE thread_id = :tid "
                    "  AND created_at < :cutoff "
                    "  AND created_at >= :month "
                    "  AND created_at < :month + interval '1 month' "
                    "ORDER BY seq"
                ),
                {"tid": thread_id, "cutoff": cutoff, "month": month},
            )
            rows = [_normalise_row(dict(r._mapping)) for r in result.fetchall()]
            await session.commit()
        return rows

    async def _delete_group(self, thread_id: str, month: datetime, cutoff: datetime) -> int:
        """Delete the group archived by :meth:`_fetch_group`; return the row count."""
        async with self._sf() as session:
            result = await session.execute(
                text(
                    "DELETE FROM event_log "
                    "WHERE thread_id = :tid "
                    "  AND created_at < :cutoff "
                    "  AND created_at >= :month "
                    "  AND created_at < :month + interval '1 month' "
                    "RETURNING id"
                ),
                {"tid": thread_id, "cutoff": cutoff, "month": month},
            )
            count = len(result.fetchall())
            await session.commit()
        return count


def _object_key(tenant_id: str, thread_id: str, month: datetime) -> str:
    """Deterministic archive key — stable across re-runs for one group."""
    return f"event-log/{tenant_id}/{month.year:04d}/{month.month:02d}/{thread_id}.jsonl"


def _normalise_row(row: dict[str, Any]) -> dict[str, Any]:
    """JSONB ``payload`` surfaces as a str under a raw ``text()`` query — re-parse it."""
    payload = row.get("payload")
    if isinstance(payload, str):
        row["payload"] = json.loads(payload)
    return row


def _to_jsonl(rows: list[dict[str, Any]]) -> bytes:
    """One compact JSON object per line; datetimes as ISO 8601."""
    lines = [json.dumps(row, default=_json_default, separators=(",", ":")) for row in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    msg = f"not JSON-serialisable: {type(value).__name__}"
    raise TypeError(msg)
