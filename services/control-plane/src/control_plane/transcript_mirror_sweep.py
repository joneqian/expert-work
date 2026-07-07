"""Transcript mirror sweep — conversation full-text search (IA M4).

Message history lives in LangGraph's ``checkpoints`` blob: no SQL pushdown,
no tenant RLS, so the conversation browser can't search content directly.
This resident worker mirrors user/assistant text turns into
``thread_message`` (trigram-indexed, RLS-scoped — migration 0106), where
``ThreadMessageStore.search_thread_ids`` serves the browser's ``q`` filter.

Work queue = ``ThreadMessageStore.pending_thread_ids``: threads with no
watermark row yet (backfill — history converges without a one-off script)
or with a run updated after ``synced_at`` (fresh activity, ordered first so
a large backfill can't starve it). Mirror writes are idempotent
(``ON CONFLICT (thread_id, seq) DO NOTHING`` — the messages channel is
append-only), so overlapping sweeps across instances are harmless: worst
case both do the same no-op write. Search visibility lags at most one
sweep interval.

RLS posture mirrors :class:`ApprovalTimeoutSweep`: the cross-tenant scan
runs under the bypass ContextVar; each thread's mirror WRITE runs under its
own tenant scope so the GUC satisfies the FORCE-RLS ``WITH CHECK``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID

from control_plane.runtime import AgentRuntime
from control_plane.transcript import read_turns
from expert_work.common.observability import expert_work_counter
from expert_work.persistence import ThreadMessageStore
from expert_work.persistence.rls import bypass_rls_var, current_tenant_id_var

logger = logging.getLogger("expert_work.control_plane.transcript_mirror_sweep")

#: Default cadence — content search sees a new message within a minute.
_DEFAULT_INTERVAL_S = 60.0

_synced_total = expert_work_counter(
    "expert_work_control_plane_transcript_mirror_synced_total",
    "Threads whose transcript mirror was refreshed by the sweep.",
)
_read_errors = expert_work_counter(
    "expert_work_control_plane_transcript_mirror_read_errors_total",
    "Checkpoint reads that failed during a transcript mirror sweep.",
)
_cycle_errors = expert_work_counter(
    "expert_work_control_plane_transcript_mirror_cycle_errors_total",
    "Transcript mirror sweep cycles that ended in a caught exception.",
)


@contextmanager
def _bypass_rls() -> Iterator[None]:
    """RLS-bypass scope for the cross-tenant work-queue scan."""
    bypass = bypass_rls_var.set(True)
    tenant = current_tenant_id_var.set(None)
    try:
        yield
    finally:
        current_tenant_id_var.reset(tenant)
        bypass_rls_var.reset(bypass)


@contextmanager
def _tenant_scope(tenant_id: UUID) -> Iterator[None]:
    """Scope one thread's mirror write to its own tenant (FORCE-RLS check)."""
    tenant = current_tenant_id_var.set(tenant_id)
    bypass = bypass_rls_var.set(False)
    try:
        yield
    finally:
        bypass_rls_var.reset(bypass)
        current_tenant_id_var.reset(tenant)


class TranscriptMirrorSweep:
    """Background task: mirror stale threads' transcripts for content search."""

    def __init__(
        self,
        *,
        message_store: ThreadMessageStore,
        runtime: AgentRuntime,
        interval_s: float = _DEFAULT_INTERVAL_S,
        batch_size: int = 200,
    ) -> None:
        if interval_s <= 0:
            msg = "interval_s must be positive"
            raise ValueError(msg)
        self._messages = message_store
        self._runtime = runtime
        self._interval_s = interval_s
        self._batch_size = batch_size
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Start the periodic loop. Idempotent."""
        if self.is_running:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="transcript-mirror-sweep")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=self._interval_s + 5)
        except (TimeoutError, asyncio.CancelledError):
            self._task.cancel()
        finally:
            self._task = None

    async def _loop(self) -> None:
        # Sleep first (the platform likely just restarted); first sweep after
        # one interval. A failed cycle is logged + counted, never fatal.
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_s)
                return  # stop event fired
            except TimeoutError:
                pass
            try:
                await self.run_once()
            except Exception:
                _cycle_errors.inc()
                logger.exception("transcript_mirror_sweep.cycle_failed")

    async def run_once(self) -> int:
        """Mirror every stale thread once; returns the threads refreshed."""
        checkpointer = self._runtime.durable_checkpointer
        if checkpointer is None:
            return 0
        with _bypass_rls():
            pending = await self._messages.pending_thread_ids(limit=self._batch_size)
        synced = 0
        for thread_id, tenant_id in pending:
            # Watermark BEFORE the checkpoint read: a run that lands mid-read
            # bumps agent_run.updated_at past this mark, so the next cycle
            # re-selects the thread instead of losing the tail.
            mark = datetime.now(UTC)
            try:
                # The mirror feeds content search + audit — it must stay
                # faithful to the durable transcript. ``include_hidden=True``
                # keeps orchestrator scaffolding (``expert_work_hide_from_ui``) in
                # the record; only the UI bubble view filters it (RT-ADR-9).
                turns = await read_turns(checkpointer, thread_id, include_hidden=True)
            except Exception:
                _read_errors.inc()
                logger.warning(
                    "transcript_mirror_sweep.read_failed",
                    extra={"thread_id": str(thread_id)},
                    exc_info=True,
                )
                continue
            with _tenant_scope(tenant_id):
                await self._messages.sync_thread(
                    thread_id=thread_id, tenant_id=tenant_id, turns=turns, synced_at=mark
                )
            synced += 1
        if synced:
            _synced_total.inc(synced)
            logger.info("transcript_mirror_sweep.synced", extra={"count": synced})
        return synced


__all__ = ["TranscriptMirrorSweep"]
