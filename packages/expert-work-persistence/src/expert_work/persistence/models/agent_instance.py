"""``agent_instance`` ORM model — Stream Agent-Templates (M1-5b).

Per-(tenant, agent_code, end-user) binding. Tenant-scoped RLS is declared in
migration ``0097_agent_instance`` (the canonical tenant-isolation policy), not
here — the model is purely structural.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Index, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from expert_work.persistence.base import Base


class AgentInstanceRow(Base):
    """One end-user's binding to a tenant agent (the per-user "instance" anchor)."""

    __tablename__ = "agent_instance"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    # The fork's name (agent_code) — the tenant agent this end-user uses.
    agent_code: Mapped[str] = mapped_column(Text, nullable=False)
    # The end-user (tenant_user.id) this instance belongs to.
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_code", "user_id", name="agent_instance_identity_uniq"),
        Index("agent_instance_tenant_agent_idx", "tenant_id", "agent_code"),
        Index("agent_instance_tenant_user_idx", "tenant_id", "user_id"),
    )
