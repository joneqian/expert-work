"""``tenant_member`` ORM model — Stream R (member onboarding roster)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Index, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from helix_agent.persistence.base import Base


class TenantMemberRow(Base):
    """Invitation-state roster row — a member of a tenant (Stream R).

    Tenant-scoped, RLS-enabled (migration ``0051_tenant_member``). Distinct
    from ``tenant_user`` (runtime JIT registry): this is the control-plane
    source of truth for the ``invited → active → suspended / revoked``
    lifecycle, connected to ``tenant_user`` by ``keycloak_user_id`` (no FK).

    The partial-unique index ``tenant_member_active_email_uniq`` (created in
    the migration, ``WHERE status != 'revoked'``) enforces one active invite
    per ``(tenant_id, lower(email))`` — Mini-ADR R-10.
    """

    __tablename__ = "tenant_member"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    keycloak_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    invited_by: Mapped[str] = mapped_column(Text, nullable=False)
    invited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('invited', 'active', 'suspended', 'revoked')",
            name="tenant_member_status_check",
        ),
        CheckConstraint(
            "status != 'active' OR (keycloak_user_id IS NOT NULL AND activated_at IS NOT NULL)",
            name="tenant_member_active_consistency",
        ),
        Index("tenant_member_tenant_idx", "tenant_id"),
        Index(
            "tenant_member_kc_user_idx",
            "keycloak_user_id",
            postgresql_where=text("keycloak_user_id IS NOT NULL"),
        ),
    )
