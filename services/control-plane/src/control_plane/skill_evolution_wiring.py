"""Real evolution wiring (Stream SE, SE-6d) — providers + replay invoker.

Assembles the production :class:`SkillEvolutionWorker`: real distiller /
attributor (aux LLM), trajectory-backed evidence, dual-source held-out set
(eval-dataset golden preferred, same-agent success trajectories as fallback),
and a graph-backed replay invoker.

This module imports the orchestrator agent graph (``agent_factory`` via
``graph_runner``), so it is imported **lazily by the app lifespan only** — never
from ``control_plane`` package import — to avoid an import cycle. Its graph /
LLM paths cannot run in CI (no model keys); they are validated by the SE-9
benchmark + manual runs. The CI-testable decision logic lives in
``skill_evolution_assembly``; the persistence path is covered by an integration
test against real Postgres with a stub invoker.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from langchain_core.runnables import RunnableConfig

from control_plane.memory_consolidator import ConsolidatorAuxModel
from control_plane.skill_attribution import FailureSignal, SkillAttributor
from control_plane.skill_distiller import SkillDistiller, SkillDraft, render_trajectory, tools_used
from control_plane.skill_evolution import EvolutionConfig, ReplayOutcome
from control_plane.skill_evolution_assembly import (
    extract_task_prompt,
    first_user_message,
    is_screen_sampled,
    select_signal_tier,
)
from control_plane.skill_evolution_limits import CircuitBreaker, RateLimiter
from control_plane.skill_evolution_processor import EvolutionProcessor, SkillEvidence
from control_plane.skill_evolution_worker import (
    ScreenDecision,
    SkillEvolutionWorker,
    TenantGate,
)
from control_plane.skill_promotion_gate import PromotionGate
from helix_agent.persistence import CurationCandidateStore, SkillStore
from helix_agent.persistence.agent_spec import AgentSpecStore
from helix_agent.persistence.curation import EvalDatasetStore
from helix_agent.persistence.feedback_store import FeedbackStore
from helix_agent.protocol import AgentSpecStatus, CurationCandidateRecord, EvalDatasetRecord
from helix_agent.runtime.audit.logger import AuditLogger
from orchestrator.evolution.graph_runner import GraphReplayTaskRunner

# Orchestrator imports — heavy (pull agent_factory); safe here because this
# module is only imported lazily from the lifespan.
from orchestrator.evolution.grounding import SignalTier
from orchestrator.evolution.replay import ReplayRequest, ReplayRunner, ReplayTask
from orchestrator.trajectory import TrajectoryReader

__all__ = ["build_evolution_worker"]

# How many trajectories / golden cases to pull into one replay set.
_MAX_SUCCESS_EVIDENCE = 4
_MAX_FAILURE_EVIDENCE = 4
_MAX_HELD_OUT = 12

_EXPECTED_KEYS = ("answer", "expected", "output", "result", "text", "content")


class _AuxText:
    """Adapts a :class:`ConsolidatorAuxModel` to the ``(prompt) -> str`` seam."""

    def __init__(self, aux: ConsolidatorAuxModel, *, default_model: str | None = None) -> None:
        self._aux = aux
        self._default_model = default_model

    async def __call__(self, *, prompt: str, tenant_id: UUID, model: str | None = None) -> str:
        reply = await self._aux(
            prompt=prompt, model=model or self._default_model, tenant_id=tenant_id
        )
        return reply.text


class _AuxJudge:
    """A pointwise replay judge backed by the aux LLM (control-plane local)."""

    def __init__(self, aux: ConsolidatorAuxModel, *, model: str | None = None) -> None:
        self._aux = aux
        self._model = model

    async def score(self, *, case_id: str, prompt: str) -> int:
        reply = await self._aux(prompt=prompt, model=self._model, tenant_id=_NULL_TENANT)
        return _parse_score(reply.text)


_NULL_TENANT = UUID(int=0)


def _parse_score(text: str) -> int:
    for token in text.split():
        cleaned = token.strip().strip(".,:")
        if cleaned.isdigit():
            value = int(cleaned)
            if 1 <= value <= 5:
                return value
    return 0  # unparseable → hard fail (clips below the pass threshold)


# --------------------------------------------------------------------------- #
# SE-A45 — sampled quality screen over implicit-success candidates
# --------------------------------------------------------------------------- #

#: Minimum 1-5 conversation-quality score for an implicit candidate to
#: survive the screen. Matches the replay judge's pass bar.
_SCREEN_PASS_SCORE = 4

_SCREEN_PROMPT = """\
You are auditing one AI-agent conversation. Rate its quality on a 1-5 scale:
did the agent truly complete the user's task, and were the answers on-topic
and correct? 5 = task fully completed with accurate, on-topic answers;
3 = partially completed or generic; 1 = off-topic, wrong, or unfinished.

