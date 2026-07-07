"""``tenant_user`` ORM model — Stream J.14 per-user scope registry."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Index, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from expert_work.persistence.base import Base


class TenantUserRow(Base):
    """Per-user registry — a principal acting within a tenant (Stream J.14).

    Tenant-scoped, RLS-enabled (migration ``0015_tenant_user``). The
    surrogate ``id`` is the ``user_id`` referenced by owned tables.
    """

    __tablename__ = "tenant_user"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    subject_type: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "subject_type",
            "subject_id",
            name="tenant_user_identity_uniq",
        ),
        Index("tenant_user_tenant_idx", "tenant_id"),
    )
