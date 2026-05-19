"""``SandboxReaper`` — the warm-session idle reaper (STREAM-J-DESIGN § 9).

A J.15 warm per-user sandbox stays ``IN_USE`` across runs / messages.
The reaper sweeps periodically and force-destroys any session idle past
``last_used_at + session_idle_ttl_s`` — freeing compute while the
persistent volume is kept. It also backstops a caller that crashed
before ``release`` (the leaked ``IN_USE`` sandbox is just an idle one).
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
    """Periodically force-destroys idle ``IN_USE`` sandbox sessions."""

    def __init__(
        self,
        *,
        supervisor: SandboxSupervisor,
        store: SandboxStore,
        interval_s: float,
        idle_ttl_s: int,
    ) -> None:
        self._supervisor = supervisor
        self._store = store
        self._interval_s = interval_s
        self._idle_ttl_s = idle_ttl_s

    async def run_once(self) -> int:
        """Destroy every idle session; return how many were reaped.

        One session's failure (e.g. a Docker hiccup) does not abort the
        sweep — it is logged and the next session is still processed.
        """
        idle = await self._store.list_idle_sessions(
            now=datetime.now(UTC), idle_ttl_s=self._idle_ttl_s
        )
        reaped = 0
        for session in idle:
            try:
                await self._supervisor.destroy(session.id, reason=DESTROY_REASON_IDLE_TIMEOUT)
            except SupervisorError as exc:
                logger.warning("reaper.destroy_failed sandbox=%s reason=%s", session.id, exc)
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
