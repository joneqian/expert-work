"""``agent_disable`` ORM model — Stream RT-4 (RT-ADR-16, kill switch).

Per-(tenant, agent_name) emergency-stop flag. The composite primary key is
``(tenant_id, agent_name)`` — disable covers all versions of an agent name
(agents are stored per ``(name, version)`` in ``agent_spec``; the kill switch is
name-scoped and version-agnostic). Schema mirrors migration 0114_agent_disable.
Tenant RLS uses the standard ``app.tenant_id`` GUC pattern (same as
``agent_run``); a tenant admin operates only within their own scope.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from helix_agent.persistence.base import Base


class AgentDisableRow(Base):
    """One agent-level kill-switch row (one per tenant + agent name)."""

    __tablename__ = "agent_disable"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    agent_name: Mapped[str] = mapped_column(Text, primary_key=True)
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    disabled_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
