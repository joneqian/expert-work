"""RBAC matrix coverage for the mcp_server resource (Stream V-C)."""

from __future__ import annotations

from uuid import UUID

from control_plane.auth.rbac import is_allowed
from helix_agent.protocol import Principal, Role

_TENANT = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _principal(role: Role) -> Principal:
    return Principal(
        subject_id="admin@acme",
        subject_type="user",
        tenant_id=_TENANT,
        roles=(role.value,),
        scopes=(),
        auth_method="jwt",
    )


def test_admin_can_write_and_delete_mcp_server() -> None:
    p = _principal(Role.ADMIN)
    assert is_allowed(p, resource="mcp_server", action="read")
    assert is_allowed(p, resource="mcp_server", action="write")
    assert is_allowed(p, resource="mcp_server", action="delete")


def test_operator_can_read_not_write_mcp_server() -> None:
    p = _principal(Role.OPERATOR)
    assert is_allowed(p, resource="mcp_server", action="read")
    assert not is_allowed(p, resource="mcp_server", action="write")
    assert not is_allowed(p, resource="mcp_server", action="delete")


def test_viewer_read_only_mcp_server() -> None:
    p = _principal(Role.VIEWER)
    assert is_allowed(p, resource="mcp_server", action="read")
    assert not is_allowed(p, resource="mcp_server", action="write")
