"""Tests for the SE-7d-3a rollback gate (executes regression rollback).

The symmetric counterpart of SE-7c's ``PromotionGate``: given a live skill
version + its promote-time baseline, it aggregates the post-promotion outcome
window (SE-7d-1 ``skill_run_usage_window``), runs the SE-7d-2 judge
(``decide_rollback``), and on a ROLLBACK verdict archives the skill, feeds the
breaker (a rolled-back promote is a failed promote), and audits. The cross-
tenant enumeration loop + the run-end emission live in SE-7d-3b (real path).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from control_plane.skill_evolution_limits import CircuitBreaker
from control_plane.skill_rollback import RollbackAction
from control_plane.skill_rollback_gate import RollbackGate
from helix_agent.persistence.feedback_store import FeedbackRecord, InMemoryFeedbackStore
from helix_agent.persistence.skill.memory import InMemorySkillStore
from helix_agent.protocol import SkillRunUsage, TrajectoryOutcome
from helix_agent.protocol.skill import SkillStatus

_TENANT = UUID("44444444-4444-4444-4444-444444444444")
_NOW = datetime(2026, 6, 8, 12, 0, tzinfo=UTC)
_SINCE = _NOW - timedelta(days=7)


async def _active_skill(store: InMemorySkillStore, name: str = "s") -> tuple[UUID, int]:
    skill = await store.create_skill(
        skill_id=uuid4(), tenant_id=_TENANT, name=name, visibility="agent_private"
    )
    version = await store.add_version(
        version_id=uuid4(), skill_id=skill.id, tenant_id=_TENANT, prompt_fragment="do x"
    )
    await store.set_status(skill_id=skill.id, tenant_id=_TENANT, status=SkillStatus.ACTIVE)
    return skill.id, version.version


async def _seed_window(
    store: InMemorySkillStore,
    skill_id: UUID,
    version: int,
    *,
    success: int,
    failed: int,
) -> None:
    outcomes: list[TrajectoryOutcome] = ["success"] * success + ["failed"] * failed
    for oc in outcomes:
        await store.record_skill_run_usage(
            usage=SkillRunUsage(
                id=uuid4(),
                tenant_id=_TENANT,
                skill_id=skill_id,
                skill_version=version,
                thread_id=uuid4(),
                agent_name="assistant",
                outcome=oc,
                created_at=_NOW,
            )
        )


def _gate(
    store: InMemorySkillStore, *, breaker: CircuitBreaker | None = None, audit=None
) -> RollbackGate:
    return RollbackGate(
        skill_store=store,
        breaker=breaker
        or CircuitBreaker(failure_threshold=0.5, min_samples=5, window=timedelta(hours=24)),
        audit_logger=audit,
    )


async def _status(store: InMemorySkillStore, skill_id: UUID) -> SkillStatus:
    skill = await store.get_skill(skill_id=skill_id, tenant_id=_TENANT)
    assert skill is not None
    return skill.status


async def _rollback(gate: RollbackGate, skill_id: UUID, version: int, *, baseline: float):
    return await gate.maybe_rollback(
        skill_id=skill_id,
        skill_version=version,
        tenant_id=_TENANT,
        agent_name="assistant",
        promote_baseline=baseline,
        since=_SINCE,
        now=_NOW,
    )


async def test_regressed_version_is_archived() -> None:
    store = InMemorySkillStore()
    skill_id, version = await _active_skill(store)
    await _seed_window(store, skill_id, version, success=8, failed=12)  # 0.4 vs 0.9

    decision = await _rollback(_gate(store), skill_id, version, baseline=0.9)

    assert decision.action is RollbackAction.ROLLBACK
    assert await _status(store, skill_id) is SkillStatus.ARCHIVED


async def test_healthy_version_stays_active() -> None:
    store = InMemorySkillStore()
    skill_id, version = await _active_skill(store)
    await _seed_window(store, skill_id, version, success=18, failed=2)  # 0.9 vs 0.9

    decision = await _rollback(_gate(store), skill_id, version, baseline=0.9)

    assert decision.action is RollbackAction.KEEP
    assert await _status(store, skill_id) is SkillStatus.ACTIVE


async def test_insufficient_window_stays_active() -> None:
    store = InMemorySkillStore()
    skill_id, version = await _active_skill(store)
    await _seed_window(store, skill_id, version, success=1, failed=1)  # n=2 < n_min

    decision = await _rollback(_gate(store), skill_id, version, baseline=0.9)

    assert decision.action is RollbackAction.INSUFFICIENT
    assert await _status(store, skill_id) is SkillStatus.ACTIVE


async def test_rollback_feeds_the_breaker() -> None:
    # A rolled-back promote is a failed promote → trips the shared auto-promote
    # breaker (same {tenant}:{agent} scope), degrading the channel to all-human.
    store = InMemorySkillStore()
    breaker = CircuitBreaker(failure_threshold=0.5, min_samples=2, window=timedelta(hours=24))
    gate = _gate(store, breaker=breaker)
    key = f"{_TENANT}:assistant"

    for name in ("s1", "s2"):
        sid, ver = await _active_skill(store, name=name)
        await _seed_window(store, sid, ver, success=8, failed=12)
        await _rollback(gate, sid, ver, baseline=0.9)

    assert breaker.is_open(key, _NOW)


async def test_audit_emitted_on_rollback() -> None:
    store = InMemorySkillStore()
    skill_id, version = await _active_skill(store)
    await _seed_window(store, skill_id, version, success=8, failed=12)
    written: list[object] = []

    class FakeAudit:
        async def write(self, entry: object) -> None:
            written.append(entry)

    await _rollback(_gate(store, audit=FakeAudit()), skill_id, version, baseline=0.9)
    assert len(written) == 1


async def test_no_audit_when_kept() -> None:
    store = InMemorySkillStore()
    skill_id, version = await _active_skill(store)
    await _seed_window(store, skill_id, version, success=18, failed=2)
    written: list[object] = []

    class FakeAudit:
        async def write(self, entry: object) -> None:
            written.append(entry)

    await _rollback(_gate(store, audit=FakeAudit()), skill_id, version, baseline=0.9)
    assert written == []


# ---------------------------------------------------------------------------
# Stream HX-2 (Mini-ADR HX-B2) — user 👎 joins the window as demoted samples
# ---------------------------------------------------------------------------


async def _seed_thread(
    store: InMemorySkillStore,
    skill_id: UUID,
    version: int,
    *,
    outcome: TrajectoryOutcome = "success",
) -> UUID:
    thread_id = uuid4()
    await store.record_skill_run_usage(
        usage=SkillRunUsage(
            id=uuid4(),
            tenant_id=_TENANT,
            skill_id=skill_id,
            skill_version=version,
            thread_id=thread_id,
            agent_name="assistant",
            outcome=outcome,
            created_at=_NOW,
        )
    )
    return thread_id


async def _down(feedback: InMemoryFeedbackStore, thread_id: UUID) -> None:
    await feedback.insert(
        FeedbackRecord(tenant_id=_TENANT, thread_id=thread_id, rating="down", actor_id="user-1")
    )


async def test_disapproved_threads_demote_to_failed_and_trigger_rollback() -> None:
    """12 successes / 8 of them 👎 → observed 0.4 vs baseline 0.9 → rollback.
    Without the feedback join the same window is all-success and keeps."""
    store = InMemorySkillStore()
    feedback = InMemoryFeedbackStore()
    skill_id, version = await _active_skill(store)
    threads = [await _seed_thread(store, skill_id, version) for _ in range(20)]
    for t in threads[:12]:
        await _down(feedback, t)

    gate = RollbackGate(
        skill_store=store,
        breaker=CircuitBreaker(failure_threshold=0.5, min_samples=5, window=timedelta(hours=24)),
        feedback_store=feedback,
    )
    decision = await _rollback(gate, skill_id, version, baseline=0.9)

    assert decision.action is RollbackAction.ROLLBACK
    assert await _status(store, skill_id) is SkillStatus.ARCHIVED


async def test_without_feedback_store_machine_outcomes_only() -> None:
    store = InMemorySkillStore()
    feedback = InMemoryFeedbackStore()
    skill_id, version = await _active_skill(store)
    threads = [await _seed_thread(store, skill_id, version) for _ in range(20)]
    for t in threads[:12]:
        await _down(feedback, t)  # 👎 exists but the gate is not wired to it

    decision = await _rollback(_gate(store), skill_id, version, baseline=0.9)

    assert decision.action is RollbackAction.KEEP


async def test_disapproved_cancelled_runs_stay_excluded() -> None:
    """👎 on a cancelled run must not turn it into a failed sample —
    cancellation is not the skill's fault (SE-7d-2 exclusion holds)."""
    store = InMemorySkillStore()
    feedback = InMemoryFeedbackStore()
    skill_id, version = await _active_skill(store)
    cancelled = await _seed_thread(store, skill_id, version, outcome="cancelled")
    await _down(feedback, cancelled)
    for _ in range(3):
        await _seed_thread(store, skill_id, version)

    gate = RollbackGate(
        skill_store=store,
        breaker=CircuitBreaker(failure_threshold=0.5, min_samples=5, window=timedelta(hours=24)),
        feedback_store=feedback,
    )
    decision = await _rollback(gate, skill_id, version, baseline=0.9)

    # 3 effective samples (< n_min=6): the cancelled+👎 row did not join.
    assert decision.action is RollbackAction.INSUFFICIENT
    assert decision.n_cases == 3


