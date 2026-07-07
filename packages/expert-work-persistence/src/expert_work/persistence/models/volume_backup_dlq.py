"""``volume_backup_dlq`` ORM model — Stream J.15-补强-2.

Dead-letter queue for the J.15 volume backup + archive pipeline. Same
backoff schedule (1m → 5m → 30m → 2h → 6h, then dead-letter at 365 d)
and shape as :class:`MemoryWritebackDLQRow` (Stream K.K7); the
``op_kind`` column distinguishes the two operations sharing the table:

* ``"archive"`` — physical archive of a soft-deleted workspace
  (Mini-ADR J-36 lifecycle 第 2 档 → 第 3 档).
* ``"backup"`` — daily snapshot of an active workspace
  (Mini-ADR J-29 第 2 项 DR pipeline).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from expert_work.persistence.base import Base


class VolumeBackupDLQRow(Base):
    """One pending volume archive / backup that failed to land."""

    __tablename__ = "volume_backup_dlq"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    volume_name: Mapped[str] = mapped_column(Text, nullable=False)
    #: One of {"archive", "backup"}; the worker uses a different ObjectStore
    #: prefix per kind.
    op_kind: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    next_retry_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint("op_kind IN ('archive', 'backup')", name="volume_backup_dlq_op_kind"),
        Index("volume_backup_dlq_ready_idx", "next_retry_at"),
    )
