"""Abstract :class:`AgentSpecStore` — Stream B.5."""

from __future__ import annotations

import abc
from dataclasses import dataclass
from uuid import UUID

from expert_work.protocol import (
    AgentSpec,
    AgentSpecRecord,
    AgentSpecRevisionRecord,
    AgentSpecStatus,
)


@dataclass(frozen=True)
class AgentSpecUpdateResult:
    """Outcome of :meth:`AgentSpecStore.update_spec` — Stream HX-5.

    ``revision`` is the history row this update appended, or ``None``
    for a no-op (the new sha equals the stored one — nothing changed,
    nothing recorded). ``prev_sha256`` feeds the MANIFEST_WRITE audit
    so every change carries its before/after pair.
    """

    record: AgentSpecRecord
    revision: int | None
    prev_sha256: str


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
    async def list_all_tenants(
        self,
        *,
        status: AgentSpecStatus | None = None,
        name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AgentSpecRecord]:
        """Cross-tenant list — Stream N (Mini-ADR N-4).

        Caller MUST be inside a ``bypass_rls_session()`` (or ``applied_scope``
        with a :class:`CrossTenant` resolution); otherwise RLS filters
        all rows out. Used only by ``system_admin`` requests with
        ``tenant_id=*``.
        """

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
    ) -> AgentSpecUpdateResult | None:
        """Replace the spec payload + sha256 in place, appending one
        ``agent_spec_revision`` history row in the same transaction
        (Stream HX-5; a same-sha update is a recorded no-op — no new
        revision). Returns the result, or ``None`` if no row matched
        (404)."""

    @abc.abstractmethod
    async def list_revisions(
        self,
        *,
        tenant_id: UUID,
        name: str,
        version: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AgentSpecRevisionRecord]:
        """Revision history for one manifest, newest first — Stream HX-5."""

    @abc.abstractmethod
    async def get_revision(
        self,
        *,
        tenant_id: UUID,
        name: str,
        version: str,
        revision: int,
    ) -> AgentSpecRevisionRecord | None:
        """One revision snapshot, or ``None`` (unknown / cross-tenant)."""

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