async def test_few_disapprovals_within_tolerance_keep() -> None:
    store = InMemorySkillStore()
    feedback = InMemoryFeedbackStore()
    skill_id, version = await _active_skill(store)
    threads = [await _seed_thread(store, skill_id, version) for _ in range(20)]
    await _down(feedback, threads[0])  # one 👎 in twenty

    gate = RollbackGate(
        skill_store=store,
        breaker=CircuitBreaker(failure_threshold=0.5, min_samples=5, window=timedelta(hours=24)),
        feedback_store=feedback,
    )
    decision = await _rollback(gate, skill_id, version, baseline=0.9)

    assert decision.action is RollbackAction.KEEP


async def test_rollback_invalidates_agent_cache() -> None:
    """Live pilot finding #8 — archiving changes the auto-attach set without
    a spec-version bump; the gate must drop the tenant's BuiltAgent cache."""
    store = InMemorySkillStore()
    skill_id, version = await _active_skill(store)
    await _seed_window(store, skill_id, version, success=0, failed=10)
    invalidated: list[UUID] = []
    gate = RollbackGate(
        skill_store=store,
        breaker=CircuitBreaker(failure_threshold=0.5, min_samples=5, window=timedelta(hours=24)),
        cache_invalidator=invalidated.append,
    )

    await _rollback(gate, skill_id, version, baseline=0.9)
    assert invalidated == [_TENANT]


async def test_kept_version_does_not_invalidate() -> None:
    store = InMemorySkillStore()
    skill_id, version = await _active_skill(store)
    await _seed_window(store, skill_id, version, success=10, failed=0)
    invalidated: list[UUID] = []
    gate = RollbackGate(
        skill_store=store,
        breaker=CircuitBreaker(failure_threshold=0.5, min_samples=5, window=timedelta(hours=24)),
        cache_invalidator=invalidated.append,
    )

    await _rollback(gate, skill_id, version, baseline=0.9)
    assert invalidated == []
