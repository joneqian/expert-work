"""SQLAlchemy-backed ``TriggerStore`` — Stream J.10 (Mini-ADR J-26 / J-42)."""

from __future__ import annotations

from typing import cast
from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from helix_agent.persistence.models import AgentTriggerRow
from helix_agent.persistence.trigger.base import TriggerStore
from helix_agent.protocol import TriggerKind, TriggerRecord, TriggerSource


def _row_to_dto(row: AgentTriggerRow) -> TriggerRecord:
    return TriggerRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        agent_name=row.agent_name,
        agent_version=row.agent_version,
        name=row.name,
        kind=cast(TriggerKind, row.kind),
        config=dict(row.config or {}),
        enabled=row.enabled,
        source=cast(TriggerSource, row.source),
        webhook_secret_hash=row.webhook_secret_hash,
        last_fired_at=row.last_fired_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlTriggerStore(TriggerStore):
    """Postgres-backed trigger registry — the ``agent_trigger`` table."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create(self, record: TriggerRecord) -> TriggerRecord:
        async with self._sf() as session:
            session.add(
                AgentTriggerRow(
                    id=record.id,
                    tenant_id=record.tenant_id,
                    user_id=record.user_id,
                    agent_name=record.agent_name,
                    agent_version=record.agent_version,
                    name=record.name,
                    kind=record.kind,
                    config=dict(record.config),
                    enabled=record.enabled,
                    source=record.source,
                    webhook_secret_hash=record.webhook_secret_hash,
                    last_fired_at=record.last_fired_at,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                )
            )
            await session.commit()
        return record

    async def get(self, *, trigger_id: UUID, tenant_id: UUID) -> TriggerRecord | None:
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(AgentTriggerRow).where(
                        AgentTriggerRow.id == trigger_id,
                        AgentTriggerRow.tenant_id == tenant_id,
                    )
                )
            ).scalar_one_or_none()
        return _row_to_dto(row) if row is not None else None

    async def list_by_agent(self, *, tenant_id: UUID, agent_name: str) -> list[TriggerRecord]:
        async with self._sf() as session:
            rows = (
                (
                    await session.execute(
                        select(AgentTriggerRow)
                        .where(
                            AgentTriggerRow.tenant_id == tenant_id,
                            AgentTriggerRow.agent_name == agent_name,
                        )
                        .order_by(AgentTriggerRow.created_at.asc())
                    )
                )
                .scalars()
                .all()
            )
        return [_row_to_dto(r) for r in rows]

    async def list_enabled_cron(self) -> list[TriggerRecord]:
        async with self._sf() as session:
            rows = (
                (
                    await session.execute(
                        select(AgentTriggerRow)
                        .where(
                            AgentTriggerRow.kind == "cron",
                            AgentTriggerRow.enabled.is_(True),
                        )
                        .order_by(AgentTriggerRow.created_at.asc())
                    )
                )
                .scalars()
                .all()
            )
        return [_row_to_dto(r) for r in rows]

    async def update(self, record: TriggerRecord) -> bool:
        async with self._sf() as session:
            result = await session.execute(
                sa_update(AgentTriggerRow)
                .where(
                    AgentTriggerRow.id == record.id,
                    AgentTriggerRow.tenant_id == record.tenant_id,
                )
                .values(
                    user_id=record.user_id,
                    agent_version=record.agent_version,
                    name=record.name,
                    kind=record.kind,
                    config=dict(record.config),
                    enabled=record.enabled,
                    source=record.source,
                    webhook_secret_hash=record.webhook_secret_hash,
                    last_fired_at=record.last_fired_at,
                    updated_at=record.updated_at,
                )
            )
            await session.commit()
        return int(getattr(result, "rowcount", 0) or 0) > 0

    async def delete(self, *, trigger_id: UUID, tenant_id: UUID) -> bool:
        async with self._sf() as session:
            result = await session.execute(
                sa_delete(AgentTriggerRow).where(
                    AgentTriggerRow.id == trigger_id,
                    AgentTriggerRow.tenant_id == tenant_id,
                )
            )
            await session.commit()
        return int(getattr(result, "rowcount", 0) or 0) > 0
