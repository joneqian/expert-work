"""``run_event`` ORM — Stream H.3 PR 3 (Mini-ADR H-7).

Mirrors migration 0038. Tenant RLS is enforced by the migration's
policy walking the FK to ``agent_run.tenant_id``; the model carries no
``tenant_id`` column of its own — that lives on the parent row.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, ForeignKey, PrimaryKeyConstraint, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from expert_work.persistence.base import Base


class RunEventRow(Base):
    """One persisted SSE frame for a run (Mini-ADR H-7 decision A)."""

    __tablename__ = "run_event"

    run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agent_run.id", ondelete="RESTRICT"),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_name: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[Any] = mapped_column(JSONB, nullable=False)
    created_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (PrimaryKeyConstraint("run_id", "seq", name="pk_run_event"),)
