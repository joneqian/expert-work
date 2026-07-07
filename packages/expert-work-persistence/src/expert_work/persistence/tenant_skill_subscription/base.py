"""Persistence Protocol for tenant skill subscriptions — Skill Marketplace."""

from __future__ import annotations

import abc
from uuid import UUID

from expert_work.protocol import TenantSkillSubscriptionRecord


class TenantSkillSubscriptionNotFoundError(Exception):
    """No ``tenant_skill_subscription`` row for (tenant, platform_skill_id)."""

    def __init__(self, *, tenant_id: UUID, platform_skill_id: UUID) -> None:
        super().__init__(
            f"tenant_skill_subscription not found: tenant_id={tenant_id} "
            f"platform_skill_id={platform_skill_id}"
        )
        self.tenant_id = tenant_id
        self.platform_skill_id = platform_skill_id


class TenantSkillSubscriptionStore(abc.ABC):
    """Tenant→platform-skill subscription markers (semantic A: accounting/UX
    only, never gates the runtime resolver)."""

    @abc.abstractmethod
    async def subscribe(
        self,
        *,
        tenant_id: UUID,
        platform_skill_id: UUID,
        created_by: str,
    ) -> TenantSkillSubscriptionRecord:
        """Subscribe the tenant to a platform skill. Idempotent: if a row
        already exists (including a soft-cancelled ``enabled=false`` one), it is
        re-enabled and returned."""

    @abc.abstractmethod
    async def set_enabled(
        self,
        *,
        tenant_id: UUID,
        platform_skill_id: UUID,
        enabled: bool,
    ) -> TenantSkillSubscriptionRecord:
        """Flip the ``enabled`` flag. Raises
        :class:`TenantSkillSubscriptionNotFoundError` if absent."""

    @abc.abstractmethod
    async def unsubscribe(self, *, tenant_id: UUID, platform_skill_id: UUID) -> None:
        """Hard-delete the row. Raises
        :class:`TenantSkillSubscriptionNotFoundError` if absent.

        The marketplace cancel flow uses the soft :meth:`set_enabled` path; this
        is the hard variant for completeness / admin cleanup."""

    @abc.abstractmethod
    async def list_for_tenant(self, *, tenant_id: UUID) -> list[TenantSkillSubscriptionRecord]:
        """Return all subscription rows for the tenant (enabled and disabled),
        ordered by ``created_at``."""

    @abc.abstractmethod
    async def is_subscribed(self, *, tenant_id: UUID, platform_skill_id: UUID) -> bool:
        """True iff an ``enabled=true`` row exists. Reserved for a future
        semantic-B runtime gate; unused on the current hot path."""
