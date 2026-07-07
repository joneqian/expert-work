"""``quality_drift_alert`` ORM model — Stream RT-5 (RT-ADR-24).

Append-only per-tenant history of raised quality-drift alerts. The
``(tenant_id, agent_name, detected_at)`` index serves both the per-agent
cooldown lookup (latest ``detected_at``) and the dashboard alert list. Tenant
RLS uses the standard ``app.tenant_id`` GUC pattern. Schema mirrors migration
0118_quality_drift_alert.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Float, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from expert_work.persistence.base import Base


class QualityDriftAlertRow(Base):
    """One raised quality-drift alert."""

    __tablename__ = "quality_drift_alert"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    agent_name: Mapped[str] = mapped_column(Text, nullable=False)
    recent_mean: Mapped[float] = mapped_column(Float, nullable=False)
    baseline_mean: Mapped[float] = mapped_column(Float, nullable=False)
    drift_pct: Mapped[float] = mapped_column(Float, nullable=False)
    recent_count: Mapped[int] = mapped_column(Integer, nullable=False)
    baseline_count: Mapped[int] = mapped_column(Integer, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # Cooldown lookup (latest per agent) + dashboard alert list.
        Index(
            "quality_drift_alert_tenant_agent_time_idx",
            "tenant_id",
            "agent_name",
            "detected_at",
        ),
    )
