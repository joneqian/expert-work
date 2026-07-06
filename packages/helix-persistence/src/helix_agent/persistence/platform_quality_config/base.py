"""Abstract :class:`PlatformQualityConfigStore` — Stream RT-5 (PR-3b, §14).

Single-row singleton storing the platform's production quality-monitoring
config (sampling / judge model / drift thresholds / ``enabled`` toggle).
Tenant-less (platform-global) with no RLS policy on the table (migration 0119),
so a plain session is sufficient — no per-tenant scope, like
``platform_judge_config``.

An absent row (or a null field) means "use the ``Settings`` env default" per
field; ``enabled`` is False (opt-in via UI) when no row exists.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformQualityConfigRow:
    """The platform's quality-monitor knobs (all optional → per-field fallback)."""

    enabled: bool | None
    sampling_rate_pct: int | None
    daily_cap: int | None
    monitor_interval_s: int | None
    monitor_batch_size: int | None
    judge_provider: str | None
    judge_model: str | None
    drift_interval_s: int | None
    drift_recent_window_h: int | None
    drift_baseline_window_h: int | None
    drift_min_samples: int | None
    drift_threshold: float | None
    drift_cooldown_h: int | None
    updated_by: str | None


class PlatformQualityConfigStore(abc.ABC):
    """Persistence Protocol for the single-row platform quality config."""

    @abc.abstractmethod
    async def get(self) -> PlatformQualityConfigRow | None:
        """The singleton row, or None if not configured. SQL callers bypass RLS."""

    @abc.abstractmethod
    async def put(self, row: PlatformQualityConfigRow) -> None:
        """Upsert the singleton row (last write wins). SQL callers bypass RLS."""
