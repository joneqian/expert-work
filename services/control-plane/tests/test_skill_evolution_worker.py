"""Tests for the SE-6b evolution worker shell.

Exercises the scan → per-candidate processing → tally control flow with a fake
processor + in-memory candidate store. The real processor (aux LLM + graph
replay + DRAFT persistence) is wired in SE-6c.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from control_plane.skill_evolution import EvolutionResult, TransientEvolutionError
from control_plane.skill_evolution_metering import current_metering
from control_plane.skill_evolution_worker import ScreenDecision, SkillEvolutionWorker
from expert_work.persistence.curation.memory import InMemoryCurationCandidateStore
from expert_work.protocol import CandidateStatus, CurationCandidateRecord, CurationSignal


def _candidate(
    *, signal: CurationSignal, status: CandidateStatus = CandidateStatus.PENDING, tenant: UUID
) -> CurationCandidateRecord:
    reviewed = None if status is CandidateStatus.PENDING else datetime.now(UTC)
    return CurationCandidateRecord(
        id=uuid4(),
        tenant_id=tenant,
        agent_name="assistant",
        thread_id=uuid4(),
        trajectory_key=f"k/{uuid4()}",
        outcome="success",
        signal=signal,
        status=status,
        detected_at=datetime.now(UTC),
        reviewed_at=reviewed,
    )


def _result(outcome: str) -> EvolutionResult:
    return EvolutionResult(outcome=outcome, draft=None, rounds=1, reason=outcome, history=())  # type: ignore[arg-type]


async def _seed(
    store: InMemoryCurationCandidateStore, records: list[CurationCandidateRecord]
) -> None:
    for rec in records:
        await store.upsert(rec)


class RecordingProcessor:
    def __init__(self, outcome: str = "grounded") -> None:
        self.outcome = outcome
        self.seen: list[CurationCandidateRecord] = []

    async def __call__(self, candidate: CurationCandidateRecord) -> EvolutionResult:
        self.seen.append(candidate)
        return _result(self.outcome)


async def test_run_once_processes_evolvable_signals_only() -> None:
    tenant = uuid4()
    store = InMemoryCurationCandidateStore()
    await _seed(
        store,
        [
            _candidate(signal="positive_feedback", tenant=tenant),
            _candidate(signal="failed_outcome", tenant=tenant),
            _candidate(signal="negative_feedback", tenant=tenant),  # skipped
        ],
    )
    proc = RecordingProcessor("grounded")
    worker = SkillEvolutionWorker(candidate_store=store, processor=proc, interval_s=60)

    tally = await worker.run_once()

    assert tally.processed == 2  # negative_feedback skipped
    assert tally.grounded == 2
    assert {c.signal for c in proc.seen} == {"positive_feedback", "failed_outcome"}


async def test_run_once_skips_non_pending() -> None:
    tenant = uuid4()
    store = InMemoryCurationCandidateStore()
    await _seed(
        store,
        [
            _candidate(signal="positive_feedback", status=CandidateStatus.DISMISSED, tenant=tenant),
        ],
    )
    proc = RecordingProcessor()
    worker = SkillEvolutionWorker(candidate_store=store, processor=proc, interval_s=60)
    tally = await worker.run_once()
    assert tally.processed == 0
    assert proc.seen == []


async def test_tally_counts_outcomes() -> None:
    tenant = uuid4()
    store = InMemoryCurationCandidateStore()
    await _seed(store, [_candidate(signal="positive_feedback", tenant=tenant) for _ in range(3)])

    outcomes = iter(["grounded", "rejected", "exhausted"])

    async def processor(candidate: CurationCandidateRecord) -> EvolutionResult:
        return _result(next(outcomes))

    worker = SkillEvolutionWorker(candidate_store=store, processor=processor, interval_s=60)
    tally = await worker.run_once()
    assert tally.grounded == 1
    assert tally.rejected == 1
    assert tally.exhausted == 1


async def test_batch_size_caps_processing() -> None:
    tenant = uuid4()
    store = InMemoryCurationCandidateStore()
    await _seed(store, [_candidate(signal="positive_feedback", tenant=tenant) for _ in range(5)])
    proc = RecordingProcessor()
    worker = SkillEvolutionWorker(
        candidate_store=store, processor=proc, interval_s=60, batch_size=2
    )
    tally = await worker.run_once()
    assert tally.processed == 2


def test_interval_must_be_positive() -> None:
    store = InMemoryCurationCandidateStore()
    with pytest.raises(ValueError):
        SkillEvolutionWorker(candidate_store=store, processor=RecordingProcessor(), interval_s=0)


async def test_start_stop_lifecycle() -> None:
    store = InMemoryCurationCandidateStore()
    worker = SkillEvolutionWorker(
        candidate_store=store, processor=RecordingProcessor(), interval_s=60
    )
    assert worker.is_running is False
    worker.start()
    assert worker.is_running is True
    worker.start()  # idempotent
    await worker.stop()
    assert worker.is_running is False


class _RaisingProcessor:
    """Raises on the first candidate, succeeds on the rest — exercises the
    per-candidate isolation (one bad candidate must not abort the batch)."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, candidate: CurationCandidateRecord) -> EvolutionResult:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("aux credential unresolvable for this tenant")
        return _result("grounded")


