"""In-memory :class:`TenantMcpServerStore` — Stream V."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

from helix_agent.persistence.tenant_mcp_server.base import (
    TenantMcpServerAlreadyExistsError,
    TenantMcpServerNotFoundError,
    TenantMcpServerStore,
)
from helix_agent.protocol import (
    McpServerAuthType,
    McpServerTransport,
    TenantMcpServerPatch,
    TenantMcpServerRecord,
)


def _now() -> datetime:
    return datetime.now(tz=UTC)


class InMemoryTenantMcpServerStore(TenantMcpServerStore):
    """Dict-backed store keyed by ``(tenant_id, name)``; lock-guarded."""

    def __init__(self) -> None:
        self._rows: dict[tuple[UUID, str], TenantMcpServerRecord] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        *,
        tenant_id: UUID,
        name: str,
        transport: McpServerTransport,
        url: str,
        auth_type: McpServerAuthType,
        token_secret_ref: str | None,
        timeout_s: float,
        created_by: str,
        catalog_id: UUID | None = None,
    ) -> TenantMcpServerRecord:
        async with self._lock:
            key = (tenant_id, name)
            if key in self._rows:
                raise TenantMcpServerAlreadyExistsError(tenant_id=tenant_id, name=name)
            now = _now()
            record = TenantMcpServerRecord(
                id=uuid4(),
                tenant_id=tenant_id,
                name=name,
                transport=transport,
                url=url,
                auth_type=auth_type,
                token_secret_ref=token_secret_ref,
                catalog_id=catalog_id,
                timeout_s=timeout_s,
                enabled=True,
                created_at=now,
                updated_at=now,
                created_by=created_by,
            )
            self._rows[key] = record
            return record

    async def get(self, *, tenant_id: UUID, name: str) -> TenantMcpServerRecord | None:
        async with self._lock:
            return self._rows.get((tenant_id, name))

    async def list_for_tenant(self, *, tenant_id: UUID) -> list[TenantMcpServerRecord]:
        async with self._lock:
            rows = [r for (tid, _), r in self._rows.items() if tid == tenant_id]
        return sorted(rows, key=lambda r: r.name)

    async def update(
        self, *, tenant_id: UUID, name: str, patch: TenantMcpServerPatch
    ) -> TenantMcpServerRecord:
        async with self._lock:
            key = (tenant_id, name)
            existing = self._rows.get(key)
            if existing is None:
                raise TenantMcpServerNotFoundError(tenant_id=tenant_id, name=name)
            # patch field == None means "leave unchanged" (not "clear");
            # auth-type/token changes go via delete+recreate.
            changes: dict[str, object] = {"updated_at": _now()}
            if patch.url is not None:
                changes["url"] = patch.url
            if patch.token_secret_ref is not None:
                changes["token_secret_ref"] = patch.token_secret_ref
            if patch.timeout_s is not None:
                changes["timeout_s"] = patch.timeout_s
            if patch.enabled is not None:
                changes["enabled"] = patch.enabled
            updated = TenantMcpServerRecord.model_validate(
                existing.model_copy(update=changes).model_dump()
            )
            self._rows[key] = updated
            return updated

    async def delete(self, *, tenant_id: UUID, name: str) -> None:
        async with self._lock:
            key = (tenant_id, name)
            if key not in self._rows:
                raise TenantMcpServerNotFoundError(tenant_id=tenant_id, name=name)
            del self._rows[key]
