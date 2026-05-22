"""In-memory ``TriggerStore`` for unit tests."""

from __future__ import annotations

from uuid import UUID

from helix_agent.persistence.trigger.base import TriggerStore
from helix_agent.protocol import TriggerRecord


class InMemoryTriggerStore(TriggerStore):
    """In-memory ``TriggerStore`` — keyed by trigger id."""

    def __init__(self) -> None:
        self._rows: dict[UUID, TriggerRecord] = {}

    async def create(self, record: TriggerRecord) -> TriggerRecord:
        for existing in self._rows.values():
            if (
                existing.tenant_id == record.tenant_id
                and existing.agent_name == record.agent_name
                and existing.name == record.name
            ):
                msg = f"trigger {record.name!r} already exists for agent {record.agent_name!r}"
                raise ValueError(msg)
        self._rows[record.id] = record
        return record

    async def get(self, *, trigger_id: UUID, tenant_id: UUID) -> TriggerRecord | None:
        row = self._rows.get(trigger_id)
        if row is None or row.tenant_id != tenant_id:
            return None
        return row

    async def list_by_agent(self, *, tenant_id: UUID, agent_name: str) -> list[TriggerRecord]:
        return [
            r
            for r in self._rows.values()
            if r.tenant_id == tenant_id and r.agent_name == agent_name
        ]

    async def list_enabled_cron(self) -> list[TriggerRecord]:
        return [r for r in self._rows.values() if r.kind == "cron" and r.enabled]

    async def update(self, record: TriggerRecord) -> bool:
        existing = self._rows.get(record.id)
        if existing is None or existing.tenant_id != record.tenant_id:
            return False
        self._rows[record.id] = record
        return True

    async def delete(self, *, trigger_id: UUID, tenant_id: UUID) -> bool:
        row = self._rows.get(trigger_id)
        if row is None or row.tenant_id != tenant_id:
            return False
        del self._rows[trigger_id]
        return True
