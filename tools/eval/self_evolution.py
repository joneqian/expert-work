"""Stream SE (SE-9) — self-evolution benchmark + SLO.

Proves the self-evolving-skill closed loop is real, not benchmark-gaming, on
fully-deterministic fakes (no LLM — CI path). Each scenario exercises a real
SE component end-to-end:

* **closed loop** — drive :class:`ReplayRunner` (SE-4b) with a fake task runner
  where the candidate skill turns failing answers into passing ones; assert the
  with-vs-without replay grounds a ``pass`` verdict with ``delta > 0`` and is
  auto-promote eligible (SE-4a), then :func:`decide_promotion` (SE-7a) → AUTO.
* **anti-gaming** — held-out tasks drawn from the distill-source trajectory are
  dropped before scoring (SPARK held-out separation, SE-4b).
* **no false positives** — a no-gain candidate stays ``inconclusive``; a harmful
  candidate is ``fail`` (never silently promoted).
* **safety arm** — the kill-switch (SE-8) degrades AUTO→HUMAN_REVIEW; a regressed
  live version rolls back (SE-7d-2) while a healthy one is kept.
* **SLO** — the full replay over a batch completes within a wall-clock budget.

The real LLM / graph path (true distill + true judge) is covered by the SE-6/
SE-4c integration seams; CI has no model key, so this benchmark fixes the
verification math + control flow with deterministic doubles (Mini-ADR SE-A14).
"""

from __future__ import annotations

import sys as _sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from control_plane.skill_promotion import PromoteAction, decide_promotion
from control_plane.skill_rollback import RollbackAction, decide_rollback
from expert_work.persistence import InMemorySkillStore
from expert_work.protocol import TrajectoryOutcome
from orchestrator.evolution.grounding import SignalTier
from orchestrator.evolution.replay import (
    ReplayRequest,
    ReplayRunner,
    ReplayTask,
)

_EVAL_DIR = Path(__file__).resolve().parent
if str(_EVAL_DIR) not in _sys.path:
    _sys.path.insert(0, str(_EVAL_DIR))

from _capability import (  # type: ignore[import-not-found]  # noqa: E402
    CapabilityCaseResult,
    CapabilityReport,
)

CAPABILITY = "SE.9_self_evolution"
METRIC_TYPE = "pass-rate"
THRESHOLD = {"pass_rate": 1.0}

# SLO budget (wall-clock) for a deterministic replay batch. Generous vs the
# fakes' actual cost (sub-ms) — guards against an accidental O(n²) regression
# in the replay loop, not against real LLM latency (that is an integration SLO).
_REPLAY_BUDGET_S = 5.0

_NOW = datetime(2026, 6, 8, 12, 0, 0, tzinfo=UTC)


def _correct(answer: str) -> bool:
    return answer == "correct"


@dataclass
class _FakeTaskRunner:
    """Returns ``correct`` only when the named variant is active."""

    good_with_skill: bool = True

    async def run(self, *, case_id: str, prompt: str, with_skill: bool) -> str:
        wins = with_skill if self.good_with_skill else not with_skill
        return "correct" if wins else "wrong"


@dataclass
class _ConstantRunner:
    """Always returns the same answer regardless of variant (no-gain case)."""

    answer: str = "correct"

    async def run(self, *, case_id: str, prompt: str, with_skill: bool) -> str:
        return self.answer


class _StubJudge:
    """Never invoked when tasks carry assertions (hard verifier); present to
    satisfy the :class:`ReplayJudge` Protocol field on the runner."""

    async def score(self, *, case_id: str, prompt: str) -> int:  # pragma: no cover
        return 3


def _tasks(n: int, *, extra_source: str | None = None) -> list[ReplayTask]:
    tasks = [
        ReplayTask(case_id=f"c{i}", prompt=f"task {i}", assertions=(_correct,)) for i in range(n)
    ]
    if extra_source is not None:
        tasks.append(
            ReplayTask(
                case_id="src",
                prompt="from the distill source",
                assertions=(_correct,),
                trajectory_key=extra_source,
            )
        )
    return tasks


