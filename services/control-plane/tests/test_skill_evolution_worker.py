"""Tests for the SE-6b evolution worker shell.

Exercises the scan → per-candidate processing → tally control flow with a fake
processor + in-memory candidate store. The real processor (aux LLM + graph
replay + DRAFT persistence) is wired in SE-6c.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from control_plane.skill_evolution import EvolutionResult
from control_plane.skill_evolution_worker import SkillEvolutionWorker
from helix_agent.persistence.curation.memory import InMemoryCurationCandidateStore
from helix_agent.protocol import CandidateStatus, CurationCandidateRecord, CurationSignal


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
