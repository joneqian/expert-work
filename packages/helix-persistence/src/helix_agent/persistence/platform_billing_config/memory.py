"""In-memory :class:`PlatformBillingConfigStore` — Stream 12.4."""

from __future__ import annotations

import asyncio

from helix_agent.persistence.platform_billing_config.base import (
    PlatformBillingConfigRow,
    PlatformBillingConfigStore,
)


class InMemoryPlatformBillingConfigStore(PlatformBillingConfigStore):
    """Holds a single optional row; lock-guarded for asyncio safety."""

    def __init__(self) -> None:
        self._row: PlatformBillingConfigRow | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> PlatformBillingConfigRow | None:
        async with self._lock:
            return self._row

    async def put(self, *, rollup_enabled: bool, updated_by: str | None) -> None:
        async with self._lock:
            self._row = PlatformBillingConfigRow(
                rollup_enabled=rollup_enabled,
                updated_by=updated_by,
            )
