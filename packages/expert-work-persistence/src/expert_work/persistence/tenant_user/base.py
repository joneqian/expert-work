"""Abstract ``TenantUserStore`` repository — Stream J.14.

Implementations:
- :class:`expert_work.persistence.tenant_user.memory.InMemoryTenantUserStore`
- :class:`expert_work.persistence.tenant_user.sql.SqlTenantUserStore`
"""

from __future__ import annotations

import abc
from collections.abc import Collection
from uuid import UUID

from expert_work.protocol import SubjectType, TenantUser


class TenantUserStore(abc.ABC):
    """Per-user registry repository.

    Every method takes ``tenant_id`` explicitly — the tenant is the hard
    isolation boundary. ``user_id`` (the surrogate ``TenantUser.id``) is
    an application-layer ownership scope.
    """

    @abc.abstractmethod
    async def resolve(
        self,
        *,
        tenant_id: UUID,
        subject_type: SubjectType,
        subject_id: str,
        display_name: str | None = None,
    ) -> TenantUser:
        """Return the registry row for this principal, creating it if absent.

        Idempotent upsert keyed by ``(tenant_id, subject_type,
        subject_id)``. ``last_active_at`` is bumped to *now* on every
        call; ``display_name`` overwrites the stored value only when a
        non-``None`` value is supplied.
        """

    @abc.abstractmethod
    async def get(self, user_id: UUID, *, tenant_id: UUID) -> TenantUser | None:
        """Read a user by surrogate id, filtered to ``tenant_id``.

        Returns ``None`` when the row does not exist or belongs to a
        different tenant — never reveals cross-tenant existence.
        """

    @abc.abstractmethod
    async def get_many(
        self, user_ids: Collection[UUID], *, tenant_id: UUID
    ) -> dict[UUID, TenantUser]:
        """Batch :meth:`get` — one read for the M2 users rollup.

        Returns a map keyed by ``TenantUser.id``; ids that don't exist or
        belong to a different tenant are simply absent (same non-disclosure
        semantics as :meth:`get`). An empty ``user_ids`` returns ``{}``.
        """
