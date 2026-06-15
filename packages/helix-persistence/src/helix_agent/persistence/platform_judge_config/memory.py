"""In-memory :class:`PlatformJudgeConfigStore` — Stream PI-3-A1."""

from __future__ import annotations

import asyncio

from helix_agent.persistence.platform_judge_config.base import (
    PlatformJudgeConfigRow,
    PlatformJudgeConfigStore,
)


class InMemoryPlatformJudgeConfigStore(PlatformJudgeConfigStore):
    """Holds a single optional row; lock-guarded for asyncio safety."""

    def __init__(self) -> None:
        self._row: PlatformJudgeConfigRow | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> PlatformJudgeConfigRow | None:
        async with self._lock:
            return self._row

    async def put(
        self,
        *,
        judge_provider: str | None,
        judge_model: str | None,
        updated_by: str | None,
    ) -> None:
        async with self._lock:
            self._row = PlatformJudgeConfigRow(
                judge_provider=judge_provider,
                judge_model=judge_model,
                updated_by=updated_by,
            )
