"""Replay-verification runner (Stream SE, SE-4b) — the 咽喉's hands.

SE-4a (:mod:`orchestrator.evolution.grounding`) is the judgment brain. This
module orchestrates the actual *replay*: for each held-out task it runs the
agent **without** the candidate skill (baseline) and **with** it (treatment),
scores both, and feeds the per-case outcomes to ``decide_grounding`` — then
persists the resulting :class:`SkillEvalResult` as the grounding evidence
(Mini-ADR SE-A0/SE-A2).

The heavy parts — building + running two real agent graphs, and a real LLM
judge — are injected behind two seams so the orchestration stays unit-testable
in CI (Mini-ADR SE-A6: CI uses fakes / scripted judge; the real graph + Haiku
judge run only under integration):

* :class:`TaskRunner` — runs one task with or without the candidate skill and
  returns the agent's answer text. The real-graph adapter (builds two
  ``AgentSpec``\\ s differing only in ``skills``, sandboxes high-risk replays)
  lands in SE-4c.
* :class:`ReplayJudge` — scores one answer 1..N. Structurally matches the eval
  harness ``JudgeProvider`` so ``ScriptedJudge`` / ``AnthropicHaikuJudge``
  satisfy it by duck typing when wired from integration.

Scoring is **pointwise** (baseline and treatment are scored independently),
which sidesteps the 60-75% position bias of pairwise LLM judges - no
swap-order needed (that mitigation only applies if a pairwise judge is used).

Held-out integrity (SPARK): a task whose ``trajectory_key`` matches the
request's ``distilled_from_trajectory_key`` is dropped before replay, so a
distilled skill is never scored against its own source trajectory.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from helix_agent.persistence.skill.base import SkillStore
from helix_agent.protocol.skill import ReplaySource, SkillEvalResult
from orchestrator.evolution.grounding import (
    CaseOutcome,
    GroundingConfig,
    GroundingDecision,
    SignalTier,
    decide_grounding,
    to_eval_result,
)

__all__ = [
    "ReplayJudge",
    "ReplayRequest",
    "ReplayRunner",
    "ReplayTask",
    "TaskRunner",
]


class TaskRunner(Protocol):
    """Runs one replay task and returns the agent's answer text."""

    async def run(self, *, case_id: str, prompt: str, with_skill: bool) -> str: ...


class ReplayJudge(Protocol):
    """Scores one answer (the answer is embedded in ``prompt``) on an int scale."""

    async def score(self, *, case_id: str, prompt: str) -> int: ...


@dataclass(frozen=True)
class ReplayTask:
    """One held-out task to replay.

    ``assertions`` (when present) make the case a *hard verifier* signal: the
    answer passes iff every assertion holds, scored 1.0/0.0. Otherwise the
    judge scores it on an ordinal scale. ``is_anchor`` flags a verifiable
    anchor case mixed in to keep the judge honest (Mini-ADR SE-A5b/SE-A9).
    """

    case_id: str
    prompt: str
    assertions: tuple[Callable[[str], bool], ...] = ()
    is_anchor: bool = False
    trajectory_key: str | None = None


@dataclass(frozen=True)
class ReplayRequest:
    """The candidate under test plus its provenance + signal metadata."""

    skill_id: UUID
    skill_version: int
    tenant_id: UUID | None
    signal_tier: SignalTier
    replay_source: ReplaySource
    high_risk: bool = False
    #: SE-A5b T2 — tracked judge↔human agreement is above the calibration bar.
    judge_calibrated: bool = True
    #: SPARK held-out — drop any task replayed from this source trajectory.
    distilled_from_trajectory_key: str | None = None
    evolution_round: int = 0


def _judge_prompt(task_prompt: str, answer: str) -> str:
    return (
        "Score how well the response completes the task on a 1-5 scale "
        "(5 = fully correct and helpful, 1 = wrong or unhelpful).\n\n"
        f"Task:\n{task_prompt}\n\nResponse:\n{answer}\n\n"
        "Reply with a single integer from 1 to 5."
    )


@dataclass(frozen=True)
class ReplayRunner:
    """Orchestrates with-vs-without replay → grounding decision → persistence."""

    task_runner: TaskRunner
    judge: ReplayJudge
    store: SkillStore
    config: GroundingConfig = field(default_factory=GroundingConfig)
    judge_scale: tuple[int, int] = (1, 5)

    async def _score(self, task: ReplayTask, answer: str) -> float:
        if task.assertions:
            return 1.0 if all(check(answer) for check in task.assertions) else 0.0
        raw = await self.judge.score(
            case_id=task.case_id, prompt=_judge_prompt(task.prompt, answer)
        )
        lo, hi = self.judge_scale
        clamped = max(lo, min(hi, raw))
        return (clamped - lo) / (hi - lo)

    async def run(
        self,
        request: ReplayRequest,
        tasks: Sequence[ReplayTask],
        *,
        result_id: UUID,
        created_at: datetime,
    ) -> tuple[SkillEvalResult, GroundingDecision]:
        """Replay ``tasks`` with vs without the candidate, score, decide, persist.

        ``result_id`` and ``created_at`` are injected by the caller (the SE-6
        worker / integration test) to keep replay deterministic.
        """
        held_out = [
            t
            for t in tasks
            if not (
                request.distilled_from_trajectory_key is not None
                and t.trajectory_key == request.distilled_from_trajectory_key
            )
        ]

        outcomes: list[CaseOutcome] = []
        anchors_passed = True
        for task in held_out:
            baseline = await self.task_runner.run(
                case_id=task.case_id, prompt=task.prompt, with_skill=False
            )
            treatment = await self.task_runner.run(
                case_id=task.case_id, prompt=task.prompt, with_skill=True
            )
            baseline_score = await self._score(task, baseline)
            treatment_score = await self._score(task, treatment)
            outcomes.append(
                CaseOutcome(
                    case_id=task.case_id,
                    baseline_score=baseline_score,
                    treatment_score=treatment_score,
                )
            )
            if task.is_anchor and treatment_score < self.config.pass_threshold:
                anchors_passed = False

        decision = decide_grounding(
            outcomes,
            signal_tier=request.signal_tier,
            config=self.config,
            high_risk=request.high_risk,
            anchors_passed=anchors_passed,
            judge_calibrated=request.judge_calibrated,
        )
        result = to_eval_result(
            decision,
            result_id=result_id,
            tenant_id=request.tenant_id,
            skill_id=request.skill_id,
            skill_version=request.skill_version,
            replay_source=request.replay_source,
            created_at=created_at,
            high_risk=request.high_risk,
            evolution_round=request.evolution_round,
        )
        await self.store.record_eval_result(result=result)
        return result, decision
