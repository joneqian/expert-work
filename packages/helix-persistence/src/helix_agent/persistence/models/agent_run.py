"""``agent_run`` ORM model — Stream J.8 closeout follow-up (Mini-ADR J-41).

Schema mirrors migration 0032_agent_run exactly. Tenant RLS is enforced
at the row level by the migration's policy; the application still
passes ``tenant_id`` so an in-memory backend can match semantics
without a Postgres GUC.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from helix_agent.persistence.base import Base

_STATUS_VALUES = "('pending', 'running', 'success', 'error', 'timeout', 'interrupted', 'paused')"
_DISCONNECT_VALUES = "('cancel', 'continue')"


class AgentRunRow(Base):
    """One run's durable lifecycle row — the backing for ``RunManager``."""

    __tablename__ = "agent_run"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    thread_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    on_disconnect: Mapped[str] = mapped_column(Text, nullable=False)
    is_resume: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(f"status IN {_STATUS_VALUES}", name="agent_run_status_valid"),
        CheckConstraint(
            f"on_disconnect IN {_DISCONNECT_VALUES}", name="agent_run_on_disconnect_valid"
        ),
        Index("ix_agent_run_tenant_id", "tenant_id"),
        Index("ix_agent_run_thread_id", "thread_id"),
        Index(
            "ix_agent_run_thread_inflight",
            "thread_id",
            "status",
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
    )
