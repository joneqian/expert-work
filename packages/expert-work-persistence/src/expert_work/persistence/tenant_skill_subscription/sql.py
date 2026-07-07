"""Postgres-backed :class:`TenantSkillSubscriptionStore` — Skill Marketplace."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from expert_work.persistence.models import TenantSkillSubscriptionRow
from expert_work.persistence.tenant_skill_subscription.base import (
    TenantSkillSubscriptionNotFoundError,
    TenantSkillSubscriptionStore,
)
from expert_work.protocol import TenantSkillSubscriptionRecord


def _row_to_record(row: TenantSkillSubscriptionRow) -> TenantSkillSubscriptionRecord:
    return TenantSkillSubscriptionRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        platform_skill_id=row.platform_skill_id,
        enabled=row.enabled,
        created_at=row.created_at,
        created_by=row.created_by,
    )


class SqlTenantSkillSubscriptionStore(TenantSkillSubscriptionStore):
    """Postgres-backed subscription store (RLS-scoped sessions)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def subscribe(
        self,
        *,
        tenant_id: UUID,
        platform_skill_id: UUID,
        created_by: str,
    ) -> TenantSkillSubscriptionRecord:
        # Idempotent upsert: an existing (possibly soft-cancelled) row is
        # re-enabled; created_at / created_by stay from the original insert.
        stmt = (
            pg_insert(TenantSkillSubscriptionRow)
            .values(
                tenant_id=tenant_id,
                platform_skill_id=platform_skill_id,
                enabled=True,
                created_by=created_by,
            )
            .on_conflict_do_update(
                index_elements=["tenant_id", "platform_skill_id"],
                set_={"enabled": True},
            )
            .returning(TenantSkillSubscriptionRow)
        )
        async with self._sf() as session:
            row = (await session.execute(stmt)).scalar_one()
            await session.commit()
            await session.refresh(row)
            return _row_to_record(row)

    async def set_enabled(
        self,
        *,
        tenant_id: UUID,
        platform_skill_id: UUID,
        enabled: bool,
    ) -> TenantSkillSubscriptionRecord:
        async with self._sf() as session:
            stmt = select(TenantSkillSubscriptionRow).where(
                TenantSkillSubscriptionRow.tenant_id == tenant_id,
                TenantSkillSubscriptionRow.platform_skill_id == platform_skill_id,
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if existing is None:
                raise TenantSkillSubscriptionNotFoundError(
                    tenant_id=tenant_id, platform_skill_id=platform_skill_id
                )
            existing.enabled = enabled
            record = _row_to_record(existing)
            await session.commit()
            return record

    async def unsubscribe(self, *, tenant_id: UUID, platform_skill_id: UUID) -> None:
        stmt = (
            sa_delete(TenantSkillSubscriptionRow)
            .where(
                TenantSkillSubscriptionRow.tenant_id == tenant_id,
                TenantSkillSubscriptionRow.platform_skill_id == platform_skill_id,
            )
            .returning(TenantSkillSubscriptionRow.id)
        )
        async with self._sf() as session:
            deleted = (await session.execute(stmt)).scalar_one_or_none()
            await session.commit()
        if deleted is None:
            raise TenantSkillSubscriptionNotFoundError(
                tenant_id=tenant_id, platform_skill_id=platform_skill_id
            )

    async def list_for_tenant(self, *, tenant_id: UUID) -> list[TenantSkillSubscriptionRecord]:
        stmt = (
            select(TenantSkillSubscriptionRow)
            .where(TenantSkillSubscriptionRow.tenant_id == tenant_id)
            .order_by(TenantSkillSubscriptionRow.created_at)
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_record(r) for r in rows]

    async def is_subscribed(self, *, tenant_id: UUID, platform_skill_id: UUID) -> bool:
        stmt = select(TenantSkillSubscriptionRow.id).where(
            TenantSkillSubscriptionRow.tenant_id == tenant_id,
            TenantSkillSubscriptionRow.platform_skill_id == platform_skill_id,
            TenantSkillSubscriptionRow.enabled.is_(True),
        )
        async with self._sf() as session:
            found = (await session.execute(stmt)).scalar_one_or_none()
        return found is not None
