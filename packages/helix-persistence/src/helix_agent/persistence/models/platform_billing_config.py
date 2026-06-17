"""Platform billing-rollup config ORM model — Stream 12.4.

A single-row (``id == "singleton"``) table holding platform billing toggles read
by the offline billing-rollup job. For now one flag: ``rollup_enabled`` (default
true). When false the cron-driven ``BillingRollupJob`` skips its run — a platform
operator can pause cost rollup from the admin UI without touching the k8s
CronJob.

Platform-global, tenant-less (like ``platform_judge_config``) — no RLS policy;
all access goes through ``bypass_rls_session()``. An absent row means "default"
→ rollup enabled.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from helix_agent.persistence.base import Base


class PlatformBillingConfigRow(Base):
    """The single platform billing-config row."""

    __tablename__ = "platform_billing_config"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    rollup_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_by: Mapped[str | None] = mapped_column(Text, nullable=True)
