"""Unit tests for :mod:`control_plane.auth.rbac`."""

from __future__ import annotations

from uuid import UUID

import pytest

from control_plane.auth.rbac import collect_roles_for_audit, is_admin, is_allowed
from helix_agent.protocol import Principal

_TENANT = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _user(roles: tuple[str, ...]) -> Principal:
    return Principal(
        subject_id="u-1",
        subject_type="user",
        tenant_id=_TENANT,
        roles=roles,
        scopes=(),
        auth_method="jwt",
    )


def _service() -> Principal:
    return Principal(
        subject_id="orchestrator",
        subject_type="service",
        tenant_id=_TENANT,
        roles=("service",),
        scopes=(),
        auth_method="mtls",
    )


def _service_account(scopes: tuple[str, ...]) -> Principal:
    return Principal(
        subject_id="sa-1",
        subject_type="service_account",
        tenant_id=_TENANT,
        roles=("service_account",),
        scopes=scopes,
        auth_method="api_key",
    )


# ---------------------------------------------------------------------------
# JWT user roles
# ---------------------------------------------------------------------------


def test_admin_grants_destructive_actions() -> None:
    p = _user(("admin",))
    assert is_allowed(p, resource="service_account", action="delete")
    assert is_allowed(p, resource="role_binding", action="write")
    assert is_allowed(p, resource="api_key", action="write")


def test_operator_cannot_admin_service_accounts() -> None:
    p = _user(("operator",))
    assert is_allowed(p, resource="manifest", action="write")
    assert not is_allowed(p, resource="service_account", action="write")
    assert not is_allowed(p, resource="role_binding", action="write")


def test_viewer_is_read_only() -> None:
    p = _user(("viewer",))
    assert is_allowed(p, resource="manifest", action="read")
    assert not is_allowed(p, resource="manifest", action="write")
    assert not is_allowed(p, resource="api_key", action="read")


def test_no_role_denies_everything() -> None:
    p = _user(())
    assert not is_allowed(p, resource="manifest", action="read")
    assert not is_allowed(p, resource="api_key", action="write")


def test_unknown_role_silently_dropped() -> None:
    p = _user(("god", "viewer"))
    assert is_allowed(p, resource="manifest", action="read")
    assert not is_allowed(p, resource="manifest", action="write")


# ---------------------------------------------------------------------------
# mTLS service principals
# ---------------------------------------------------------------------------


def test_mtls_service_grants_operator_read_write_on_business_resources() -> None:
    p = _service()
    assert is_allowed(p, resource="quota", action="read")
    assert is_allowed(p, resource="manifest", action="write")
    # Admin-only resources stay locked.
    assert not is_allowed(p, resource="role_binding", action="write")
    assert not is_allowed(p, resource="user", action="write")


# ---------------------------------------------------------------------------
# API key scopes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scope,resource,action,expected",
    [
        ("admin", "service_account", "delete", True),
        ("admin", "manifest", "write", True),
        ("write", "manifest", "write", True),
        ("write", "service_account", "delete", False),
        ("read", "manifest", "read", True),
        ("read", "manifest", "write", False),
    ],
)
def test_service_account_scope_to_role_mapping(
    scope: str,
    resource: str,
    action: str,
    expected: bool,
) -> None:
    principal = _service_account((scope,))
    assert is_allowed(principal, resource=resource, action=action) is expected  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# audit helper
# ---------------------------------------------------------------------------


def test_collect_roles_for_audit_is_stable_sorted() -> None:
    p = _user(("viewer", "admin", "operator"))
    assert list(collect_roles_for_audit(p)) == ["admin", "operator", "viewer"]


# ---------------------------------------------------------------------------
# Stream V follow-up — system_admin gets tenant-ADMIN authority over
# tenant-scoped resources (within allowed tenants). Fixes the matrix-gated
# tenant pages (mcp_server / user / tenant_config …) that previously denied
# a pure platform admin. Tenant data isolation stays enforced by tenant_id/RLS.
# ---------------------------------------------------------------------------


def _system_admin() -> Principal:
    # No tenant roles — platform-scope only, as produced by Principal.as_system_admin().
    return Principal(
        subject_id="11111111-1111-1111-1111-111111111111",
        subject_type="user",
        tenant_id=_TENANT,
        roles=(),
        scopes=(),
        auth_method="jwt",
        allowed_tenants="*",
        is_system_admin=True,
    )


def test_system_admin_can_manage_mcp_server() -> None:
    p = _system_admin()
    assert is_allowed(p, resource="mcp_server", action="read")
    assert is_allowed(p, resource="mcp_server", action="write")
    assert is_allowed(p, resource="mcp_server", action="delete")


def test_system_admin_can_manage_other_tenant_resources() -> None:
    p = _system_admin()
    assert is_allowed(p, resource="user", action="write")
    assert is_allowed(p, resource="tenant_config", action="write")
    assert is_allowed(p, resource="role_binding", action="write")


def test_non_system_admin_with_no_roles_still_denied() -> None:
    p = _user(())
    assert not is_allowed(p, resource="mcp_server", action="read")
    assert not is_allowed(p, resource="user", action="write")


# ---------------------------------------------------------------------------
# is_admin — per-user scope gate (J.14 / conversation-centric IA)
# ---------------------------------------------------------------------------


def test_is_admin_for_tenant_admin_and_system_admin() -> None:
    assert is_admin(_user(("admin",)))
    assert not is_admin(_user(("viewer",)))
    # A platform system_admin acts with tenant-ADMIN authority inside a
    # tenant (same rationale as is_allowed) — the per-user gates must not
    # deny it what a plain tenant admin may do.
    assert is_admin(_system_admin())