def _request(*, distilled_from: str | None = None) -> ReplayRequest:
    return ReplayRequest(
        skill_id=uuid4(),
        skill_version=1,
        tenant_id=uuid4(),
        signal_tier=SignalTier.HARD_VERIFIER,
        replay_source="eval_dataset",
        distilled_from_trajectory_key=distilled_from,
    )


async def _replay(task_runner: Any, tasks: Sequence[ReplayTask], request: ReplayRequest) -> Any:
    runner = ReplayRunner(
        task_runner=task_runner,
        judge=_StubJudge(),
        store=InMemorySkillStore(),
    )
    return await runner.run(request, tasks, result_id=uuid4(), created_at=_NOW)


# --------------------------------------------------------------------------- #
# Scenarios
# --------------------------------------------------------------------------- #


async def _run_closed_loop_grounds() -> tuple[bool, str]:
    result, decision = await _replay(_FakeTaskRunner(), _tasks(6), _request())
    if decision.verdict != "pass":
        return False, f"a clearly-better skill should ground pass, got {decision.verdict}"
    if decision.delta <= 0:
        return False, f"delta should be positive, got {decision.delta}"
    if not decision.auto_promote_eligible:
        return False, "a hard-verifier pass should be auto-promote eligible"
    if result.verdict != "pass":
        return False, "the persisted eval result should mirror the pass verdict"
    return True, ""


async def _run_held_out_success_up() -> tuple[bool, str]:
    _, decision = await _replay(_FakeTaskRunner(), _tasks(6), _request())
    if not (decision.treatment_score > decision.baseline_score):
        return False, "treatment mean must exceed baseline mean on held-out tasks"
    return True, ""


async def _run_held_out_excludes_distill_source() -> tuple[bool, str]:
    src = "trajectories/distill-source.jsonl"
    _, decision = await _replay(
        _FakeTaskRunner(), _tasks(6, extra_source=src), _request(distilled_from=src)
    )
    # 7 tasks in, but the source-derived one is dropped → 6 scored (anti-gaming).
    if decision.n_cases != 6:
        return False, f"the distill-source task must be excluded; n_cases={decision.n_cases}"
    return True, ""


async def _run_no_gain_inconclusive() -> tuple[bool, str]:
    _, decision = await _replay(_ConstantRunner("correct"), _tasks(6), _request())
    if decision.verdict == "pass":
        return False, "a no-gain candidate must not ground a pass"
    return True, ""


async def _run_harmful_fails() -> tuple[bool, str]:
    # good_with_skill=False → the skill makes answers worse.
    _, decision = await _replay(_FakeTaskRunner(good_with_skill=False), _tasks(6), _request())
    if decision.verdict != "fail":
        return False, f"a significantly harmful skill should fail, got {decision.verdict}"
    return True, ""


async def _run_promote_auto_when_grounded() -> tuple[bool, str]:
    d = decide_promotion(
        grounded=True,
        auto_promote_eligible=True,
        high_risk=False,
        breaker_open=False,
        within_rate_limit=True,
    )
    if d.action is not PromoteAction.AUTO_PROMOTE:
        return False, f"a grounded eligible candidate should auto-promote, got {d.action}"
    return True, ""


async def _run_kill_switch_blocks_auto() -> tuple[bool, str]:
    d = decide_promotion(
        grounded=True,
        auto_promote_eligible=True,
        high_risk=False,
        breaker_open=False,
        within_rate_limit=True,
        evolution_halted=True,
    )
    if d.action is not PromoteAction.HUMAN_REVIEW:
        return False, "an engaged kill-switch must degrade auto-promote to human review"
    return True, ""