@pytest.mark.asyncio
async def test_run_once_isolates_a_failing_candidate() -> None:
    tenant = uuid4()
    store = InMemoryCurationCandidateStore()
    await _seed(
        store,
        [
            _candidate(signal="positive_feedback", tenant=tenant),
            _candidate(signal="positive_feedback", tenant=tenant),
        ],
    )
    worker = SkillEvolutionWorker(
        candidate_store=store, processor=_RaisingProcessor(), interval_s=60
    )
    tally = await worker.run_once()
    # First candidate raised → isolated; second still processed (not aborted).
    assert tally.scanned == 2
    assert tally.processed == 1
    assert tally.grounded == 1


@pytest.mark.asyncio
async def test_run_once_marks_evolved_and_does_not_reprocess() -> None:
    # 4.4 #5 — a processed candidate is marked evolved so the next sweep skips
    # it (the live loop previously re-distilled the same trajectory forever).
    tenant = uuid4()
    store = InMemoryCurationCandidateStore()
    await _seed(
        store,
        [
            _candidate(signal="positive_feedback", tenant=tenant),
            _candidate(signal="positive_feedback", tenant=tenant),
        ],
    )
    proc = RecordingProcessor("no_draft")
    worker = SkillEvolutionWorker(candidate_store=store, processor=proc, interval_s=60)

    first = await worker.run_once()
    assert first.processed == 2
    assert len(proc.seen) == 2

    # Second sweep: all candidates now evolved → nothing to process.
    second = await worker.run_once()
    assert second.scanned == 0
    assert second.processed == 0
    assert len(proc.seen) == 2  # processor not called again


# ---------------------------------------------------------------------------
# SE-16 — transient retry budget (SE-A40) + per-tenant rollout gate (SE-A41)
# ---------------------------------------------------------------------------


class _ExplodingProcessor:
    """Fails N times with ``exc``, then succeeds."""

    def __init__(self, exc: Exception, failures: int = 10**9) -> None:
        self.exc = exc
        self.failures = failures
        self.calls = 0

    async def __call__(self, candidate: CurationCandidateRecord) -> EvolutionResult:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exc
        return _result("grounded")


async def _sole_candidate(store: InMemoryCurationCandidateStore) -> CurationCandidateRecord:
    rows = await store.list_for_review_all_tenants()
    assert len(rows) == 1
    return rows[0]


async def test_transient_failure_requeues_instead_of_burning() -> None:
    tenant = uuid4()
    store = InMemoryCurationCandidateStore()
    await _seed(store, [_candidate(signal="positive_feedback", tenant=tenant)])
    proc = _ExplodingProcessor(TransientEvolutionError("aux rate limited"), failures=1)
    worker = SkillEvolutionWorker(candidate_store=store, processor=proc, interval_s=60)

    await worker.run_once()
    row = await _sole_candidate(store)
    # Requeued: retry bumped, NOT marked evolved.
    assert row.retry_count == 1
    assert row.evolved_at is None

    # Next sweep re-picks it and succeeds.
    await worker.run_once()
    row = await _sole_candidate(store)
    assert row.evolved_at is not None
    assert proc.calls == 2


