"""In-memory :class:`TenantConfigStore` — Stream C.7."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from helix_agent.persistence.tenant_config.base import TenantConfigStore
from helix_agent.protocol import TenantConfigPatch, TenantConfigRecord, TenantPlan


def _now() -> datetime:
    return datetime.now(tz=UTC)


class FirstUpsertRequiresDisplayNameError(ValueError):
    """``upsert`` for a tenant with no existing row must include ``display_name``."""


class InMemoryTenantConfigStore(TenantConfigStore):
    """Single-dict store; lock-guarded for asyncio safety."""

    def __init__(self) -> None:
        self._rows: dict[UUID, TenantConfigRecord] = {}
        self._lock = asyncio.Lock()

    async def get(self, *, tenant_id: UUID) -> TenantConfigRecord | None:
        async with self._lock:
            return self._rows.get(tenant_id)

    async def upsert(
        self,
        *,
        tenant_id: UUID,
        patch: TenantConfigPatch,
        actor_id: str,
    ) -> TenantConfigRecord:
        now = _now()
        async with self._lock:
            existing = self._rows.get(tenant_id)
            if existing is None:
                if patch.display_name is None:
                    msg = (
                        "first upsert for a tenant must include display_name; "
                        f"got tenant_id={tenant_id}"
                    )
                    raise FirstUpsertRequiresDisplayNameError(msg)
                row = TenantConfigRecord(
                    tenant_id=tenant_id,
                    display_name=patch.display_name,
                    plan=patch.plan or TenantPlan.FREE,
                    model_credentials_ref=patch.model_credentials_ref or {},
                    mcp_allowlist=patch.mcp_allowlist or [],
                    rate_limit_override=patch.rate_limit_override or {},
                    pii_fields=patch.pii_fields or [],
                    created_at=now,
                    updated_at=now,
                    updated_by=actor_id,
                )
            else:
                row = existing.model_copy(
                    update={
                        k: v
                        for k, v in {
                            "display_name": patch.display_name,
                            "plan": patch.plan,
                            "model_credentials_ref": patch.model_credentials_ref,
                            "mcp_allowlist": patch.mcp_allowlist,
                            "rate_limit_override": patch.rate_limit_override,
                            "pii_fields": patch.pii_fields,
                        }.items()
                        if v is not None
                    }
                    | {"updated_at": now, "updated_by": actor_id},
                )
            self._rows[tenant_id] = row
            return row
