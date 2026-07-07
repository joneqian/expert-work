"""Stream SE — SE-11 predict→falsify verdict (Mini-ADR SE-A18/A19).

Two layers: the pure-logic band decision (:func:`decide_prediction_verdict`)
and its integration into the rollback monitor sweep (recorded alongside the
rollback decision, 叠加不替代 — never gates archive).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from control_plane.skill_evolution_limits import CircuitBreaker
from control_plane.skill_prediction_verdict import (
    PredictionVerdictAction,
    decide_prediction_verdict,
)
from control_plane.skill_rollback_gate import RollbackGate
from control_plane.skill_rollback_monitor import RollbackMonitor, RollbackMonitorConfig
from expert_work.persistence.skill.memory import InMemorySkillStore
from expert_work.protocol import SkillEvalResult, SkillRunUsage, SkillStatus, TrajectoryOutcome

_NOW = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)


# ── pure logic: bands on the realized fraction ────────────────────────────


def test_insufficient_window_skips() -> None:
    r = decide_prediction_verdict(
        baseline_score=0.5, skill_score=0.9, observed_rate=0.9, n_window=5
    )
    assert r.action is PredictionVerdictAction.INSUFFICIENT


def test_effective_when_full_gain_held() -> None:
    # observed 0.9 == skill_score → realized 100% of the 0.4 predicted gain.
    r = decide_prediction_verdict(
        baseline_score=0.5, skill_score=0.9, observed_rate=0.9, n_window=20
    )
    assert r.action is PredictionVerdictAction.EFFECTIVE
    assert r.realized_fraction == 1.0


def test_partially_effective_when_half_gain_held() -> None:
    # observed 0.7 → (0.7-0.5)/0.4 = 0.5 → PARTIALLY.
    r = decide_prediction_verdict(
        baseline_score=0.5, skill_score=0.9, observed_rate=0.7, n_window=20
    )
    assert r.action is PredictionVerdictAction.PARTIALLY_EFFECTIVE


def test_ineffective_when_back_at_baseline() -> None:
    # observed 0.55 → fraction ~0.125 → INEFFECTIVE (≈ no-skill level).
    r = decide_prediction_verdict(
        baseline_score=0.5, skill_score=0.9, observed_rate=0.55, n_window=20
    )
    assert r.action is PredictionVerdictAction.INEFFECTIVE


def test_mixed_below_baseline_above_floor() -> None:
    # baseline 0.7, observed 0.6 → below no-skill baseline but above floor 0.5.
    r = decide_prediction_verdict(
        baseline_score=0.7, skill_score=0.95, observed_rate=0.6, n_window=20
    )
    assert r.action is PredictionVerdictAction.MIXED


def test_harmful_below_absolute_floor() -> None:
    r = decide_prediction_verdict(
        baseline_score=0.5, skill_score=0.9, observed_rate=0.2, n_window=20
    )
    assert r.action is PredictionVerdictAction.HARMFUL


# ── integration: recorded in the rollback sweep ───────────────────────────


async def _distilled_active_skill(store: InMemorySkillStore, *, tenant_id: UUID) -> UUID:
    skill = await store.create_skill(
        skill_id=uuid4(),
        tenant_id=tenant_id,
        name="s",
        visibility="agent_private",
        created_by_agent_name="assistant",
    )
    await store.add_version(
        version_id=uuid4(),
        skill_id=skill.id,
        tenant_id=tenant_id,
        prompt_fragment="do x",
        authored_by="agent",
        evolution_origin="distilled",  # type: ignore[arg-type]
    )
    await store.set_status(skill_id=skill.id, tenant_id=tenant_id, status=SkillStatus.ACTIVE)
    return skill.id


async def _seed_baseline(store, *, tenant_id, skill_id, baseline, skill_score) -> None:
    await store.record_eval_result(
        result=SkillEvalResult(
            id=uuid4(),
            tenant_id=tenant_id,
            skill_id=skill_id,
            skill_version=1,
            baseline_score=baseline,
            skill_score=skill_score,
            delta=skill_score - baseline,
            n_cases=10,
            replay_source="trajectory",
            verdict="pass",
            created_at=_NOW - timedelta(days=10),
        )
    )


async def _seed_window(store, *, tenant_id, skill_id, success, failed) -> None:
    outcomes: list[TrajectoryOutcome] = ["success"] * success + ["failed"] * failed
    for oc in outcomes:
        await store.record_skill_run_usage(
            usage=SkillRunUsage(
                id=uuid4(),
                tenant_id=tenant_id,
                skill_id=skill_id,
                skill_version=1,
                thread_id=uuid4(),
                agent_name="assistant",
                outcome=oc,
                created_at=_NOW,
            )
        )


def _monitor(store: InMemorySkillStore) -> RollbackMonitor:
    breaker = CircuitBreaker(failure_threshold=0.5, min_samples=5, window=timedelta(hours=24))
    gate = RollbackGate(skill_store=store, breaker=breaker)
    return RollbackMonitor(
        skill_store=store,
        gate=gate,
        config=RollbackMonitorConfig(window=timedelta(days=7)),
        clock=lambda: _NOW,
    )


async def test_sweep_records_effective_verdict() -> None:
    store = InMemorySkillStore()
    tid = uuid4()
    sid = await _distilled_active_skill(store, tenant_id=tid)
    await _seed_baseline(store, tenant_id=tid, skill_id=sid, baseline=0.5, skill_score=0.9)
    await _seed_window(store, tenant_id=tid, skill_id=sid, success=18, failed=2)

    await _monitor(store).run_once()

    verdicts = await store.list_prediction_verdicts(skill_id=sid, tenant_id=tid)
    assert len(verdicts) == 1
    assert verdicts[0].verdict == "effective"
    # Diagnostic only — the healthy skill is NOT archived.
    skill = await store.get_skill(skill_id=sid, tenant_id=tid)
    assert skill is not None and skill.status is SkillStatus.ACTIVE


async def test_sweep_records_harmful_verdict_alongside_rollback() -> None:
    store = InMemorySkillStore()
    tid = uuid4()
    sid = await _distilled_active_skill(store, tenant_id=tid)
    await _seed_baseline(store, tenant_id=tid, skill_id=sid, baseline=0.5, skill_score=0.9)
    await _seed_window(store, tenant_id=tid, skill_id=sid, success=4, failed=16)

    tally = await _monitor(store).run_once()

    verdicts = await store.list_prediction_verdicts(skill_id=sid, tenant_id=tid)
    assert len(verdicts) == 1
    assert verdicts[0].verdict == "harmful"
    # The binomial rollback judge — not the verdict — still decides archive.
    assert tally.rolled_back == 1


async def test_sweep_skips_verdict_when_window_insufficient() -> None:
    store = InMemorySkillStore()
    tid = uuid4()
    sid = await _distilled_active_skill(store, tenant_id=tid)
    await _seed_baseline(store, tenant_id=tid, skill_id=sid, baseline=0.5, skill_score=0.9)
    await _seed_window(store, tenant_id=tid, skill_id=sid, success=3, failed=2)  # n=5 < n_min

    await _monitor(store).run_once()

    verdicts = await store.list_prediction_verdicts(skill_id=sid, tenant_id=tid)
    assert verdicts == []
