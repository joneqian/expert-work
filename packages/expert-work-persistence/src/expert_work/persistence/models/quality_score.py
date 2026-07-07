"""``quality_score`` ORM model — Stream RT-5 (RT-ADR-24).

Per-run production quality verdict, per-tenant RLS time-series. One row per
sampled+judged run (``UNIQUE (tenant_id, run_id)`` so a re-scan is an
idempotent ``ON CONFLICT DO NOTHING``). Tenant RLS uses the standard
``app.tenant_id`` GUC pattern (same as ``agent_run``). Schema mirrors
migration 0117_quality_score.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Index, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from expert_work.persistence.base import Base


class QualityScoreRow(Base):
    """One judged run's quality verdict (per-agent time-series point)."""

    __tablename__ = "quality_score"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    agent_name: Mapped[str] = mapped_column(Text, nullable=False)
    agent_version: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    thread_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    overall: Mapped[int] = mapped_column(Integer, nullable=False)
    dimensions: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    judge_model: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # Idempotent re-scan: one verdict per run.
        UniqueConstraint("tenant_id", "run_id", name="quality_score_tenant_run_uniq"),
        # Per-agent time-series read (drift window + dashboard trend).
        Index("quality_score_tenant_agent_time_idx", "tenant_id", "agent_name", "observed_at"),
    )