Conversation transcript:
{transcript}

Reply with a single digit from 1 to 5 and nothing else."""


@dataclass(frozen=True)
class _ImplicitScreener:
    """Screens ``implicit_success`` candidates before distillation (SE-A45).

    Candidate purification, deliberately separate from the promotion gate's
    calibrated-judge tier: a deterministic per-tenant sample of the abundant
    implicit pool is scored for conversation quality by the cheap aux model;
    un-sampled and low-scoring candidates never reach the distiller. Aux
    transport faults propagate — the worker's transient retry budget
    (SE-A40) handles them.
    """

    aux: _AuxText
    reader: TrajectoryReader
    sample_pct: Callable[[UUID], Awaitable[int]]

    async def __call__(self, candidate: CurationCandidateRecord) -> ScreenDecision:
        pct = await self.sample_pct(candidate.tenant_id)
        if not is_screen_sampled(candidate.trajectory_key, pct):
            return ScreenDecision(proceed=False, reason="not_sampled")
        source = await self.reader.read(candidate.trajectory_key)
        if source is None:
            # Nothing to distil from either — drop without spending a judge call.
            return ScreenDecision(proceed=False, reason="trajectory_missing")
        prompt = _SCREEN_PROMPT.format(transcript=render_trajectory(source.messages))
        # Real tenant_id — the screen bills to the candidate's own tenant
        # (SE-A43 alignment), unlike the replay judge's null placeholder.
        text = await self.aux(prompt=prompt, tenant_id=candidate.tenant_id)
        score = _parse_score(text)
        if score >= _SCREEN_PASS_SCORE:
            return ScreenDecision(proceed=True, reason="judge_passed", score=score)
        return ScreenDecision(proceed=False, reason="judge_filtered", score=score)


# --------------------------------------------------------------------------- #
# Evidence + held-out providers
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _TrajectoryEvidenceProvider:
    reader: TrajectoryReader
    # SE-16 (SE-A39) — optional stores that let 👎-rated trajectories (with
    # the user's own comment) join the contrastive failure side. ``None``
    # keeps the legacy outcome-only sourcing.
    candidate_store: CurationCandidateStore | None = None
    feedback_store: FeedbackStore | None = None

    async def __call__(self, candidate: CurationCandidateRecord) -> SkillEvidence:
        successes: list[str] = []
        allowed: set[str] = set()
        source = await self.reader.read(candidate.trajectory_key)
        if source is not None:
            successes.append(render_trajectory(source.messages))
            allowed |= set(tools_used(source.messages))

        # SE-A39 — 👎 trajectories first (a 👎 on a *successful* run is a
        # "false success": the highest-information contrast, invisible to
        # outcome-based sourcing), then top up with failed outcomes.
        failures = await self._downvoted_failures(candidate, limit=_MAX_FAILURE_EVIDENCE)
        if len(failures) < _MAX_FAILURE_EVIDENCE:
            failures.extend(
                await self._render_some(
                    candidate, outcome="failed", limit=_MAX_FAILURE_EVIDENCE - len(failures)
                )
            )
        if len(successes) < _MAX_SUCCESS_EVIDENCE:
            successes.extend(
                await self._render_some(
                    candidate,
                    outcome="success",
                    limit=_MAX_SUCCESS_EVIDENCE - len(successes),
                    skip_key=candidate.trajectory_key,
                )
            )
        return SkillEvidence(
            successes=tuple(successes),
            failures=tuple(failures),
            allowed_tools=frozenset(allowed) or None,
        )

    async def _downvoted_failures(
        self, candidate: CurationCandidateRecord, *, limit: int
    ) -> list[str]:
        """Same-agent 👎 trajectories, prefixed with the user's comment —
        their own words are the best failure label the distiller can get."""
        if self.candidate_store is None:
            return []
        negatives = await self.candidate_store.list_for_review(
            tenant_id=candidate.tenant_id,
            agent_name=candidate.agent_name,
            signal="negative_feedback",
        )
        rendered: list[str] = []
        for neg in negatives:
            if len(rendered) >= limit:
                break
            traj = await self.reader.read(neg.trajectory_key)
            if traj is None:
                continue
            text = render_trajectory(traj.messages)
            if self.feedback_store is not None:
                feedback = await self.feedback_store.list_for_thread(thread_id=neg.thread_id)
                comments = [f.comment for f in feedback if f.rating == "down" and f.comment]
                if comments:
                    text = "User feedback (thumbs-down): " + " | ".join(comments) + "\n" + text
            rendered.append(text)
        return rendered

    async def _render_some(
        self,
        candidate: CurationCandidateRecord,
        *,
        outcome: str,
        limit: int,
        skip_key: str | None = None,
    ) -> list[str]:
        keys = await self.reader.list_keys(tenant_id=candidate.tenant_id, outcome=outcome)  # type: ignore[arg-type]
        rendered: list[str] = []
        for key in keys:
            if key == skip_key:
                continue
            if len(rendered) >= limit:
                break
            traj = await self.reader.read(key)
            if traj is not None:
                rendered.append(render_trajectory(traj.messages))
        return rendered


@dataclass(frozen=True)
class HeldOut:
    """The replay set + the grounding signal tier it supports."""

    tasks: tuple[ReplayTask, ...]
    signal_tier: SignalTier
    replay_source: str


def _assertion_for(expected: dict[str, object] | None) -> Callable[[str], bool] | None:
    if not expected:
        return None
    for key in _EXPECTED_KEYS:
        value = expected.get(key)
        if isinstance(value, str) and value.strip():
            needle = value.strip().lower()

            def check(answer: str, needle: str = needle) -> bool:
                return needle in answer.lower()

            return check
    return None


def _golden_tasks(records: Sequence[EvalDatasetRecord]) -> list[ReplayTask]:
    tasks: list[ReplayTask] = []
    for record in records:
        if record.source not in ("golden", "regression"):
            continue
        prompt = extract_task_prompt(record.input)
        if prompt is None:
            continue
        assertion = _assertion_for(record.expected)
        tasks.append(
            ReplayTask(
                case_id=str(record.id),
                prompt=prompt,
                assertions=(assertion,) if assertion else (),
                is_anchor=assertion is not None,
            )
        )
    return tasks


@dataclass(frozen=True)
class _DualSourceHeldOutProvider:
    eval_store: EvalDatasetStore
    reader: TrajectoryReader

    async def __call__(self, candidate: CurationCandidateRecord) -> HeldOut:
        golden = await self.eval_store.list_by_agent(
            tenant_id=candidate.tenant_id, agent_name=candidate.agent_name
        )
        tasks = _golden_tasks(golden)
        source = "eval_dataset" if tasks else "trajectory"

        if len(tasks) < _MAX_HELD_OUT:
            tasks.extend(await self._trajectory_tasks(candidate, limit=_MAX_HELD_OUT - len(tasks)))

        has_hard = any(t.assertions for t in tasks)
        tier = SignalTier(select_signal_tier(has_hard_verifier=has_hard, judge_calibrated=True))
        return HeldOut(tasks=tuple(tasks[:_MAX_HELD_OUT]), signal_tier=tier, replay_source=source)

    async def _trajectory_tasks(
        self, candidate: CurationCandidateRecord, *, limit: int
    ) -> list[ReplayTask]:
        keys = await self.reader.list_keys(tenant_id=candidate.tenant_id, outcome="success")
        tasks: list[ReplayTask] = []
        for key in keys:
            if key == candidate.trajectory_key or len(tasks) >= limit:
                continue
            traj = await self.reader.read(key)
            if traj is None:
                continue
            prompt = first_user_message(traj.messages)
            if prompt:
                tasks.append(ReplayTask(case_id=key, prompt=prompt, trajectory_key=key))
        return tasks


# --------------------------------------------------------------------------- #
# Graph-backed replay invoker
# --------------------------------------------------------------------------- #


def _replay_config(case_id: str, with_skill: bool) -> RunnableConfig:
    return {"configurable": {"thread_id": f"se-replay-{uuid4()}"}}


@dataclass(frozen=True)
class _GraphReplayInvoker:
    agent_spec_store: AgentSpecStore
    agent_builder: Callable[..., Awaitable[Any]]
    judge: Any
    skill_store: SkillStore

    async def __call__(
        self,
        *,
        candidate: CurationCandidateRecord,
        draft: SkillDraft,
        skill_id: UUID,
        skill_version: int,
        held_out: HeldOut,
    ) -> ReplayOutcome:
        base = await self._base_spec(candidate)
        if base is None or not held_out.tasks:
            return ReplayOutcome(verdict="inconclusive")

        user = str(candidate.user_id) if candidate.user_id else None

        async def build(spec: Any) -> Any:
            return await self.agent_builder(spec, tenant_id=candidate.tenant_id, user_id=user)

        task_runner = GraphReplayTaskRunner.from_candidate(
            base,
            skill_name=draft.name,
            skill_version=skill_version,
            agent_builder=build,
            config_factory=_replay_config,
        )
        runner = ReplayRunner(task_runner=task_runner, judge=self.judge, store=self.skill_store)
        request = ReplayRequest(
            skill_id=skill_id,
            skill_version=skill_version,
            tenant_id=candidate.tenant_id,
            signal_tier=held_out.signal_tier,
            replay_source=held_out.replay_source,  # type: ignore[arg-type]
            high_risk=draft.high_risk,
            distilled_from_trajectory_key=candidate.trajectory_key,
        )
        result, decision = await runner.run(
            request, held_out.tasks, result_id=uuid4(), created_at=datetime.now(UTC)
        )
        signal = FailureSignal(error_text=decision.reason) if decision.verdict == "fail" else None
        return ReplayOutcome(
            verdict=decision.verdict,
            failure_signal=signal,
            eval_result_id=result.id,
            auto_promote_eligible=decision.auto_promote_eligible,
        )

    async def _base_spec(self, candidate: CurationCandidateRecord) -> Any:
        rows = await self.agent_spec_store.list_by_tenant(
            tenant_id=candidate.tenant_id,
            name=candidate.agent_name,
            status=AgentSpecStatus.ACTIVE,
            limit=1,
        )
        return rows[0].spec if rows else None


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #


def build_evolution_worker(
    *,
    aux_model: ConsolidatorAuxModel,
    aux_default_model: str | None,
    candidate_store: CurationCandidateStore,
    skill_store: SkillStore,
    eval_store: EvalDatasetStore,
    agent_spec_store: AgentSpecStore,
    trajectory_reader: TrajectoryReader,
    agent_builder: Callable[..., Awaitable[Any]],
    interval_s: int,
    audit_logger: AuditLogger | None = None,
    batch_size: int = 50,
    max_rounds: int = 3,
    max_promotes_per_hour: int = 5,
    breaker: CircuitBreaker | None = None,
    tenant_gate: TenantGate | None = None,
    feedback_store: FeedbackStore | None = None,
    judge_sample_pct: Callable[[UUID], Awaitable[int]] | None = None,
) -> SkillEvolutionWorker:
    """Assemble the production skill-evolution worker (lifespan wiring).

    Wires the SE-7c governance gate (auto-promote policy + rate limiter +
    circuit breaker) so a grounded, eligible, non-high-risk DRAFT auto-promotes
    to ACTIVE within the guardrails; everything else stays DRAFT for review.

    ``breaker`` may be injected so the SE-7d rollback monitor shares the SAME
    circuit breaker instance: a promote that later rolls back feeds ``ok=False``
    on the same ``{tenant}:{agent}`` scope, tripping the auto-promote channel
    (SE-A12). When ``None`` the worker owns a private breaker.
    """
    aux_text = _AuxText(aux_model, default_model=aux_default_model)
    gate = PromotionGate(
        skill_store=skill_store,
        rate_limiter=RateLimiter(max_per_window=max_promotes_per_hour, window=timedelta(hours=1)),
        breaker=breaker
        or CircuitBreaker(failure_threshold=0.5, min_samples=5, window=timedelta(hours=24)),
        audit_logger=audit_logger,
    )
    processor = EvolutionProcessor(
        distiller=SkillDistiller(model=aux_text, model_name=aux_default_model),
        attributor=SkillAttributor(model=aux_text, model_name=aux_default_model),
        skill_store=skill_store,
        evidence_provider=_TrajectoryEvidenceProvider(
            trajectory_reader,
            candidate_store=candidate_store,
            feedback_store=feedback_store,
        ),
        held_out_provider=_DualSourceHeldOutProvider(eval_store, trajectory_reader),
        replay_invoker=_GraphReplayInvoker(
            agent_spec_store=agent_spec_store,
            agent_builder=agent_builder,
            judge=_AuxJudge(aux_model, model=aux_default_model),
            skill_store=skill_store,
        ),
        config=EvolutionConfig(max_rounds=max_rounds),
        promotion_gate=gate,
    )
    # SE-A45 — the sampled quality screen only assembles when the caller can
    # resolve a per-tenant sample rate (``tenant_config``); otherwise implicit
    # candidates distil unscreened (legacy single-tenant assemblies).
    screener = (
        _ImplicitScreener(aux=aux_text, reader=trajectory_reader, sample_pct=judge_sample_pct)
        if judge_sample_pct is not None
        else None
    )
    return SkillEvolutionWorker(
        candidate_store=candidate_store,
        processor=processor,
        interval_s=interval_s,
        batch_size=batch_size,
        tenant_gate=tenant_gate,
        screener=screener,
    )
