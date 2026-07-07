"""Unit tests for the platform-scope role binding — Stream N.

DTO validator + ``InMemoryRoleBindingStore`` cover (DB-level CHECK
constraint + SQL store is tested in :mod:`test_sql_auth_store`).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from expert_work.persistence.auth import (
    DuplicateRoleBindingError,
    InMemoryRoleBindingStore,
)
from expert_work.protocol import (
    PLATFORM_SCOPE_ROLES,
    TENANT_SCOPE_ROLES,
    BindingConditions,
    Role,
    RoleBinding,
)

# ---------------------------------------------------------------------------
# Role enum + scope constants
# ---------------------------------------------------------------------------


def test_role_enum_includes_system_admin() -> None:
    assert Role.SYSTEM_ADMIN.value == "system_admin"
    assert Role.SYSTEM_ADMIN in PLATFORM_SCOPE_ROLES
    assert Role.SYSTEM_ADMIN not in TENANT_SCOPE_ROLES


def test_tenant_scope_roles_are_admin_operator_viewer() -> None:
    assert TENANT_SCOPE_ROLES == frozenset({Role.ADMIN, Role.OPERATOR, Role.VIEWER})
    # And the two sets are disjoint.
    assert PLATFORM_SCOPE_ROLES.isdisjoint(TENANT_SCOPE_ROLES)


# ---------------------------------------------------------------------------
# RoleBinding DTO validator — triple invariant
# ---------------------------------------------------------------------------


def _now() -> object:
    # 用 datetime 但本地引入避免与 pydantic ConfigDict 命名冲突
    from datetime import UTC, datetime

    return datetime.now(UTC)


def test_dto_tenant_scope_happy_path() -> None:
    binding = RoleBinding(
        subject_type="user",
        subject_id=uuid4(),
        tenant_id=uuid4(),
        role=Role.ADMIN,
        platform_scope=False,
        granted_by="root",
        granted_at=_now(),  # type: ignore[arg-type]
    )
    assert binding.platform_scope is False
    assert binding.tenant_id is not None


def test_dto_platform_scope_happy_path() -> None:
    binding = RoleBinding(
        subject_type="user",
        subject_id=uuid4(),
        tenant_id=None,
        role=Role.SYSTEM_ADMIN,
        platform_scope=True,
        granted_by="root",
        granted_at=_now(),  # type: ignore[arg-type]
    )
    assert binding.platform_scope is True
    assert binding.tenant_id is None


def test_dto_platform_scope_with_tenant_id_rejected() -> None:
    with pytest.raises(ValidationError, match="platform_scope binding must have tenant_id=None"):
        RoleBinding(
            subject_type="user",
            subject_id=uuid4(),
            tenant_id=uuid4(),  # 错:platform binding 不允许 tenant_id
            role=Role.SYSTEM_ADMIN,
            platform_scope=True,
            granted_by="root",
            granted_at=_now(),  # type: ignore[arg-type]
        )


def test_dto_platform_scope_with_non_system_role_rejected() -> None:
    with pytest.raises(ValidationError, match="platform_scope binding requires role in"):
        RoleBinding(
            subject_type="user",
            subject_id=uuid4(),
            tenant_id=None,
            role=Role.ADMIN,  # 错:platform binding 不允许 ADMIN
            platform_scope=True,
            granted_by="root",
            granted_at=_now(),  # type: ignore[arg-type]
        )


def test_dto_tenant_scope_without_tenant_id_rejected() -> None:
    with pytest.raises(ValidationError, match="tenant-scoped binding requires tenant_id"):
        RoleBinding(
            subject_type="user",
            subject_id=uuid4(),
            tenant_id=None,  # 错:tenant binding 必须有 tenant_id
            role=Role.ADMIN,
            platform_scope=False,
            granted_by="root",
            granted_at=_now(),  # type: ignore[arg-type]
        )


def test_dto_tenant_scope_with_system_admin_role_rejected() -> None:
    with pytest.raises(ValidationError, match="tenant-scoped binding requires role in"):
        RoleBinding(
            subject_type="user",
            subject_id=uuid4(),
            tenant_id=uuid4(),
            role=Role.SYSTEM_ADMIN,  # 错:tenant binding 不允许 SYSTEM_ADMIN
            platform_scope=False,
            granted_by="root",
            granted_at=_now(),  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# InMemoryRoleBindingStore — platform-scope CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inmem_create_platform_scope_binding() -> None:
    store = InMemoryRoleBindingStore()
    subject = uuid4()
    binding = await store.create(
        subject_type="user",
        subject_id=subject,
        tenant_id=None,
        role=Role.SYSTEM_ADMIN,
        platform_scope=True,
        granted_by="root",
    )
    assert binding.platform_scope is True
    assert binding.tenant_id is None
    assert binding.role is Role.SYSTEM_ADMIN


@pytest.mark.asyncio
async def test_inmem_tenant_binding_conditions_round_trip() -> None:
    """Stream 8.5 — a tenant-scope binding persists + reloads its ABAC conditions."""
    store = InMemoryRoleBindingStore()
    subject = uuid4()
    tenant = uuid4()
    conditions = BindingConditions(
        resource_ids=("agent-foo",), labels={"team": "支持"}, owner_only=True
    )
    created = await store.create(
        subject_type="user",
        subject_id=subject,
        tenant_id=tenant,
        role=Role.OPERATOR,
        granted_by="admin",
        conditions=conditions,
    )
    assert created.conditions == conditions
    assert created.has_conditions is True
    rows = await store.list_for_subject(subject_type="user", subject_id=subject, tenant_id=tenant)
    assert rows[0].conditions == conditions


@pytest.mark.asyncio
async def test_inmem_unconditioned_binding_defaults_none() -> None:
    store = InMemoryRoleBindingStore()
    subject = uuid4()
    tenant = uuid4()
    created = await store.create(
        subject_type="user",
        subject_id=subject,
        tenant_id=tenant,
        role=Role.OPERATOR,
        granted_by="admin",
    )
    assert created.conditions is None
    assert created.has_conditions is False


@pytest.mark.asyncio
async def test_inmem_platform_scope_one_per_subject() -> None:
    store = InMemoryRoleBindingStore()
    subject = uuid4()
    await store.create(
        subject_type="user",
        subject_id=subject,
        tenant_id=None,
        role=Role.SYSTEM_ADMIN,
        platform_scope=True,
        granted_by="root",
    )
    with pytest.raises(DuplicateRoleBindingError):
        await store.create(
            subject_type="user",
            subject_id=subject,
            tenant_id=None,
            role=Role.SYSTEM_ADMIN,
            platform_scope=True,
            granted_by="root",
        )


@pytest.mark.asyncio
async def test_inmem_subject_can_have_both_tenant_and_platform_bindings() -> None:
    """一个用户可以同时是某租户的 ADMIN + 平台域 SYSTEM_ADMIN(虽然实战很少见)。"""
    store = InMemoryRoleBindingStore()
    subject, tenant = uuid4(), uuid4()
    tenant_b = await store.create(
        subject_type="user",
        subject_id=subject,
        tenant_id=tenant,
        role=Role.ADMIN,
        granted_by="root",
    )
    platform_b = await store.create(
        subject_type="user",
        subject_id=subject,
        tenant_id=None,
        role=Role.SYSTEM_ADMIN,
        platform_scope=True,
        granted_by="root",
    )
    assert tenant_b.platform_scope is False
    assert platform_b.platform_scope is True


@pytest.mark.asyncio
async def test_inmem_list_platform_scope_returns_only_platform_bindings() -> None:
    store = InMemoryRoleBindingStore()
    # Two tenant bindings + one platform binding.
    await store.create(
        subject_type="user",
        subject_id=uuid4(),
        tenant_id=uuid4(),
        role=Role.ADMIN,
        granted_by="root",
    )
    await store.create(
        subject_type="user",
        subject_id=uuid4(),
        tenant_id=uuid4(),
        role=Role.VIEWER,
        granted_by="root",
    )
    platform = await store.create(
        subject_type="user",
        subject_id=uuid4(),
        tenant_id=None,
        role=Role.SYSTEM_ADMIN,
        platform_scope=True,
        granted_by="root",
    )
    listed = await store.list_platform_scope()
    assert [b.id for b in listed] == [platform.id]


@pytest.mark.asyncio
async def test_inmem_get_platform_admin_for_subject() -> None:
    store = InMemoryRoleBindingStore()
    sys_admin_subject = uuid4()
    not_admin_subject = uuid4()

    await store.create(
        subject_type="user",
        subject_id=sys_admin_subject,
        tenant_id=None,
        role=Role.SYSTEM_ADMIN,
        platform_scope=True,
        granted_by="root",
    )
    # 干扰项:另一个 user 是某租户的 ADMIN,不应被认成 system_admin。
    await store.create(
        subject_type="user",
        subject_id=not_admin_subject,
        tenant_id=uuid4(),
        role=Role.ADMIN,
        granted_by="root",
    )

    hit = await store.get_platform_admin_for_subject(
        subject_type="user", subject_id=sys_admin_subject
    )
    assert hit is not None
    assert hit.role is Role.SYSTEM_ADMIN

    miss = await store.get_platform_admin_for_subject(
        subject_type="user", subject_id=not_admin_subject
    )
    assert miss is None


@pytest.mark.asyncio
async def test_inmem_list_for_tenant_excludes_platform_bindings() -> None:
    store = InMemoryRoleBindingStore()
    tenant = uuid4()
    tenant_b = await store.create(
        subject_type="user",
        subject_id=uuid4(),
        tenant_id=tenant,
        role=Role.ADMIN,
        granted_by="root",
    )
    await store.create(
        subject_type="user",
        subject_id=uuid4(),
        tenant_id=None,
        role=Role.SYSTEM_ADMIN,
        platform_scope=True,
        granted_by="root",
    )
    listed = await store.list_for_tenant(tenant_id=tenant)
    assert [b.id for b in listed] == [tenant_b.id]


@pytest.mark.asyncio
async def test_inmem_delete_platform_scope_with_tenant_none() -> None:
    store = InMemoryRoleBindingStore()
    binding = await store.create(
        subject_type="user",
        subject_id=uuid4(),
        tenant_id=None,
        role=Role.SYSTEM_ADMIN,
        platform_scope=True,
        granted_by="root",
    )
    ok = await store.delete(tenant_id=None, role_binding_id=binding.id)
    assert ok is True
    again = await store.delete(tenant_id=None, role_binding_id=binding.id)
    assert again is False


@pytest.mark.asyncio
async def test_inmem_delete_wrong_scope_returns_false() -> None:
    """Tenant-scoped delete cannot remove a platform binding (and vice versa)."""
    store = InMemoryRoleBindingStore()
    tenant = uuid4()
    platform = await store.create(
        subject_type="user",
        subject_id=uuid4(),
        tenant_id=None,
        role=Role.SYSTEM_ADMIN,
        platform_scope=True,
        granted_by="root",
    )
    tenant_b = await store.create(
        subject_type="user",
        subject_id=uuid4(),
        tenant_id=tenant,
        role=Role.ADMIN,
        granted_by="root",
    )

    # Tenant-scoped delete called against the platform binding -> False.
    ok = await store.delete(tenant_id=tenant, role_binding_id=platform.id)
    assert ok is False
    # Platform-scoped delete called against the tenant binding -> False.
    ok = await store.delete(tenant_id=None, role_binding_id=tenant_b.id)
    assert ok is False


@pytest.mark.asyncio
async def test_inmem_list_for_subject_returns_all_scopes() -> None:
    """list_for_subject with tenant_id=None returns tenant + platform bindings together."""
    store = InMemoryRoleBindingStore()
    subject, tenant = uuid4(), uuid4()
    await store.create(
        subject_type="user",
        subject_id=subject,
        tenant_id=tenant,
        role=Role.ADMIN,
        granted_by="root",
    )
    await store.create(
        subject_type="user",
        subject_id=subject,
        tenant_id=None,
        role=Role.SYSTEM_ADMIN,
        platform_scope=True,
        granted_by="root",
    )
    rows = await store.list_for_subject(subject_type="user", subject_id=subject)
    assert len(rows) == 2
    roles = {r.role for r in rows}
    assert roles == {Role.ADMIN, Role.SYSTEM_ADMIN}
