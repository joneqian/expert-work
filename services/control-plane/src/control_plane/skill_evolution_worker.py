"""Evolution worker shell (Stream SE, SE-6b) — Layer B's loop.

Background worker that scans pending curation candidates worth evolving and runs
each through the co-evolve orchestrator (SE-6a). Mirrors the ``CurationWorker``
skeleton (start / stop / periodic loop + RLS scoping): cross-tenant scan under
``_bypass_rls``, per-candidate processing scoped to its own tenant.

The heavy per-candidate work — assembling the success/failure replay set,
wiring the real aux-LLM distiller/attributor + graph replay, and persisting the
DRAFT — is injected as a ``processor`` so this shell stays unit-testable. The
real processor + app-lifespan wiring land in SE-6c.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID

from control_plane.skill_evolution import EvolutionResult
from helix_agent.common.observability import helix_counter
from helix_agent.persistence import CurationCandidateStore
from helix_agent.persistence.rls import (
    bypass_rls_var,
    current_tenant_id_var,
    current_user_id_var,
)
from helix_agent.protocol import CandidateStatus, CurationCandidateRecord, CurationSignal

logger = logging.getLogger(__name__)

# Signals worth distilling a skill from: success patterns, and failures the
# co-evolve loop may contrast against (SkillGen contrastive induction).
EVOLVE_SIGNALS: frozenset[CurationSignal] = frozenset({"positive_feedback", "failed_outcome"})

_cycle_errors = helix_counter(
    "helix_control_plane_skill_evolution_cycle_errors_total",
    "Skill-evolution worker cycles that ended in a caught exception.",
)
_grounded = helix_counter(
    "helix_control_plane_skill_evolution_grounded_total",
    "Candidates that produced a grounded (replay-verified) DRAFT skill.",
)

#: Processes one candidate end-to-end and reports how the co-evolve loop ended.
CandidateProcessor = Callable[[CurationCandidateRecord], Awaitable[EvolutionResult]]


@contextmanager
def _bypass_rls() -> Iterator[None]:
    """RLS-bypass scope for the cross-tenant candidate scan (reaper pattern)."""
    bypass = bypass_rls_var.set(True)
    tenant = current_tenant_id_var.set(None)
    try:
        yield
    finally:
        current_tenant_id_var.reset(tenant)
        bypass_rls_var.reset(bypass)


@contextmanager
def _tenant_scope(tenant_id: UUID) -> Iterator[None]:
    """Scope per-candidate store calls to that candidate's own tenant."""
    tenant = current_tenant_id_var.set(tenant_id)
    bypass = bypass_rls_var.set(False)
    user = current_user_id_var.set(None)
    try:
        yield
    finally:
        current_user_id_var.reset(user)
        bypass_rls_var.reset(bypass)
        current_tenant_id_var.reset(tenant)


@dataclass(frozen=True)
class EvolutionTally:
    """One sweep's accounting (observability + test assertions)."""

    scanned: int
    processed: int
    grounded: int
    rejected: int
    exhausted: int
    no_draft: int


class SkillEvolutionWorker:
    """Background worker: scan candidates + run the co-evolve loop per candidate."""

    def __init__(
        self,
        *,
        candidate_store: CurationCandidateStore,
        processor: CandidateProcessor,
        interval_s: int,
        batch_size: int = 50,
    ) -> None:
        if interval_s <= 0:
            raise ValueError("interval_s must be positive")
        self._candidates = candidate_store
        self._processor = processor
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
        self._task = asyncio.create_task(self._loop(), name="skill-evolution-worker")

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

    async def run_once(self) -> EvolutionTally:
        """Scan pending evolvable candidates and process a batch of them."""
        with _bypass_rls():
            candidates = await self._candidates.list_for_review_all_tenants(
                status=CandidateStatus.PENDING
            )
        todo = [c for c in candidates if c.signal in EVOLVE_SIGNALS][: self._batch_size]

        counts = {"grounded": 0, "rejected": 0, "exhausted": 0, "no_draft": 0}
        for candidate in todo:
            with _tenant_scope(candidate.tenant_id):
                result = await self._processor(candidate)
            counts[result.outcome] += 1
            if result.outcome == "grounded":
                _grounded.inc()

        return EvolutionTally(
            scanned=len(candidates),
            processed=len(todo),
            grounded=counts["grounded"],
            rejected=counts["rejected"],
            exhausted=counts["exhausted"],
            no_draft=counts["no_draft"],
        )

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                tally = await self.run_once()
                if tally.processed:
                    logger.info(
                        "skill_evolution_worker.swept",
                        extra={"processed": tally.processed, "grounded": tally.grounded},
                    )
            except Exception:
                logger.exception("skill_evolution_worker.cycle_failed")
                _cycle_errors.inc()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_s)
            except TimeoutError:
                pass
