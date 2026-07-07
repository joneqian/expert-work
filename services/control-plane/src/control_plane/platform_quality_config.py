"""``PlatformQualityConfigService`` — Stream RT-5 (PR-3b, §14).

Returns the EFFECTIVE production quality-monitoring config the resident workers
read each cycle: the DB row wins per field; an absent row (or null field) falls
back to the ``Settings`` env default. The master ``enabled`` is the AND of the
deploy gate (``settings.enable_quality_monitor``) and the UI toggle
(``row.enabled``) — a fresh install with no row is OFF (opt-in via the UI),
while ``enable_quality_monitor=false`` is a deploy-level hard override.

Mirrors :class:`PlatformJudgeConfigService`: the resolved view is TTL-cached;
the write endpoint calls :meth:`invalidate` for immediate effect on the writing
instance, and multi-replica staleness is bounded by the TTL. No
``bypass_rls_session()``: ``platform_quality_config`` is a tenant-less platform
table with no RLS policy (migration 0119).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass

from control_plane.settings import Settings
from expert_work.persistence.platform_quality_config.base import (
    PlatformQualityConfigRow,
    PlatformQualityConfigStore,
)


@dataclass(frozen=True)
class EffectiveQualityConfig:
    """The resolved knobs the sampler + drift worker use for one cycle."""

    enabled: bool
    sampling_rate_pct: int
    daily_cap: int
    monitor_interval_s: int
    monitor_batch_size: int
    judge_provider: str
    judge_model: str
    drift_interval_s: int
    drift_recent_window_h: int
    drift_baseline_window_h: int
    drift_min_samples: int
    drift_threshold: float
    drift_cooldown_h: int


def resolve_effective_quality_config(
    settings: Settings, row: PlatformQualityConfigRow | None
) -> EffectiveQualityConfig:
    """Merge the DB row over the env defaults; ``enabled`` is env AND UI toggle."""

    def _pick_int(row_val: int | None, default: int) -> int:
        return row_val if row_val is not None else default

    def _pick_str(row_val: str | None, default: str) -> str:
        return row_val if row_val else default

    ui_enabled = bool(row.enabled) if (row is not None and row.enabled is not None) else False
    return EffectiveQualityConfig(
        # Deploy hard-gate AND the UI opt-in toggle (no row → off).
        enabled=settings.enable_quality_monitor and ui_enabled,
        sampling_rate_pct=_pick_int(
            row.sampling_rate_pct if row else None, settings.quality_sampling_rate_pct
        ),
        daily_cap=_pick_int(row.daily_cap if row else None, settings.quality_daily_cap),
        monitor_interval_s=_pick_int(
            row.monitor_interval_s if row else None, settings.quality_monitor_interval_s
        ),
        monitor_batch_size=_pick_int(
            row.monitor_batch_size if row else None, settings.quality_monitor_batch_size
        ),
        judge_provider=_pick_str(
            row.judge_provider if row else None, settings.quality_judge_provider
        ),
        judge_model=_pick_str(row.judge_model if row else None, settings.quality_judge_model),
        drift_interval_s=_pick_int(
            row.drift_interval_s if row else None, settings.quality_drift_interval_s
        ),
        drift_recent_window_h=_pick_int(
            row.drift_recent_window_h if row else None, settings.quality_drift_recent_window_h
        ),
        drift_baseline_window_h=_pick_int(
            row.drift_baseline_window_h if row else None,
            settings.quality_drift_baseline_window_h,
        ),
        drift_min_samples=_pick_int(
            row.drift_min_samples if row else None, settings.quality_drift_min_samples
        ),
        drift_threshold=(
            row.drift_threshold
            if (row is not None and row.drift_threshold is not None)
            else settings.quality_drift_threshold
        ),
        drift_cooldown_h=_pick_int(
            row.drift_cooldown_h if row else None, settings.quality_drift_cooldown_h
        ),
    )


class PlatformQualityConfigService:
    """DB-wins effective quality config, TTL-cached; workers read it per cycle."""

    def __init__(
        self,
        *,
        store: PlatformQualityConfigStore,
        settings: Settings,
        ttl_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._settings = settings
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._effective: EffectiveQualityConfig | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def get_row(self) -> PlatformQualityConfigRow | None:
        """The raw stored row (or None) — for the read API."""
        return await self._store.get()

    async def effective(self) -> EffectiveQualityConfig:
        """The resolved config for this cycle (TTL-cached)."""
        if self._effective is not None and self._clock() < self._expires_at:
            return self._effective
        async with self._lock:
            if self._effective is not None and self._clock() < self._expires_at:
                return self._effective
            row = await self._store.get()
            self._effective = resolve_effective_quality_config(self._settings, row)
            self._expires_at = self._clock() + self._ttl_seconds
            return self._effective

    async def put(self, row: PlatformQualityConfigRow) -> None:
        """Upsert the singleton config row then invalidate the cache."""
        await self._store.put(row)
        self.invalidate()

    def invalidate(self) -> None:
        """Drop the cache so the next read reloads from DB."""
        self._expires_at = 0.0


__all__ = [
    "EffectiveQualityConfig",
    "PlatformQualityConfigService",
    "resolve_effective_quality_config",
]