async def test_transient_failures_give_up_at_budget() -> None:
    tenant = uuid4()
    store = InMemoryCurationCandidateStore()
    await _seed(store, [_candidate(signal="positive_feedback", tenant=tenant)])
    # Wrapped transport fault sniffed through the cause chain.
    exc = RuntimeError("distill failed")
    exc.__cause__ = TimeoutError("aux timed out")
    proc = _ExplodingProcessor(exc)
    worker = SkillEvolutionWorker(candidate_store=store, processor=proc, interval_s=60)

    await worker.run_once()  # retry 1
    await worker.run_once()  # retry 2
    row = await _sole_candidate(store)
    assert row.retry_count == 2 and row.evolved_at is None

    await worker.run_once()  # retry 3 → budget spent → burned
    row = await _sole_candidate(store)
    assert row.retry_count == 3
    assert row.evolved_at is not None

    await worker.run_once()  # burned candidate is not re-picked
    assert proc.calls == 3


async def test_permanent_failure_burns_immediately() -> None:
    tenant = uuid4()
    store = InMemoryCurationCandidateStore()
    await _seed(store, [_candidate(signal="positive_feedback", tenant=tenant)])
    proc = _ExplodingProcessor(ValueError("malformed trajectory"))
    worker = SkillEvolutionWorker(candidate_store=store, processor=proc, interval_s=60)

    await worker.run_once()
    row = await _sole_candidate(store)
    assert row.retry_count == 0
    assert row.evolved_at is not None


async def test_tenant_gate_skips_unenrolled_without_burning() -> None:
    enrolled, unenrolled = uuid4(), uuid4()
    store = InMemoryCurationCandidateStore()
    await _seed(
        store,
        [
            _candidate(signal="positive_feedback", tenant=enrolled),
            _candidate(signal="positive_feedback", tenant=unenrolled),
        ],
    )
    proc = RecordingProcessor()

    async def gate(tenant_id: UUID) -> bool:
        return tenant_id == enrolled

    worker = SkillEvolutionWorker(
        candidate_store=store, processor=proc, interval_s=60, tenant_gate=gate
    )
    await worker.run_once()

    assert {c.tenant_id for c in proc.seen} == {enrolled}
    rows = await store.list_for_review_all_tenants()
    by_tenant = {r.tenant_id: r for r in rows}
    # Enrolled tenant's candidate is processed + marked; the unenrolled one
    # stays pristine so it distils normally once the tenant is enrolled.
    assert by_tenant[enrolled].evolved_at is not None
    assert by_tenant[unenrolled].evolved_at is None
    assert by_tenant[unenrolled].retry_count == 0


# ---------------------------------------------------------------------------
# SE-16 (SE-A45) — sampled quality screen over implicit candidates
# ---------------------------------------------------------------------------


class RecordingScreener:
    def __init__(self, decision: ScreenDecision) -> None:
        self.decision = decision
        self.seen: list[CurationCandidateRecord] = []

    async def __call__(self, candidate: CurationCandidateRecord) -> ScreenDecision:
        self.seen.append(candidate)
        return self.decision


async def test_screen_reject_drops_implicit_without_distilling() -> None:
    tenant = uuid4()
    store = InMemoryCurationCandidateStore()
    await _seed(store, [_candidate(signal="implicit_success", tenant=tenant)])
    proc = RecordingProcessor()
    screener = RecordingScreener(ScreenDecision(proceed=False, reason="judge_filtered", score=2))
    worker = SkillEvolutionWorker(
        candidate_store=store, processor=proc, interval_s=60, screener=screener
    )

    tally = await worker.run_once()

    assert proc.seen == []  # never reached the distiller
    assert tally.screened == 1
    assert tally.processed == 0
    # Dropped for good — marked evolved so the sweep never re-screens it.
    row = await _sole_candidate(store)
    assert row.evolved_at is not None


async def test_screen_pass_proceeds_to_distillation() -> None:
    tenant = uuid4()
    store = InMemoryCurationCandidateStore()
    await _seed(store, [_candidate(signal="implicit_success", tenant=tenant)])
    proc = RecordingProcessor("grounded")
    screener = RecordingScreener(ScreenDecision(proceed=True, reason="judge_passed", score=5))
    worker = SkillEvolutionWorker(
        candidate_store=store, processor=proc, interval_s=60, screener=screener
    )

    tally = await worker.run_once()

    assert len(proc.seen) == 1
    assert tally.screened == 0
    assert tally.grounded == 1


