"""In-memory ``SkillStore`` tests for ``skill_run_usage`` (Stream SE, SE-7d-1).

``skill_run_usage`` is the **skill-centric** attribution table the regression-
rollback monitor (SE-7d-3) reads: per ``(skill_id, skill_version)`` it answers
"what were the outcomes of the runs that used this version in the last window".
SQL parity + RLS isolation live in the integration suite; here we pin the
in-memory store's write + windowed-aggregation logic.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from helix_agent.persistence.skill import InMemorySkillStore
from helix_agent.protocol import SkillRunUsage, TrajectoryOutcome

_T0 = datetime(2026, 6, 8, 12, 0, tzinfo=UTC)


def _usage(
    tenant_id: UUID,
    skill_id: UUID,
    *,
    version: int = 1,
    outcome: TrajectoryOutcome = "success",
    at: datetime = _T0,
    agent_name: str = "assistant",
) -> SkillRunUsage:
    return SkillRunUsage(
        id=uuid4(),
        tenant_id=tenant_id,
        skill_id=skill_id,
        skill_version=version,
        thread_id=uuid4(),
        agent_name=agent_name,
        outcome=outcome,
        created_at=at,
    )


async def test_record_and_aggregate_window_outcomes() -> None:
    store = InMemorySkillStore()
    tid, sid = uuid4(), uuid4()
    await store.record_skill_run_usage(usage=_usage(tid, sid, outcome="success"))
    await store.record_skill_run_usage(usage=_usage(tid, sid, outcome="success"))
    await store.record_skill_run_usage(usage=_usage(tid, sid, outcome="failed"))

    outcomes = await store.skill_run_outcomes(
        skill_id=sid, skill_version=1, tenant_id=tid, since=_T0 - timedelta(hours=1)
    )
    assert Counter(outcomes) == Counter({"success": 2, "failed": 1})


async def test_skill_run_outcomes_filters_by_version() -> None:
    store = InMemorySkillStore()
    tid, sid = uuid4(), uuid4()
    await store.record_skill_run_usage(usage=_usage(tid, sid, version=1, outcome="failed"))
    await store.record_skill_run_usage(usage=_usage(tid, sid, version=2, outcome="success"))

    # promote is per-version → rollback judges per-version, never连坐 the next one.
    v1 = await store.skill_run_outcomes(
        skill_id=sid, skill_version=1, tenant_id=tid, since=_T0 - timedelta(hours=1)
    )
    v2 = await store.skill_run_outcomes(
        skill_id=sid, skill_version=2, tenant_id=tid, since=_T0 - timedelta(hours=1)
    )
    assert v1 == ["failed"]
    assert v2 == ["success"]


async def test_skill_run_outcomes_excludes_rows_before_window() -> None:
    store = InMemorySkillStore()
    tid, sid = uuid4(), uuid4()
    await store.record_skill_run_usage(usage=_usage(tid, sid, outcome="success", at=_T0))
    await store.record_skill_run_usage(
        usage=_usage(tid, sid, outcome="failed", at=_T0 - timedelta(days=30))
    )

    recent = await store.skill_run_outcomes(
        skill_id=sid, skill_version=1, tenant_id=tid, since=_T0 - timedelta(days=1)
    )
    assert recent == ["success"]  # the 30-day-old row is outside the window


async def test_skill_run_outcomes_isolates_tenant_and_skill() -> None:
    store = InMemorySkillStore()
    tid_a, tid_b, sid = uuid4(), uuid4(), uuid4()
    await store.record_skill_run_usage(usage=_usage(tid_a, sid, outcome="success"))
    await store.record_skill_run_usage(usage=_usage(tid_b, sid, outcome="failed"))
    await store.record_skill_run_usage(usage=_usage(tid_a, uuid4(), outcome="failed"))

    rows = await store.skill_run_outcomes(
        skill_id=sid, skill_version=1, tenant_id=tid_a, since=_T0 - timedelta(hours=1)
    )
    assert rows == ["success"]  # only tenant_a's run of this skill
