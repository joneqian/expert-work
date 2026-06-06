"""Stream SE (SE-2) — SkillStore evolution API (in-memory backend).

Covers the threaded ownership/provenance fields on ``create_skill`` /
``add_version``, the ``fork_skill`` composition, and ``record_eval_result``
/ ``list_eval_results``. SQL parity + RLS live in the integration suite
(``test_sql_skill_evolution_store.py``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from helix_agent.persistence.skill import InMemorySkillStore
from helix_agent.persistence.skill.base import (
    SkillNotFoundError,
    SkillVersionNotFoundError,
)
from helix_agent.protocol import EvalVerdict, SkillEvalResult


async def _seed_skill(
    store: InMemorySkillStore, tenant_id: UUID, *, name: str = "researcher"
) -> UUID:
    skill_id = uuid4()
    await store.create_skill(skill_id=skill_id, tenant_id=tenant_id, name=name)
    await store.add_version(
        version_id=uuid4(),
        skill_id=skill_id,
        tenant_id=tenant_id,
        prompt_fragment="search arxiv then summarize",
        tool_names=("web_search",),
        description="research helper",
    )
    return skill_id


# ── create_skill: ownership / lineage ─────────────────────────────────────


@pytest.mark.asyncio
async def test_create_skill_threads_ownership() -> None:
    store = InMemorySkillStore()
    tid, agent_id, src = uuid4(), uuid4(), uuid4()
    sid = uuid4()
    await store.create_skill(
        skill_id=sid,
        tenant_id=tid,
        name="agent-skill",
        visibility="agent_private",
        created_by_agent_id=agent_id,
        forked_from=src,
    )
    got = await store.get_skill(skill_id=sid, tenant_id=tid)
    assert got is not None
    assert got.visibility == "agent_private"
    assert got.created_by_agent_id == agent_id
    assert got.forked_from == src


@pytest.mark.asyncio
async def test_create_skill_defaults_tenant_visibility() -> None:
    store = InMemorySkillStore()
    tid = uuid4()
    sid = uuid4()
    await store.create_skill(skill_id=sid, tenant_id=tid, name="human-skill")
    got = await store.get_skill(skill_id=sid, tenant_id=tid)
    assert got is not None and got.visibility == "tenant"
    assert got.created_by_agent_id is None and got.forked_from is None


# ── add_version: evolution provenance ─────────────────────────────────────


@pytest.mark.asyncio
async def test_add_version_threads_evolution_fields() -> None:
    store = InMemorySkillStore()
    tid, cand = uuid4(), uuid4()
    sid = uuid4()
    await store.create_skill(skill_id=sid, tenant_id=tid, name="distilled-skill")
    vid = uuid4()
    v = await store.add_version(
        version_id=vid,
        skill_id=sid,
        tenant_id=tid,
        prompt_fragment="distilled steps",
        authored_by="agent",
        evolution_origin="distilled",
        distilled_from_trajectory_key="t/x.jsonl",
        distilled_from_candidate_id=cand,
        evolution_round=2,
    )
    assert v.evolution_origin == "distilled"
    assert v.distilled_from_trajectory_key == "t/x.jsonl"
    assert v.distilled_from_candidate_id == cand
    assert v.evolution_round == 2
    # round-trips via get_version
    got = await store.get_version(version_id=vid, tenant_id=tid)
    assert got is not None and got.evolution_origin == "distilled"


# ── fork_skill ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fork_skill_copies_latest_and_sets_lineage() -> None:
    store = InMemorySkillStore()
    tid, agent_id = uuid4(), uuid4()
    src = await _seed_skill(store, tid, name="researcher")
    new_sid, new_vid = uuid4(), uuid4()
    forked = await store.fork_skill(
        tenant_id=tid,
        source_skill_id=src,
        new_name="my-researcher",
        by_agent_id=agent_id,
        new_skill_id=new_sid,
        new_version_id=new_vid,
    )
    assert forked.name == "my-researcher"
    assert forked.visibility == "agent_private"
    assert forked.created_by_agent_id == agent_id
    assert forked.forked_from == src
    assert forked.latest_version == 1
    # content copied from source latest version
    v = await store.get_version_by_number(skill_id=new_sid, tenant_id=tid, version=1)
    assert v is not None
    assert v.prompt_fragment == "search arxiv then summarize"
    assert v.tool_names == ("web_search",)
    assert v.authored_by == "agent"
    assert v.evolution_origin == "in_session"


@pytest.mark.asyncio
async def test_fork_skill_unknown_source_raises() -> None:
    store = InMemorySkillStore()
    with pytest.raises(SkillNotFoundError):
        await store.fork_skill(
            tenant_id=uuid4(),
            source_skill_id=uuid4(),
            new_name="x",
            by_agent_id=uuid4(),
            new_skill_id=uuid4(),
            new_version_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_fork_skill_source_without_version_raises() -> None:
    store = InMemorySkillStore()
    tid = uuid4()
    src = uuid4()
    await store.create_skill(skill_id=src, tenant_id=tid, name="empty")  # latest_version=0
    with pytest.raises(SkillVersionNotFoundError):
        await store.fork_skill(
            tenant_id=tid,
            source_skill_id=src,
            new_name="copy",
            by_agent_id=uuid4(),
            new_skill_id=uuid4(),
            new_version_id=uuid4(),
        )


# ── eval results ──────────────────────────────────────────────────────────


def _eval(
    tenant_id: UUID | None,
    skill_id: UUID,
    *,
    delta: float = 0.3,
    verdict: EvalVerdict = "pass",
    at: datetime | None = None,
) -> SkillEvalResult:
    return SkillEvalResult(
        id=uuid4(),
        tenant_id=tenant_id,
        skill_id=skill_id,
        skill_version=1,
        baseline_score=0.5,
        skill_score=0.5 + delta,
        delta=delta,
        n_cases=8,
        replay_source="trajectory",
        verdict=verdict,
        created_at=at or datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_record_and_list_eval_results_newest_first() -> None:
    store = InMemorySkillStore()
    tid, sid = uuid4(), uuid4()
    older = _eval(tid, sid, at=datetime(2026, 6, 1, tzinfo=UTC))
    newer = _eval(tid, sid, at=datetime(2026, 6, 5, tzinfo=UTC))
    await store.record_eval_result(result=older)
    await store.record_eval_result(result=newer)
    rows = await store.list_eval_results(skill_id=sid, tenant_id=tid)
    assert [r.id for r in rows] == [newer.id, older.id]


@pytest.mark.asyncio
async def test_list_eval_results_isolates_tenant_and_skill() -> None:
    store = InMemorySkillStore()
    tid_a, tid_b, sid = uuid4(), uuid4(), uuid4()
    await store.record_eval_result(result=_eval(tid_a, sid))
    await store.record_eval_result(result=_eval(tid_b, sid))
    await store.record_eval_result(result=_eval(tid_a, uuid4()))
    rows = await store.list_eval_results(skill_id=sid, tenant_id=tid_a)
    assert len(rows) == 1 and rows[0].tenant_id == tid_a
