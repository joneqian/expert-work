"""Abstract ``TriggerStore`` repository — Stream J.10 (Mini-ADR J-26 / J-42).

The durable registry of cron / webhook triggers (the ``agent_trigger``
table). The scheduler polls :meth:`list_enabled_cron` for due cron
triggers; the CRUD API + manifest reconciliation use the rest.

:meth:`list_enabled_cron` is **cross-tenant** — the single-replica
scheduler scans every tenant's triggers. The caller (the scheduler) is
responsible for entering an RLS-bypass context (``bypass_rls_var``)
around it; per-trigger work re-scopes to the trigger's own tenant.

Implementations:
- :class:`expert_work.persistence.trigger.memory.InMemoryTriggerStore`
- :class:`expert_work.persistence.trigger.sql.SqlTriggerStore`
"""

from __future__ import annotations

import abc
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from expert_work.protocol import TriggerRecord, TriggerRunRecord


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
    async def list_by_tenant(
        self,
        *,
        tenant_id: UUID,
        agent_name: str | None = None,
        agent_version: str | None = None,
    ) -> list[TriggerRecord]:
        """Return every trigger in a tenant; optional agent filters.

        Differs from :meth:`list_by_agent`:that one requires ``agent_name``
        (the original CRUD list); this one is the relaxed admin variant
        used by the Stream H Admin UI tenant view. ``agent_version``
        narrows further (Stream H.6 — per-agent Triggers tab).
        """

    @abc.abstractmethod
    async def list_all_tenants(
        self,
        *,
        agent_name: str | None = None,
        agent_version: str | None = None,
    ) -> list[TriggerRecord]:
        """Cross-tenant trigger list — Stream N (Mini-ADR N-4).

        Caller MUST be inside ``bypass_rls_session()``. Optional
        agent filters applied across all tenants.
        """

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
    async def claim_cron_fire(
        self,
        *,
        trigger_id: UUID,
        tenant_id: UUID,
        expected_last_fired_at: datetime | None,
        new_last_fired_at: datetime,
    ) -> bool:
        """Atomically claim a due cron slot; ``True`` iff this caller won.

        Stream 9.5 — the scheduler runs on every instance. The CAS stamps
        ``last_fired_at = new`` only while it still equals ``expected`` (the
        value the caller read when it judged the trigger due), using
        ``IS NOT DISTINCT FROM`` so a never-fired trigger (``expected=None``)
        matches. Exactly one instance wins the slot and goes on to fire; the
        loser's UPDATE matches zero rows → it skips, so blue+green don't both
        spawn a run for the same tick. Without this two schedulers both
        ``list``-then-fire the same due trigger → duplicate runs.
        """

    @abc.abstractmethod
    async def delete(self, *, trigger_id: UUID, tenant_id: UUID) -> bool:
        """Delete a trigger row; return ``True`` iff it existed."""

    @abc.abstractmethod
    async def delete_all_for_user(self, *, tenant_id: UUID, user_id: UUID) -> list[UUID]:
        """Phase 3a (purge_user) — hard-delete EVERY trigger owned by a user.

        Removes all of the user's ``agent_trigger`` rows and returns the ids of
        the rows deleted (empty on none / re-purge) so the caller can drop the
        matching ``trigger_run`` children via
        :meth:`TriggerRunStore.delete_for_triggers` (there is no FK cascade —
        ``trigger_run.trigger_id`` is a bare column). Tenant- AND user-scoped;
        a NULL ``user_id`` trigger is not this user's and is left untouched."""

    @abc.abstractmethod
    async def get_for_webhook(self, *, trigger_id: UUID) -> TriggerRecord | None:
        """Tenant-unscoped lookup for the webhook ingest path.

        The webhook caller is an external system with no tenant
        context; the endpoint resolves the trigger by id alone to learn
        its tenant + secret hash. The caller enters an RLS-bypass
        context (``bypass_rls_var``) around this — it is the only
        cross-tenant read on the trigger store.
        """

    @abc.abstractmethod
    async def count_cron_by_tenant(self, *, tenant_id: UUID) -> int:
        """Count a tenant's ``cron`` triggers — backs the create-time quota."""


class TriggerRunStore(abc.ABC):
    """Registry of trigger firings — the ``trigger_run`` table.

    The scheduler writes one row per firing; the DLQ sweep (Stream
    J.10-step4) updates the retry state.
    """

    @abc.abstractmethod
    async def create(self, record: TriggerRunRecord) -> TriggerRunRecord:
        """Persist a new trigger-firing row."""

    @abc.abstractmethod
    async def get(self, *, trigger_run_id: UUID, tenant_id: UUID) -> TriggerRunRecord | None:
        """Return the firing row, or ``None`` when unknown / cross-tenant."""

    @abc.abstractmethod
    async def update(self, record: TriggerRunRecord) -> bool:
        """Replace a firing row (matched by ``id`` + ``tenant_id``); return hit."""

    @abc.abstractmethod
    async def claim_retry(self, *, trigger_run_id: UUID, tenant_id: UUID) -> bool:
        """Atomically claim a due ``retrying`` firing for re-fire; ``True`` iff won.

        Stream 9.5 — CAS ``status='retrying'`` → ``fired`` (clearing
        ``next_retry_at``) so when several instances scan
        :meth:`list_due_retries` only one re-fires the run. The loser sees
        ``False`` and skips. Without this two schedulers both re-fire the same
        retrying firing → duplicate runs.
        """

    @abc.abstractmethod
    async def list_by_trigger(self, *, trigger_id: UUID, tenant_id: UUID) -> list[TriggerRunRecord]:
        """Return every firing of ``trigger_id`` under the tenant, newest first."""

    @abc.abstractmethod
    async def delete_for_triggers(self, *, trigger_ids: Sequence[UUID], tenant_id: UUID) -> int:
        """Phase 3a (purge_user) — hard-delete every firing of the given triggers.

        Removes the ``trigger_run`` rows whose ``trigger_id`` is in
        ``trigger_ids`` (the parent triggers a purged user owned) and returns
        the count deleted. Tenant-scoped; an empty ``trigger_ids`` is a no-op
        returning 0. Called after :meth:`TriggerStore.delete_all_for_user`
        (there is no FK cascade)."""

    @abc.abstractmethod
    async def list_fired(self, *, limit: int = 1000) -> list[TriggerRunRecord]:
        """Cross-tenant — every ``fired`` firing awaiting an outcome reconcile.

        The caller (the scheduler) enters an RLS-bypass context.
        """

    @abc.abstractmethod
    async def list_due_retries(
        self, *, before: datetime, limit: int = 1000
    ) -> list[TriggerRunRecord]:
        """Cross-tenant — ``retrying`` firings whose ``next_retry_at`` has passed."""
