"""Abstract :class:`TenantConfigStore` — Stream C.7."""

from __future__ import annotations

import abc
from uuid import UUID

from helix_agent.protocol import TenantConfigPatch, TenantConfigRecord


class TenantConfigNotFoundError(Exception):
    """No ``tenant_config`` row exists for the requested tenant."""

    def __init__(self, *, tenant_id: UUID) -> None:
        super().__init__(f"tenant_config not found for tenant_id={tenant_id}")
        self.tenant_id = tenant_id


class TenantConfigStore(abc.ABC):
    """Persistence Protocol for the per-tenant runtime config row."""

    @abc.abstractmethod
    async def get(self, *, tenant_id: UUID) -> TenantConfigRecord | None:
        """Return the row, or None if no config has been seeded yet."""

    @abc.abstractmethod
    async def upsert(
        self,
        *,
        tenant_id: UUID,
        patch: TenantConfigPatch,
        actor_id: str,
    ) -> TenantConfigRecord:
        """Insert-or-merge the patch. ``display_name`` is required for first insert."""
