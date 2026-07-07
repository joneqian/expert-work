"""SQLAlchemy-backed :class:`PlatformQualityConfigStore` — Stream RT-5 (PR-3b).

Single-row singleton (``id == "singleton"``), tenant-less. No RLS policy on the
table (migration 0119), so a plain session is sufficient. Mirrors
:class:`SqlPlatformJudgeConfigStore`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from expert_work.persistence.models import PlatformQualityConfigRow as _Model
from expert_work.persistence.platform_quality_config.base import (
    PlatformQualityConfigRow,
    PlatformQualityConfigStore,
)

_SINGLETON_ID = "singleton"


def _record(row: _Model) -> PlatformQualityConfigRow:
    return PlatformQualityConfigRow(
        enabled=row.enabled,
        sampling_rate_pct=row.sampling_rate_pct,
        daily_cap=row.daily_cap,
        monitor_interval_s=row.monitor_interval_s,
        monitor_batch_size=row.monitor_batch_size,
        judge_provider=row.judge_provider,
        judge_model=row.judge_model,
        drift_interval_s=row.drift_interval_s,
        drift_recent_window_h=row.drift_recent_window_h,
        drift_baseline_window_h=row.drift_baseline_window_h,
        drift_min_samples=row.drift_min_samples,
        drift_threshold=row.drift_threshold,
        drift_cooldown_h=row.drift_cooldown_h,
        updated_by=row.updated_by,
    )


class SqlPlatformQualityConfigStore(PlatformQualityConfigStore):
    """Postgres-backed single-row platform quality config repository."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get(self) -> PlatformQualityConfigRow | None:
        async with self._sf() as session:
            row = (
                await session.execute(select(_Model).where(_Model.id == _SINGLETON_ID))
            ).scalar_one_or_none()
        return _record(row) if row is not None else None

    async def put(self, row: PlatformQualityConfigRow) -> None:
        now = datetime.now(tz=UTC)
        values = {
            "enabled": row.enabled,
            "sampling_rate_pct": row.sampling_rate_pct,
            "daily_cap": row.daily_cap,
            "monitor_interval_s": row.monitor_interval_s,
            "monitor_batch_size": row.monitor_batch_size,
            "judge_provider": row.judge_provider,
            "judge_model": row.judge_model,
            "drift_interval_s": row.drift_interval_s,
            "drift_recent_window_h": row.drift_recent_window_h,
            "drift_baseline_window_h": row.drift_baseline_window_h,
            "drift_min_samples": row.drift_min_samples,
            "drift_threshold": row.drift_threshold,
            "drift_cooldown_h": row.drift_cooldown_h,
            "updated_by": row.updated_by,
        }
        async with self._sf() as session:
            stmt = (
                pg_insert(_Model)
                .values(id=_SINGLETON_ID, updated_at=now, **values)
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_={**values, "updated_at": now},
                )
            )
            await session.execute(stmt)
            await session.commit()
