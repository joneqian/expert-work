"""``sandbox_instance`` ORM model — Stream F.1.

One row per ``exec_python`` sandbox container; see migration
``0012_sandbox_instance`` and STREAM-F-DESIGN § 4.6.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, Index, Integer, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from helix_agent.persistence.base import Base


class SandboxInstanceRow(Base):
    """The Sandbox Supervisor's record of one sandbox container's lifecycle."""

    __tablename__ = "sandbox_instance"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    image_ref: Mapped[str] = mapped_column(Text, nullable=False)
    node: Mapped[str] = mapped_column(Text, nullable=False)
    #: ``None`` while ``CREATING`` — set once ``docker run`` returns an id.
    container_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: CREATING / IN_USE / DESTROYED / FAILED — STREAM-F-DESIGN § 2.2.
    state: Mapped[str] = mapped_column(Text, nullable=False)
    thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    cpu_quota: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    memory_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    pids_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    timeout_s: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    destroyed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: release / idle_timeout / cancelled / oom — ``None`` until terminal.
    destroy_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("sandbox_instance_tenant_state_idx", "tenant_id", "state"),
        Index("sandbox_instance_state_acquired_idx", "state", "acquired_at"),
    )