async def _run_rollback_on_regression() -> tuple[bool, str]:
    # Promote-time baseline 0.9; live window collapses to 0.2 → regression.
    outcomes: list[TrajectoryOutcome] = ["failed"] * 8 + ["success"] * 2
    d = decide_rollback(outcomes, promote_baseline=0.9)
    if d.action is not RollbackAction.ROLLBACK:
        return False, f"a regressed live version should roll back, got {d.action}"
    return True, ""


async def _run_rollback_keeps_healthy() -> tuple[bool, str]:
    outcomes: list[TrajectoryOutcome] = ["success"] * 9 + ["failed"] * 1
    d = decide_rollback(outcomes, promote_baseline=0.9)
    if d.action is RollbackAction.ROLLBACK:
        return False, "a healthy live version must not roll back"
    return True, ""


async def _run_replay_latency_within_budget() -> tuple[bool, str]:
    start = time.perf_counter()
    await _replay(_FakeTaskRunner(), _tasks(12), _request())
    elapsed = time.perf_counter() - start
    if elapsed > _REPLAY_BUDGET_S:
        return False, f"replay batch took {elapsed:.2f}s > {_REPLAY_BUDGET_S}s budget"
    return True, ""


_SCENARIOS: dict[str, Any] = {
    "closed_loop_grounds": _run_closed_loop_grounds,
    "held_out_success_up": _run_held_out_success_up,
    "held_out_excludes_distill_source": _run_held_out_excludes_distill_source,
    "no_gain_inconclusive": _run_no_gain_inconclusive,
    "harmful_fails": _run_harmful_fails,
    "promote_auto_when_grounded": _run_promote_auto_when_grounded,
    "kill_switch_blocks_auto": _run_kill_switch_blocks_auto,
    "rollback_on_regression": _run_rollback_on_regression,
    "rollback_keeps_healthy": _run_rollback_keeps_healthy,
    "replay_latency_within_budget": _run_replay_latency_within_budget,
}


@dataclass(frozen=True)
class SelfEvolutionEvalCase:
    case_id: str
    scenario: str
    args: dict[str, Any] = field(default_factory=dict)


async def _run_case(case: SelfEvolutionEvalCase) -> CapabilityCaseResult:
    runner = _SCENARIOS.get(case.scenario)
    if runner is None:
        return CapabilityCaseResult(
            case_id=case.case_id, passed=False, notes=(f"unknown scenario {case.scenario!r}",)
        )
    passed, note = await runner()
    return CapabilityCaseResult(case_id=case.case_id, passed=passed, notes=(note,) if note else ())


def load_cases(path: Path) -> tuple[SelfEvolutionEvalCase, ...]:
    with path.open("r", encoding="utf-8") as fh:
        payload = yaml.safe_load(fh)
    out: list[SelfEvolutionEvalCase] = []
    for raw in payload.get("cases", []):
        out.append(
            SelfEvolutionEvalCase(
                case_id=str(raw["id"]),
                scenario=str(raw["scenario"]),
                args=dict(raw.get("args", {})),
            )
        )
    return tuple(out)


async def evaluate_set(cases: Sequence[SelfEvolutionEvalCase]) -> CapabilityReport:
    per_case = [await _run_case(case) for case in cases]
    sample_size = len(per_case)
    passed = sum(1 for r in per_case if r.passed)
    pass_rate = passed / sample_size if sample_size else 0.0
    status = "PASS" if pass_rate >= THRESHOLD["pass_rate"] and sample_size > 0 else "FAIL"
    return CapabilityReport(
        capability=CAPABILITY,
        metric_type=METRIC_TYPE,
        sample_size=sample_size,
        threshold=dict(THRESHOLD),
        aggregate_score={"pass_rate": pass_rate},
        status=status,
        per_case=tuple(per_case),
    )


__all__ = [
    "CAPABILITY",
    "METRIC_TYPE",
    "THRESHOLD",
    "SelfEvolutionEvalCase",
    "evaluate_set",
    "load_cases",
]
