"""Tests for :func:`control_plane.runtime.make_skill_resolver` — Stream X
(Mini-ADR X-4).

Tenant-first / platform-fallback resolution with the R3 plan-tier gate:

* tenant active skill → ``ok``.
* tenant draft / archived skill (exists) → ``not_active``, NO platform
  fallback (R2 name-shadowing).
* tenant absent + platform active + entitled → ``ok``.
* tenant absent + platform present but NOT entitled (free tenant, pro
  skill) → ``not_entitled``.
* tenant absent + platform absent → ``not_found``.
* pinned variants (tenant + platform).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from control_plane.runtime import make_skill_resolver
from control_plane.tenancy import TenantConfigNotConfiguredError
from expert_work.persistence.skill import InMemorySkillStore
from expert_work.protocol import SkillStatus, TenantConfigRecord, TenantPlan


class _StubTenantConfigService:
    """Minimal :class:`TenantConfigService` shape — returns a record with
    ``plan`` or raises :class:`TenantConfigNotConfiguredError`."""

    def __init__(self, *, plan: TenantPlan | None) -> None:
        self._plan = plan

    async def get(self, *, tenant_id: UUID, actor_id: str | None = None) -> TenantConfigRecord:
        if self._plan is None:
            raise TenantConfigNotConfiguredError(tenant_id=tenant_id)
        now = datetime.now(UTC)
        return TenantConfigRecord(
            tenant_id=tenant_id,
            display_name="t",
            plan=self._plan,
            created_at=now,
            updated_at=now,
            updated_by="test",
        )


async def _seed_tenant_skill(
    store: InMemorySkillStore,
    *,
    tenant_id: UUID,
    name: str,
    status: SkillStatus,
) -> UUID:
    skill_id = uuid4()
    await store.create_skill(skill_id=skill_id, tenant_id=tenant_id, name=name)
    await store.add_version(
        version_id=uuid4(), skill_id=skill_id, tenant_id=tenant_id, prompt_fragment="tenant body"
    )
    if status != SkillStatus.DRAFT:
        await store.set_status(skill_id=skill_id, tenant_id=tenant_id, status=status)
    return skill_id


async def _seed_platform_skill(
    store: InMemorySkillStore,
    *,
    name: str,
    status: SkillStatus,
    required_tier: TenantPlan = TenantPlan.FREE,
) -> UUID:
    skill_id = uuid4()
    await store.create_platform_skill(skill_id=skill_id, name=name, required_tier=required_tier)
    await store.add_platform_version(
        version_id=uuid4(), skill_id=skill_id, prompt_fragment="platform body"
    )
    if status != SkillStatus.DRAFT:
        await store.set_platform_status(skill_id=skill_id, status=status)
    return skill_id


@pytest.mark.asyncio
async def test_tenant_active_skill_resolves_ok() -> None:
    store = InMemorySkillStore()
    tenant_id = uuid4()
    await _seed_tenant_skill(store, tenant_id=tenant_id, name="foo", status=SkillStatus.ACTIVE)
    resolve = make_skill_resolver(
        store=store, tenant_config_service=_StubTenantConfigService(plan=TenantPlan.FREE)
    )
    result = await resolve(tenant_id, "foo", None)
    assert result.reason is None
    assert result.version is not None
    assert result.version.prompt_fragment == "tenant body"


class _CountingSkillStore(InMemorySkillStore):
    """Counts ``get_skill_by_name`` calls — to lock the no-redundant-refetch
    property (``resolve_by_name`` used to re-run it, an N+1 over the manifest)."""

    def __init__(self) -> None:
        super().__init__()
        self.get_by_name_calls = 0

    async def get_skill_by_name(self, *, tenant_id: UUID, name: str):  # type: ignore[no-untyped-def]
        self.get_by_name_calls += 1
        return await super().get_skill_by_name(tenant_id=tenant_id, name=name)


@pytest.mark.asyncio
async def test_tenant_resolve_fetches_skill_row_once() -> None:
    """The tenant-active path reads the skill row exactly once — reusing it for
    the version lookup instead of the old resolve_by_name refetch."""
    store = _CountingSkillStore()
    tenant_id = uuid4()
    await _seed_tenant_skill(store, tenant_id=tenant_id, name="foo", status=SkillStatus.ACTIVE)
    store.get_by_name_calls = 0  # ignore the seeding call
    resolve = make_skill_resolver(
        store=store, tenant_config_service=_StubTenantConfigService(plan=TenantPlan.FREE)
    )
    result = await resolve(tenant_id, "foo", None)
    assert result.version is not None
    assert store.get_by_name_calls == 1  # was 2 (resolve_by_name refetched)


@pytest.mark.asyncio
async def test_tenant_draft_skill_shadows_platform_returns_not_active() -> None:
    """R2 — a tenant-owned name shadows the platform library even when the
    tenant's copy is draft; we never fall back to the platform skill."""
    store = InMemorySkillStore()
    tenant_id = uuid4()
    await _seed_tenant_skill(store, tenant_id=tenant_id, name="foo", status=SkillStatus.DRAFT)
    # A platform skill of the SAME name is active + entitled — must be ignored.
    await _seed_platform_skill(store, name="foo", status=SkillStatus.ACTIVE)
    resolve = make_skill_resolver(
        store=store, tenant_config_service=_StubTenantConfigService(plan=TenantPlan.FREE)
    )
    result = await resolve(tenant_id, "foo", None)
    assert result.reason == "not_active"
    assert result.version is None


