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
                # Use the field defaults from TenantConfigRecord unless the
                # patch overrides them; ``None`` means "use default".
                row_kwargs: dict[str, object] = {
                    "tenant_id": tenant_id,
                    "display_name": patch.display_name,
                    "plan": patch.plan or TenantPlan.FREE,
                    "model_credentials_ref": patch.model_credentials_ref or {},
                    "mcp_allowlist": patch.mcp_allowlist or [],
                    "rate_limit_override": patch.rate_limit_override or {},
                    "pii_fields": patch.pii_fields or [],
                    "http_tool_allowlist": patch.http_tool_allowlist or [],
                    "mcp_servers": patch.mcp_servers or [],
                    "created_at": now,
                    "updated_at": now,
                    "updated_by": actor_id,
                }
                if patch.audit_retention_days is not None:
                    row_kwargs["audit_retention_days"] = patch.audit_retention_days
                if patch.event_log_retention_days is not None:
                    row_kwargs["event_log_retention_days"] = patch.event_log_retention_days
                if patch.trigger_fire_scan_mode is not None:
                    row_kwargs["trigger_fire_scan_mode"] = patch.trigger_fire_scan_mode
                if patch.memory_recall_mode is not None:
                    row_kwargs["memory_recall_mode"] = patch.memory_recall_mode
                row = TenantConfigRecord(**row_kwargs)  # type: ignore[arg-type]
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
                            "http_tool_allowlist": patch.http_tool_allowlist,
                            "mcp_servers": patch.mcp_servers,
                            "audit_retention_days": patch.audit_retention_days,
                            "event_log_retention_days": patch.event_log_retention_days,
                            "trigger_fire_scan_mode": patch.trigger_fire_scan_mode,
                            "memory_recall_mode": patch.memory_recall_mode,
                        }.items()
                        if v is not None
                    }
                    | {"updated_at": now, "updated_by": actor_id},
                )
            self._rows[tenant_id] = row
            return row
