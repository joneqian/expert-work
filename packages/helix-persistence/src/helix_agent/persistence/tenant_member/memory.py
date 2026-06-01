"""In-memory ``TenantMemberStore`` for unit tests."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from helix_agent.persistence.tenant_member.base import (
    DuplicateMemberError,
    TenantMemberStore,
)
from helix_agent.persistence.tenant_member.transitions import LEGAL_PREDECESSORS
from helix_agent.protocol import MemberRole, MemberStatus, TenantMember


class InMemoryTenantMemberStore(TenantMemberStore):
    def __init__(self) -> None:
        self._rows: dict[UUID, TenantMember] = {}

    def _active_invite_exists(self, *, tenant_id: UUID, email: str) -> bool:
        key = email.lower()
        return any(
            row.tenant_id == tenant_id and row.email.lower() == key and row.status != "revoked"
            for row in self._rows.values()
        )

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
        if self._active_invite_exists(tenant_id=tenant_id, email=email):
            raise DuplicateMemberError(tenant_id=tenant_id, email=email)
        member = TenantMember(
            id=uuid4(),
            tenant_id=tenant_id,
            email=email,
            display_name=display_name,
            role=role,
            status="invited",
            keycloak_user_id=keycloak_user_id,
            subject_id=None,
            invited_by=invited_by,
        )
        self._rows[member.id] = member
        return member

    async def get(self, *, tenant_id: UUID, member_id: UUID) -> TenantMember | None:
        row = self._rows.get(member_id)
        if row is None or row.tenant_id != tenant_id:
            return None
        return row

    async def get_by_keycloak_user_id(self, *, keycloak_user_id: str) -> TenantMember | None:
        for row in self._rows.values():
            if row.keycloak_user_id == keycloak_user_id:
                return row
        return None

    async def list_for_tenant(
        self,
        *,
        tenant_id: UUID,
        status: MemberStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TenantMember]:
        rows = [
            row
            for row in self._rows.values()
            if row.tenant_id == tenant_id and (status is None or row.status == status)
        ]
        # Newest invite first; rows without invited_at (never, in memory) sort last.
        rows.sort(key=lambda r: r.invited_at or datetime.min, reverse=True)
        return rows[offset : offset + limit]

    async def set_keycloak_user_id(self, *, member_id: UUID, keycloak_user_id: str) -> None:
        row = self._rows.get(member_id)
        if row is None:
            return
        self._rows[member_id] = row.model_copy(update={"keycloak_user_id": keycloak_user_id})

    async def transition(
        self,
        *,
        member_id: UUID,
        tenant_id: UUID,
        to: MemberStatus,
        now: datetime,
        subject_id: UUID | None = None,
    ) -> bool:
        row = self._rows.get(member_id)
        if row is None or row.tenant_id != tenant_id:
            return False
        if row.status not in LEGAL_PREDECESSORS[to]:
            return False
        update: dict[str, object] = {"status": to, "updated_at": now}
        if to == "active":
            update["activated_at"] = now
            if subject_id is not None:
                update["subject_id"] = subject_id
        self._rows[member_id] = row.model_copy(update=update)
        return True
