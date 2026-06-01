"""Unit tests for InMemoryTenantMemberStore — Stream R contract.

Same behaviour the SQL store must honour (state machine, idempotent key,
cross-tenant isolation, Keycloak-id reverse lookup).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from helix_agent.persistence import DuplicateMemberError, InMemoryTenantMemberStore


async def _invite(store: InMemoryTenantMemberStore, *, tenant, email="a@co.com", role="viewer"):
    return await store.create(tenant_id=tenant, email=email, role=role, invited_by="admin-sub")


@pytest.mark.asyncio
async def test_create_starts_invited() -> None:
    store = InMemoryTenantMemberStore()
    tenant = uuid4()
    m = await _invite(store, tenant=tenant)
    assert m.status == "invited"
    assert m.keycloak_user_id is None
    assert m.subject_id is None
    assert m.role == "viewer"


@pytest.mark.asyncio
async def test_active_email_unique_per_tenant() -> None:
    store = InMemoryTenantMemberStore()
    tenant = uuid4()
    await _invite(store, tenant=tenant, email="dup@co.com")
    with pytest.raises(DuplicateMemberError):
        await _invite(store, tenant=tenant, email="DUP@co.com")  # case-insensitive


@pytest.mark.asyncio
async def test_revoked_email_can_be_reinvited() -> None:
    store = InMemoryTenantMemberStore()
    tenant = uuid4()
    m = await _invite(store, tenant=tenant, email="x@co.com")
    assert await store.transition(
        member_id=m.id, tenant_id=tenant, to="revoked", now=datetime.now(UTC)
    )
    # No longer an active invite — re-invite must succeed.
    m2 = await _invite(store, tenant=tenant, email="x@co.com")
    assert m2.id != m.id
    assert m2.status == "invited"


@pytest.mark.asyncio
async def test_same_email_different_tenants_ok() -> None:
    store = InMemoryTenantMemberStore()
    t1, t2 = uuid4(), uuid4()
    await _invite(store, tenant=t1, email="shared@co.com")
    # Different tenant — not a conflict.
    await _invite(store, tenant=t2, email="shared@co.com")


@pytest.mark.asyncio
async def test_get_is_tenant_scoped() -> None:
    store = InMemoryTenantMemberStore()
    t1, t2 = uuid4(), uuid4()
    m = await _invite(store, tenant=t1)
    assert await store.get(tenant_id=t1, member_id=m.id) is not None
    # Cross-tenant read never reveals existence.
    assert await store.get(tenant_id=t2, member_id=m.id) is None


@pytest.mark.asyncio
async def test_transition_invited_to_active_backfills() -> None:
    store = InMemoryTenantMemberStore()
    tenant = uuid4()
    m = await _invite(store, tenant=tenant)
    await store.set_keycloak_user_id(member_id=m.id, keycloak_user_id="kc-1")
    user_id = uuid4()
    now = datetime.now(UTC)
    assert await store.transition(
        member_id=m.id, tenant_id=tenant, to="active", now=now, subject_id=user_id
    )
    got = await store.get(tenant_id=tenant, member_id=m.id)
    assert got is not None
    assert got.status == "active"
    assert got.subject_id == user_id
    assert got.activated_at == now


@pytest.mark.asyncio
async def test_illegal_transition_returns_false() -> None:
    store = InMemoryTenantMemberStore()
    tenant = uuid4()
    m = await _invite(store, tenant=tenant)
    # invited cannot jump straight to suspended (only active→suspended is legal).
    assert not await store.transition(
        member_id=m.id, tenant_id=tenant, to="suspended", now=datetime.now(UTC)
    )
    got = await store.get(tenant_id=tenant, member_id=m.id)
    assert got is not None and got.status == "invited"


@pytest.mark.asyncio
async def test_transition_idempotent_second_call_false() -> None:
    store = InMemoryTenantMemberStore()
    tenant = uuid4()
    m = await _invite(store, tenant=tenant)
    await store.set_keycloak_user_id(member_id=m.id, keycloak_user_id="kc-1")
    now = datetime.now(UTC)
    assert await store.transition(
        member_id=m.id, tenant_id=tenant, to="active", now=now, subject_id=uuid4()
    )
    # Already active — invited is no longer a legal predecessor.
    assert not await store.transition(
        member_id=m.id, tenant_id=tenant, to="active", now=now, subject_id=uuid4()
    )


@pytest.mark.asyncio
async def test_active_then_suspended() -> None:
    store = InMemoryTenantMemberStore()
    tenant = uuid4()
    m = await _invite(store, tenant=tenant)
    await store.set_keycloak_user_id(member_id=m.id, keycloak_user_id="kc-1")
    now = datetime.now(UTC)
    await store.transition(
        member_id=m.id, tenant_id=tenant, to="active", now=now, subject_id=uuid4()
    )
    assert await store.transition(member_id=m.id, tenant_id=tenant, to="suspended", now=now)
    got = await store.get(tenant_id=tenant, member_id=m.id)
    assert got is not None and got.status == "suspended"


@pytest.mark.asyncio
async def test_get_by_keycloak_user_id_crosses_tenants() -> None:
    store = InMemoryTenantMemberStore()
    tenant = uuid4()
    m = await _invite(store, tenant=tenant)
    await store.set_keycloak_user_id(member_id=m.id, keycloak_user_id="kc-xyz")
    found = await store.get_by_keycloak_user_id(keycloak_user_id="kc-xyz")
    assert found is not None and found.id == m.id
    assert await store.get_by_keycloak_user_id(keycloak_user_id="nope") is None


@pytest.mark.asyncio
async def test_list_for_tenant_filters_and_scopes() -> None:
    store = InMemoryTenantMemberStore()
    t1, t2 = uuid4(), uuid4()
    a = await _invite(store, tenant=t1, email="a@co.com")
    await _invite(store, tenant=t1, email="b@co.com")
    await _invite(store, tenant=t2, email="c@co.com")
    await store.set_keycloak_user_id(member_id=a.id, keycloak_user_id="kc-a")
    await store.transition(
        member_id=a.id, tenant_id=t1, to="active", now=datetime.now(UTC), subject_id=uuid4()
    )
    all_t1 = await store.list_for_tenant(tenant_id=t1)
    assert len(all_t1) == 2  # t2's member excluded
    invited_only = await store.list_for_tenant(tenant_id=t1, status="invited")
    assert len(invited_only) == 1
    assert invited_only[0].email == "b@co.com"
