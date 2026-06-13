"""``EvalWorker`` — P1-S2.1b (eval platform ops layer).

Resident background worker that drains queued ``eval_run`` rows: claim →
run the suite through an injected :class:`EvalEngine` → persist per-case
results + a summary → advance the status machine. The capability-eval
*engine* (``tools/eval/run_baseline.py``) stays the execution core; this
worker only does scheduling + persistence + the status machine, so the
eval logic is never re-implemented here.

Cross-tenant claim scan runs under ``_bypass_rls`` (queued rows span
tenants); each run's per-tenant work runs under ``_tenant_scope`` so the
FORCE-RLS writes land — the same posture as the memory consolidator /
skill evolution worker.

Cadence mirrors :class:`MemoryConsolidator`: sleep first, then one
``run_once`` per ``interval_s``; each cycle is independent and a failed
run is isolated to its own row (``status=error``), never the whole sweep.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from helix_agent.persistence.eval import EvalRunStore
from helix_agent.persistence.rls import (
    bypass_rls_var,
    current_tenant_id_var,
    current_user_id_var,
)
from helix_agent.protocol import EvalCaseResultRecord, EvalRunRecord, EvalRunStatus

logger = logging.getLogger(__name__)

#: Default cadence — one drain per 5 minutes.
_DEFAULT_INTERVAL_S = 300.0


@dataclass(frozen=True)
class EvalCaseOutcome:
    """One case result from the engine — the neutral shape the worker persists.

    Decoupled from ``tools/eval``'s ``CapabilityReport`` on purpose: the
    production engine adapter (S2.1c) maps reports to these; the worker and
    its tests never import the eval harness.
    """

    capability: str
    case_id: str
    passed: bool
    scores: dict[str, float] = field(default_factory=dict)
    session_id: str | None = None
    session_metrics: dict[str, float] | None = None


class EvalEngine(Protocol):
    """Executes a suite, returning per-case outcomes. Injected so the worker
    stays decoupled from the eval harness (tests inject a fake)."""

    async def run(self, suite: str) -> Sequence[EvalCaseOutcome]:
        """Execute ``suite`` and return one outcome per case."""


@dataclass
class EvalWorkerRunSummary:
    """Per-sweep counts — returned so tests can assert transitions."""

    claimed: int = 0
    passed: int = 0
    failed: int = 0
    errored: int = 0


@contextmanager
def _bypass_rls() -> Iterator[None]:
    """RLS-bypass scope for the cross-tenant queued scan."""
    bypass = bypass_rls_var.set(True)
    tenant = current_tenant_id_var.set(None)
    try:
        yield
    finally:
        current_tenant_id_var.reset(tenant)
        bypass_rls_var.reset(bypass)


@contextmanager
def _tenant_scope(tenant_id: UUID) -> Iterator[None]:
    """Scope a run's store writes to its own tenant so FORCE-RLS allows them."""
    tenant = current_tenant_id_var.set(tenant_id)
    bypass = bypass_rls_var.set(False)
    user = current_user_id_var.set(None)
    try:
        yield
    finally:
        current_user_id_var.reset(user)
        bypass_rls_var.reset(bypass)
        current_tenant_id_var.reset(tenant)


class EvalWorker:
    """Drains queued eval runs through ``engine`` on a periodic loop."""

    def __init__(
        self,
        *,
        store: EvalRunStore,
        engine: EvalEngine,
        interval_s: float = _DEFAULT_INTERVAL_S,
    ) -> None:
        if interval_s <= 0:
            msg = "interval_s must be positive"
            raise ValueError(msg)
        self._store = store
        self._engine = engine
        self._interval_s = interval_s
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
        self._task = asyncio.create_task(self._loop(), name="eval-worker")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=min(self._interval_s, 30.0) + 5.0)
        except (TimeoutError, asyncio.CancelledError):
            self._task.cancel()
        finally:
            self._task = None

    async def _loop(self) -> None:
        # Sleep first (platform likely just restarted); first drain after interval.
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_s)
                return  # stop event fired
            except TimeoutError:
                pass
            try:
                await self.run_once()
            except Exception:
                logger.exception("eval_worker.cycle_failed")

    async def run_once(self) -> EvalWorkerRunSummary:
        """Claim + execute every queued run once. Returns per-sweep counts."""
        summary = EvalWorkerRunSummary()
        with _bypass_rls():
            queued = await self._store.list_by_status_all_tenants(EvalRunStatus.QUEUED)
        for run in queued:
            summary.claimed += 1
            with _tenant_scope(run.tenant_id):
                await self._execute(run, summary)
        return summary

    async def _execute(self, run: EvalRunRecord, summary: EvalWorkerRunSummary) -> None:
        await self._store.set_status(
            run_id=run.id, tenant_id=run.tenant_id, status=EvalRunStatus.RUNNING
        )
        try:
            outcomes = await self._engine.run(run.suite)
        except Exception:
            logger.exception("eval_worker.engine_failed run_id=%s suite=%s", run.id, run.suite)
            await self._store.set_status(
                run_id=run.id,
                tenant_id=run.tenant_id,
                status=EvalRunStatus.ERROR,
                summary={"error": "engine_failed"},
            )
            summary.errored += 1
            return

        for outcome in outcomes:
            await self._store.append_case_result(
                EvalCaseResultRecord(
                    run_id=run.id,
                    tenant_id=run.tenant_id,
                    capability=outcome.capability,
                    case_id=outcome.case_id,
                    passed=outcome.passed,
                    scores=outcome.scores,
                    session_id=outcome.session_id,
                    session_metrics=outcome.session_metrics,
                )
            )

        total = len(outcomes)
        pass_count = sum(1 for o in outcomes if o.passed)
        # An empty suite is a FAILED gate (nothing proven), not a pass.
        passed = total > 0 and pass_count == total
        status = EvalRunStatus.PASSED if passed else EvalRunStatus.FAILED
        await self._store.set_status(
            run_id=run.id,
            tenant_id=run.tenant_id,
            status=status,
            summary={"pass_count": pass_count, "total": total},
        )
        if passed:
            summary.passed += 1
        else:
            summary.failed += 1