async def test_screen_only_applies_to_implicit_signals() -> None:
    """Explicit 👍 / failed candidates always distil — never screened."""
    tenant = uuid4()
    store = InMemoryCurationCandidateStore()
    await _seed(
        store,
        [
            _candidate(signal="positive_feedback", tenant=tenant),
            _candidate(signal="failed_outcome", tenant=tenant),
        ],
    )
    proc = RecordingProcessor()
    screener = RecordingScreener(ScreenDecision(proceed=False, reason="judge_filtered", score=1))
    worker = SkillEvolutionWorker(
        candidate_store=store, processor=proc, interval_s=60, screener=screener
    )

    tally = await worker.run_once()

    assert screener.seen == []
    assert len(proc.seen) == 2
    assert tally.screened == 0


async def test_screen_transient_fault_requeues_instead_of_burning() -> None:
    """An aux timeout during the screen routes through the SE-A40 retry budget."""
    tenant = uuid4()
    store = InMemoryCurationCandidateStore()
    await _seed(store, [_candidate(signal="implicit_success", tenant=tenant)])
    proc = RecordingProcessor()

    async def screener(candidate: CurationCandidateRecord) -> ScreenDecision:
        raise TransientEvolutionError("aux judge timed out")

    worker = SkillEvolutionWorker(
        candidate_store=store, processor=proc, interval_s=60, screener=screener
    )
    await worker.run_once()

    row = await _sole_candidate(store)
    assert row.evolved_at is None
    assert row.retry_count == 1
    assert proc.seen == []


async def test_processor_runs_inside_metering_scope() -> None:
    """SE-A43 — aux calls made while processing a candidate can attribute
    their spend: the worker enters the metering scope per candidate."""
    tenant = uuid4()
    store = InMemoryCurationCandidateStore()
    await _seed(store, [_candidate(signal="positive_feedback", tenant=tenant)])
    seen: list[UUID | None] = []

    async def proc(candidate: CurationCandidateRecord) -> EvolutionResult:
        ctx = current_metering()
        seen.append(ctx.tenant_id if ctx is not None else None)
        return _result("grounded")

    worker = SkillEvolutionWorker(candidate_store=store, processor=proc, interval_s=60)
    await worker.run_once()

    assert seen == [tenant]
    assert current_metering() is None


async def test_llm_router_faults_requeue_instead_of_burning() -> None:
    """Live pilot finding #2 — the aux path surfaces 429 / 5xx / key faults
    as ``LLM*`` exceptions with no httpx cause (they wrap normal HTTP
    responses); each must hit the retry budget, not burn the candidate.
    Key/auth faults are deliberately retryable: the platform fixing its
    credential should re-pick the candidate up."""
    from expert_work.runtime.middleware.llm_error_handling import (
        LLMKeyUnavailableError,
        LLMRateLimitError,
        LLMServerError,
        LLMUnauthorizedError,
    )

    for exc_type in (
        LLMRateLimitError,
        LLMKeyUnavailableError,
        LLMServerError,
        LLMUnauthorizedError,
    ):
        store = InMemoryCurationCandidateStore()
        await _seed(store, [_candidate(signal="positive_feedback", tenant=uuid4())])
        proc = _ExplodingProcessor(exc_type("aux fault"), failures=1)
        worker = SkillEvolutionWorker(candidate_store=store, processor=proc, interval_s=60)

        await worker.run_once()
        row = await _sole_candidate(store)
        assert row.evolved_at is None, exc_type.__name__
        assert row.retry_count == 1, exc_type.__name__


async def test_llm_client_error_still_burns() -> None:
    """A 4xx request fault (bad request / context overflow) is permanent —
    retrying the identical aux call cannot succeed."""
    from expert_work.runtime.middleware.llm_error_handling import LLMClientError

    store = InMemoryCurationCandidateStore()
    await _seed(store, [_candidate(signal="positive_feedback", tenant=uuid4())])
    proc = _ExplodingProcessor(LLMClientError("400 context too long"))
    worker = SkillEvolutionWorker(candidate_store=store, processor=proc, interval_s=60)

    await worker.run_once()
    row = await _sole_candidate(store)
    assert row.retry_count == 0
    assert row.evolved_at is not None
