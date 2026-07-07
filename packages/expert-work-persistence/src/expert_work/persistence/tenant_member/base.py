"""Abstract ``TenantMemberStore`` repository — Stream R (member onboarding).

Implementations:
- :class:`expert_work.persistence.tenant_member.memory.InMemoryTenantMemberStore`
- :class:`expert_work.persistence.tenant_member.sql.SqlTenantMemberStore`
"""

from __future__ import annotations

import abc
from datetime import datetime
from uuid import UUID

from expert_work.protocol import MemberRole, MemberStatus, TenantMember


class DuplicateMemberError(Exception):
    """Raised when an active invite already exists for ``(tenant_id, lower(email))``.

    Maps to the partial-unique index ``tenant_member_active_email_uniq``
    (Mini-ADR R-10). A revoked row does not trip this — the email can be
    re-invited.
    """

    def __init__(self, *, tenant_id: UUID, email: str) -> None:
        super().__init__(f"active invite already exists: tenant_id={tenant_id} email={email!r}")
        self.tenant_id = tenant_id
        self.email = email


class TenantMemberStore(abc.ABC):
    """Invitation-state roster repository.

    Every method takes ``tenant_id`` explicitly — the tenant is the hard
    isolation boundary. ``get_by_keycloak_user_id`` is the one exception: it
    is the W3 first-login reverse lookup, called inside ``bypass_rls_session()``
    where the request has no ``app.tenant_id`` yet.
    """

    @abc.abstractmethod
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
        """Write a new roster row in ``invited`` status.

        ``email`` is stored verbatim but the identity key normalises with
        ``lower()``. Raises :class:`DuplicateMemberError` when an active
        (non-revoked) invite already exists for ``(tenant_id, lower(email))``.
        """

    @abc.abstractmethod
    async def get(self, *, tenant_id: UUID, member_id: UUID) -> TenantMember | None:
        """Read a member by id, filtered to ``tenant_id`` (never reveals cross-tenant)."""

    @abc.abstractmethod
    async def get_by_keycloak_user_id(self, *, keycloak_user_id: str) -> TenantMember | None:
        """Reverse lookup by Keycloak user id — the W3 first-login alignment path.

        Crosses tenants by design (the caller is in ``bypass_rls_session()``);
        ``keycloak_user_id`` is globally unique in Keycloak so this returns at
        most one row.
        """

    @abc.abstractmethod
    async def list_for_tenant(
        self,
        *,
        tenant_id: UUID,
        status: MemberStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TenantMember]:
        """List a tenant's members, newest invite first, optionally by status."""

    @abc.abstractmethod
    async def list_all_tenants(
        self,
        *,
        status: MemberStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TenantMember]:
        """Cross-tenant member roster — Stream ACCT (platform admin view).

        Returns members across every tenant, newest invite first. ``tenant_member``
        is FORCE-RLS, so the SQL implementation assumes the ``audit_reader``
        BYPASSRLS role (the ledger / audit cross-tenant precedent); the caller
        must be inside ``bypass_rls_session()`` so no tenant GUC is emitted.
        Reserved for ``system_admin`` principals.
        """

    @abc.abstractmethod
    async def set_keycloak_user_id(self, *, member_id: UUID, keycloak_user_id: str) -> None:
        """Back-fill ``keycloak_user_id`` after Keycloak account provisioning succeeds."""

    @abc.abstractmethod
    async def transition(
        self,
        *,
        member_id: UUID,
        tenant_id: UUID,
        to: MemberStatus,
        now: datetime,
        subject_id: UUID | None = None,
    ) -> bool:
        """Move a member to ``to`` if the current status is a legal predecessor.

        Optimistic: the underlying write guards on the legal predecessor set,
        so a racing transition (or an illegal one) returns ``False`` without
        mutating. ``to='active'`` back-fills ``subject_id`` + ``activated_at``.
        Returns ``True`` when the row moved.
        """
