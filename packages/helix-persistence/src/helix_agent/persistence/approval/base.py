"""Abstract ``ApprovalStore`` repository — Stream J.8 (Mini-ADR J-24).

The durable registry of runs paused for human approval. helix's
``RunManager`` is in-memory only, so this store is the only place a
paused run survives a control-plane restart — what ``GET`` reads to
surface ``pending_approval``, what ``POST .../resume`` looks up, and
what the 24h timeout job scans.

Implementations:
- :class:`helix_agent.persistence.approval.memory.InMemoryApprovalStore`
- :class:`helix_agent.persistence.approval.sql.SqlApprovalStore`
"""

from __future__ import annotations

import abc
from datetime import datetime
from uuid import UUID

from helix_agent.protocol import ApprovalRecord, ApprovalStatus


class ApprovalStore(abc.ABC):
    """Registry of paused-run approval records, tenant-scoped."""

    @abc.abstractmethod
    async def create(self, record: ApprovalRecord) -> ApprovalRecord:
        """Persist a new ``pending`` approval row when a run pauses.

        ``run_id`` is unique — a run pauses at most once at a time. A
        second ``create`` for the same run is a programming error and
        the SQL backend's unique constraint surfaces it.
        """

    @abc.abstractmethod
    async def get_by_run(self, *, run_id: UUID, tenant_id: UUID) -> ApprovalRecord | None:
        """Return the approval row for ``run_id``, or ``None``.

        ``None`` when the run id is unknown or belongs to another
        tenant — callers turn that into 404, never raising on a
        cross-tenant probe.
        """

    @abc.abstractmethod
    async def list_expired(
        self,
        *,
        before: datetime,
        limit: int = 1000,
    ) -> list[ApprovalRecord]:
        """Pending rows whose ``timeout_at < before`` — the timeout sweep.

        The 24h job calls this with ``before = now``; every returned
        row is past its deadline and eligible for auto-reject.
        """

    @abc.abstractmethod
    async def count_pending(self) -> int:
        """Number of ``pending`` rows across all tenants — Stream HX-4.

        Feeds the ``helix_control_plane_approvals_pending`` gauge (one
        platform-wide number; per-tenant counts come from the API, not
        a metric label). Like :meth:`list_expired`, the SQL read relies
        on the owner's exemption under ENABLE-only RLS — callers run it
        under a bypass scope (no tenant GUC).
        """

    @abc.abstractmethod
    async def mark_decided(
        self,
        *,
        run_id: UUID,
        tenant_id: UUID,
        status: ApprovalStatus,
        decided_by: str,
        decided_at: datetime,
        modified_args: dict[str, object] | None = None,
    ) -> bool:
        """Flip a ``pending`` row to a terminal verdict; return ``True`` on hit.

        Returns ``False`` when the run id is unknown, in another
        tenant, or already decided (callers treat that as a 404 /
        409 — the verdict is idempotent-once). ``status`` must be one
        of the terminal :class:`ApprovalStatus` values.
        """
