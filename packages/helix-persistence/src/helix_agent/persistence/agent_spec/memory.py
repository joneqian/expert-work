"""In-memory :class:`AgentSpecStore` — used by control-plane tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

from helix_agent.persistence.agent_spec.base import AgentSpecStore, DuplicateAgentSpecError
from helix_agent.protocol import AgentSpec, AgentSpecRecord, AgentSpecStatus


def _now() -> datetime:
    return datetime.now(tz=UTC)


class InMemoryAgentSpecStore(AgentSpecStore):
    def __init__(self) -> None:
        self._rows: dict[tuple[UUID, str, str], AgentSpecRecord] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        *,
        tenant_id: UUID,
        spec: AgentSpec,
        spec_sha256: str,
        created_by: str,
    ) -> AgentSpecRecord:
        name = spec.metadata.name
        version = spec.metadata.version
        async with self._lock:
            key = (tenant_id, name, version)
            if key in self._rows:
                raise DuplicateAgentSpecError(tenant_id=tenant_id, name=name, version=version)
            now = _now()
            record = AgentSpecRecord(
                id=uuid4(),
                tenant_id=tenant_id,
                name=name,
                version=version,
                spec=spec,
                spec_sha256=spec_sha256,
                status=AgentSpecStatus.ACTIVE,
                created_by=created_by,
                created_at=now,
                updated_at=now,
            )
            self._rows[key] = record
            return record

    async def get(
        self,
        *,
        tenant_id: UUID,
        name: str,
        version: str,
        include_deleted: bool = False,
    ) -> AgentSpecRecord | None:
        async with self._lock:
            record = self._rows.get((tenant_id, name, version))
        if record is None:
            return None
        if not include_deleted and record.status is AgentSpecStatus.DELETED:
            return None
        return record

    async def list_by_tenant(
        self,
        *,
        tenant_id: UUID,
        status: AgentSpecStatus | None = None,
        name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AgentSpecRecord]:
        async with self._lock:
            matched = [
                r
                for r in self._rows.values()
                if r.tenant_id == tenant_id
                and (status is None or r.status is status)
                and (name is None or r.name == name)
            ]
        matched.sort(key=lambda r: r.created_at, reverse=True)
        return matched[offset : offset + limit]

    async def update_spec(
        self,
        *,
        tenant_id: UUID,
        name: str,
        version: str,
        spec: AgentSpec,
        spec_sha256: str,
        updated_by: str,
    ) -> AgentSpecRecord | None:
        async with self._lock:
            key = (tenant_id, name, version)
            existing = self._rows.get(key)
            if existing is None or existing.status is AgentSpecStatus.DELETED:
                return None
            replaced = existing.model_copy(
                update={
                    "spec": spec,
                    "spec_sha256": spec_sha256,
                    "updated_at": _now(),
                }
            )
            # The signature accepts ``updated_by`` for parity with the SQL
            # impl, but ``created_by`` is the row's permanent provenance.
            _ = updated_by
            self._rows[key] = replaced
            return replaced

    async def update_status(
        self,
        *,
        tenant_id: UUID,
        name: str,
        version: str,
        status: AgentSpecStatus,
    ) -> AgentSpecRecord | None:
        async with self._lock:
            key = (tenant_id, name, version)
            existing = self._rows.get(key)
            if existing is None:
                return None
            replaced = existing.model_copy(update={"status": status, "updated_at": _now()})
            self._rows[key] = replaced
            return replaced
