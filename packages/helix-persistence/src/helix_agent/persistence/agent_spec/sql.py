"""SQLAlchemy-backed :class:`AgentSpecStore` (Postgres / asyncpg)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from helix_agent.persistence.agent_spec.base import AgentSpecStore, DuplicateAgentSpecError
from helix_agent.persistence.models import AgentSpecRow
from helix_agent.protocol import AgentSpec, AgentSpecRecord, AgentSpecStatus


def _row_to_record(row: AgentSpecRow) -> AgentSpecRecord:
    return AgentSpecRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        name=row.name,
        version=row.version,
        spec=AgentSpec.model_validate(row.spec_json),
        spec_sha256=row.spec_sha256,
        status=AgentSpecStatus(row.status),
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlAgentSpecStore(AgentSpecStore):
    """Postgres-backed manifest registry."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create(
        self,
        *,
        tenant_id: UUID,
        spec: AgentSpec,
        spec_sha256: str,
        created_by: str,
    ) -> AgentSpecRecord:
        now = datetime.now(UTC)
        row = AgentSpecRow(
            tenant_id=tenant_id,
            name=spec.metadata.name,
            version=spec.metadata.version,
            spec_json=spec.model_dump(by_alias=True, mode="json"),
            spec_sha256=spec_sha256,
            status=AgentSpecStatus.ACTIVE.value,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        async with self._sf() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                raise DuplicateAgentSpecError(
                    tenant_id=tenant_id,
                    name=spec.metadata.name,
                    version=spec.metadata.version,
                ) from exc
            await session.refresh(row)
            return _row_to_record(row)

    async def get(
        self,
        *,
        tenant_id: UUID,
        name: str,
        version: str,
        include_deleted: bool = False,
    ) -> AgentSpecRecord | None:
        stmt = select(AgentSpecRow).where(
            AgentSpecRow.tenant_id == tenant_id,
            AgentSpecRow.name == name,
            AgentSpecRow.version == version,
        )
        if not include_deleted:
            stmt = stmt.where(AgentSpecRow.status != AgentSpecStatus.DELETED.value)
        async with self._sf() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
        return _row_to_record(row) if row is not None else None

    async def list_by_tenant(
        self,
        *,
        tenant_id: UUID,
        status: AgentSpecStatus | None = None,
        name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AgentSpecRecord]:
        stmt = select(AgentSpecRow).where(AgentSpecRow.tenant_id == tenant_id)
        if status is not None:
            stmt = stmt.where(AgentSpecRow.status == status.value)
        if name is not None:
            stmt = stmt.where(AgentSpecRow.name == name)
        stmt = stmt.order_by(AgentSpecRow.created_at.desc()).limit(limit).offset(offset)
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_record(r) for r in rows]

    async def list_all_tenants(
        self,
        *,
        status: AgentSpecStatus | None = None,
        name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AgentSpecRecord]:
        # Stream N (Mini-ADR N-4) — no tenant_id WHERE clause; caller MUST
        # have ``bypass_rls_var=True`` or RLS filters everything out.
        stmt = select(AgentSpecRow)
        if status is not None:
            stmt = stmt.where(AgentSpecRow.status == status.value)
        if name is not None:
            stmt = stmt.where(AgentSpecRow.name == name)
        stmt = stmt.order_by(AgentSpecRow.created_at.desc()).limit(limit).offset(offset)
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_record(r) for r in rows]

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
        # ``updated_by`` is currently captured only via the surrounding
        # audit_log row (B.5 emits manifest:write); the column stays the
        # original creator. M1 introduces a row history table.
        _ = updated_by
        stmt = (
            update(AgentSpecRow)
            .where(
                AgentSpecRow.tenant_id == tenant_id,
                AgentSpecRow.name == name,
                AgentSpecRow.version == version,
                AgentSpecRow.status != AgentSpecStatus.DELETED.value,
            )
            .values(
                spec_json=spec.model_dump(by_alias=True, mode="json"),
                spec_sha256=spec_sha256,
                updated_at=datetime.now(UTC),
            )
            .returning(AgentSpecRow)
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            await session.commit()
            row = result.scalar_one_or_none()
        return _row_to_record(row) if row is not None else None

    async def update_status(
        self,
        *,
        tenant_id: UUID,
        name: str,
        version: str,
        status: AgentSpecStatus,
    ) -> AgentSpecRecord | None:
        stmt = (
            update(AgentSpecRow)
            .where(
                AgentSpecRow.tenant_id == tenant_id,
                AgentSpecRow.name == name,
                AgentSpecRow.version == version,
            )
            .values(status=status.value, updated_at=datetime.now(UTC))
            .returning(AgentSpecRow)
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            await session.commit()
            row = result.scalar_one_or_none()
        return _row_to_record(row) if row is not None else None
