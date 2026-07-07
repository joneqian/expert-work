"""Unit tests for :class:`InMemoryTenantConfigStore.create` — Stream P (P-1/P-3)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from expert_work.persistence.tenant_config.base import (
    TenantConfigAlreadyExistsError,
    TenantConfigNotFoundError,
)
from expert_work.persistence.tenant_config.memory import InMemoryTenantConfigStore
from expert_work.protocol import TenantConfigPatch, TenantPlan


@pytest.mark.asyncio
async def test_create_writes_first_row_with_defaults() -> None:
    store = InMemoryTenantConfigStore()
    tenant_id = uuid4()

    record = await store.create(
        tenant_id=tenant_id,
        display_name="Acme Inc",
        actor_id="bootstrap",
    )

    assert record.tenant_id == tenant_id
    assert record.display_name == "Acme Inc"
    assert record.plan is TenantPlan.FREE  # default
    assert record.updated_by == "bootstrap"
    # Every other field falls back to its baseline default.
    assert record.model_credentials_ref == {}
    assert record.credentials_mode == "platform"
    # And the row is now readable via get().
    assert (await store.get(tenant_id=tenant_id)) == record


@pytest.mark.asyncio
async def test_create_honours_explicit_plan() -> None:
    store = InMemoryTenantConfigStore()
    tenant_id = uuid4()

    record = await store.create(
        tenant_id=tenant_id,
        display_name="Pro Co",
        plan=TenantPlan.PRO,
        actor_id="admin",
    )

    assert record.plan is TenantPlan.PRO


@pytest.mark.asyncio
async def test_create_rejects_existing_tenant() -> None:
    store = InMemoryTenantConfigStore()
    tenant_id = uuid4()
    await store.create(tenant_id=tenant_id, display_name="First", actor_id="a")

    with pytest.raises(TenantConfigAlreadyExistsError) as exc:
        await store.create(tenant_id=tenant_id, display_name="Second", actor_id="b")
    assert exc.value.tenant_id == tenant_id
    # The original row is untouched (no silent overwrite, unlike upsert).
    fetched = await store.get(tenant_id=tenant_id)
    assert fetched is not None
    assert fetched.display_name == "First"


@pytest.mark.asyncio
async def test_created_tenant_status_is_active() -> None:
    store = InMemoryTenantConfigStore()
    tenant_id = uuid4()
    record = await store.create(tenant_id=tenant_id, display_name="Acme", actor_id="a")
    assert record.status == "active"


@pytest.mark.asyncio
async def test_set_status_suspends_tenant() -> None:
    store = InMemoryTenantConfigStore()
    tenant_id = uuid4()
    await store.create(tenant_id=tenant_id, display_name="Acme", actor_id="a")

    updated = await store.set_status(tenant_id=tenant_id, status="suspended", actor_id="ops")

    assert updated.status == "suspended"
    assert updated.updated_by == "ops"
    fetched = await store.get(tenant_id=tenant_id)
    assert fetched is not None
    assert fetched.status == "suspended"


@pytest.mark.asyncio
async def test_set_status_unknown_tenant_raises() -> None:
    store = InMemoryTenantConfigStore()
    missing = uuid4()
    with pytest.raises(TenantConfigNotFoundError) as exc:
        await store.set_status(tenant_id=missing, status="suspended", actor_id="ops")
    assert exc.value.tenant_id == missing


@pytest.mark.asyncio
async def test_list_all_empty() -> None:
    store = InMemoryTenantConfigStore()
    assert await store.list_all() == []


@pytest.mark.asyncio
async def test_list_all_returns_created_tenants() -> None:
    store = InMemoryTenantConfigStore()
    a = await store.create(tenant_id=uuid4(), display_name="Acme", actor_id="sys")
    b = await store.create(tenant_id=uuid4(), display_name="Beta", actor_id="sys")
    got = {r.tenant_id for r in await store.list_all()}
    assert got == {a.tenant_id, b.tenant_id}


@pytest.mark.asyncio
async def test_list_all_paginates() -> None:
    store = InMemoryTenantConfigStore()
    for i in range(3):
        await store.create(tenant_id=uuid4(), display_name=f"T{i}", actor_id="sys")
    assert len(await store.list_all(limit=2, offset=0)) == 2
    assert len(await store.list_all(limit=2, offset=2)) == 1


@pytest.mark.asyncio
async def test_allow_custom_mcp_servers_defaults_true() -> None:
    store = InMemoryTenantConfigStore()
    record = await store.create(tenant_id=uuid4(), display_name="Acme", actor_id="sys")
    assert record.allow_custom_mcp_servers is True


@pytest.mark.asyncio
async def test_allow_custom_mcp_servers_patch_round_trips() -> None:
    store = InMemoryTenantConfigStore()
    tid = uuid4()
    await store.create(tenant_id=tid, display_name="Acme", actor_id="sys")
    updated = await store.upsert(
        tenant_id=tid,
        patch=TenantConfigPatch(allow_custom_mcp_servers=False),
        actor_id="ops",
    )
    assert updated.allow_custom_mcp_servers is False
    got = await store.get(tenant_id=tid)
    assert got is not None and got.allow_custom_mcp_servers is False


@pytest.mark.asyncio
async def test_allow_custom_mcp_servers_set_false_on_first_upsert() -> None:
    store = InMemoryTenantConfigStore()
    tid = uuid4()
    record = await store.upsert(
        tenant_id=tid,
        patch=TenantConfigPatch(display_name="Acme", allow_custom_mcp_servers=False),
        actor_id="sys",
    )
    assert record.allow_custom_mcp_servers is False


@pytest.mark.asyncio
async def test_skill_evolution_judge_sample_pct_defaults_and_round_trips() -> None:
    """SE-16 (SE-A45) — implicit judge sample rate defaults 5, patches through."""
    store = InMemoryTenantConfigStore()
    tid = uuid4()
    created = await store.create(tenant_id=tid, display_name="Acme", actor_id="sys")
    assert created.skill_evolution_judge_sample_pct == 5

    updated = await store.upsert(
        tenant_id=tid,
        patch=TenantConfigPatch(skill_evolution_judge_sample_pct=25),
        actor_id="ops",
    )
    assert updated.skill_evolution_judge_sample_pct == 25
    got = await store.get(tenant_id=tid)
    assert got is not None and got.skill_evolution_judge_sample_pct == 25
