"""Stream V-C — audit enum + resource-type additions for MCP servers."""

from __future__ import annotations

from expert_work.protocol import AuditAction


def test_mcp_server_audit_actions_exist() -> None:
    assert AuditAction.MCP_SERVER_CREATE.value == "mcp_server:create"
    assert AuditAction.MCP_SERVER_UPDATE.value == "mcp_server:update"
    assert AuditAction.MCP_SERVER_DELETE.value == "mcp_server:delete"


def test_resource_type_literal_includes_tenant_mcp_server() -> None:
    # Both Literals must include the new resource type (drift guard).
    from typing import get_args

    from control_plane.audit import ResourceType as CpResourceType
    from expert_work.protocol.audit import ResourceType as ProtoResourceType

    assert "tenant_mcp_server" in get_args(CpResourceType)
    assert "tenant_mcp_server" in get_args(ProtoResourceType)
