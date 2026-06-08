"""Tests for the SE-7d-3b-i rollback monitor sweep (CI-testable core).

``RollbackMonitor.run_once`` enumerates ACTIVE *distilled* skill versions across
tenants (owner RLS exemption, like the curator — a no-op for the in-memory
store), resolves each version's promote-time baseline (latest ``pass``
``skill_eval_result.skill_score``), and runs it through the SE-7d-3a
:class:`RollbackGate`. The cross-tenant ``bypass_rls`` GUC wiring + the app
lifespan are real-path (integration); the sweep logic is pinned here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from control_plane.skill_evolution_limits import CircuitBreaker
from control_plane.skill_rollback_gate import RollbackGate
from control_plane.skill_rollback_monitor import RollbackMonitor, RollbackMonitorConfig
from helix_agent.persistence.skill.memory import InMemorySkillStore
from helix_agent.protocol import SkillEvalResult, SkillRunUsage, TrajectoryOutcome
from helix_agent.protocol.skill import SkillStatus

_NOW = datetime(2026, 6, 8, 12, 0, tzinfo=UTC)


async def _distilled_active_skill(
    store: InMemorySkillStore,
    *,
    tenant_id: UUID,
    name: str = "s",
    agent_name: str | None = "assistant",
    origin: str | None = "distilled",
) -> UUID:
    skill = await store.create_skill(
        skill_id=uuid4(),
        tenant_id=tenant_id,
        name=name,
        visibility="agent_private",
        created_by_agent_name=agent_name,
    )
    await store.add_version(
        version_id=uuid4(),
        skill_id=skill.id,
        tenant_id=tenant_id,
        prompt_fragment="do x",
        authored_by="agent",
        evolution_origin=origin,  # type: ignore[arg-type]
    )
    await store.set_status(skill_id=skill.id, tenant_id=tenant_id, status=SkillStatus.ACTIVE)
    return skill.id


async def _seed_baseline(
    store: InMemorySkillStore, *, tenant_id: UUID, skill_id: UUID, score: float
) -> None:
    await store.record_eval_result(
        result=SkillEvalResult(
            id=uuid4(),
            tenant_id=tenant_id,
            skill_id=skill_id,
            skill_version=1,
            baseline_score=0.5,
            skill_score=score,
            delta=score - 0.5,
            n_cases=10,
            replay_source="trajectory",
            verdict="pass",
            created_at=_NOW - timedelta(days=10),
        )
    )


async def _seed_window(
    store: InMemorySkillStore,
    *,
    tenant_id: UUID,
    skill_id: UUID,
    success: int,
    failed: int,
) -> None:
    outcomes: list[TrajectoryOutcome] = ["success"] * success + ["failed"] * failed
    for oc in outcomes:
        await store.record_skill_run_usage(
            usage=SkillRunUsage(
                id=uuid4(),
                tenant_id=tenant_id,
                skill_id=skill_id,
                skill_version=1,
                thread_id=uuid4(),
                agent_name="assistant",
                outcome=oc,
                created_at=_NOW,
            )
        )


def _monitor(store: InMemorySkillStore) -> RollbackMonitor:
    breaker = CircuitBreaker(failure_threshold=0.5, min_samples=5, window=timedelta(hours=24))
    gate = RollbackGate(skill_store=store, breaker=breaker)
    return RollbackMonitor(
        skill_store=store,
        gate=gate,
        config=RollbackMonitorConfig(window=timedelta(days=7)),
        clock=lambda: _NOW,
    )


async def _status(store: InMemorySkillStore, tenant_id: UUID, skill_id: UUID) -> SkillStatus:
    skill = await store.get_skill(skill_id=skill_id, tenant_id=tenant_id)
    assert skill is not None
    return skill.status


async def test_regressed_distilled_skill_is_archived() -> None:
    store = InMemorySkillStore()
    tid = uuid4()
    sid = await _distilled_active_skill(store, tenant_id=tid)
    await _seed_baseline(store, tenant_id=tid, skill_id=sid, score=0.9)
    await _seed_window(store, tenant_id=tid, skill_id=sid, success=8, failed=12)

    tally = await _monitor(store).run_once()

    assert tally.rolled_back == 1
    assert await _status(store, tid, sid) is SkillStatus.ARCHIVED


async def test_healthy_distilled_skill_kept() -> None:
    store = InMemorySkillStore()
    tid = uuid4()
    sid = await _distilled_active_skill(store, tenant_id=tid)
    await _seed_baseline(store, tenant_id=tid, skill_id=sid, score=0.9)
    await _seed_window(store, tenant_id=tid, skill_id=sid, success=18, failed=2)

    tally = await _monitor(store).run_once()

    assert tally.kept == 1
    assert await _status(store, tid, sid) is SkillStatus.ACTIVE


async def test_human_skill_is_skipped() -> None:
    # Non-distilled ACTIVE skills are never rollback targets.
    store = InMemorySkillStore()
    tid = uuid4()
    sid = await _distilled_active_skill(store, tenant_id=tid, origin=None, agent_name=None)
    await _seed_window(store, tenant_id=tid, skill_id=sid, success=8, failed=12)

    tally = await _monitor(store).run_once()

    assert tally.rolled_back == 0
    assert tally.skipped == 1
    assert await _status(store, tid, sid) is SkillStatus.ACTIVE


async def test_distilled_without_pass_evidence_skipped() -> None:
    # No promote baseline to compare against → skip (don't guess).
    store = InMemorySkillStore()
    tid = uuid4()
    sid = await _distilled_active_skill(store, tenant_id=tid)
    await _seed_window(store, tenant_id=tid, skill_id=sid, success=8, failed=12)

    tally = await _monitor(store).run_once()

    assert tally.skipped == 1
    assert await _status(store, tid, sid) is SkillStatus.ACTIVE


async def test_sweep_spans_tenants() -> None:
    store = InMemorySkillStore()
    tid_a, tid_b = uuid4(), uuid4()
    sid_a = await _distilled_active_skill(store, tenant_id=tid_a, name="a")
    sid_b = await _distilled_active_skill(store, tenant_id=tid_b, name="b")
    for tid, sid in ((tid_a, sid_a), (tid_b, sid_b)):
        await _seed_baseline(store, tenant_id=tid, skill_id=sid, score=0.9)
        await _seed_window(store, tenant_id=tid, skill_id=sid, success=8, failed=12)

    tally = await _monitor(store).run_once()

    assert tally.rolled_back == 2
    assert await _status(store, tid_a, sid_a) is SkillStatus.ARCHIVED
    assert await _status(store, tid_b, sid_b) is SkillStatus.ARCHIVED
