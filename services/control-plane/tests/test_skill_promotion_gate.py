"""Tests for the SE-7c promotion gate (executes auto-promote + guardrails)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from control_plane.skill_evolution_limits import CircuitBreaker, RateLimiter
from control_plane.skill_promotion import PromoteAction
from control_plane.skill_promotion_gate import PromotionGate
from expert_work.persistence.skill.memory import InMemorySkillStore
from expert_work.protocol import CurationCandidateRecord
from expert_work.protocol.skill import SkillStatus

_TENANT = UUID("33333333-3333-3333-3333-333333333333")
_NOW = datetime(2026, 6, 8, tzinfo=UTC)


def _candidate() -> CurationCandidateRecord:
    return CurationCandidateRecord(
        id=uuid4(),
        tenant_id=_TENANT,
        agent_name="assistant",
        thread_id=uuid4(),
        trajectory_key=f"k/{uuid4()}",
        outcome="success",
        signal="positive_feedback",
        detected_at=_NOW,
    )


async def _draft_skill(store: InMemorySkillStore, name: str = "s") -> UUID:
    skill = await store.create_skill(
        skill_id=uuid4(), tenant_id=_TENANT, name=name, visibility="agent_private"
    )
    await store.add_version(
        version_id=uuid4(), skill_id=skill.id, tenant_id=_TENANT, prompt_fragment="do x"
    )
    return skill.id


def _gate(store: InMemorySkillStore) -> PromotionGate:
    return PromotionGate(
        skill_store=store,
        rate_limiter=RateLimiter(max_per_window=5, window=timedelta(hours=1)),
        breaker=CircuitBreaker(failure_threshold=0.5, min_samples=5, window=timedelta(hours=1)),
    )


async def _status(store: InMemorySkillStore, skill_id: UUID) -> SkillStatus:
    skill = await store.get_skill(skill_id=skill_id, tenant_id=_TENANT)
    assert skill is not None
    return skill.status


async def test_eligible_auto_promotes_to_active() -> None:
    store = InMemorySkillStore()
    skill_id = await _draft_skill(store)
    gate = _gate(store)

    decision = await gate.maybe_promote(
        candidate=_candidate(),
        skill_id=skill_id,
        auto_promote_eligible=True,
        high_risk=False,
        now=_NOW,
    )
    assert decision.action is PromoteAction.AUTO_PROMOTE
    assert await _status(store, skill_id) is SkillStatus.ACTIVE


async def test_high_risk_stays_draft() -> None:
    store = InMemorySkillStore()
    skill_id = await _draft_skill(store)
    gate = _gate(store)

    decision = await gate.maybe_promote(
        candidate=_candidate(),
        skill_id=skill_id,
        auto_promote_eligible=True,
        high_risk=True,
        now=_NOW,
    )
    assert decision.action is PromoteAction.HUMAN_REVIEW
    assert await _status(store, skill_id) is SkillStatus.DRAFT


async def test_open_breaker_stays_draft() -> None:
    store = InMemorySkillStore()
    skill_id = await _draft_skill(store)
    breaker = CircuitBreaker(failure_threshold=0.5, min_samples=2, window=timedelta(hours=1))
    breaker.record("33333333-3333-3333-3333-333333333333:assistant", ok=False, now=_NOW)
    breaker.record("33333333-3333-3333-3333-333333333333:assistant", ok=False, now=_NOW)
    gate = PromotionGate(
        skill_store=store,
        rate_limiter=RateLimiter(max_per_window=5, window=timedelta(hours=1)),
        breaker=breaker,
    )
    decision = await gate.maybe_promote(
        candidate=_candidate(),
        skill_id=skill_id,
        auto_promote_eligible=True,
        high_risk=False,
        now=_NOW,
    )
    assert decision.action is PromoteAction.HUMAN_REVIEW
    assert await _status(store, skill_id) is SkillStatus.DRAFT


async def test_rate_limit_blocks_after_cap() -> None:
    store = InMemorySkillStore()
    gate = PromotionGate(
        skill_store=store,
        rate_limiter=RateLimiter(max_per_window=1, window=timedelta(hours=1)),
        breaker=CircuitBreaker(failure_threshold=0.5, min_samples=5, window=timedelta(hours=1)),
    )
    cand = _candidate()
    first = await _draft_skill(store, name="s1")
    second = await _draft_skill(store, name="s2")

    d1 = await gate.maybe_promote(
        candidate=cand, skill_id=first, auto_promote_eligible=True, high_risk=False, now=_NOW
    )
    d2 = await gate.maybe_promote(
        candidate=cand, skill_id=second, auto_promote_eligible=True, high_risk=False, now=_NOW
    )
    assert d1.action is PromoteAction.AUTO_PROMOTE
    assert d2.action is PromoteAction.HUMAN_REVIEW  # rate cap hit
    assert await _status(store, second) is SkillStatus.DRAFT


async def test_audit_emitted_on_promote() -> None:
    store = InMemorySkillStore()
    skill_id = await _draft_skill(store)

    written: list[object] = []

    class FakeAudit:
        async def write(self, entry: object) -> None:
            written.append(entry)

    gate = PromotionGate(
        skill_store=store,
        rate_limiter=RateLimiter(max_per_window=5, window=timedelta(hours=1)),
        breaker=CircuitBreaker(failure_threshold=0.5, min_samples=5, window=timedelta(hours=1)),
        audit_logger=FakeAudit(),  # type: ignore[arg-type]
    )
    await gate.maybe_promote(
        candidate=_candidate(),
        skill_id=skill_id,
        auto_promote_eligible=True,
        high_risk=False,
        now=_NOW,
    )
    assert len(written) == 1


async def test_kill_switch_engaged_stays_draft() -> None:
    # SE-8 (SE-A13c) — a persistent manual stop degrades even a fully eligible
    # candidate to human review.
    store = InMemorySkillStore()
    skill_id = await _draft_skill(store)
    await store.set_kill_switch(switch_id=uuid4(), scope="tenant", tenant_id=_TENANT, engaged=True)
    gate = _gate(store)
    decision = await gate.maybe_promote(
        candidate=_candidate(),
        skill_id=skill_id,
        auto_promote_eligible=True,
        high_risk=False,
        now=_NOW,
    )
    assert decision.action is PromoteAction.HUMAN_REVIEW
    assert "kill-switch" in decision.reason
    assert await _status(store, skill_id) is SkillStatus.DRAFT


async def test_global_kill_switch_halts_any_tenant() -> None:
    store = InMemorySkillStore()
    skill_id = await _draft_skill(store)
    await store.set_kill_switch(switch_id=uuid4(), scope="global", tenant_id=None, engaged=True)
    gate = _gate(store)
    decision = await gate.maybe_promote(
        candidate=_candidate(),
        skill_id=skill_id,
        auto_promote_eligible=True,
        high_risk=False,
        now=_NOW,
    )
    assert decision.action is PromoteAction.HUMAN_REVIEW
    assert await _status(store, skill_id) is SkillStatus.DRAFT


async def test_no_audit_when_not_promoted() -> None:
    store = InMemorySkillStore()
    skill_id = await _draft_skill(store)
    written: list[object] = []

    class FakeAudit:
        async def write(self, entry: object) -> None:
            written.append(entry)

    gate = PromotionGate(
        skill_store=store,
        rate_limiter=RateLimiter(max_per_window=5, window=timedelta(hours=1)),
        breaker=CircuitBreaker(failure_threshold=0.5, min_samples=5, window=timedelta(hours=1)),
        audit_logger=FakeAudit(),  # type: ignore[arg-type]
    )
    await gate.maybe_promote(
        candidate=_candidate(),
        skill_id=skill_id,
        auto_promote_eligible=False,  # not eligible → human review
        high_risk=False,
        now=_NOW,
    )
    assert written == []


async def test_weak_label_origin_never_auto_promotes() -> None:
    """SE-16 (SE-A44) — an implicit_success candidate stays DRAFT for human
    review even with full replay eligibility."""
    store = InMemorySkillStore()
    skill_id = await _draft_skill(store)
    gate = _gate(store)

    implicit = _candidate().model_copy(update={"signal": "implicit_success"})
    decision = await gate.maybe_promote(
        candidate=implicit,
        skill_id=skill_id,
        auto_promote_eligible=True,
        high_risk=False,
        now=_NOW,
    )
    assert decision.action is not PromoteAction.AUTO_PROMOTE
    assert await _status(store, skill_id) is SkillStatus.DRAFT


async def test_auto_promote_invalidates_agent_cache() -> None:
    """Live pilot finding #8 — flipping ACTIVE changes the auto-attach set
    without a spec-version bump; the gate must drop the tenant's BuiltAgent
    cache entries or the promotion is invisible until a restart."""
    store = InMemorySkillStore()
    skill_id = await _draft_skill(store)
    invalidated: list[UUID] = []
    gate = PromotionGate(
        skill_store=store,
        rate_limiter=RateLimiter(max_per_window=5, window=timedelta(hours=1)),
        breaker=CircuitBreaker(failure_threshold=0.5, min_samples=5, window=timedelta(hours=1)),
        cache_invalidator=invalidated.append,
    )

    await gate.maybe_promote(
        candidate=_candidate(),
        skill_id=skill_id,
        auto_promote_eligible=True,
        high_risk=False,
        now=_NOW,
    )
    assert invalidated == [_TENANT]


async def test_human_review_path_does_not_invalidate() -> None:
    store = InMemorySkillStore()
    skill_id = await _draft_skill(store)
    invalidated: list[UUID] = []
    gate = PromotionGate(
        skill_store=store,
        rate_limiter=RateLimiter(max_per_window=5, window=timedelta(hours=1)),
        breaker=CircuitBreaker(failure_threshold=0.5, min_samples=5, window=timedelta(hours=1)),
        cache_invalidator=invalidated.append,
    )

    await gate.maybe_promote(
        candidate=_candidate(),
        skill_id=skill_id,
        auto_promote_eligible=True,
        high_risk=True,  # high-risk always lands in human review
        now=_NOW,
    )
    assert invalidated == []
