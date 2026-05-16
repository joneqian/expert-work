"""``SandboxReaper`` — the TTL safety net (STREAM-F-DESIGN § 2.7).

A caller that crashes before ``release`` would leak an ``IN_USE``
sandbox forever. The reaper sweeps periodically and force-destroys any
``IN_USE`` sandbox older than ``acquired_at + timeout_s + grace_s``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sandbox_supervisor.domain import DESTROY_REASON_IDLE_TIMEOUT, SupervisorError
from sandbox_supervisor.store import SandboxStore
from sandbox_supervisor.supervisor import SandboxSupervisor

logger = logging.getLogger(__name__)


class SandboxReaper:
    """Periodically force-destroys orphaned ``IN_USE`` sandboxes."""

    def __init__(
        self,
        *,
        supervisor: SandboxSupervisor,
        store: SandboxStore,
        interval_s: float,
        grace_s: int,
    ) -> None:
        self._supervisor = supervisor
        self._store = store
        self._interval_s = interval_s
        self._grace_s = grace_s

    async def run_once(self) -> int:
        """Destroy every orphaned sandbox; return how many were reaped.

        One orphan's failure (e.g. a Docker hiccup) does not abort the
        sweep — it is logged and the next orphan is still processed.
        """
        orphans = await self._store.list_orphans(now=datetime.now(UTC), grace_s=self._grace_s)
        reaped = 0
        for orphan in orphans:
            try:
                await self._supervisor.destroy(orphan.id, reason=DESTROY_REASON_IDLE_TIMEOUT)
            except SupervisorError as exc:
                logger.warning("reaper.destroy_failed sandbox=%s reason=%s", orphan.id, exc)
            else:
                reaped += 1
        if reaped:
            logger.info("reaper.swept reaped=%d", reaped)
        return reaped

    async def run_forever(self, stop: asyncio.Event) -> None:
        """Sweep every ``interval_s`` until ``stop`` is set."""
        while not stop.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.exception("reaper.sweep_failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._interval_s)
            except TimeoutError:
                continue
