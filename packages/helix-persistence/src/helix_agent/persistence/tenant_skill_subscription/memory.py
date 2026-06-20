"""In-memory :class:`TenantSkillSubscriptionStore` — Skill Marketplace."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

from helix_agent.persistence.tenant_skill_subscription.base import (
    TenantSkillSubscriptionNotFoundError,
    TenantSkillSubscriptionStore,
)
from helix_agent.protocol import TenantSkillSubscriptionRecord


def _now() -> datetime:
    return datetime.now(tz=UTC)


class InMemoryTenantSkillSubscriptionStore(TenantSkillSubscriptionStore):
    """Dict-backed store keyed by ``(tenant_id, platform_skill_id)``."""

    def __init__(self) -> None:
        self._rows: dict[tuple[UUID, UUID], TenantSkillSubscriptionRecord] = {}
        self._lock = asyncio.Lock()

    async def subscribe(
        self,
        *,
        tenant_id: UUID,
        platform_skill_id: UUID,
        created_by: str,
    ) -> TenantSkillSubscriptionRecord:
        async with self._lock:
            key = (tenant_id, platform_skill_id)
            existing = self._rows.get(key)
            if existing is not None:
                # Idempotent re-enable; preserve original created_at / created_by.
                updated = existing.model_copy(update={"enabled": True})
                self._rows[key] = updated
                return updated
            record = TenantSkillSubscriptionRecord(
                id=uuid4(),
                tenant_id=tenant_id,
                platform_skill_id=platform_skill_id,
                enabled=True,
                created_at=_now(),
                created_by=created_by,
            )
            self._rows[key] = record
            return record

    async def set_enabled(
        self,
        *,
        tenant_id: UUID,
        platform_skill_id: UUID,
        enabled: bool,
    ) -> TenantSkillSubscriptionRecord:
        async with self._lock:
            key = (tenant_id, platform_skill_id)
            existing = self._rows.get(key)
            if existing is None:
                raise TenantSkillSubscriptionNotFoundError(
                    tenant_id=tenant_id, platform_skill_id=platform_skill_id
                )
            updated = existing.model_copy(update={"enabled": enabled})
            self._rows[key] = updated
            return updated

    async def unsubscribe(self, *, tenant_id: UUID, platform_skill_id: UUID) -> None:
        async with self._lock:
            key = (tenant_id, platform_skill_id)
            if key not in self._rows:
                raise TenantSkillSubscriptionNotFoundError(
                    tenant_id=tenant_id, platform_skill_id=platform_skill_id
                )
            del self._rows[key]

    async def list_for_tenant(self, *, tenant_id: UUID) -> list[TenantSkillSubscriptionRecord]:
        async with self._lock:
            rows = [r for (tid, _), r in self._rows.items() if tid == tenant_id]
        return sorted(rows, key=lambda r: r.created_at)

    async def is_subscribed(self, *, tenant_id: UUID, platform_skill_id: UUID) -> bool:
        async with self._lock:
            row = self._rows.get((tenant_id, platform_skill_id))
        return row is not None and row.enabled
