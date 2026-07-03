"""Tests for the SE-6c evolution processor glue.

Drives the full distil → persist DRAFT → replay → attribute → revise → evolve
chain with real distiller/attributor/evolve + an in-memory SkillStore; only the
LLM and replay boundaries are faked (those are integration-validated).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from control_plane.skill_attribution import SkillAttributor
from control_plane.skill_distiller import SkillDistiller
from control_plane.skill_evolution import ReplayOutcome
from control_plane.skill_evolution_processor import (
    DedupMatch,
    EvolutionProcessor,
    SkillEvidence,
)
from helix_agent.persistence.skill.memory import InMemorySkillStore
from helix_agent.protocol import CurationCandidateRecord
from helix_agent.protocol.skill import SkillStatus

_TENANT = UUID("33333333-3333-3333-3333-333333333333")


class FakeModel:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    async def __call__(self, *, prompt: str, tenant_id: UUID, model: str | None = None) -> str:
        return self.reply


def _draft_reply(name: str = "summarise-data") -> str:
    return json.dumps(
        {
            "name": name,
            "prompt_fragment": "Read headers first, then aggregate per column.",
            "tool_names": [],
            "description": "Summarise tabular data",
            "category": "data",
        }
    )


def _candidate() -> CurationCandidateRecord:
    return CurationCandidateRecord(
        id=uuid4(),
        tenant_id=_TENANT,
        agent_name="assistant",
        user_id=uuid4(),
        thread_id=uuid4(),
        trajectory_key=f"k/{uuid4()}",
        outcome="success",
        signal="positive_feedback",
        detected_at=datetime.now(UTC),
    )


async def _evidence(_c: CurationCandidateRecord) -> SkillEvidence:
    return SkillEvidence(successes=("user: summarise\nassistant: done",), failures=())


async def _held_out(_c: CurationCandidateRecord) -> Any:
    return {"tasks": "opaque"}


def _processor(*, draft_reply: str, invoker: Any) -> EvolutionProcessor:
    return EvolutionProcessor(
        distiller=SkillDistiller(model=FakeModel(draft_reply)),
        attributor=SkillAttributor(model=FakeModel("content_error")),
        skill_store=InMemorySkillStore(),
        evidence_provider=_evidence,
        held_out_provider=_held_out,
        replay_invoker=invoker,
    )


async def test_promotion_gate_activates_eligible_grounded_draft() -> None:
    from datetime import timedelta

    from control_plane.skill_evolution_limits import CircuitBreaker, RateLimiter
    from control_plane.skill_promotion_gate import PromotionGate
    from helix_agent.protocol.skill import SkillStatus

    store = InMemorySkillStore()

    async def invoker(**_kw: Any) -> ReplayOutcome:
        return ReplayOutcome(verdict="pass", auto_promote_eligible=True)

    gate = PromotionGate(
        skill_store=store,
        rate_limiter=RateLimiter(max_per_window=5, window=timedelta(hours=1)),
        breaker=CircuitBreaker(failure_threshold=0.5, min_samples=5, window=timedelta(hours=1)),
    )
    proc = EvolutionProcessor(
        distiller=SkillDistiller(model=FakeModel(_draft_reply())),
        attributor=SkillAttributor(model=FakeModel("content_error")),
        skill_store=store,
        evidence_provider=_evidence,
        held_out_provider=_held_out,
        replay_invoker=invoker,
        promotion_gate=gate,
        clock=lambda: datetime(2026, 6, 8, tzinfo=UTC),
    )
    result = await proc(_candidate())
    assert result.outcome == "grounded"
    skill = await store.get_skill_by_name(tenant_id=_TENANT, name="summarise-data")
    assert skill is not None
    assert skill.status is SkillStatus.ACTIVE  # auto-promoted by the gate


async def test_grounded_persists_distilled_draft() -> None:
    async def invoker(**_kw: Any) -> ReplayOutcome:
        return ReplayOutcome(verdict="pass")

    proc = _processor(draft_reply=_draft_reply(), invoker=invoker)
    result = await proc(_candidate())

    assert result.outcome == "grounded"
    # the DRAFT skill + version were persisted with distilled provenance
    skill = await proc.skill_store.get_skill_by_name(tenant_id=_TENANT, name="summarise-data")
    assert skill is not None
    assert skill.visibility == "agent_private"
    version = await proc.skill_store.get_version_by_number(
        skill_id=skill.id, version=1, tenant_id=_TENANT
    )
    assert version is not None
    assert version.evolution_origin == "distilled"


async def test_no_draft_when_distillation_empty() -> None:
    async def invoker(**_kw: Any) -> ReplayOutcome:
        raise AssertionError("replay should not run without a draft")

    proc = _processor(draft_reply="not json", invoker=invoker)
    result = await proc(_candidate())
    assert result.outcome == "no_draft"


async def test_content_fail_then_revise_adds_version() -> None:
    verdicts = iter(
        [
            ReplayOutcome(verdict="fail", failure_signal=_signal()),
            ReplayOutcome(verdict="pass"),
        ]
    )

    async def invoker(**_kw: Any) -> ReplayOutcome:
        return next(verdicts)

    proc = _processor(draft_reply=_draft_reply(), invoker=invoker)
    result = await proc(_candidate())

    assert result.outcome == "grounded"
    assert result.rounds == 2
    skill = await proc.skill_store.get_skill_by_name(tenant_id=_TENANT, name="summarise-data")
    assert skill is not None
    # two versions: initial draft + revised draft
    v2 = await proc.skill_store.get_version_by_number(
        skill_id=skill.id, version=2, tenant_id=_TENANT
    )
    assert v2 is not None
    assert v2.evolution_round == 1


async def test_execution_fail_rejected_keeps_single_version() -> None:
    async def invoker(**_kw: Any) -> ReplayOutcome:
        return ReplayOutcome(verdict="fail", failure_signal=_signal(timed_out=True))

    proc = EvolutionProcessor(
        distiller=SkillDistiller(model=FakeModel(_draft_reply())),
        attributor=SkillAttributor(model=FakeModel("ignored")),  # rule will fire on timeout
        skill_store=InMemorySkillStore(),
        evidence_provider=_evidence,
        held_out_provider=_held_out,
        replay_invoker=invoker,
    )
    result = await proc(_candidate())
    assert result.outcome == "rejected"  # execution error -> not learned


def _signal(*, timed_out: bool = False):
    from control_plane.skill_attribution import FailureSignal

    return FailureSignal(error_text="boom", timed_out=timed_out)


# ---------------------------------------------------------------------------
# SE-16 (SE-A47) — ingest-time dedup: similar draft -> revision, not new entry
# ---------------------------------------------------------------------------


async def _seed_existing(store: InMemorySkillStore, *, name: str, status: SkillStatus) -> UUID:
    skill = await store.create_skill(
        skill_id=uuid4(),
        tenant_id=_TENANT,
        name=name,
        description=f"{name} desc",
        visibility="agent_private",
        created_by_agent_name="assistant",
    )
    await store.add_version(
        version_id=uuid4(),
        skill_id=skill.id,
        tenant_id=_TENANT,
        prompt_fragment="existing content",
        description=f"{name} desc",
        authored_by="agent",
        evolution_origin="distilled",
    )
    await store.set_status(skill_id=skill.id, tenant_id=_TENANT, status=status)
    return skill.id


async def test_dedup_hit_becomes_revision_of_existing_active_skill() -> None:
    from helix_agent.protocol.skill import SkillStatus

    store = InMemorySkillStore()
    existing_id = await _seed_existing(store, name="tabular-howto", status=SkillStatus.ACTIVE)

    async def deduper(draft: Any, candidate: CurationCandidateRecord) -> DedupMatch:
        return DedupMatch(skill_id=existing_id, skill_name="tabular-howto", similarity=0.95)

    async def invoker(**_kw: Any) -> ReplayOutcome:
        return ReplayOutcome(verdict="inconclusive")

    proc = EvolutionProcessor(
        distiller=SkillDistiller(model=FakeModel(_draft_reply("summarise-data"))),
        attributor=SkillAttributor(model=FakeModel("content_error")),
        skill_store=store,
        evidence_provider=_evidence,
        held_out_provider=_held_out,
        replay_invoker=invoker,
        deduper=deduper,
    )
    await proc(_candidate())

    # No new skill entry — the draft landed as version 2 of the existing one.
    assert await store.get_skill_by_name(tenant_id=_TENANT, name="summarise-data") is None
    existing = await store.get_skill(skill_id=existing_id, tenant_id=_TENANT)
    assert existing is not None
    assert existing.latest_version == 2
    # ACTIVE target re-enters review: an unverified revision must not be
    # served live (add_version bumps what bare-name resolution returns).
    assert existing.status is SkillStatus.DRAFT


async def test_dedup_miss_creates_new_skill_as_before() -> None:
    store = InMemorySkillStore()

    async def deduper(draft: Any, candidate: CurationCandidateRecord) -> None:
        return None

    async def invoker(**_kw: Any) -> ReplayOutcome:
        return ReplayOutcome(verdict="inconclusive")

    proc = EvolutionProcessor(
        distiller=SkillDistiller(model=FakeModel(_draft_reply())),
        attributor=SkillAttributor(model=FakeModel("content_error")),
        skill_store=store,
        evidence_provider=_evidence,
        held_out_provider=_held_out,
        replay_invoker=invoker,
        deduper=deduper,
    )
    await proc(_candidate())
    assert await store.get_skill_by_name(tenant_id=_TENANT, name="summarise-data") is not None


async def test_dedup_hit_on_draft_target_keeps_draft_status() -> None:
    from helix_agent.protocol.skill import SkillStatus

    store = InMemorySkillStore()
    existing_id = await _seed_existing(store, name="tabular-howto", status=SkillStatus.DRAFT)

    async def deduper(draft: Any, candidate: CurationCandidateRecord) -> DedupMatch:
        return DedupMatch(skill_id=existing_id, skill_name="tabular-howto", similarity=0.92)

    async def invoker(**_kw: Any) -> ReplayOutcome:
        return ReplayOutcome(verdict="inconclusive")

    proc = EvolutionProcessor(
        distiller=SkillDistiller(model=FakeModel(_draft_reply())),
        attributor=SkillAttributor(model=FakeModel("content_error")),
        skill_store=store,
        evidence_provider=_evidence,
        held_out_provider=_held_out,
        replay_invoker=invoker,
        deduper=deduper,
    )
    await proc(_candidate())
    existing = await store.get_skill(skill_id=existing_id, tenant_id=_TENANT)
    assert existing is not None
    assert existing.status is SkillStatus.DRAFT
    assert existing.latest_version == 2
