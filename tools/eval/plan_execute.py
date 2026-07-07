"""J.1 plan_execute eval — Stream J.13a (M0 baseline).

Drives :func:`orchestrator.graph_builder.planner.parse_plan` (a pure
function) against a curated dataset of ``(task, llm_reply)`` pairs.
Two metric axes per Mini-ADR J-37 (the only capability where LLM-judge
is in scope):

* ``pass_rate`` — structural validity. A case passes when
  :class:`~expert_work.protocol.Plan.goal` contains every keyword the
  case lists *and* :class:`Plan.steps` has at least ``min_steps``
  entries. Both the well-formed cases and the fallback cases must
  satisfy this — the planner's "any malformed reply degrades to a
  single-step plan wrapping the task" guarantee (Mini-ADR J-3a) means
  ``min_steps=1`` is a meaningful floor.
* ``judge_mean`` — quality score 1-5 from a :class:`JudgeProvider`.
  CI uses :class:`ScriptedJudge` (per-case scripted scores from the
  YAML); the weekly 周跑 swaps in :class:`AnthropicHaikuJudge` via
  :func:`make_judge_from_env` (Mini-ADR J-39).
"""

from __future__ import annotations

import statistics
import sys as _sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from pathlib import Path as _Path
from typing import Any, cast

import yaml

from orchestrator.graph_builder.planner import parse_plan

_EVAL_DIR = _Path(__file__).resolve().parent
if str(_EVAL_DIR) not in _sys.path:
    _sys.path.insert(0, str(_EVAL_DIR))

from _capability import (  # type: ignore[import-not-found]  # noqa: E402
    CapabilityCaseResult,
    CapabilityReport,
    JudgeCompletionFn,
)
from _judge import (  # type: ignore[import-not-found]  # noqa: E402
    JudgeProvider,
    ScriptedJudge,
)

CAPABILITY = "J.1_plan_execute"
METRIC_TYPE = "pass-rate+llm-judge"
THRESHOLD = {"pass_rate": 0.80, "judge_mean": 4.0}

_JUDGE_PROMPT = (
    "You are scoring a generated execution plan. The task and plan are "
    "below. Reply with a single digit 1-5 (no prose, no punctuation) "
    "rating how well the plan covers what the task asks for. 5 = covers "
    "everything; 3 = partial; 1 = misses the task entirely.\n\n"
    "Task:\n{task}\n\nPlan goal: {goal}\nPlan steps:\n{steps}"
)


@dataclass(frozen=True)
class PlanCase:
    """One plan_execute case.

    ``llm_reply`` is the *pre-recorded* output of the planner LLM — the
    eval drives :func:`parse_plan` deterministically, so CI doesn't pay
    LLM cost on the structural axis. The judge axis still calls an
    LLM-or-mock to score the resulting plan.
    """

    case_id: str
    task: str
    llm_reply: str
    expected_goal_keywords: tuple[str, ...]
    min_steps: int
    mock_judge_score: int


async def _run_case(case: PlanCase, judge: JudgeProvider) -> CapabilityCaseResult:
    plan = parse_plan(case.llm_reply, fallback_goal=case.task)
    goal_lower = plan.goal.lower()
    missing = [kw for kw in case.expected_goal_keywords if kw.lower() not in goal_lower]
    structural_pass = (not missing) and len(plan.steps) >= case.min_steps

    steps_text = "\n".join(f"- {step.description}" for step in plan.steps)
    judge_prompt = _JUDGE_PROMPT.format(task=case.task, goal=plan.goal, steps=steps_text)
    score = await judge.score(case_id=case.case_id, prompt=judge_prompt)

    notes: list[str] = []
    if missing:
        notes.append(f"goal missing keywords: {missing}")
    if len(plan.steps) < case.min_steps:
        notes.append(f"steps={len(plan.steps)} < min_steps={case.min_steps}")
    if score < 1:
        notes.append(f"judge returned invalid score {score}")

    return CapabilityCaseResult(
        case_id=case.case_id,
        passed=structural_pass,
        scores={"structural_pass": float(structural_pass), "judge_score": float(score)},
        notes=tuple(notes),
    )


async def evaluate_set(
    cases: Sequence[PlanCase],
    *,
    judge: JudgeCompletionFn | JudgeProvider | None = None,
    rerun_count: int = 3,
) -> CapabilityReport:
    """Drive every case through :func:`parse_plan` + a judge call."""
    resolved_judge: JudgeProvider = (
        cast(JudgeProvider, judge)
        if judge is not None
        else ScriptedJudge({case.case_id: case.mock_judge_score for case in cases})
    )

    per_case: list[CapabilityCaseResult] = []
    for case in cases:
        per_case.append(await _run_case(case, resolved_judge))

    sample = len(per_case)
    pass_rate = sum(1 for r in per_case if r.passed) / sample if sample else 0.0
    judge_scores = [r.scores.get("judge_score", 0.0) for r in per_case]
    judge_mean = statistics.fmean(judge_scores) if judge_scores else 0.0
    meets_pass = pass_rate >= THRESHOLD["pass_rate"]
    meets_judge = judge_mean >= THRESHOLD["judge_mean"]
    status = "PASS" if meets_pass and meets_judge else "FAIL"
    return CapabilityReport(
        capability=CAPABILITY,
        metric_type=METRIC_TYPE,
        sample_size=sample,
        threshold=THRESHOLD,
        aggregate_score={"pass_rate": pass_rate, "judge_mean": judge_mean},
        status=cast(Any, status),
        per_case=tuple(per_case),
    )


def load_cases(path: Path) -> list[PlanCase]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: list[PlanCase] = []
    for entry in raw.get("cases", []):
        out.append(_parse_case(entry))
    return out


def _parse_case(entry: dict[str, Any]) -> PlanCase:
    return PlanCase(
        case_id=str(entry["id"]),
        task=str(entry["task"]),
        llm_reply=str(entry["llm_reply"]),
        expected_goal_keywords=tuple(str(k) for k in entry.get("expected_goal_keywords", ())),
        min_steps=int(entry.get("min_steps", 1)),
        mock_judge_score=int(entry["mock_judge_score"]),
    )


__all__ = [
    "CAPABILITY",
    "METRIC_TYPE",
    "THRESHOLD",
    "PlanCase",
    "evaluate_set",
    "load_cases",
]
