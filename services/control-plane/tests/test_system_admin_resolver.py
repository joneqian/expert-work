"""Unit tests for :func:`control_plane.auth.system_admin.resolve_system_admin` — Stream N."""

from __future__ import annotations

from uuid import uuid4

import pytest

from control_plane.auth.system_admin import resolve_system_admin
from helix_agent.persistence.auth import InMemoryRoleBindingStore
from helix_agent.protocol import Principal, Role


def _make_user_principal(*, subject_id: str | None = None, tenant_id: object = None) -> Principal:
    return Principal(
        subject_id=subject_id or str(uuid4()),
        subject_type="user",
        tenant_id=tenant_id if tenant_id is not None else uuid4(),
        roles=("admin",),
        allowed_tenants=(tenant_id or uuid4(),) if tenant_id is not None else (uuid4(),),
    )


@pytest.mark.asyncio
async def test_returns_unchanged_when_store_is_none() -> None:
    p = _make_user_principal()
    out = await resolve_system_admin(p, None)
    assert out is p  # same instance — no augmentation


@pytest.mark.asyncio
async def test_returns_unchanged_when_subject_type_not_user() -> None:
    """Service-account subjects skip the platform-admin lookup (M0 restriction)."""
    store = InMemoryRoleBindingStore()
    p = Principal(
        subject_id=str(uuid4()),
        subject_type="service_account",
        tenant_id=uuid4(),
    )
    out = await resolve_system_admin(p, store)
    assert out.is_system_admin is False


@pytest.mark.asyncio
async def test_returns_unchanged_when_subject_id_not_uuid_shaped() -> None:
    """Non-UUID subject ids (e.g. ``dev-user`` in test fixtures) cannot match
    the UUID-typed ``role_binding.subject_id`` column — skip the lookup."""
    store = InMemoryRoleBindingStore()
    p = _make_user_principal(subject_id="dev-user")  # not a UUID string
    out = await resolve_system_admin(p, store)
    assert out.is_system_admin is False


@pytest.mark.asyncio
async def test_returns_unchanged_when_no_platform_binding() -> None:
    """A user who has only tenant-scope bindings is NOT a system admin."""
    store = InMemoryRoleBindingStore()
    user_id = uuid4()
    # Tenant-scope binding for this user — should NOT make them system_admin.
    await store.create(
        subject_type="user",
        subject_id=user_id,
        tenant_id=uuid4(),
        role=Role.ADMIN,
        granted_by="root",
    )
    p = _make_user_principal(subject_id=str(user_id))
    out = await resolve_system_admin(p, store)
    assert out.is_system_admin is False
    # allowed_tenants is left as the original tuple (not "*").
    assert out.allowed_tenants != "*"


@pytest.mark.asyncio
async def test_augments_principal_when_platform_binding_found() -> None:
    store = InMemoryRoleBindingStore()
    user_id = uuid4()
    await store.create(
        subject_type="user",
        subject_id=user_id,
        tenant_id=None,
        role=Role.SYSTEM_ADMIN,
        platform_scope=True,
        granted_by="root",
    )
    home_tenant = uuid4()
    p = _make_user_principal(subject_id=str(user_id), tenant_id=home_tenant)
    out = await resolve_system_admin(p, store)
    assert out.is_system_admin is True
    assert out.allowed_tenants == "*"
    # Home tenant preserved — explicit per-request scope is N.3's job.
    assert out.tenant_id == home_tenant


@pytest.mark.asyncio
async def test_platform_binding_for_different_user_does_not_promote() -> None:
    """A platform binding on user X does not make user Y a system admin."""
    store = InMemoryRoleBindingStore()
    sys_admin = uuid4()
    other_user = uuid4()
    await store.create(
        subject_type="user",
        subject_id=sys_admin,
        tenant_id=None,
        role=Role.SYSTEM_ADMIN,
        platform_scope=True,
        granted_by="root",
    )
    p = _make_user_principal(subject_id=str(other_user))
    out = await resolve_system_admin(p, store)
    assert out.is_system_admin is False
