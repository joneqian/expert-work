"""Persistence Protocol for the tenant MCP server registry — Stream V."""

from __future__ import annotations

import abc
from uuid import UUID

from helix_agent.protocol import (
    McpServerAuthType,
    McpServerTransport,
    TenantMcpServerPatch,
    TenantMcpServerRecord,
)


class TenantMcpServerNotFoundError(Exception):
    """No ``tenant_mcp_server`` row for the requested (tenant, name)."""

    def __init__(self, *, tenant_id: UUID, name: str) -> None:
        super().__init__(f"tenant_mcp_server not found: tenant_id={tenant_id} name={name!r}")
        self.tenant_id = tenant_id
        self.name = name


class TenantMcpServerAlreadyExistsError(Exception):
    """A ``tenant_mcp_server`` row already exists for (tenant, name)."""

    def __init__(self, *, tenant_id: UUID, name: str) -> None:
        super().__init__(f"tenant_mcp_server already exists: tenant_id={tenant_id} name={name!r}")
        self.tenant_id = tenant_id
        self.name = name


class TenantMcpServerStore(abc.ABC):
    """CRUD for tenant-registered remote MCP servers."""

    @abc.abstractmethod
    async def create(
        self,
        *,
        tenant_id: UUID,
        name: str,
        transport: McpServerTransport,
        url: str,
        auth_type: McpServerAuthType,
        token_secret_ref: str | None,
        timeout_s: float,
        created_by: str,
        catalog_id: UUID | None = None,
    ) -> TenantMcpServerRecord:
        """Insert a new server row. Raises
        :class:`TenantMcpServerAlreadyExistsError` on (tenant, name) conflict.

        ``catalog_id`` (Stream W) binds the row to a platform catalog entry;
        ``None`` (the default) = an off-catalog custom registration."""

    @abc.abstractmethod
    async def get(self, *, tenant_id: UUID, name: str) -> TenantMcpServerRecord | None:
        """Return the row, or None if absent."""

    @abc.abstractmethod
    async def list_for_tenant(self, *, tenant_id: UUID) -> list[TenantMcpServerRecord]:
        """Return all rows for the tenant, ordered by ``name``."""

    @abc.abstractmethod
    async def update(
        self, *, tenant_id: UUID, name: str, patch: TenantMcpServerPatch
    ) -> TenantMcpServerRecord:
        """Apply a partial update. Raises
        :class:`TenantMcpServerNotFoundError` if absent."""

    @abc.abstractmethod
    async def delete(self, *, tenant_id: UUID, name: str) -> None:
        """Delete the row. Raises
        :class:`TenantMcpServerNotFoundError` if absent."""
