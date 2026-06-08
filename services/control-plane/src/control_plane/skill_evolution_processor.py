"""Evolution processor (Stream SE, SE-6c) — wires one candidate through the loop.

Implements the worker's :data:`CandidateProcessor`: assemble the distillation
evidence + held-out replay set for a candidate, then run the SE-6a co-evolve
loop with real distiller / attributor / replay, persisting each draft as a
DRAFT skill version (``evolution_origin='distilled'`` + provenance) so the agent
graph can load it for replay.

Cycle-safety: replay needs the orchestrator's agent graph (``agent_factory``),
which would create an import cycle if pulled in here. So replay is injected as a
:class:`ReplayInvoker` seam — the real implementation (ReplayRunner +
GraphReplayTaskRunner) is built lazily in the app lifespan; CI uses a fake.
This also keeps the distil → persist → attribute → revise → evolve glue fully
unit-testable in CI (only the LLM and graph boundaries are faked).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from control_plane.skill_attribution import AttributionVerdict, FailureSignal, SkillAttributor
from control_plane.skill_distiller import SkillDistiller, SkillDraft
from control_plane.skill_evolution import EvolutionConfig, EvolutionResult, ReplayOutcome, evolve
from control_plane.skill_promotion_gate import PromotionGate
from helix_agent.persistence import DuplicateSkillError, SkillStore
from helix_agent.protocol import CurationCandidateRecord


def _utcnow() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "EvidenceProvider",
    "EvolutionProcessor",
    "HeldOutProvider",
    "ReplayInvoker",
    "SkillEvidence",
]


@dataclass(frozen=True)
class SkillEvidence:
    """Rendered trajectory text the distiller learns from (SE-5a)."""

    successes: tuple[str, ...]
    failures: tuple[str, ...] = ()
    allowed_tools: frozenset[str] | None = None


EvidenceProvider = Callable[[CurationCandidateRecord], Awaitable[SkillEvidence]]
#: Returns an opaque held-out replay spec consumed only by the ReplayInvoker.
HeldOutProvider = Callable[[CurationCandidateRecord], Awaitable[Any]]


class ReplayInvoker(Protocol):
    """Replays a persisted DRAFT version and reports the grounding outcome."""

    async def __call__(
        self,
        *,
        candidate: CurationCandidateRecord,
        draft: SkillDraft,
        skill_id: UUID,
        skill_version: int,
        held_out: Any,
    ) -> ReplayOutcome:
        """Run with-vs-without replay for the draft and return the outcome."""


@dataclass
class _DraftState:
    skill_id: UUID | None = None
    version: int = 0


def _failure_feedback(outcome: ReplayOutcome) -> str:
    signal = outcome.failure_signal
    if signal is None:
        return "The previous version failed replay verification; revise the approach."
    bits = [signal.error_text]
    bits.extend(signal.tool_errors)
    detail = "; ".join(b for b in bits if b) or "unknown failure"
    return f"The previous version failed replay verification: {detail}. Fix the skill content."


@dataclass
class EvolutionProcessor:
    """Runs one curation candidate through distil → replay → co-evolve."""

    distiller: SkillDistiller
    attributor: SkillAttributor
    skill_store: SkillStore
    evidence_provider: EvidenceProvider
    held_out_provider: HeldOutProvider
    replay_invoker: ReplayInvoker
    config: EvolutionConfig = field(default_factory=EvolutionConfig)
    #: SE-7c governance gate. When wired, a grounded DRAFT is run through the
    #: auto-promote policy + guardrails (may flip it to ACTIVE). ``None`` leaves
    #: every grounded skill as DRAFT for human review.
    promotion_gate: PromotionGate | None = None
    clock: Callable[[], datetime] = _utcnow

    async def __call__(self, candidate: CurationCandidateRecord) -> EvolutionResult:
        evidence = await self.evidence_provider(candidate)
        held_out = await self.held_out_provider(candidate)
        state = _DraftState()

        async def distill() -> SkillDraft | None:
            draft = await self.distiller.distill(
                tenant_id=candidate.tenant_id,
                successes=list(evidence.successes),
                failures=list(evidence.failures),
                allowed_tools=evidence.allowed_tools,
            )
            if draft is None:
                return None
            await self._persist(draft, candidate, state, round_no=0)
            return draft

        async def replay(draft: SkillDraft, round_no: int) -> ReplayOutcome:
            assert state.skill_id is not None  # noqa: S101 — distill persisted it first
            return await self.replay_invoker(
                candidate=candidate,
                draft=draft,
                skill_id=state.skill_id,
                skill_version=state.version,
                held_out=held_out,
            )

        async def attribute(draft: SkillDraft, signal: FailureSignal) -> AttributionVerdict:
            return await self.attributor.attribute(
                tenant_id=candidate.tenant_id,
                signal=signal,
                skill_prompt=draft.prompt_fragment,
                skill_tools=draft.tool_names,
            )

        async def revise(draft: SkillDraft, outcome: ReplayOutcome) -> SkillDraft | None:
            revised = await self.distiller.distill(
                tenant_id=candidate.tenant_id,
                successes=list(evidence.successes),
                failures=[*evidence.failures, _failure_feedback(outcome)],
                allowed_tools=evidence.allowed_tools,
            )
            if revised is None:
                return None
            await self._persist(revised, candidate, state, round_no=state.version)
            return revised

        result = await evolve(
            distill=distill,
            replay=replay,
            attribute=attribute,
            revise=revise,
            config=self.config,
        )

        # SE-7c governance: a grounded DRAFT goes through the auto-promote gate
        # (may flip to ACTIVE); without a gate it stays DRAFT for human review.
        if (
            self.promotion_gate is not None
            and result.outcome == "grounded"
            and result.draft is not None
            and state.skill_id is not None
        ):
            await self.promotion_gate.maybe_promote(
                candidate=candidate,
                skill_id=state.skill_id,
                auto_promote_eligible=result.auto_promote_eligible,
                high_risk=result.draft.high_risk,
                now=self.clock(),
            )
        return result

    async def _persist(
        self,
        draft: SkillDraft,
        candidate: CurationCandidateRecord,
        state: _DraftState,
        *,
        round_no: int,
    ) -> None:
        """Create the skill on first draft, then append a DRAFT version each round."""
        if state.skill_id is None:
            state.skill_id = await self._ensure_skill(draft, candidate)
        version = await self.skill_store.add_version(
            version_id=uuid4(),
            skill_id=state.skill_id,
            tenant_id=candidate.tenant_id,
            prompt_fragment=draft.prompt_fragment,
            tool_names=draft.tool_names,
            description=draft.description,
            category=draft.category,
            authored_by="agent",
            high_risk=draft.high_risk,
            evolution_origin="distilled",
            distilled_from_trajectory_key=candidate.trajectory_key,
            distilled_from_candidate_id=candidate.id,
            evolution_round=round_no,
        )
        state.version = version.version

    async def _ensure_skill(self, draft: SkillDraft, candidate: CurationCandidateRecord) -> UUID:
        skill_id = uuid4()
        try:
            skill = await self.skill_store.create_skill(
                skill_id=skill_id,
                tenant_id=candidate.tenant_id,
                name=draft.name,
                description=draft.description,
                category=draft.category,
                visibility="agent_private",
                created_by_user_id=candidate.user_id,
                created_by_agent_name=candidate.agent_name,
            )
            return skill.id
        except DuplicateSkillError:
            existing = await self.skill_store.get_skill_by_name(
                tenant_id=candidate.tenant_id, name=draft.name
            )
            if existing is None:  # pragma: no cover — duplicate then vanished
                raise
            return existing.id
