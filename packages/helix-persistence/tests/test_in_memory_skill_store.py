"""Unit tests for :class:`InMemorySkillStore` — Stream J.7a (Mini-ADR J-23)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from helix_agent.persistence import (
    DuplicateSkillError,
    InMemorySkillStore,
    SkillNotFoundError,
)
from helix_agent.protocol import SkillStatus


def _t() -> UUID:
    return uuid4()


# ---------------------------------------------------------------------------
# create + get
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_skill_starts_in_draft_with_zero_version() -> None:
    store = InMemorySkillStore()
    tenant = _t()
    skill = await store.create_skill(
        skill_id=uuid4(),
        tenant_id=tenant,
        name="foo",
        description="my foo skill",
        category="data",
    )
    assert skill.status == SkillStatus.DRAFT
    assert skill.latest_version == 0
    assert skill.description == "my foo skill"
    assert skill.category == "data"


@pytest.mark.asyncio
async def test_create_skill_rejects_duplicate_tenant_name() -> None:
    store = InMemorySkillStore()
    tenant = _t()
    await store.create_skill(skill_id=uuid4(), tenant_id=tenant, name="foo")
    with pytest.raises(DuplicateSkillError):
        await store.create_skill(skill_id=uuid4(), tenant_id=tenant, name="foo")


@pytest.mark.asyncio
async def test_get_skill_cross_tenant_returns_none() -> None:
    store = InMemorySkillStore()
    skill_id = uuid4()
    await store.create_skill(skill_id=skill_id, tenant_id=_t(), name="foo")
    other_tenant = _t()
    assert await store.get_skill(skill_id=skill_id, tenant_id=other_tenant) is None


# ---------------------------------------------------------------------------
# add_version + resolve
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_version_auto_increments() -> None:
    store = InMemorySkillStore()
    tenant = _t()
    skill = await store.create_skill(skill_id=uuid4(), tenant_id=tenant, name="foo")
    v1 = await store.add_version(
        version_id=uuid4(),
        skill_id=skill.id,
        tenant_id=tenant,
        prompt_fragment="be helpful",
        tool_names=["web_search"],
    )
    v2 = await store.add_version(
        version_id=uuid4(),
        skill_id=skill.id,
        tenant_id=tenant,
        prompt_fragment="be more helpful",
        tool_names=["web_search", "exec_python"],
    )
    assert v1.version == 1
    assert v2.version == 2
    refreshed = await store.get_skill(skill_id=skill.id, tenant_id=tenant)
    assert refreshed is not None and refreshed.latest_version == 2


@pytest.mark.asyncio
async def test_add_version_unknown_skill_raises() -> None:
    store = InMemorySkillStore()
    with pytest.raises(SkillNotFoundError):
        await store.add_version(
            version_id=uuid4(),
            skill_id=uuid4(),
            tenant_id=_t(),
            prompt_fragment="x",
        )


@pytest.mark.asyncio
async def test_resolve_by_name_only_active_skills() -> None:
    store = InMemorySkillStore()
    tenant = _t()
    skill = await store.create_skill(skill_id=uuid4(), tenant_id=tenant, name="foo")
    await store.add_version(
        version_id=uuid4(), skill_id=skill.id, tenant_id=tenant, prompt_fragment="v1"
    )
    # Draft — bare name should not resolve.
    assert await store.resolve_by_name(tenant_id=tenant, name="foo") is None
    # Promote to active.
    await store.set_status(skill_id=skill.id, tenant_id=tenant, status=SkillStatus.ACTIVE)
    resolved = await store.resolve_by_name(tenant_id=tenant, name="foo")
    assert resolved is not None
    assert resolved.version == 1
    # Archive — bare name no longer resolves.
    await store.set_status(skill_id=skill.id, tenant_id=tenant, status=SkillStatus.ARCHIVED)
    assert await store.resolve_by_name(tenant_id=tenant, name="foo") is None


@pytest.mark.asyncio
async def test_resolve_pinned_allows_any_status() -> None:
    """Pinning is the reproducibility escape hatch — works regardless
    of the parent skill's lifecycle state."""
    store = InMemorySkillStore()
    tenant = _t()
    skill = await store.create_skill(skill_id=uuid4(), tenant_id=tenant, name="foo")
    await store.add_version(
        version_id=uuid4(), skill_id=skill.id, tenant_id=tenant, prompt_fragment="v1"
    )
    await store.set_status(skill_id=skill.id, tenant_id=tenant, status=SkillStatus.ARCHIVED)
    # Archived but pinned ref still resolves.
    resolved = await store.resolve_pinned(tenant_id=tenant, name="foo", version=1)
    assert resolved is not None
    assert resolved.version == 1


