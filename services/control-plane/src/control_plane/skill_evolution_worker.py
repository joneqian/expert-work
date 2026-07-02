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
from datetime import UTC, datetime
from uuid import UUID

import httpx

from control_plane.skill_evolution import EvolutionResult, TransientEvolutionError
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
# ``implicit_success`` (SE-A38) joins as a weak-label success source — its
# distillates never auto-promote (SE-A44, enforced in the promotion gate).
EVOLVE_SIGNALS: frozenset[CurationSignal] = frozenset(
    {"positive_feedback", "failed_outcome", "implicit_success"}
)

#: SE-16 (SE-A40) — transient failures per candidate before giving up.
MAX_DISTILL_RETRIES = 3

#: Async gate answering "is skill evolution rolled out to this tenant?"
#: (SE-A41 — ``tenant_config.skill_evolution_enabled``, ANDed with the
#: platform master switch by the app assembly). ``None`` → no per-tenant
#: gating (unit tests / single-tenant deployments).
TenantGate = Callable[[UUID], Awaitable[bool]]

_cycle_errors = helix_counter(
    "helix_control_plane_skill_evolution_cycle_errors_total",
    "Skill-evolution worker cycles that ended in a caught exception.",
)
_grounded = helix_counter(
    "helix_control_plane_skill_evolution_grounded_total",
    "Candidates that produced a grounded (replay-verified) DRAFT skill.",
)
_retried = helix_counter(
    "helix_control_plane_skill_evolution_retries_total",
    "Distillation attempts that died on a transient fault and were requeued.",
)


def _is_transient(exc: BaseException) -> bool:
    """Retryable fault sniffing — the explicit wrapper wins; otherwise walk
    the cause/context chain for transport-class faults (timeout/connection).
    ``TimeoutError`` covers ``asyncio.TimeoutError`` (alias since 3.11)."""
    node: BaseException | None = exc
    while node is not None:
        if isinstance(
            node,
            TransientEvolutionError
            | httpx.TimeoutException
            | httpx.TransportError
            | TimeoutError
            | ConnectionError,
        ):
            return True
        node = node.__cause__ if node.__cause__ is not None else node.__context__
    return False


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
        tenant_gate: TenantGate | None = None,
    ) -> None:
        if interval_s <= 0:
            raise ValueError("interval_s must be positive")
        self._candidates = candidate_store
        self._processor = processor
        self._interval_s = interval_s
        self._batch_size = batch_size
        self._tenant_gate = tenant_gate
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
        """Scan un-evolved evolvable candidates and process a batch of them."""
        with _bypass_rls():
            # 4.4 #5 — only candidates not yet evolved, so the worker doesn't
            # re-distil the same trajectory every interval (a cost runaway the
            # single-shot unit tests never exercised).
            candidates = await self._candidates.list_for_review_all_tenants(
                status=CandidateStatus.PENDING, unevolved_only=True
            )
        todo = [c for c in candidates if c.signal in EVOLVE_SIGNALS][: self._batch_size]

        counts = {"grounded": 0, "rejected": 0, "exhausted": 0, "no_draft": 0}
        failed = 0
        # SE-A41 — per-tenant rollout gate, resolved once per tenant per
        # sweep. An ungated candidate is SKIPPED (not marked evolved) so it
        # distils normally once the tenant is enrolled later.
        gate_cache: dict[UUID, bool] = {}
        now = datetime.now(UTC)
        for candidate in todo:
            if self._tenant_gate is not None:
                enabled = gate_cache.get(candidate.tenant_id)
                if enabled is None:
                    enabled = await self._tenant_gate(candidate.tenant_id)
                    gate_cache[candidate.tenant_id] = enabled
                if not enabled:
                    continue
            with _tenant_scope(candidate.tenant_id):
                try:
                    result = await self._processor(candidate)
                except Exception as exc:
                    # Isolate a per-candidate failure (e.g. a tenant whose aux
                    # credential isn't resolvable) so one bad candidate doesn't
                    # abort the whole batch.
                    failed += 1
                    # SE-A40 — a transient fault (aux LLM timeout / rate limit /
                    # connection) requeues the candidate instead of burning it;
                    # give up (mark evolved) once the retry budget is spent.
                    if _is_transient(exc):
                        retries = await self._candidates.record_retry(
                            candidate_id=candidate.id, tenant_id=candidate.tenant_id
                        )
                        if retries < MAX_DISTILL_RETRIES:
                            _retried.inc()
                            logger.warning(
                                "skill_evolution.candidate_requeued candidate_id=%s retries=%s",
                                candidate.id,
                                retries,
                            )
                            continue
                    logger.warning("skill_evolution.candidate_failed candidate_id=%s", candidate.id)
                else:
                    counts[result.outcome] += 1
                    if result.outcome == "grounded":
                        _grounded.inc()
                # Mark evolved so the candidate is not re-processed every
                # interval (4.4 #5) — on success, permanent failure, or a
                # spent retry budget.
                await self._candidates.mark_evolved(
                    candidate_id=candidate.id, tenant_id=candidate.tenant_id, at=now
                )

        return EvolutionTally(
            scanned=len(candidates),
            processed=len(todo) - failed,
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
                # Normal periodic wake-up — the interval elapsed with no stop
                # signal, so loop round for the next sweep.
                pass
