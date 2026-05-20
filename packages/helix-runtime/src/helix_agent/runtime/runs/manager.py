# ============================================================
# Adapted from bytedance/deer-flow @ 813d3c94efa7fdea6aafcb4f459304db91fcaed0
# Source: backend/packages/harness/deerflow/runtime/runs/manager.py
# License: MIT (see vendor LICENSE)
# Modifications:
#   - Dropped persistent RunStore backing — M0 is in-memory only; the
#     `runs` table is M1+ work behind its own ADR (per 06-OPEN-SOURCE-DEPS)
#   - run_id / thread_id typed as UUID (helix-agent convention)
#   - Added tenant_id (ADR-0002 + Stream C.4 RLS)
#   - Dropped assistant_id / multitask_strategy / metadata / kwargs / error
#     fields — those can be added when needed for a specific use case
#   - Lock retained from DeerFlow; mutations are serialized
# Last sync: 2026-05-11
# ============================================================

"""In-memory ``RunManager`` — per-process run lifecycle registry."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from helix_agent.runtime.runs.schemas import DisconnectMode, RunStatus

logger = logging.getLogger(__name__)


@dataclass
class RunRecord:
    """Mutable per-run state held in the in-memory registry.

    The ``task`` and ``abort_event`` fields back live orchestrator execution;
    they are not serialized.
    """

    run_id: UUID
    thread_id: UUID
    tenant_id: UUID
    status: RunStatus
    on_disconnect: DisconnectMode = DisconnectMode.CANCEL
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    task: asyncio.Task[None] | None = field(default=None, repr=False)
    abort_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    #: Stream K.K10 — whether this run resumed an existing checkpointed
    #: thread (caller computes from prior ``thread_meta`` state). The SSE
    #: worker observes ``helix_durable_resume_seconds`` only when ``True``
    #: so the histogram cleanly tracks SLO #5 (durable resume latency).
    is_resume: bool = False


class RunManager:
    """Per-process registry of active runs.

    All mutations are serialized by an :class:`asyncio.Lock`. Persistent
    history (e.g. a ``runs`` Postgres table) is deferred to M1+.
    """

    def __init__(self) -> None:
        self._runs: dict[UUID, RunRecord] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        *,
        run_id: UUID,
        thread_id: UUID,
        tenant_id: UUID,
        on_disconnect: DisconnectMode = DisconnectMode.CANCEL,
        is_resume: bool = False,
    ) -> RunRecord:
        """Create + register a new run in PENDING state.

        ``is_resume`` (Stream K.K10) flags the run as resuming a thread
        with a non-empty checkpoint so the SSE worker can observe the
        durable-resume histogram only on those runs.
        """
        async with self._lock:
            if run_id in self._runs:
                msg = f"run_id={run_id} already exists"
                raise ValueError(msg)
            record = RunRecord(
                run_id=run_id,
                thread_id=thread_id,
                tenant_id=tenant_id,
                status=RunStatus.PENDING,
                on_disconnect=on_disconnect,
                is_resume=is_resume,
            )
            self._runs[run_id] = record
            logger.info("run.create id=%s thread=%s tenant=%s", run_id, thread_id, tenant_id)
            return record

    def get(self, run_id: UUID) -> RunRecord | None:
        """Snapshot lookup; safe outside the lock since dict reads are atomic."""
        return self._runs.get(run_id)

    async def list_by_thread(self, thread_id: UUID, *, tenant_id: UUID) -> list[RunRecord]:
        """Return all runs for ``thread_id`` belonging to ``tenant_id``."""
        async with self._lock:
            return [
                r
                for r in self._runs.values()
                if r.thread_id == thread_id and r.tenant_id == tenant_id
            ]

    async def set_status(self, run_id: UUID, status: RunStatus) -> bool:
        """Update a run's status. Returns ``True`` iff the run exists."""
        async with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                return False
            record.status = status
            record.updated_at = datetime.now(UTC)
            logger.info("run.status_change id=%s status=%s", run_id, status)
            return True

    async def attach_task(self, run_id: UUID, task: asyncio.Task[None]) -> bool:
        """Bind the live orchestrator task to its run record."""
        async with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                return False
            record.task = task
            return True

    async def cancel(self, run_id: UUID) -> bool:
        """Signal an in-flight run to abort.

        Sets ``abort_event`` (orchestrator polls this) and transitions status
        to INTERRUPTED if currently RUNNING/PENDING. Returns ``True`` iff
        the run exists.
        """
        async with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                return False
            record.abort_event.set()
            if record.status in (RunStatus.PENDING, RunStatus.RUNNING):
                record.status = RunStatus.INTERRUPTED
                record.updated_at = datetime.now(UTC)
            logger.info("run.cancel id=%s prev_status=%s", run_id, record.status)
            return True

    async def has_inflight(self, thread_id: UUID, *, tenant_id: UUID) -> bool:
        """Return True if there is any PENDING/RUNNING run for the thread."""
        async with self._lock:
            return any(
                r.thread_id == thread_id
                and r.tenant_id == tenant_id
                and r.status in (RunStatus.PENDING, RunStatus.RUNNING)
                for r in self._runs.values()
            )

    async def cleanup(self, run_id: UUID, *, delay: float = 300.0) -> None:
        """Remove a run from the registry after ``delay`` seconds.

        Default 5 min — long enough for late SSE consumers to drain
        replayed events from the stream bridge but short enough to keep
        memory bounded.
        """
        if delay > 0:
            await asyncio.sleep(delay)
        async with self._lock:
            self._runs.pop(run_id, None)
