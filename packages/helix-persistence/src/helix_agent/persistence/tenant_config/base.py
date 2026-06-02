"""Abstract :class:`TenantConfigStore` — Stream C.7."""

from __future__ import annotations

import abc
from uuid import UUID

from helix_agent.protocol import TenantConfigPatch, TenantConfigRecord, TenantPlan


class TenantConfigNotFoundError(Exception):
    """No ``tenant_config`` row exists for the requested tenant."""

    def __init__(self, *, tenant_id: UUID) -> None:
        super().__init__(f"tenant_config not found for tenant_id={tenant_id}")
        self.tenant_id = tenant_id


class TenantConfigAlreadyExistsError(Exception):
    """A ``tenant_config`` row already exists for the requested tenant.

    Raised by :meth:`TenantConfigStore.create` — Stream P (Mini-ADR P-3).
    ``create`` is the explicit "provision a new tenant" path and must fail
    loudly on a pre-existing tenant rather than silently overwriting it the
    way :meth:`upsert` would.
    """

    def __init__(self, *, tenant_id: UUID) -> None:
        super().__init__(f"tenant_config already exists for tenant_id={tenant_id}")
        self.tenant_id = tenant_id


class TenantConfigStore(abc.ABC):
    """Persistence Protocol for the per-tenant runtime config row."""

    @abc.abstractmethod
    async def get(self, *, tenant_id: UUID) -> TenantConfigRecord | None:
        """Return the row, or None if no config has been seeded yet."""

    @abc.abstractmethod
    async def create(
        self,
        *,
        tenant_id: UUID,
        display_name: str,
        plan: TenantPlan | None = None,
        actor_id: str,
    ) -> TenantConfigRecord:
        """Provision a new tenant — write the first ``tenant_config`` row.

        Stream P (Mini-ADR P-1/P-3): the explicit tenant-creation path behind
        ``POST /v1/tenants``. Only ``display_name`` (and optionally ``plan``)
        are set; every other field takes its column default and is tuned
        later via :meth:`upsert`.

        Raises :class:`TenantConfigAlreadyExistsError` if a row already exists
        for ``tenant_id`` (unlike :meth:`upsert`, which merges).
        """

    @abc.abstractmethod
    async def upsert(
        self,
        *,
        tenant_id: UUID,
        patch: TenantConfigPatch,
        actor_id: str,
    ) -> TenantConfigRecord:
        """Insert-or-merge the patch. ``display_name`` is required for first insert."""

    @abc.abstractmethod
    async def set_status(
        self, *, tenant_id: UUID, status: str, actor_id: str
    ) -> TenantConfigRecord:
        """Set tenant lifecycle status ('active'|'suspended'). Raises
        TenantConfigNotFoundError if the tenant has no config row."""

    @abc.abstractmethod
    async def list_all(self, *, limit: int = 50, offset: int = 0) -> list[TenantConfigRecord]:
        """Return tenant config rows ordered by ``created_at`` (oldest first).

        Platform-level cross-tenant read behind ``GET /v1/tenants``
        (system_admin only). Paginated via ``limit``/``offset``.
        """
