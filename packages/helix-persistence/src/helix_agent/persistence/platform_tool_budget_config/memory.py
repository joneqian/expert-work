"""In-memory :class:`PlatformToolBudgetConfigStore` — Phase 3."""

from __future__ import annotations

import asyncio

from helix_agent.persistence.platform_tool_budget_config.base import (
    PlatformToolBudgetConfigRow,
    PlatformToolBudgetConfigStore,
)


class InMemoryPlatformToolBudgetConfigStore(PlatformToolBudgetConfigStore):
    """Holds a single optional row; lock-guarded for asyncio safety."""

    def __init__(self) -> None:
        self._row: PlatformToolBudgetConfigRow | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> PlatformToolBudgetConfigRow | None:
        async with self._lock:
            return self._row

    async def put(self, *, enabled: bool, updated_by: str | None) -> None:
        async with self._lock:
            self._row = PlatformToolBudgetConfigRow(enabled=enabled, updated_by=updated_by)
