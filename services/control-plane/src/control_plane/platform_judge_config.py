"""``PlatformJudgeConfigService`` — Stream PI-3-A1.

Returns the EFFECTIVE output/action **judge** provider+model: the runtime DB
row wins; absent a row, ``None`` — and the runtime judge builder falls back to
each agent's own primary model (PI-2b-3 behaviour). There is no env fallback
(unlike embedding): the judge has a graceful per-agent default, so an unset
platform config is a normal state, not a missing one.

Mirrors :class:`PlatformEmbeddingConfigService`: the resolved view is
TTL-cached; write endpoints call :meth:`invalidate` for immediate effect on the
writing instance. Multi-replica staleness is bounded by the TTL.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from expert_work.persistence.platform_judge_config.base import (
    PlatformJudgeConfigStore,
)


class PlatformJudgeConfigService:
    """DB-wins effective judge config (provider, model), TTL-cached."""

    def __init__(
        self,
        *,
        store: PlatformJudgeConfigStore,
        ttl_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._judge: tuple[str, str] | None = None
        self._loaded = False
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def effective_judge_config(self) -> tuple[str, str] | None:
        """``(provider, model)`` for the judge, or ``None`` (→ agent's own model)."""
        await self._maybe_refresh()
        return self._judge

    async def put(
        self,
        *,
        judge_provider: str | None,
        judge_model: str | None,
        updated_by: str | None,
    ) -> None:
        """Upsert the singleton config row then invalidate the cache."""
        await self._store.put(
            judge_provider=judge_provider,
            judge_model=judge_model,
            updated_by=updated_by,
        )
        self.invalidate()

    def invalidate(self) -> None:
        """Drop the cache so the next read reloads from DB."""
        self._expires_at = 0.0

    async def _maybe_refresh(self) -> None:
        if self._loaded and self._clock() < self._expires_at:
            return
        async with self._lock:
            if self._loaded and self._clock() < self._expires_at:
                return
            await self._reload()

    async def _reload(self) -> None:
        # No ``bypass_rls_session()``: ``platform_judge_config`` is a tenant-less
        # platform table with no RLS policy (migration 0077).
        row = await self._store.get()
        self._judge = self._pair(row.judge_provider, row.judge_model) if row else None
        self._loaded = True
        self._expires_at = self._clock() + self._ttl_seconds

    @staticmethod
    def _pair(provider: str | None, model: str | None) -> tuple[str, str] | None:
        if provider and model:
            return (provider, model)
        return None
