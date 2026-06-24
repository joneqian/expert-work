"""Persistence Protocol for per-user MCP OAuth connections — Stream MCP-OAUTH (OA-1b).

User-level scoping is enforced here (every method takes ``user_id`` and filters
on it) on top of the table's tenant-level RLS — a user can only see/mutate their
own connections.
"""

from __future__ import annotations

import abc
from uuid import UUID

from helix_agent.protocol import McpOAuthConnectionPatch, McpOAuthConnectionRecord


class McpOAuthConnectionNotFoundError(Exception):
    """No ``mcp_oauth_connection`` row for the requested id (within tenant/user)."""

    def __init__(self, *, connection_id: UUID) -> None:
        super().__init__(f"mcp_oauth_connection not found: id={connection_id}")
        self.connection_id = connection_id


class McpOAuthConnectionAlreadyExistsError(Exception):
    """A connection already exists for (tenant, user, catalog)."""

    def __init__(self, *, tenant_id: UUID, user_id: str, catalog_id: UUID) -> None:
        super().__init__(
            f"mcp_oauth_connection already exists: tenant_id={tenant_id} "
            f"user_id={user_id!r} catalog_id={catalog_id}"
        )
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.catalog_id = catalog_id


class McpOAuthConnectionStore(abc.ABC):
    """CRUD for per-user OAuth connections to hosted MCP connectors."""

    @abc.abstractmethod
    async def create(
        self,
        *,
        tenant_id: UUID,
        user_id: str,
        catalog_id: UUID,
        name: str,
        resolved_url: str,
        scopes: str = "",
        oauth_state: str | None = None,
        pkce_verifier: str | None = None,
        redirect_uri: str | None = None,
    ) -> McpOAuthConnectionRecord:
        """Insert a new ``pending`` connection. Raises
        :class:`McpOAuthConnectionAlreadyExistsError` on (tenant, user, catalog).

        ``redirect_uri`` (multi-client OAuth) is the per-initiate redirect a
        client supplied; ``None`` = the global default was used."""

    @abc.abstractmethod
    async def get(
        self, *, connection_id: UUID, tenant_id: UUID, user_id: str
    ) -> McpOAuthConnectionRecord | None:
        """Return the connection by id (scoped to tenant+user), or None."""

    @abc.abstractmethod
    async def get_for_connector(
        self, *, tenant_id: UUID, user_id: str, catalog_id: UUID
    ) -> McpOAuthConnectionRecord | None:
        """Return the user's connection for a catalog connector, or None."""

    @abc.abstractmethod
    async def get_by_state(
        self, *, tenant_id: UUID, user_id: str, oauth_state: str
    ) -> McpOAuthConnectionRecord | None:
        """Return the pending connection matching ``oauth_state`` (callback lookup)."""

    @abc.abstractmethod
    async def list_for_user(
        self, *, tenant_id: UUID, user_id: str
    ) -> list[McpOAuthConnectionRecord]:
        """Return the user's connections, ordered by ``name``."""

    @abc.abstractmethod
    async def update(
        self, *, connection_id: UUID, tenant_id: UUID, user_id: str, patch: McpOAuthConnectionPatch
    ) -> McpOAuthConnectionRecord:
        """Apply a partial update. Raises
        :class:`McpOAuthConnectionNotFoundError` if absent."""

    @abc.abstractmethod
    async def delete(self, *, connection_id: UUID, tenant_id: UUID, user_id: str) -> None:
        """Delete the connection. Raises
        :class:`McpOAuthConnectionNotFoundError` if absent."""
