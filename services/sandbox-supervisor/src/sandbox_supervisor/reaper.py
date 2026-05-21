"""``SandboxReaper`` — the warm-session idle reaper (STREAM-J-DESIGN § 9).

A J.15 warm per-user sandbox stays ``IN_USE`` across runs / messages.
The reaper sweeps periodically and force-destroys any session idle past
``last_used_at + session_idle_ttl_s`` — freeing compute while the
persistent volume is kept. It also backstops a caller that crashed
before ``release`` (the leaked ``IN_USE`` sandbox is just an idle one).

Stream J.15-补强-2 — the same tick also drives the volume lifecycle:
:meth:`VolumeLifecycleManager.archive_pending` consumes soft-deleted
workspaces (Mini-ADR J-36 第 2 → 第 3 档) and
:meth:`VolumeLifecycleManager.drain_dlq` retries ready failures
(Mini-ADR J-29 第 2 项 + J-36 共享 DLQ).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sandbox_supervisor.domain import DESTROY_REASON_IDLE_TIMEOUT, SupervisorError
from sandbox_supervisor.lifecycle import VolumeLifecycleManager
from sandbox_supervisor.store import SandboxStore
from sandbox_supervisor.supervisor import SandboxSupervisor

logger = logging.getLogger(__name__)


class SandboxReaper:
    """Periodically force-destroys idle ``IN_USE`` sandbox sessions
    and runs the J.15-补强-2 volume lifecycle sweep."""

    def __init__(
        self,
        *,
        supervisor: SandboxSupervisor,
        store: SandboxStore,
        interval_s: float,
        idle_ttl_s: int,
        lifecycle: VolumeLifecycleManager | None = None,
    ) -> None:
        self._supervisor = supervisor
        self._store = store
        self._interval_s = interval_s
        self._idle_ttl_s = idle_ttl_s
        # Stream J.15-补强-2 — when ``None``, the reaper just sweeps idle
        # sessions (legacy behavior). Production callers in ``create_app``
        # always inject one.
        self._lifecycle = lifecycle

    async def run_once(self) -> int:
        """Destroy every idle session; return how many were reaped.

        One session's failure (e.g. a Docker hiccup) does not abort the
        sweep — it is logged and the next session is still processed.
        After the idle sweep, the J.15-补强-2 lifecycle sweep runs
        (archive pending soft-deleted volumes + drain the DLQ) — each
        flow swallows its own per-volume errors.
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
        if self._lifecycle is not None:
            try:
                archive = await self._lifecycle.archive_pending()
                dlq = await self._lifecycle.drain_dlq()
                if archive.succeeded or archive.failed or dlq.succeeded or dlq.failed:
                    logger.info(
                        "reaper.lifecycle archive=%d/%d dlq=%d/%d",
                        archive.succeeded,
                        archive.failed,
                        dlq.succeeded,
                        dlq.failed,
                    )
            except Exception:
                logger.exception("reaper.lifecycle_sweep_failed")
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