@pytest.mark.asyncio
async def test_tenant_archived_skill_shadows_platform_returns_not_active() -> None:
    store = InMemorySkillStore()
    tenant_id = uuid4()
    await _seed_tenant_skill(store, tenant_id=tenant_id, name="foo", status=SkillStatus.ARCHIVED)
    await _seed_platform_skill(store, name="foo", status=SkillStatus.ACTIVE)
    resolve = make_skill_resolver(
        store=store, tenant_config_service=_StubTenantConfigService(plan=TenantPlan.PRO)
    )
    result = await resolve(tenant_id, "foo", None)
    assert result.reason == "not_active"


@pytest.mark.asyncio
async def test_platform_fallback_entitled_resolves_ok() -> None:
    store = InMemorySkillStore()
    tenant_id = uuid4()
    await _seed_platform_skill(store, name="plat", status=SkillStatus.ACTIVE)
    resolve = make_skill_resolver(
        store=store, tenant_config_service=_StubTenantConfigService(plan=TenantPlan.FREE)
    )
    result = await resolve(tenant_id, "plat", None)
    assert result.reason is None
    assert result.version is not None
    assert result.version.prompt_fragment == "platform body"


@pytest.mark.asyncio
async def test_platform_fallback_not_entitled_returns_not_entitled() -> None:
    """R3 — a free tenant cannot bind a pro platform skill."""
    store = InMemorySkillStore()
    tenant_id = uuid4()
    await _seed_platform_skill(
        store, name="plat", status=SkillStatus.ACTIVE, required_tier=TenantPlan.PRO
    )
    resolve = make_skill_resolver(
        store=store, tenant_config_service=_StubTenantConfigService(plan=TenantPlan.FREE)
    )
    result = await resolve(tenant_id, "plat", None)
    assert result.reason == "not_entitled"
    assert result.required_tier == "pro"


@pytest.mark.asyncio
async def test_unconfigured_tenant_defaults_free_and_gates() -> None:
    """An unconfigured tenant_config row defaults to FREE — a pro platform
    skill is still gated."""
    store = InMemorySkillStore()
    tenant_id = uuid4()
    await _seed_platform_skill(
        store, name="plat", status=SkillStatus.ACTIVE, required_tier=TenantPlan.PRO
    )
    resolve = make_skill_resolver(
        store=store, tenant_config_service=_StubTenantConfigService(plan=None)
    )
    result = await resolve(tenant_id, "plat", None)
    assert result.reason == "not_entitled"


@pytest.mark.asyncio
async def test_absent_everywhere_returns_not_found() -> None:
    store = InMemorySkillStore()
    resolve = make_skill_resolver(
        store=store, tenant_config_service=_StubTenantConfigService(plan=TenantPlan.FREE)
    )
    result = await resolve(uuid4(), "ghost", None)
    assert result.reason == "not_found"


@pytest.mark.asyncio
async def test_tenant_pinned_resolves_ok() -> None:
    store = InMemorySkillStore()
    tenant_id = uuid4()
    await _seed_tenant_skill(store, tenant_id=tenant_id, name="foo", status=SkillStatus.ACTIVE)
    resolve = make_skill_resolver(
        store=store, tenant_config_service=_StubTenantConfigService(plan=TenantPlan.FREE)
    )
    result = await resolve(tenant_id, "foo", 1)
    assert result.reason is None
    assert result.version is not None
    assert result.version.version == 1


@pytest.mark.asyncio
async def test_tenant_pinned_missing_version_returns_version_not_found() -> None:
    store = InMemorySkillStore()
    tenant_id = uuid4()
    await _seed_tenant_skill(store, tenant_id=tenant_id, name="foo", status=SkillStatus.ACTIVE)
    resolve = make_skill_resolver(
        store=store, tenant_config_service=_StubTenantConfigService(plan=TenantPlan.FREE)
    )
    result = await resolve(tenant_id, "foo", 99)
    assert result.reason == "version_not_found"


@pytest.mark.asyncio
async def test_platform_pinned_entitled_resolves_ok() -> None:
    store = InMemorySkillStore()
    tenant_id = uuid4()
    await _seed_platform_skill(store, name="plat", status=SkillStatus.ACTIVE)
    resolve = make_skill_resolver(
        store=store, tenant_config_service=_StubTenantConfigService(plan=TenantPlan.FREE)
    )
    result = await resolve(tenant_id, "plat", 1)
    assert result.reason is None
    assert result.version is not None
    assert result.version.version == 1


@pytest.mark.asyncio
async def test_platform_pinned_missing_version_returns_version_not_found() -> None:
    store = InMemorySkillStore()
    tenant_id = uuid4()
    await _seed_platform_skill(store, name="plat", status=SkillStatus.ACTIVE)
    resolve = make_skill_resolver(
        store=store, tenant_config_service=_StubTenantConfigService(plan=TenantPlan.FREE)
    )
    result = await resolve(tenant_id, "plat", 7)
    assert result.reason == "version_not_found"


@pytest.mark.asyncio
async def test_platform_present_but_draft_returns_not_active() -> None:
    store = InMemorySkillStore()
    tenant_id = uuid4()
    await _seed_platform_skill(store, name="plat", status=SkillStatus.DRAFT)
    resolve = make_skill_resolver(
        store=store, tenant_config_service=_StubTenantConfigService(plan=TenantPlan.FREE)
    )
    result = await resolve(tenant_id, "plat", None)
    assert result.reason == "not_active"
