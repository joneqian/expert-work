"""SQLAlchemy-backed ``TenantMemberStore`` (Postgres / asyncpg)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from helix_agent.persistence.models import TenantMemberRow
from helix_agent.persistence.tenant_member.base import (
    DuplicateMemberError,
    TenantMemberStore,
)
from helix_agent.persistence.tenant_member.transitions import LEGAL_PREDECESSORS
from helix_agent.protocol import MemberRole, MemberStatus, TenantMember


def _row_to_member(row: TenantMemberRow) -> TenantMember:
    return TenantMember(
        id=row.id,
        tenant_id=row.tenant_id,
        email=row.email,
        display_name=row.display_name,
        role=row.role,  # type: ignore[arg-type]
        status=row.status,  # type: ignore[arg-type]
        keycloak_user_id=row.keycloak_user_id,
        subject_id=row.subject_id,
        invited_by=row.invited_by,
        invited_at=row.invited_at,
        activated_at=row.activated_at,
        updated_at=row.updated_at,
    )


class SqlTenantMemberStore(TenantMemberStore):
    """Postgres-backed invitation-state roster repository."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create(
        self,
        *,
        tenant_id: UUID,
        email: str,
        role: MemberRole,
        invited_by: str,
        display_name: str | None = None,
        keycloak_user_id: str | None = None,
    ) -> TenantMember:
        row = TenantMemberRow(
            tenant_id=tenant_id,
            email=email,
            display_name=display_name,
            role=role,
            status="invited",
            keycloak_user_id=keycloak_user_id,
            invited_by=invited_by,
        )
        async with self._sf() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                # The only unique constraint on an inserted row is the
                # partial-unique active-email index (Mini-ADR R-10).
                raise DuplicateMemberError(tenant_id=tenant_id, email=email) from exc
            await session.refresh(row)
            return _row_to_member(row)

    async def get(self, *, tenant_id: UUID, member_id: UUID) -> TenantMember | None:
        async with self._sf() as session:
            row = await session.get(TenantMemberRow, member_id)
            if row is None or row.tenant_id != tenant_id:
                return None
            return _row_to_member(row)

    async def get_by_keycloak_user_id(self, *, keycloak_user_id: str) -> TenantMember | None:
        stmt = select(TenantMemberRow).where(TenantMemberRow.keycloak_user_id == keycloak_user_id)
        async with self._sf() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return _row_to_member(row) if row is not None else None

    async def list_for_tenant(
        self,
        *,
        tenant_id: UUID,
        status: MemberStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TenantMember]:
        stmt = select(TenantMemberRow).where(TenantMemberRow.tenant_id == tenant_id)
        if status is not None:
            stmt = stmt.where(TenantMemberRow.status == status)
        stmt = stmt.order_by(TenantMemberRow.invited_at.desc()).limit(limit).offset(offset)
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [_row_to_member(r) for r in rows]

    async def set_keycloak_user_id(self, *, member_id: UUID, keycloak_user_id: str) -> None:
        stmt = (
            update(TenantMemberRow)
            .where(TenantMemberRow.id == member_id)
            .values(keycloak_user_id=keycloak_user_id)
        )
        async with self._sf() as session:
            await session.execute(stmt)
            await session.commit()

    async def transition(
        self,
        *,
        member_id: UUID,
        tenant_id: UUID,
        to: MemberStatus,
        now: datetime,
        subject_id: UUID | None = None,
    ) -> bool:
        legal_from = LEGAL_PREDECESSORS[to]
        if not legal_from:
            return False
        values: dict[str, object] = {"status": to, "updated_at": now}
        if to == "active":
            values["activated_at"] = now
            if subject_id is not None:
                values["subject_id"] = subject_id
        # Optimistic guard: WHERE status IN <legal predecessors> makes the
        # transition atomic and idempotent — a racing or illegal move matches
        # zero rows and returns False.
        stmt = (
            update(TenantMemberRow)
            .where(
                TenantMemberRow.id == member_id,
                TenantMemberRow.tenant_id == tenant_id,
                TenantMemberRow.status.in_(tuple(legal_from)),
            )
            .values(**values)
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            await session.commit()
            # ``Result.rowcount`` is only typed on ``CursorResult``; the
            # asyncpg backend returns one, so read it defensively.
            return int(getattr(result, "rowcount", 0) or 0) == 1
