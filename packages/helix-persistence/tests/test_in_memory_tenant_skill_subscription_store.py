"""Unit tests for the in-memory tenant skill subscription store."""

from __future__ import annotations

from uuid import uuid4

import pytest

from helix_agent.persistence.tenant_skill_subscription import (
    InMemoryTenantSkillSubscriptionStore,
    TenantSkillSubscriptionNotFoundError,
)


@pytest.mark.asyncio
async def test_subscribe_then_list_round_trip() -> None:
    store = InMemoryTenantSkillSubscriptionStore()
    tid, sid = uuid4(), uuid4()
    rec = await store.subscribe(tenant_id=tid, platform_skill_id=sid, created_by="admin@acme")
    assert rec.tenant_id == tid
    assert rec.platform_skill_id == sid
    assert rec.enabled is True
    rows = await store.list_for_tenant(tenant_id=tid)
    assert [r.platform_skill_id for r in rows] == [sid]


@pytest.mark.asyncio
async def test_subscribe_idempotent_reenables_soft_cancelled() -> None:
    store = InMemoryTenantSkillSubscriptionStore()
    tid, sid = uuid4(), uuid4()
    first = await store.subscribe(tenant_id=tid, platform_skill_id=sid, created_by="a")
    # soft cancel
    await store.set_enabled(tenant_id=tid, platform_skill_id=sid, enabled=False)
    # re-subscribe flips enabled back on, same row identity / created_at
    again = await store.subscribe(tenant_id=tid, platform_skill_id=sid, created_by="b")
    assert again.enabled is True
    assert again.id == first.id
    assert again.created_at == first.created_at
    assert again.created_by == first.created_by  # original creator preserved
    assert len(await store.list_for_tenant(tenant_id=tid)) == 1


@pytest.mark.asyncio
async def test_set_enabled_soft_stop_keeps_row() -> None:
    store = InMemoryTenantSkillSubscriptionStore()
    tid, sid = uuid4(), uuid4()
    await store.subscribe(tenant_id=tid, platform_skill_id=sid, created_by="a")
    rec = await store.set_enabled(tenant_id=tid, platform_skill_id=sid, enabled=False)
    assert rec.enabled is False
    assert len(await store.list_for_tenant(tenant_id=tid)) == 1  # not deleted


@pytest.mark.asyncio
async def test_set_enabled_absent_raises() -> None:
    store = InMemoryTenantSkillSubscriptionStore()
    with pytest.raises(TenantSkillSubscriptionNotFoundError):
        await store.set_enabled(tenant_id=uuid4(), platform_skill_id=uuid4(), enabled=False)


@pytest.mark.asyncio
async def test_is_subscribed_reflects_enabled_flag() -> None:
    store = InMemoryTenantSkillSubscriptionStore()
    tid, sid = uuid4(), uuid4()
    assert await store.is_subscribed(tenant_id=tid, platform_skill_id=sid) is False
    await store.subscribe(tenant_id=tid, platform_skill_id=sid, created_by="a")
    assert await store.is_subscribed(tenant_id=tid, platform_skill_id=sid) is True
    await store.set_enabled(tenant_id=tid, platform_skill_id=sid, enabled=False)
    assert await store.is_subscribed(tenant_id=tid, platform_skill_id=sid) is False


@pytest.mark.asyncio
async def test_unsubscribe_hard_deletes() -> None:
    store = InMemoryTenantSkillSubscriptionStore()
    tid, sid = uuid4(), uuid4()
    await store.subscribe(tenant_id=tid, platform_skill_id=sid, created_by="a")
    await store.unsubscribe(tenant_id=tid, platform_skill_id=sid)
    assert await store.list_for_tenant(tenant_id=tid) == []


@pytest.mark.asyncio
async def test_unsubscribe_absent_raises() -> None:
    store = InMemoryTenantSkillSubscriptionStore()
    with pytest.raises(TenantSkillSubscriptionNotFoundError):
        await store.unsubscribe(tenant_id=uuid4(), platform_skill_id=uuid4())


@pytest.mark.asyncio
async def test_list_scoped_to_tenant() -> None:
    store = InMemoryTenantSkillSubscriptionStore()
    a, b = uuid4(), uuid4()
    sid = uuid4()
    await store.subscribe(tenant_id=a, platform_skill_id=sid, created_by="a")
    await store.subscribe(tenant_id=b, platform_skill_id=sid, created_by="b")
    assert len(await store.list_for_tenant(tenant_id=a)) == 1
    assert len(await store.list_for_tenant(tenant_id=b)) == 1


@pytest.mark.asyncio
async def test_list_empty_when_no_rows() -> None:
    store = InMemoryTenantSkillSubscriptionStore()
    assert await store.list_for_tenant(tenant_id=uuid4()) == []
