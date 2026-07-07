"""Unit tests for :func:`control_plane.auth.tenant_roles.resolve_tenant_roles` — Stream R.

This resolver is what makes ``role_binding`` rows (written by the invite flow /
``POST /v1/role_bindings``) actually load-bearing for tenant RBAC — without it
``auth.rbac`` only ever saw the JWT ``roles`` claim.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from control_plane.auth.tenant_roles import resolve_tenant_roles
from expert_work.persistence.auth import InMemoryRoleBindingStore
from expert_work.protocol import Principal, Role


def _user(*, subject_id: str | None = None, tenant_id=None, roles=()) -> Principal:
    tid = tenant_id if tenant_id is not None else uuid4()
    return Principal(
        subject_id=subject_id or str(uuid4()),
        subject_type="user",
        tenant_id=tid,
        roles=roles,
        allowed_tenants=(tid,),
    )


@pytest.mark.asyncio
async def test_unchanged_when_store_none() -> None:
    p = _user()
    assert await resolve_tenant_roles(p, None) is p


@pytest.mark.asyncio
async def test_unchanged_for_service_account() -> None:
    store = InMemoryRoleBindingStore()
    p = Principal(subject_id=str(uuid4()), subject_type="service_account", tenant_id=uuid4())
    assert await resolve_tenant_roles(p, store) is p


@pytest.mark.asyncio
async def test_unchanged_when_no_bindings() -> None:
    store = InMemoryRoleBindingStore()
    p = _user()
    out = await resolve_tenant_roles(p, store)
    assert out is p  # nothing granted → same instance


@pytest.mark.asyncio
async def test_merges_tenant_role_from_binding() -> None:
    store = InMemoryRoleBindingStore()
    subject = uuid4()
    tenant = uuid4()
    await store.create(
        subject_type="user",
        subject_id=subject,
        tenant_id=tenant,
        role=Role.ADMIN,
        granted_by="bootstrap",
    )
    # JWT carried no roles — the binding is the only source.
    p = _user(subject_id=str(subject), tenant_id=tenant, roles=())
    out = await resolve_tenant_roles(p, store)
    assert "admin" in out.roles


@pytest.mark.asyncio
async def test_only_home_tenant_bindings_apply() -> None:
    store = InMemoryRoleBindingStore()
    subject = uuid4()
    home = uuid4()
    other = uuid4()
    # Binding in a DIFFERENT tenant must not leak into the home-tenant principal.
    await store.create(
        subject_type="user",
        subject_id=subject,
        tenant_id=other,
        role=Role.ADMIN,
        granted_by="x",
    )
    p = _user(subject_id=str(subject), tenant_id=home, roles=())
    out = await resolve_tenant_roles(p, store)
    assert "admin" not in out.roles


@pytest.mark.asyncio
async def test_existing_claim_roles_preserved() -> None:
    store = InMemoryRoleBindingStore()
    subject = uuid4()
    tenant = uuid4()
    await store.create(
        subject_type="user",
        subject_id=subject,
        tenant_id=tenant,
        role=Role.VIEWER,
        granted_by="x",
    )
    p = _user(subject_id=str(subject), tenant_id=tenant, roles=("operator",))
    out = await resolve_tenant_roles(p, store)
    assert "operator" in out.roles  # claim role kept
    assert "viewer" in out.roles  # binding role merged


@pytest.mark.asyncio
async def test_non_uuid_subject_skipped() -> None:
    store = InMemoryRoleBindingStore()
    p = _user(subject_id="not-a-uuid")
    assert await resolve_tenant_roles(p, store) is p
