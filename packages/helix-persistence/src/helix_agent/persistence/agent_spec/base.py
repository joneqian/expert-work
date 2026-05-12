"""Abstract :class:`AgentSpecStore` — Stream B.5."""

from __future__ import annotations

import abc
from uuid import UUID

from helix_agent.protocol import AgentSpec, AgentSpecRecord, AgentSpecStatus


class DuplicateAgentSpecError(Exception):
    """Raised when the unique ``(tenant_id, name, version)`` index trips
    on insert. The API layer maps it to ``HTTP 409``."""

    def __init__(self, *, tenant_id: UUID, name: str, version: str) -> None:
        super().__init__(
            f"agent_spec already exists: tenant_id={tenant_id} name={name} version={version}"
        )
        self.tenant_id = tenant_id
        self.name = name
        self.version = version


class AgentSpecStore(abc.ABC):
    """Per-tenant manifest registry.

    Every method takes ``tenant_id`` explicitly. Reads / writes filtered
    on a different tenant return ``None`` / ``False`` rather than the
    record, so cross-tenant existence never leaks.
    """

    @abc.abstractmethod
    async def create(
        self,
        *,
        tenant_id: UUID,
        spec: AgentSpec,
        spec_sha256: str,
        created_by: str,
    ) -> AgentSpecRecord:
        """Insert a new ``ACTIVE`` row; raises :class:`DuplicateAgentSpecError`
        if ``(tenant_id, name, version)`` already exists."""

    @abc.abstractmethod
    async def get(
        self,
        *,
        tenant_id: UUID,
        name: str,
        version: str,
        include_deleted: bool = False,
    ) -> AgentSpecRecord | None:
        """Fetch one row; defaults skip soft-deleted entries."""

    @abc.abstractmethod
    async def list_by_tenant(
        self,
        *,
        tenant_id: UUID,
        status: AgentSpecStatus | None = None,
        name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AgentSpecRecord]:
        """Paginated list, newest first."""

    @abc.abstractmethod
    async def update_spec(
        self,
        *,
        tenant_id: UUID,
        name: str,
        version: str,
        spec: AgentSpec,
        spec_sha256: str,
        updated_by: str,
    ) -> AgentSpecRecord | None:
        """Replace the spec payload + sha256 in place. Returns the
        updated record, or ``None`` if no row matched (404)."""

    @abc.abstractmethod
    async def update_status(
        self,
        *,
        tenant_id: UUID,
        name: str,
        version: str,
        status: AgentSpecStatus,
    ) -> AgentSpecRecord | None:
        """Update status. Returns the updated record, or ``None`` if no
        row matched. Used by the soft-delete path and the deprecate UI."""
