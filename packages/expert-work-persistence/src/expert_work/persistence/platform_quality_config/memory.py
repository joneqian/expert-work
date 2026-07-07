"""In-memory :class:`PlatformQualityConfigStore` — Stream RT-5 (PR-3b)."""

from __future__ import annotations

import asyncio

from expert_work.persistence.platform_quality_config.base import (
    PlatformQualityConfigRow,
    PlatformQualityConfigStore,
)


class InMemoryPlatformQualityConfigStore(PlatformQualityConfigStore):
    """Holds a single optional row; lock-guarded for asyncio safety."""

    def __init__(self) -> None:
        self._row: PlatformQualityConfigRow | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> PlatformQualityConfigRow | None:
        async with self._lock:
            return self._row

    async def put(self, row: PlatformQualityConfigRow) -> None:
        async with self._lock:
            self._row = row
