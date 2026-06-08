"""Tests for the SE-4b replay-runner orchestration core.

CI-side: the real agent-graph + Haiku judge are integration-only (Mini-ADR
SE-A6), so the orchestration is exercised here with fake seams (a fake
``TaskRunner`` + a marker-based judge) against an in-memory ``SkillStore``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from helix_agent.persistence.skill.memory import InMemorySkillStore
from orchestrator.evolution.grounding import SignalTier
from orchestrator.evolution.replay import (
    ReplayRequest,
    ReplayRunner,
    ReplayTask,
)

_TENANT = UUID("33333333-3333-3333-3333-333333333333")
_SKILL = UUID("22222222-2222-2222-2222-222222222222")
_NOW = datetime(2026, 6, 8, tzinfo=UTC)


class FakeRunner:
    """Returns a canned answer per (case_id, with_skill)."""

    def __init__(self, answers: dict[tuple[str, bool], str]) -> None:
        self._answers = answers
        self.calls: list[tuple[str, bool]] = []

    async def run(self, *, case_id: str, prompt: str, with_skill: bool) -> str:
        self.calls.append((case_id, with_skill))
        return self._answers[(case_id, with_skill)]


class MarkerJudge:
    """Scores 5 if the composed judge prompt contains GOOD, else 2."""

    async def score(self, *, case_id: str, prompt: str) -> int:
        return 5 if "GOOD" in prompt else 2


def _request(**over: object) -> ReplayRequest:
    base: dict[str, object] = {
        "skill_id": _SKILL,
        "skill_version": 1,
        "tenant_id": _TENANT,
        "signal_tier": SignalTier.HARD_VERIFIER,
        "replay_source": "trajectory",
    }
    base.update(over)
    return ReplayRequest(**base)  # type: ignore[arg-type]


def _runner(answers: dict[tuple[str, bool], str], *, store: InMemorySkillStore) -> ReplayRunner:
    return ReplayRunner(task_runner=FakeRunner(answers), judge=MarkerJudge(), store=store)


# --------------------------------------------------------------------------- #
# Judge-scored (ordinal) replay
# --------------------------------------------------------------------------- #


async def test_judge_improvement_passes_and_persists() -> None:
    store = InMemorySkillStore()
    answers = {}
    for i in range(8):
        answers[(f"c{i}", False)] = "BAD answer"
        answers[(f"c{i}", True)] = "GOOD answer"
    runner = _runner(answers, store=store)
    tasks = [ReplayTask(case_id=f"c{i}", prompt="do the thing") for i in range(8)]

    result, decision = await runner.run(
        _request(signal_tier=SignalTier.CALIBRATED_JUDGE),
        tasks,
        result_id=UUID("11111111-1111-1111-1111-111111111111"),
        created_at=_NOW,
    )

    assert decision.verdict == "pass"
    assert decision.test == "wilcoxon"
    assert result.skill_score > result.baseline_score
    # persisted
    rows = await store.list_eval_results(skill_id=_SKILL, tenant_id=_TENANT)
    assert len(rows) == 1
    assert rows[0].id == result.id


async def test_runs_both_baseline_and_treatment_per_case() -> None:
    store = InMemorySkillStore()
    answers = {("c0", False): "BAD", ("c0", True): "GOOD"}
    fake = FakeRunner(answers)
    runner = ReplayRunner(task_runner=fake, judge=MarkerJudge(), store=store)
    await runner.run(
        _request(),
        [ReplayTask(case_id="c0", prompt="x")],
        result_id=UUID("11111111-1111-1111-1111-111111111111"),
        created_at=_NOW,
    )
    assert ("c0", False) in fake.calls and ("c0", True) in fake.calls


# --------------------------------------------------------------------------- #
# Assertion-scored (binary, hard-verifier) replay
# --------------------------------------------------------------------------- #


async def test_assertion_binary_scoring_uses_mcnemar() -> None:
    store = InMemorySkillStore()
    answers = {}
    for i in range(6):
        answers[(f"c{i}", False)] = "nope"
        answers[(f"c{i}", True)] = "DONE"
    runner = _runner(answers, store=store)
    done = lambda ans: "DONE" in ans  # noqa: E731
    tasks = [ReplayTask(case_id=f"c{i}", prompt="x", assertions=(done,)) for i in range(6)]

    _, decision = await runner.run(
        _request(),
        tasks,
        result_id=UUID("11111111-1111-1111-1111-111111111111"),
        created_at=_NOW,
    )
    assert decision.test == "mcnemar"
    assert decision.verdict == "pass"
    assert decision.auto_promote_eligible is True  # T1 hard verifier


# --------------------------------------------------------------------------- #
# Anchor handling (SE-A5b / SE-A9)
# --------------------------------------------------------------------------- #


async def test_anchor_regression_blocks_calibrated_auto_promote() -> None:
    store = InMemorySkillStore()
    answers = {}
    for i in range(8):
        answers[(f"c{i}", False)] = "BAD answer"
        answers[(f"c{i}", True)] = "GOOD answer"
    # the anchor regresses under treatment (verifiable check fails)
    answers[("anchor", False)] = "DONE"
    answers[("anchor", True)] = "broken"
    runner = _runner(answers, store=store)
    done = lambda ans: "DONE" in ans  # noqa: E731
    tasks = [ReplayTask(case_id=f"c{i}", prompt="x") for i in range(8)]
    tasks.append(ReplayTask(case_id="anchor", prompt="x", assertions=(done,), is_anchor=True))

    _, decision = await runner.run(
        _request(signal_tier=SignalTier.CALIBRATED_JUDGE),
        tasks,
        result_id=UUID("11111111-1111-1111-1111-111111111111"),
        created_at=_NOW,
    )
    # anchor failed → not auto-eligible regardless of verdict
    assert decision.auto_promote_eligible is False


# --------------------------------------------------------------------------- #
# Held-out leakage guard (SPARK held-out, SE-A6)
# --------------------------------------------------------------------------- #


async def test_distillation_source_task_is_filtered_out() -> None:
    store = InMemorySkillStore()
    answers = {}
    for i in range(8):
        answers[(f"c{i}", False)] = "BAD answer"
        answers[(f"c{i}", True)] = "GOOD answer"
    answers[("leak", False)] = "BAD answer"
    answers[("leak", True)] = "GOOD answer"
    fake = FakeRunner(answers)
    runner = ReplayRunner(task_runner=fake, judge=MarkerJudge(), store=store)
    tasks = [ReplayTask(case_id=f"c{i}", prompt="x", trajectory_key=f"t{i}") for i in range(8)]
    tasks.append(ReplayTask(case_id="leak", prompt="x", trajectory_key="SOURCE"))

    _, decision = await runner.run(
        _request(distilled_from_trajectory_key="SOURCE"),
        tasks,
        result_id=UUID("11111111-1111-1111-1111-111111111111"),
        created_at=_NOW,
    )
    assert decision.n_cases == 8  # the leaking task was dropped
    assert ("leak", False) not in fake.calls


# --------------------------------------------------------------------------- #
# High-risk never auto-promotes
# --------------------------------------------------------------------------- #


async def test_high_risk_pass_not_auto_eligible() -> None:
    store = InMemorySkillStore()
    answers = {}
    for i in range(6):
        answers[(f"c{i}", False)] = "nope"
        answers[(f"c{i}", True)] = "DONE"
    runner = _runner(answers, store=store)
    done = lambda ans: "DONE" in ans  # noqa: E731
    tasks = [ReplayTask(case_id=f"c{i}", prompt="x", assertions=(done,)) for i in range(6)]

    result, decision = await runner.run(
        _request(high_risk=True),
        tasks,
        result_id=UUID("11111111-1111-1111-1111-111111111111"),
        created_at=_NOW,
    )
    assert decision.verdict == "pass"
    assert decision.auto_promote_eligible is False
    assert result.high_risk is True
