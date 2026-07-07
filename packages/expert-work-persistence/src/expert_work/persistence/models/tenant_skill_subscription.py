"""``tenant_skill_subscription`` ORM model — Skill Marketplace Phase 1.

A tenant's "I selected this platform skill" marker. RLS (tenant isolation) is
declared in migration ``0086_tenant_skill_subscription``, not here — the model
is purely structural (mirrors ``tenant_mcp_server``).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from expert_work.persistence.base import Base


class TenantSkillSubscriptionRow(Base):
    """One tenant→platform-skill subscription marker."""

    __tablename__ = "tenant_skill_subscription"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    # No FK — the platform skill is a NULL-tenant row (cross-RLS) and the
    # association is soft (a deleted platform skill leaves a harmless dangling
    # row). UNIQUE(tenant_id, platform_skill_id) declared in migration 0086.
    platform_skill_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
