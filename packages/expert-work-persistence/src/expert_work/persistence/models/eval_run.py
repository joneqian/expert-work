"""``eval_run`` + ``eval_case_result`` ORM models — P1-S2.1.

Schema mirrors migration 0076_eval_run exactly. Tenant RLS (ENABLE +
FORCE) is enforced at the row level by the migration's policies; the
application still passes ``tenant_id`` so an in-memory backend matches
semantics without a Postgres GUC.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from expert_work.persistence.base import Base

_STATUS_VALUES = "('queued', 'running', 'passed', 'failed', 'error')"
_TRIGGER_VALUES = "('manual', 'ci', 'schedule')"


class EvalRunRow(Base):
    """One execution of an eval suite."""

    __tablename__ = "eval_run"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    suite: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'"))
    triggered_by: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(f"status IN {_STATUS_VALUES}", name="eval_run_status_valid"),
        CheckConstraint(f"triggered_by IN {_TRIGGER_VALUES}", name="eval_run_triggered_by_valid"),
        Index("ix_eval_run_tenant_id", "tenant_id"),
        Index(
            "ix_eval_run_queued",
            "created_at",
            postgresql_where=text("status = 'queued'"),
        ),
    )


class EvalCaseResultRow(Base):
    """One case outcome under a run."""

    __tablename__ = "eval_case_result"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("eval_run.id", ondelete="CASCADE", name="eval_case_result_run_fk"),
        nullable=False,
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    capability: Mapped[str] = mapped_column(Text, nullable=False)
    case_id: Mapped[str] = mapped_column(Text, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    session_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    scores: Mapped[dict[str, float]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    session_metrics: Mapped[dict[str, float] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_eval_case_result_run_id", "run_id"),
        Index("ix_eval_case_result_tenant_id", "tenant_id"),
    )
