"""SE-16 (SE-A47) — embedding dedup over the same agent's distilled skills.

Exercises ``_EmbeddingDeduper`` with a deterministic fake embedder: a fresh
draft semantically close to an existing distilled skill (DRAFT or ACTIVE)
returns that skill as a :class:`DedupMatch`; anything below the floor — or
any embedder fault — degrades to ``None`` (dedup never gates distillation).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from control_plane.skill_distiller import SkillDraft
from control_plane.skill_evolution_wiring import _EmbeddingDeduper
from helix_agent.persistence.skill.memory import InMemorySkillStore
from helix_agent.protocol import CurationCandidateRecord
from helix_agent.protocol.skill import SkillStatus

_TENANT = UUID("44444444-4444-4444-4444-444444444444")


class _FakeEmbedder:
    """Maps texts containing a marker word onto fixed axes."""

    def __init__(self) -> None:
        self.calls = 0

    async def embed_one(self, text: str, *, tenant_id: UUID) -> tuple[float, ...]:
        self.calls += 1
        if "tabular" in text:
            return (1.0, 0.0)
        if "email" in text:
            return (0.0, 1.0)
        return (0.7, 0.7)


class _ExplodingEmbedder:
    async def embed_one(self, text: str, *, tenant_id: UUID) -> tuple[float, ...]:
        raise RuntimeError("embedding not configured")


def _draft(name: str = "summarise-tabular") -> SkillDraft:
    return SkillDraft(
        name=name,
        prompt_fragment="Read tabular headers first, then aggregate.",
        tool_names=(),
        description="Summarise tabular data",
        category="data",
        high_risk=False,
    )


def _candidate(agent: str = "assistant") -> CurationCandidateRecord:
    return CurationCandidateRecord(
        id=uuid4(),
        tenant_id=_TENANT,
        agent_name=agent,
        thread_id=uuid4(),
        trajectory_key=f"k/{uuid4()}",
        outcome="success",
        signal="positive_feedback",
        detected_at=datetime.now(UTC),
    )


async def _seed(
    store: InMemorySkillStore,
    *,
    name: str,
    fragment: str,
    agent: str = "assistant",
    status: SkillStatus = SkillStatus.ACTIVE,
    origin: str | None = "distilled",
) -> UUID:
    skill = await store.create_skill(
        skill_id=uuid4(),
        tenant_id=_TENANT,
        name=name,
        description=f"{name} desc",
        visibility="agent_private",
        created_by_agent_name=agent,
    )
    await store.add_version(
        version_id=uuid4(),
        skill_id=skill.id,
        tenant_id=_TENANT,
        prompt_fragment=fragment,
        description=f"{name} desc",
        authored_by="agent",
        evolution_origin=origin,  # type: ignore[arg-type]
    )
    await store.set_status(skill_id=skill.id, tenant_id=_TENANT, status=status)
    return skill.id


@pytest.mark.asyncio
async def test_similar_existing_skill_matches() -> None:
    store = InMemorySkillStore()
    existing = await _seed(store, name="tabular-howto", fragment="tabular aggregation recipe")
    dedup = _EmbeddingDeduper(skill_store=store, embedder=_FakeEmbedder())

    match = await dedup(_draft(), _candidate())

    assert match is not None
    assert match.skill_id == existing
    assert match.similarity >= 0.9


@pytest.mark.asyncio
async def test_dissimilar_skill_does_not_match() -> None:
    store = InMemorySkillStore()
    await _seed(store, name="email-drafting", fragment="email tone guidance")
    dedup = _EmbeddingDeduper(skill_store=store, embedder=_FakeEmbedder())

    assert await dedup(_draft(), _candidate()) is None


@pytest.mark.asyncio
async def test_other_agent_and_non_distilled_are_excluded() -> None:
    store = InMemorySkillStore()
    await _seed(store, name="theirs", fragment="tabular recipe", agent="other-agent")
    await _seed(store, name="in-session", fragment="tabular recipe", origin="in_session")
    dedup = _EmbeddingDeduper(skill_store=store, embedder=_FakeEmbedder())

    assert await dedup(_draft(), _candidate()) is None


@pytest.mark.asyncio
async def test_draft_status_target_is_eligible() -> None:
    store = InMemorySkillStore()
    existing = await _seed(
        store, name="tabular-howto", fragment="tabular recipe", status=SkillStatus.DRAFT
    )
    dedup = _EmbeddingDeduper(skill_store=store, embedder=_FakeEmbedder())

    match = await dedup(_draft(), _candidate())
    assert match is not None and match.skill_id == existing


@pytest.mark.asyncio
async def test_archived_target_is_not_eligible() -> None:
    store = InMemorySkillStore()
    await _seed(store, name="tabular-howto", fragment="tabular recipe", status=SkillStatus.ARCHIVED)
    dedup = _EmbeddingDeduper(skill_store=store, embedder=_FakeEmbedder())

    assert await dedup(_draft(), _candidate()) is None


@pytest.mark.asyncio
async def test_embedder_fault_degrades_to_none() -> None:
    store = InMemorySkillStore()
    await _seed(store, name="tabular-howto", fragment="tabular recipe")
    dedup = _EmbeddingDeduper(skill_store=store, embedder=_ExplodingEmbedder())

    assert await dedup(_draft(), _candidate()) is None


@pytest.mark.asyncio
async def test_no_existing_skills_skips_embedding_entirely() -> None:
    store = InMemorySkillStore()
    embedder = _FakeEmbedder()
    dedup = _EmbeddingDeduper(skill_store=store, embedder=embedder)

    assert await dedup(_draft(), _candidate()) is None
    assert embedder.calls == 0
