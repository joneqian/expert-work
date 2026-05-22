"""Abstract ``TriggerStore`` repository — Stream J.10 (Mini-ADR J-26 / J-42).

The durable registry of cron / webhook triggers (the ``agent_trigger``
table). The scheduler polls :meth:`list_enabled_cron` for due cron
triggers; the CRUD API + manifest reconciliation use the rest.

:meth:`list_enabled_cron` is **cross-tenant** — the single-replica
scheduler scans every tenant's triggers. The caller (the scheduler) is
responsible for entering an RLS-bypass context (``bypass_rls_var``)
around it; per-trigger work re-scopes to the trigger's own tenant.

Implementations:
- :class:`helix_agent.persistence.trigger.memory.InMemoryTriggerStore`
- :class:`helix_agent.persistence.trigger.sql.SqlTriggerStore`
"""

from __future__ import annotations

import abc
from uuid import UUID

from helix_agent.protocol import TriggerRecord


class TriggerStore(abc.ABC):
    """Registry of cron / webhook triggers, tenant-scoped."""

    @abc.abstractmethod
    async def create(self, record: TriggerRecord) -> TriggerRecord:
        """Persist a new trigger row.

        ``(tenant_id, agent_name, name)`` is unique — a second create
        with the same triple is a programming error and the SQL
        backend's unique constraint surfaces it.
        """

    @abc.abstractmethod
    async def get(self, *, trigger_id: UUID, tenant_id: UUID) -> TriggerRecord | None:
        """Return the trigger row, or ``None`` when unknown / cross-tenant."""

    @abc.abstractmethod
    async def list_by_agent(self, *, tenant_id: UUID, agent_name: str) -> list[TriggerRecord]:
        """Return every trigger registered for ``agent_name`` under the tenant."""

    @abc.abstractmethod
    async def list_enabled_cron(self) -> list[TriggerRecord]:
        """Return every enabled ``cron`` trigger across all tenants.

        Cross-tenant — the single-replica scheduler scans the whole
        table. The caller enters an RLS-bypass context around this.
        """

    @abc.abstractmethod
    async def update(self, record: TriggerRecord) -> bool:
        """Replace a trigger row (matched by ``id`` + ``tenant_id``).

        Returns ``True`` iff the row existed. Used by the CRUD PATCH
        and by the scheduler to stamp ``last_fired_at``.
        """

    @abc.abstractmethod
    async def delete(self, *, trigger_id: UUID, tenant_id: UUID) -> bool:
        """Delete a trigger row; return ``True`` iff it existed."""
