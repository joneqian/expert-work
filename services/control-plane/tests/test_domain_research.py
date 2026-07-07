"""Stream SE — SE-13 pre-evolution domain research (Mini-ADR SE-A24..A28).

Deps are faked (CI has no model key); covers the research flow, cold-start/TTL
gate, tenant-KB-only fallback, abstraction guard, and DRAFT-prior persistence.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from control_plane.domain_research import (
    DomainResearchConfig,
    DomainResearcher,
    needs_research,
    research_skill_name,
)
from expert_work.persistence.skill.memory import InMemorySkillStore
from expert_work.protocol import SkillStatus

_NOW = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)


class _FakeKb:
    def __init__(self, snippets: list[str]) -> None:
        self.snippets = snippets
        self.calls = 0

    async def __call__(self, *, tenant_id, base_names, query, limit) -> list[str]:
        self.calls += 1
        return self.snippets[:limit]


class _FakeWeb:
    def __init__(self, snippets: list[str]) -> None:
        self.snippets = snippets
        self.calls = 0

    async def __call__(self, *, tenant_id, query, max_results) -> list[str]:
        self.calls += 1
        return self.snippets[:max_results]


class _FakeSummary:
    def __init__(self, text: str) -> None:
        self.text = text
        self.last_prompt: str | None = None

    async def __call__(self, *, prompt, tenant_id) -> str:
        self.last_prompt = prompt
        return self.text


def _researcher(store, kb, summary, *, web=None, **cfg):
    return DomainResearcher(
        skill_store=store,
        kb_searcher=kb,
        summarizer=summary,
        web_searcher=web,
        config=DomainResearchConfig(**cfg),
    )


# ── gate ──────────────────────────────────────────────────────────────────


def test_needs_research_when_no_prior() -> None:
    assert needs_research(None, now=_NOW, ttl_days=30) is True


async def test_needs_research_fresh_vs_stale() -> None:
    store = InMemorySkillStore()
    skill = await store.create_skill(skill_id=uuid4(), tenant_id=uuid4(), name="x")
    # Fresh (created just now) → no research.
    assert needs_research(skill, now=skill.created_at + timedelta(days=1), ttl_days=30) is False
    # Stale (older than ttl) → research.
    assert needs_research(skill, now=skill.created_at + timedelta(days=40), ttl_days=30) is True


# ── research flow ─────────────────────────────────────────────────────────


async def test_research_kb_only_when_web_off() -> None:
    kb, web = _FakeKb(["kb fact"]), _FakeWeb(["web fact"])
    r = _researcher(InMemorySkillStore(), kb, _FakeSummary("briefing"), web=web)
    out = await r.research(tenant_id=uuid4(), base_names=["b"], topic="t")
    assert out is not None and out.summary == "briefing"
    assert out.used_web is False and out.n_web_snippets == 0
    assert web.calls == 0  # web off by default


async def test_research_uses_web_when_enabled() -> None:
    kb, web = _FakeKb(["kb"]), _FakeWeb(["web"])
    r = _researcher(InMemorySkillStore(), kb, _FakeSummary("b"), web=web, web_search_enabled=True)
    out = await r.research(tenant_id=uuid4(), base_names=["b"], topic="t")
    assert out is not None and out.used_web is True and out.n_web_snippets == 1
    assert web.calls == 1


async def test_research_none_when_no_material() -> None:
    r = _researcher(InMemorySkillStore(), _FakeKb([]), _FakeSummary("b"))
    assert await r.research(tenant_id=uuid4(), base_names=["b"], topic="t") is None


async def test_research_rejects_overspecific_summary() -> None:
    leaky = _FakeSummary("see run 123456789012345 for the value")
    r = _researcher(InMemorySkillStore(), _FakeKb(["x"]), leaky)
    assert await r.research(tenant_id=uuid4(), base_names=["b"], topic="t") is None


# ── persistence + cold-start ──────────────────────────────────────────────


async def test_persist_creates_draft_agent_private_prior() -> None:
    store = InMemorySkillStore()
    tid, uid = uuid4(), uuid4()
    r = _researcher(store, _FakeKb(["fact"]), _FakeSummary("prior text"), enabled=True)
    sid = await r.research_and_persist(
        tenant_id=tid,
        agent_name="assistant",
        user_id=uid,
        base_names=["b"],
        topic="t",
        now=_NOW,
    )
    assert sid is not None
    skill = await store.get_skill_by_name(tenant_id=tid, name=research_skill_name("assistant"))
    assert skill is not None
    assert skill.status is SkillStatus.DRAFT  # never auto-active (SE-A0)
    assert skill.visibility == "agent_private"
    assert skill.created_by_agent_name == "assistant"
    v = await store.get_version_by_number(skill_id=skill.id, tenant_id=tid, version=1)
    assert v is not None and v.evolution_origin == "distilled"


async def test_disabled_is_noop() -> None:
    store = InMemorySkillStore()
    r = _researcher(store, _FakeKb(["fact"]), _FakeSummary("x"), enabled=False)
    sid = await r.research_and_persist(
        tenant_id=uuid4(),
        agent_name="a",
        user_id=None,
        base_names=["b"],
        topic="t",
        now=_NOW,
    )
    assert sid is None


async def test_fresh_prior_skips_research() -> None:
    store = InMemorySkillStore()
    tid = uuid4()
    kb = _FakeKb(["fact"])
    r = _researcher(store, kb, _FakeSummary("x"), enabled=True, ttl_days=30)
    # First call researches + persists.
    await r.research_and_persist(
        tenant_id=tid, agent_name="a", user_id=None, base_names=["b"], topic="t", now=_NOW
    )
    calls_after_first = kb.calls
    # Second call within TTL → skipped (no new KB search).
    sid = await r.research_and_persist(
        tenant_id=tid,
        agent_name="a",
        user_id=None,
        base_names=["b"],
        topic="t",
        now=_NOW + timedelta(days=1),
    )
    assert sid is None
    assert kb.calls == calls_after_first
