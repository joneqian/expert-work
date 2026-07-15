"""Abstract ``WebhookEndpointStore`` + ``WebhookDeliveryStore`` — HX-9 (STREAM-HX § 13).

The durable registry of outbound webhook endpoints (``webhook_endpoint``)
and the delivery queue / DLQ (``webhook_delivery``). The CRUD API uses the
tenant-scoped methods; the delivery worker uses the cross-tenant scans
(:meth:`WebhookEndpointStore.list_enabled_all_tenants` /
:meth:`WebhookDeliveryStore.list_ready`), entering an RLS-bypass context
(``bypass_rls_var``) around them — the single-replica worker scans every
tenant's rows; per-delivery work re-scopes to the row's own tenant.

Implementations:
- :mod:`expert_work.persistence.webhook.memory`
- :mod:`expert_work.persistence.webhook.sql`
"""

from __future__ import annotations

import abc
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from expert_work.protocol import WebhookDeliveryRecord, WebhookEndpointRecord


class WebhookEndpointStore(abc.ABC):
    """Registry of outbound webhook endpoints, tenant-scoped."""

    @abc.abstractmethod
    async def create(self, record: WebhookEndpointRecord) -> WebhookEndpointRecord:
        """Persist a new endpoint row.

        ``(tenant_id, name)`` is unique — a second create with the same
        pair is rejected (the SQL backend's unique constraint surfaces it).
        """

    @abc.abstractmethod
    async def get(self, *, endpoint_id: UUID, tenant_id: UUID) -> WebhookEndpointRecord | None:
        """Return the endpoint row, or ``None`` when unknown / cross-tenant."""

    @abc.abstractmethod
    async def list_by_tenant(
        self, *, tenant_id: UUID, agent_name: str | None = None
    ) -> list[WebhookEndpointRecord]:
        """Return every endpoint in a tenant; optional ``agent_name`` filter."""

    @abc.abstractmethod
    async def list_all_tenants(
        self, *, agent_name: str | None = None
    ) -> list[WebhookEndpointRecord]:
        """Cross-tenant endpoint list — Stream N. Caller MUST bypass RLS."""

    @abc.abstractmethod
    async def list_enabled_all_tenants(self) -> list[WebhookEndpointRecord]:
        """Every enabled endpoint across all tenants — the worker's match scan.

        Cross-tenant; the caller (the delivery worker) enters an RLS-bypass
        context around this.
        """

    @abc.abstractmethod
    async def update(self, record: WebhookEndpointRecord) -> bool:
        """Replace an endpoint row (matched by ``id`` + ``tenant_id``); return hit."""

    @abc.abstractmethod
    async def delete(self, *, endpoint_id: UUID, tenant_id: UUID) -> bool:
        """Delete an endpoint row; return ``True`` iff it existed."""

    @abc.abstractmethod
    async def delete_all_for_user(self, *, tenant_id: UUID, user_id: UUID) -> list[UUID]:
        """Phase 3a (purge_user) — hard-delete EVERY endpoint owned by a user.

        Removes all of the user's ``webhook_endpoint`` rows and returns the ids
        deleted (empty on none / re-purge) so the caller can drop the matching
        ``webhook_delivery`` children via
        :meth:`WebhookDeliveryStore.delete_for_endpoints` (there is no FK
        cascade — ``webhook_delivery.endpoint_id`` is a bare column). Tenant-
        AND user-scoped; a NULL ``user_id`` endpoint is not this user's and is
        left untouched."""

    @abc.abstractmethod
    async def count_by_tenant(self, *, tenant_id: UUID) -> int:
        """Count a tenant's endpoints — backs the create-time quota."""


class WebhookDeliveryStore(abc.ABC):
    """Registry of webhook deliveries — the ``webhook_delivery`` queue / DLQ."""

    @abc.abstractmethod
    async def create(self, record: WebhookDeliveryRecord) -> WebhookDeliveryRecord:
        """Persist a new delivery row.

        ``(endpoint_id, event_id)`` is unique — the worker calls
        :meth:`exists_for_event` first so re-scanning the event spine
        enqueues idempotently; a racing duplicate surfaces as the SQL
        unique-constraint error.
        """

    @abc.abstractmethod
    async def get(self, *, delivery_id: UUID, tenant_id: UUID) -> WebhookDeliveryRecord | None:
        """Return the delivery row, or ``None`` when unknown / cross-tenant."""

    @abc.abstractmethod
    async def exists_for_event(self, *, endpoint_id: UUID, event_id: str) -> bool:
        """Whether a delivery already exists for ``(endpoint_id, event_id)``.

        Backs idempotent enqueue — cross-tenant (the worker checks before
        inserting). Caller bypasses RLS.
        """

    @abc.abstractmethod
    async def update(self, record: WebhookDeliveryRecord) -> bool:
        """Replace a delivery row (matched by ``id`` + ``tenant_id``); return hit."""

    @abc.abstractmethod
    async def list_by_endpoint(
        self, *, endpoint_id: UUID, tenant_id: UUID, limit: int = 100
    ) -> list[WebhookDeliveryRecord]:
        """Return ``endpoint_id``'s deliveries under the tenant, newest first."""

    @abc.abstractmethod
    async def delete_for_endpoints(self, *, endpoint_ids: Sequence[UUID], tenant_id: UUID) -> int:
        """Phase 3a (purge_user) — hard-delete every delivery of the given endpoints.

        Removes the ``webhook_delivery`` rows whose ``endpoint_id`` is in
        ``endpoint_ids`` (the endpoints a purged user owned) and returns the
        count deleted. Tenant-scoped; an empty ``endpoint_ids`` is a no-op
        returning 0. Called after
        :meth:`WebhookEndpointStore.delete_all_for_user` (no FK cascade)."""

    @abc.abstractmethod
    async def list_ready(
        self, *, before: datetime, limit: int = 1000
    ) -> list[WebhookDeliveryRecord]:
        """Cross-tenant — deliverable rows (``pending`` now, or ``retrying``
        whose ``next_retry_at`` has passed). The caller (the worker) enters
        an RLS-bypass context.
        """
