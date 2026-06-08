"""Co-evolve orchestration core (Stream SE, SE-6a) — Layer B's brain.

Ties the pieces into the bounded generation-verification-refinement loop
(SkillGen / CoEvoSkills): distil a draft, replay-verify it (SE-4), and on a
content failure revise + re-verify, up to a round cap. Execution/environment
failures are never fed back (SE-5b attribution gates this — anti-collapse).

It is **pure loop logic**: every heavy dependency is an injected seam, so it is
fully unit-testable in CI. The SE-6b worker supplies the real implementations
(distiller + aux LLM, replay runner + real graph, attributor, draft
persistence) and the anchor-mixed replay set.

Generator vs verifier separation (CoEvoSkills): ``distill`` / ``revise`` are the
generator; ``replay`` is the independent verifier — they must not be the same
model scoring its own output.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from control_plane.skill_attribution import (
    AttributionVerdict,
    FailureKind,
    FailureSignal,
    should_feed_back,
)
from control_plane.skill_distiller import SkillDraft
from helix_agent.protocol.skill import EvalVerdict

__all__ = [
    "EvolutionConfig",
    "EvolutionResult",
    "ReplayOutcome",
    "RoundRecord",
    "evolve",
]


@dataclass(frozen=True)
class ReplayOutcome:
    """One replay round's result, as the verifier seam reports it.

    ``verdict`` is the grounding verdict (SE-4). ``failure_signal`` carries the
    raw failure evidence (SE-4b retains it) for attribution; it is ``None`` on a
    pass. ``eval_result_id`` is the persisted ``skill_eval_result`` row, for
    provenance.
    """

    verdict: EvalVerdict
    failure_signal: FailureSignal | None = None
    eval_result_id: UUID | None = None
    #: SE-4a auto-promote eligibility (signal tier + anchors + not high-risk),
    #: surfaced so the SE-7c gate can act on a grounded result.
    auto_promote_eligible: bool = False


@dataclass(frozen=True)
class RoundRecord:
    round: int
    verdict: EvalVerdict
    attribution: FailureKind | None


EvolutionOutcome = Literal["grounded", "rejected", "exhausted", "no_draft"]


@dataclass(frozen=True)
class EvolutionResult:
    outcome: EvolutionOutcome
    draft: SkillDraft | None
    rounds: int
    reason: str
    history: tuple[RoundRecord, ...]
    #: True only on a grounded result whose passing replay was auto-eligible.
    auto_promote_eligible: bool = False


@dataclass(frozen=True)
class EvolutionConfig:
    max_rounds: int = 3

    def __post_init__(self) -> None:
        if self.max_rounds < 1:
            raise ValueError("max_rounds must be >= 1")


DistillFn = Callable[[], Awaitable[SkillDraft | None]]
ReplayFn = Callable[[SkillDraft, int], Awaitable[ReplayOutcome]]
AttributeFn = Callable[[SkillDraft, FailureSignal], Awaitable[AttributionVerdict]]
ReviseFn = Callable[[SkillDraft, ReplayOutcome], Awaitable[SkillDraft | None]]


async def evolve(
    *,
    distill: DistillFn,
    replay: ReplayFn,
    attribute: AttributeFn,
    revise: ReviseFn,
    config: EvolutionConfig | None = None,
) -> EvolutionResult:
    """Run the bounded co-evolve loop and report how it ended.

    * ``no_draft`` — distillation yielded nothing.
    * ``grounded`` — a round's replay passed.
    * ``rejected`` — inconclusive (→ DRAFT for human), an execution-error
      failure (not learned), a failure with no signal, or a revise that
      produced nothing.
    * ``exhausted`` — ran ``max_rounds`` content-error revisions without a pass.
    """
    cfg = config or EvolutionConfig()

    draft = await distill()
    if draft is None:
        return EvolutionResult("no_draft", None, 0, "distillation produced no draft", ())

    history: list[RoundRecord] = []
    for round_no in range(1, cfg.max_rounds + 1):
        outcome = await replay(draft, round_no)

        if outcome.verdict == "pass":
            history.append(RoundRecord(round_no, "pass", None))
            return EvolutionResult(
                "grounded",
                draft,
                round_no,
                "replay passed",
                tuple(history),
                auto_promote_eligible=outcome.auto_promote_eligible,
            )

        if outcome.verdict == "inconclusive":
            history.append(RoundRecord(round_no, "inconclusive", None))
            return EvolutionResult(
                "rejected", draft, round_no, "inconclusive -> DRAFT for human", tuple(history)
            )

        # verdict == "fail" — attribute before deciding whether to learn.
        if outcome.failure_signal is None:
            history.append(RoundRecord(round_no, "fail", None))
            return EvolutionResult(
                "rejected", draft, round_no, "fail without failure signal", tuple(history)
            )

        verdict = await attribute(draft, outcome.failure_signal)
        history.append(RoundRecord(round_no, "fail", verdict.kind))
        if not should_feed_back(verdict):
            return EvolutionResult(
                "rejected",
                draft,
                round_no,
                f"execution error, not learning ({verdict.reason})",
                tuple(history),
            )

        revised = await revise(draft, outcome)
        if revised is None:
            return EvolutionResult(
                "rejected", draft, round_no, "revise produced no draft", tuple(history)
            )
        draft = revised

    return EvolutionResult(
        "exhausted", draft, cfg.max_rounds, "max rounds reached without grounding", tuple(history)
    )
