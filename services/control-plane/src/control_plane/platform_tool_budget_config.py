"""``PlatformToolBudgetConfigService`` — Phase 3.

Returns the EFFECTIVE platform on/off for the tool-output-budget feature
(generalized externalization + persist floor + CM-12 prune): the runtime DB row
wins; absent a row, the ``HELIX_TOOL_OUTPUT_BUDGET`` env default
(:func:`orchestrator.tools.overflow.tool_output_budget_enabled`). So the env
stays the bootstrap default / ops hard-revert until an admin flips it in the UI,
after which the DB value wins.

Mirrors :class:`PlatformJudgeConfigService`: the resolved view is TTL-cached;
write endpoints call :meth:`invalidate` for immediate effect on the writing
instance. Multi-replica staleness is bounded by the TTL.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from helix_agent.persistence.platform_tool_budget_config.base import (
    PlatformToolBudgetConfigStore,
)
from orchestrator.tools.overflow import tool_output_budget_enabled


class PlatformToolBudgetConfigService:
    """DB-wins effective tool-output-budget on/off, TTL-cached."""

    def __init__(
        self,
        *,
        store: PlatformToolBudgetConfigStore,
        ttl_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._enabled = True
        self._configured: bool | None = None
        self._loaded = False
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def effective_enabled(self) -> bool:
        """The resolved on/off: DB row if configured, else the env default."""
        await self._maybe_refresh()
        return self._enabled

    async def configured_enabled(self) -> bool | None:
        """The DB row value, or ``None`` when unset (→ using the env default).

        Lets the API distinguish "explicitly configured" from "env default" so
        the UI can show whether a platform override is in effect.
        """
        await self._maybe_refresh()
        return self._configured

    async def put(self, *, enabled: bool, updated_by: str | None) -> None:
        """Upsert the singleton config row then invalidate the cache."""
        await self._store.put(enabled=enabled, updated_by=updated_by)
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
        # No ``bypass_rls_session()``: ``platform_tool_budget_config`` is a
        # tenant-less platform table with no RLS policy (migration 0102).
        row = await self._store.get()
        self._configured = row.enabled if row is not None else None
        self._enabled = row.enabled if row is not None else tool_output_budget_enabled()
        self._loaded = True
        self._expires_at = self._clock() + self._ttl_seconds
