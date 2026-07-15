"""Unit tests for InMemoryTenantUserStore — Stream J.14 contract."""

from __future__ import annotations

from uuid import uuid4

import pytest

from expert_work.persistence import InMemoryTenantUserStore


@pytest.mark.asyncio
async def test_resolve_creates_then_returns_same_row() -> None:
    store = InMemoryTenantUserStore()
    tenant_id = uuid4()

    first = await store.resolve(tenant_id=tenant_id, subject_type="user", subject_id="oidc-sub-1")
    assert first.tenant_id == tenant_id
    assert first.subject_id == "oidc-sub-1"
    assert first.created_at is not None

    again = await store.resolve(tenant_id=tenant_id, subject_type="user", subject_id="oidc-sub-1")
    # Idempotent: same identity resolves to the same surrogate id.
    assert again.id == first.id
    assert again.created_at == first.created_at


@pytest.mark.asyncio
async def test_resolve_bumps_last_active_at() -> None:
    store = InMemoryTenantUserStore()
    tenant_id = uuid4()
    first = await store.resolve(tenant_id=tenant_id, subject_type="user", subject_id="u")
    again = await store.resolve(tenant_id=tenant_id, subject_type="user", subject_id="u")
    assert again.last_active_at is not None
    assert first.last_active_at is not None
    assert again.last_active_at >= first.last_active_at


@pytest.mark.asyncio
async def test_resolve_display_name_set_then_preserved() -> None:
    store = InMemoryTenantUserStore()
    tenant_id = uuid4()

    await store.resolve(
        tenant_id=tenant_id, subject_type="user", subject_id="u", display_name="Ada"
    )
    # A later resolve with no display_name must not clobber the stored value.
    preserved = await store.resolve(tenant_id=tenant_id, subject_type="user", subject_id="u")
    assert preserved.display_name == "Ada"
    # An explicit new value overwrites.
    renamed = await store.resolve(
        tenant_id=tenant_id, subject_type="user", subject_id="u", display_name="Ada L."
    )
    assert renamed.display_name == "Ada L."


@pytest.mark.asyncio
async def test_resolve_distinguishes_identity_axes() -> None:
    store = InMemoryTenantUserStore()
    tenant_a, tenant_b = uuid4(), uuid4()

    u1 = await store.resolve(tenant_id=tenant_a, subject_type="user", subject_id="x")
    # Different tenant → different user.
    u2 = await store.resolve(tenant_id=tenant_b, subject_type="user", subject_id="x")
    # Different subject_type → different user.
    u3 = await store.resolve(tenant_id=tenant_a, subject_type="service_account", subject_id="x")
    # Different subject_id → different user.
    u4 = await store.resolve(tenant_id=tenant_a, subject_type="user", subject_id="y")

    assert len({u1.id, u2.id, u3.id, u4.id}) == 4


@pytest.mark.asyncio
async def test_get_filters_by_tenant() -> None:
    store = InMemoryTenantUserStore()
    owner, other = uuid4(), uuid4()
    user = await store.resolve(tenant_id=owner, subject_type="user", subject_id="u")

    assert await store.get(user.id, tenant_id=owner) is not None
    assert await store.get(user.id, tenant_id=other) is None
    assert await store.get(uuid4(), tenant_id=owner) is None


@pytest.mark.asyncio
async def test_get_many_batches_and_filters_by_tenant() -> None:
    store = InMemoryTenantUserStore()
    owner, other = uuid4(), uuid4()
    a = await store.resolve(tenant_id=owner, subject_type="user", subject_id="a")
    b = await store.resolve(tenant_id=owner, subject_type="user", subject_id="b")
    foreign = await store.resolve(tenant_id=other, subject_type="user", subject_id="c")

    got = await store.get_many([a.id, b.id, foreign.id, uuid4()], tenant_id=owner)
    # Foreign-tenant + unknown ids are absent, not errors.
    assert set(got) == {a.id, b.id}
    assert got[a.id].subject_id == "a"

    assert await store.get_many([], tenant_id=owner) == {}


@pytest.mark.asyncio
async def test_list_by_tenant_filters_type_and_tenant() -> None:
    store = InMemoryTenantUserStore()
    owner, other = uuid4(), uuid4()
    u1 = await store.resolve(tenant_id=owner, subject_type="user", subject_id="u1")
    u2 = await store.resolve(tenant_id=owner, subject_type="user", subject_id="u2")
    await store.resolve(tenant_id=owner, subject_type="service_account", subject_id="svc")
    await store.resolve(tenant_id=other, subject_type="user", subject_id="foreign")

    # subject_type="user" excludes the service account; the other tenant's
    # user is never returned.
    rows = await store.list_by_tenant(owner, subject_type="user")
    assert {r.id for r in rows} == {u1.id, u2.id}

    # No subject_type filter → both principal kinds in this tenant (3 rows).
    assert len(await store.list_by_tenant(owner)) == 3


@pytest.mark.asyncio
async def test_list_by_tenant_orders_by_last_active_desc() -> None:
    store = InMemoryTenantUserStore()
    tenant = uuid4()
    first = await store.resolve(tenant_id=tenant, subject_type="user", subject_id="first")
    await store.resolve(tenant_id=tenant, subject_type="user", subject_id="second")
    # Re-resolving ``first`` bumps its last_active_at to newest.
    await store.resolve(tenant_id=tenant, subject_type="user", subject_id="first")

    rows = await store.list_by_tenant(tenant, subject_type="user")
    assert rows[0].id == first.id  # most-recently-active first


@pytest.mark.asyncio
async def test_list_by_tenant_paginates() -> None:
    store = InMemoryTenantUserStore()
    tenant = uuid4()
    for i in range(5):
        await store.resolve(tenant_id=tenant, subject_type="user", subject_id=f"u{i}")

    page = await store.list_by_tenant(tenant, subject_type="user", limit=2, offset=2)
    assert len(page) == 2
    full = await store.list_by_tenant(tenant, subject_type="user")
    assert [r.id for r in page] == [r.id for r in full[2:4]]
