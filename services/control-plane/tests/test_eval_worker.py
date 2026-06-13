"""Unit tests for :class:`EvalWorker` — P1-S2.1b.

Drives the worker against an in-memory store + a fake engine: the
claim→run→persist→status-machine path, the pass/fail gate, the
per-run error isolation, and the cross-tenant sweep.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from control_plane.eval_worker import EvalCaseOutcome, EvalWorker
from helix_agent.persistence import InMemoryEvalRunStore
from helix_agent.protocol import EvalRunRecord, EvalRunStatus, EvalTriggeredBy


@dataclass
class _FakeEngine:
    outcomes: Sequence[EvalCaseOutcome] = field(default_factory=tuple)
    raises: bool = False
    seen: list[str] = field(default_factory=list)

    async def run(self, suite: str) -> Sequence[EvalCaseOutcome]:
        self.seen.append(suite)
        if self.raises:
            raise RuntimeError("engine boom")
        return self.outcomes


async def _queue(
    store: InMemoryEvalRunStore, tenant_id: object, *, suite: str = "m0_baseline"
) -> EvalRunRecord:
    run = EvalRunRecord(
        id=uuid4(),
        tenant_id=tenant_id,  # type: ignore[arg-type]
        suite=suite,
        status=EvalRunStatus.QUEUED,
        triggered_by=EvalTriggeredBy.MANUAL,
        created_at=datetime.now(UTC),
    )
    return await store.create_run(run)


@pytest.mark.asyncio
async def test_all_pass_marks_run_passed_and_writes_results() -> None:
    store = InMemoryEvalRunStore()
    tenant = uuid4()
    run = await _queue(store, tenant)
    engine = _FakeEngine(
        outcomes=[
            EvalCaseOutcome("J.1_plan_execute", "J.1_plan_execute", True, {"pass_rate": 1.0}),
            EvalCaseOutcome("J.2_reflect", "J.2_reflect", True),
        ]
    )
    worker = EvalWorker(store=store, engine=engine)

    summary = await worker.run_once()

    assert summary.claimed == 1 and summary.passed == 1 and summary.failed == 0
    assert engine.seen == ["m0_baseline"]
    done = await store.get_run(run_id=run.id, tenant_id=tenant)
    assert done is not None
    assert done.status is EvalRunStatus.PASSED
    assert done.summary == {"pass_count": 2, "total": 2}
    assert done.started_at is not None and done.finished_at is not None
    results = await store.list_case_results(run_id=run.id, tenant_id=tenant)
    assert [r.capability for r in results] == ["J.1_plan_execute", "J.2_reflect"]
    assert results[0].scores == {"pass_rate": 1.0}


@pytest.mark.asyncio
async def test_any_fail_marks_run_failed() -> None:
    store = InMemoryEvalRunStore()
    tenant = uuid4()
    run = await _queue(store, tenant)
    worker = EvalWorker(
        store=store,
        engine=_FakeEngine(
            outcomes=[
                EvalCaseOutcome("a", "a", True),
                EvalCaseOutcome("b", "b", False),
            ]
        ),
    )

    summary = await worker.run_once()

    assert summary.failed == 1 and summary.passed == 0
    done = await store.get_run(run_id=run.id, tenant_id=tenant)
    assert done is not None
    assert done.status is EvalRunStatus.FAILED
    assert done.summary == {"pass_count": 1, "total": 2}


@pytest.mark.asyncio
async def test_engine_exception_isolates_to_error_status() -> None:
    store = InMemoryEvalRunStore()
    tenant = uuid4()
    run = await _queue(store, tenant)
    worker = EvalWorker(store=store, engine=_FakeEngine(raises=True))

    summary = await worker.run_once()

    assert summary.errored == 1
    done = await store.get_run(run_id=run.id, tenant_id=tenant)
    assert done is not None
    assert done.status is EvalRunStatus.ERROR
    assert done.summary == {"error": "engine_failed"}
    # No case rows for a failed engine run.
    assert await store.list_case_results(run_id=run.id, tenant_id=tenant) == []


@pytest.mark.asyncio
async def test_empty_outcomes_is_failed_not_passed() -> None:
    store = InMemoryEvalRunStore()
    tenant = uuid4()
    run = await _queue(store, tenant)
    worker = EvalWorker(store=store, engine=_FakeEngine(outcomes=[]))

    await worker.run_once()

    done = await store.get_run(run_id=run.id, tenant_id=tenant)
    assert done is not None
    assert done.status is EvalRunStatus.FAILED
    assert done.summary == {"pass_count": 0, "total": 0}


@pytest.mark.asyncio
async def test_sweep_spans_tenants_and_skips_non_queued() -> None:
    store = InMemoryEvalRunStore()
    a, b = uuid4(), uuid4()
    run_a = await _queue(store, a)
    run_b = await _queue(store, b)
    # A run already running must not be re-claimed.
    run_done = await _queue(store, a)
    await store.set_status(run_id=run_done.id, tenant_id=a, status=EvalRunStatus.RUNNING)

    worker = EvalWorker(store=store, engine=_FakeEngine(outcomes=[EvalCaseOutcome("x", "x", True)]))
    summary = await worker.run_once()

    assert summary.claimed == 2
    assert (await store.get_run(run_id=run_a.id, tenant_id=a)).status is EvalRunStatus.PASSED  # type: ignore[union-attr]
    assert (await store.get_run(run_id=run_b.id, tenant_id=b)).status is EvalRunStatus.PASSED  # type: ignore[union-attr]
    # The already-running one was left untouched (still RUNNING, no results).
    assert (await store.get_run(run_id=run_done.id, tenant_id=a)).status is EvalRunStatus.RUNNING  # type: ignore[union-attr]
