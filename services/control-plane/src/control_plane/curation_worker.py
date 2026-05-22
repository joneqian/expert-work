"""Curation worker — Stream J.12 (Mini-ADR J-43).

A single-replica background worker inside the control-plane. Each
``run_once`` sweep scans the L7 trajectory ObjectStore cross-tenant,
joins each trajectory with its thread's agent identity (``thread_meta``)
and user feedback (G.6), and upserts a ``curation_candidate`` row for
every trajectory a rule flags:

* a 👎 on the thread            → ``negative_feedback``
* a ``failed`` / ``max_steps`` outcome → ``failed_outcome``
* a 👍 on the thread            → ``positive_feedback`` (golden material)

A plain ``success`` run with no feedback is not a candidate. The
candidate is scoped to ``(tenant, agent_name)`` — the curated dataset
is agent-level, not per-instance (Mini-ADR J-43).

The worker is best-effort: a malformed trajectory / missing thread is
skipped, never fatal. ``curation_candidate`` is unique per
``(tenant, trajectory_key)`` so re-scanning a trajectory is a cheap
no-op — a pre-check skips even the ObjectStore read.

Wiring: started from the FastAPI ``lifespan``, stopped from its
``finally`` — the same shape as :class:`TriggerScheduler`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

from helix_agent.common.observability import helix_counter
from helix_agent.persistence import CurationCandidateStore, ThreadMetaStore
from helix_agent.persistence.feedback_store import FeedbackStore
from helix_agent.persistence.rls import (
    bypass_rls_var,
    current_tenant_id_var,
    current_user_id_var,
)
from helix_agent.protocol import (
    CurationCandidateRecord,
    CurationSignal,
    FeedbackRating,
    TrajectoryOutcome,
)
from orchestrator.trajectory import StoredTrajectory, TrajectoryReader

logger = logging.getLogger("helix.control_plane.curation_worker")

#: Run outcomes that make a trajectory a regression candidate on their own.
_FAILED_OUTCOMES: frozenset[str] = frozenset({"failed", "max_steps"})

_worker_cycle_errors = helix_counter(
    "helix_control_plane_curation_worker_cycle_errors_total",
    "Curation worker cycles that ended in a caught exception.",
)
_candidates_detected = helix_counter(
    "helix_control_plane_curation_candidates_detected_total",
    "Trajectories newly flagged as curation candidates.",
)


def _tenant_from_key(key: str) -> UUID | None:
    """Parse the tenant_id segment from a trajectory key.

    Key scheme: ``{prefix}/{tenant_id}/{outcome}/Y/M/D/{thread}.jsonl``.
    """
    parts = key.split("/")
    if len(parts) < 2:
        return None
    try:
        return UUID(parts[1])
    except ValueError:
        return None


def _classify(
    outcome: TrajectoryOutcome, *, has_down: bool, has_up: bool
) -> tuple[CurationSignal | None, FeedbackRating | None]:
    """Pick the curation signal for a trajectory, or ``(None, None)`` to skip.

    Negative signals win over positive — a 👎 is the most actionable
    material even when the run also drew a 👍 on another turn.
    """
    if has_down:
        return "negative_feedback", "down"
    if outcome in _FAILED_OUTCOMES:
        return "failed_outcome", None
    if has_up:
        return "positive_feedback", "up"
    return None, None


@contextmanager
def _bypass_rls() -> Iterator[None]:
    """RLS-bypass scope for the cross-tenant trajectory scan (reaper pattern)."""
    bypass = bypass_rls_var.set(True)
    tenant = current_tenant_id_var.set(None)
    try:
        yield
    finally:
        current_tenant_id_var.reset(tenant)
        bypass_rls_var.reset(bypass)


@contextmanager
def _tenant_scope(tenant_id: UUID) -> Iterator[None]:
    """Scope a per-trajectory store call to that trajectory's own tenant."""
    tenant = current_tenant_id_var.set(tenant_id)
    bypass = bypass_rls_var.set(False)
    user = current_user_id_var.set(None)
    try:
        yield
    finally:
        current_user_id_var.reset(user)
        bypass_rls_var.reset(bypass)
        current_tenant_id_var.reset(tenant)


