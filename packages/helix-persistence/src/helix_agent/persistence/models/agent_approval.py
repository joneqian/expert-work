"""``agent_approval`` ORM model — Stream J.8-step3a (Mini-ADR J-24).

Schema mirrors migration 0031_agent_approval exactly. Tenant RLS is
enforced at the row level by the migration's policy; the application
still passes ``tenant_id`` for clarity + so an in-memory backend can
match semantics without a Postgres GUC.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from helix_agent.persistence.base import Base

_STATUS_VALUES = "('pending', 'approved', 'rejected', 'modified', 'timeout')"


class AgentApprovalRow(Base):
    """One paused run awaiting (or having received) a human verdict."""

    __tablename__ = "agent_approval"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    thread_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    request_id: Mapped[str] = mapped_column(Text, nullable=False)
    node: Mapped[str] = mapped_column(Text, nullable=False)
    reason_kind: Mapped[str] = mapped_column(Text, nullable=False)
    action_summary: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_args: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timeout_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    decided_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    modified_args: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    # Stream 13.2 — idempotent resume. Written atomically with the decision
    # (the mark_decided CAS); a retry with a matching key reads back the
    # continuation run instead of conflicting. NULL for keyless / pending rows.
    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    continuation_run_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    __table_args__ = (
        CheckConstraint(f"status IN {_STATUS_VALUES}", name="agent_approval_status_valid"),
        UniqueConstraint("run_id", name="agent_approval_run_uniq"),
        Index("ix_agent_approval_tenant_id", "tenant_id"),
        Index("ix_agent_approval_thread_id", "thread_id"),
        Index(
            "ix_agent_approval_pending_timeout",
            "timeout_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )
