"""Platform quality-monitor config ORM model — Stream RT-5 (PR-3b, §14).

A single-row (``id == "singleton"``) table storing the platform's production
quality-monitoring knobs (sampling rate / judge model / drift thresholds /
enabled toggle). Non-secret config (provider/model names only — judge keys live
in ``platform_provider_secret``).

Platform-global, tenant-less (like ``platform_judge_config``) — no RLS policy;
all access goes through ``bypass_rls_session()``. An absent row (or a null
field) means "use the ``Settings`` env default" per field; ``enabled`` defaults
to False (opt-in via the UI) when no row exists.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from expert_work.persistence.base import Base


class PlatformQualityConfigRow(Base):
    """The single platform quality-monitor config row."""

    __tablename__ = "platform_quality_config"
    __table_args__ = (
        # Intervals feed asyncio timeouts — a non-positive value tight-loops.
        CheckConstraint(
            "monitor_interval_s IS NULL OR monitor_interval_s > 0",
            name="quality_config_monitor_interval_positive",
        ),
        CheckConstraint(
            "drift_interval_s IS NULL OR drift_interval_s > 0",
            name="quality_config_drift_interval_positive",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    #: UI master switch (ANDed with the ``enable_quality_monitor`` deploy gate).
    enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sampling_rate_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_cap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monitor_interval_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monitor_batch_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    judge_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    judge_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    drift_interval_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    drift_recent_window_h: Mapped[int | None] = mapped_column(Integer, nullable=True)
    drift_baseline_window_h: Mapped[int | None] = mapped_column(Integer, nullable=True)
    drift_min_samples: Mapped[int | None] = mapped_column(Integer, nullable=True)
    drift_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    drift_cooldown_h: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_by: Mapped[str | None] = mapped_column(Text, nullable=True)