class CurationWorker:
    """Background worker: scan trajectories + upsert curation candidates."""

    def __init__(
        self,
        *,
        trajectory_reader: TrajectoryReader,
        candidate_store: CurationCandidateStore,
        thread_store: ThreadMetaStore,
        feedback_store: FeedbackStore,
        interval_s: int,
        batch_size: int = 200,
    ) -> None:
        if interval_s <= 0:
            msg = "interval_s must be positive"
            raise ValueError(msg)
        self._reader = trajectory_reader
        self._candidates = candidate_store
        self._threads = thread_store
        self._feedback = feedback_store
        self._interval_s = interval_s
        self._batch_size = batch_size
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Schedule the periodic loop. Idempotent: re-calling is a no-op."""
        if self.is_running:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="curation-worker")

    async def stop(self) -> None:
        """Signal stop + await the loop's clean exit."""
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=self._interval_s + 5)
        except (TimeoutError, asyncio.CancelledError):
            self._task.cancel()
        finally:
            self._task = None

    async def run_once(self) -> int:
        """One sweep — scan trajectories, upsert new candidates.

        Returns the number of trajectories newly flagged as candidates.
        """
        with _bypass_rls():
            keys = await self._reader.list_keys()
        detected = 0
        for key in keys[: self._batch_size]:
            try:
                if await self._process_key(key):
                    detected += 1
            except Exception:
                logger.exception("curation_worker.key_failed", extra={"key": key})
        return detected

    async def _process_key(self, key: str) -> bool:
        tenant_id = _tenant_from_key(key)
        if tenant_id is None:
            return False
        # Cheap dedup — an indexed lookup skips the ObjectStore read for
        # a trajectory already flagged on an earlier sweep.
        with _tenant_scope(tenant_id):
            existing = await self._candidates.get_by_trajectory_key(
                tenant_id=tenant_id, trajectory_key=key
            )
        if existing is not None:
            return False
        stored = await self._reader.read(key)
        if stored is None:
            return False
        candidate = await self._evaluate(stored)
        if candidate is None:
            return False
        with _tenant_scope(candidate.tenant_id):
            inserted = await self._candidates.upsert(candidate)
        if inserted:
            _candidates_detected.inc()
        return inserted

    async def _evaluate(self, stored: StoredTrajectory) -> CurationCandidateRecord | None:
        """Join a trajectory with thread / feedback, apply the candidate rule."""
        with _tenant_scope(stored.tenant_id):
            meta = await self._threads.get(stored.thread_id, tenant_id=stored.tenant_id)
        if meta is None or meta.agent_name is None:
            # No agent identity — the curated dataset is agent-scoped, so
            # a trajectory we cannot attribute to an agent is unusable.
            return None
        with _tenant_scope(stored.tenant_id):
            feedback = await self._feedback.list_for_thread(thread_id=stored.thread_id)
        has_down = any(f.rating == "down" for f in feedback)
        has_up = any(f.rating == "up" for f in feedback)
        signal, rating = _classify(stored.outcome, has_down=has_down, has_up=has_up)
        if signal is None:
            return None
        return CurationCandidateRecord(
            id=uuid4(),
            tenant_id=stored.tenant_id,
            agent_name=meta.agent_name,
            agent_version=meta.agent_version,
            thread_id=stored.thread_id,
            user_id=stored.user_id or meta.user_id,
            trajectory_key=stored.key,
            outcome=stored.outcome,
            signal=signal,
            feedback_rating=rating,
            detected_at=datetime.now(UTC),
        )

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                detected = await self.run_once()
                if detected:
                    logger.info("curation_worker.swept", extra={"detected_count": detected})
            except Exception:
                logger.exception("curation_worker.cycle_failed")
                _worker_cycle_errors.inc()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_s)
            except TimeoutError:
                # Normal periodic wake-up — the interval elapsed with no
                # stop signal, so loop round for the next sweep.
                pass


__all__ = ["CurationWorker"]