@pytest.mark.asyncio
async def test_resolve_pinned_missing_version_returns_none() -> None:
    store = InMemorySkillStore()
    tenant = _t()
    skill = await store.create_skill(skill_id=uuid4(), tenant_id=tenant, name="foo")
    await store.add_version(
        version_id=uuid4(), skill_id=skill.id, tenant_id=tenant, prompt_fragment="v1"
    )
    assert await store.resolve_pinned(tenant_id=tenant, name="foo", version=99) is None
    assert await store.resolve_pinned(tenant_id=tenant, name="missing", version=1) is None


# ---------------------------------------------------------------------------
# list + paginate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_skills_filters_by_status_and_category() -> None:
    store = InMemorySkillStore()
    tenant = _t()
    a = await store.create_skill(skill_id=uuid4(), tenant_id=tenant, name="a", category="data")
    b = await store.create_skill(skill_id=uuid4(), tenant_id=tenant, name="b", category="ops")
    c = await store.create_skill(skill_id=uuid4(), tenant_id=tenant, name="c", category="data")
    await store.set_status(skill_id=a.id, tenant_id=tenant, status=SkillStatus.ACTIVE)
    await store.set_status(skill_id=c.id, tenant_id=tenant, status=SkillStatus.ACTIVE)
    # Filter by status.
    actives, _ = await store.list_skills(tenant_id=tenant, status=SkillStatus.ACTIVE)
    assert {r.id for r in actives} == {a.id, c.id}
    # Filter by category.
    data_skills, _ = await store.list_skills(tenant_id=tenant, category="data")
    assert {r.id for r in data_skills} == {a.id, c.id}
    # Combined.
    combo, _ = await store.list_skills(tenant_id=tenant, status=SkillStatus.ACTIVE, category="ops")
    assert combo == []
    # Tenant isolation.
    other = await store.list_skills(tenant_id=_t())
    assert other == ([], None)
    # Use the unused 'b' to silence linters and assert it's not in actives.
    _ = b


@pytest.mark.asyncio
async def test_list_skills_paginates_with_cursor() -> None:
    store = InMemorySkillStore()
    tenant = _t()
    ids = []
    for i in range(5):
        skill = await store.create_skill(skill_id=uuid4(), tenant_id=tenant, name=f"s{i}")
        ids.append(skill.id)
    page1, cursor = await store.list_skills(tenant_id=tenant, limit=2)
    assert len(page1) == 2
    assert cursor is not None
    page2, cursor2 = await store.list_skills(tenant_id=tenant, limit=2, cursor=cursor)
    assert len(page2) == 2
    assert cursor2 is not None
    page3, cursor3 = await store.list_skills(tenant_id=tenant, limit=2, cursor=cursor2)
    assert len(page3) == 1
    assert cursor3 is None
    # No duplicates across pages.
    seen = {r.id for r in page1 + page2 + page3}
    assert seen == set(ids)


# ---------------------------------------------------------------------------
# version metadata mirroring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_version_mirrors_description_and_category() -> None:
    """Latest version's description / category lands on the parent skill row
    so admin list responses can render without a JOIN."""
    store = InMemorySkillStore()
    tenant = _t()
    skill = await store.create_skill(
        skill_id=uuid4(), tenant_id=tenant, name="foo", description="old"
    )
    await store.add_version(
        version_id=uuid4(),
        skill_id=skill.id,
        tenant_id=tenant,
        prompt_fragment="x",
        description="new",
        category="research",
    )
    refreshed = await store.get_skill(skill_id=skill.id, tenant_id=tenant)
    assert refreshed is not None
    assert refreshed.description == "new"
    assert refreshed.category == "research"


@pytest.mark.asyncio
async def test_set_status_unknown_skill_raises() -> None:
    store = InMemorySkillStore()
    with pytest.raises(SkillNotFoundError):
        await store.set_status(skill_id=uuid4(), tenant_id=_t(), status=SkillStatus.ACTIVE)


@pytest.mark.asyncio
async def test_list_versions_orders_desc() -> None:
    store = InMemorySkillStore()
    tenant = _t()
    skill = await store.create_skill(skill_id=uuid4(), tenant_id=tenant, name="foo")
    for n in range(3):
        await store.add_version(
            version_id=uuid4(),
            skill_id=skill.id,
            tenant_id=tenant,
            prompt_fragment=f"v{n + 1}",
        )
    versions = await store.list_versions(skill_id=skill.id, tenant_id=tenant)
    assert [v.version for v in versions] == [3, 2, 1]


@pytest.mark.asyncio
async def test_add_version_rejects_invalid_authored_by() -> None:
    store = InMemorySkillStore()
    tenant = _t()
    skill = await store.create_skill(skill_id=uuid4(), tenant_id=tenant, name="foo")
    with pytest.raises(ValueError, match="authored_by"):
        await store.add_version(
            version_id=uuid4(),
            skill_id=skill.id,
            tenant_id=tenant,
            prompt_fragment="x",
            authored_by="alien",
        )
