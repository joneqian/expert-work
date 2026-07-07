"""Service lifecycle + graceful shutdown — Stream A.12.

Design: subsystems/28-reliability-primitives § 3.2 / § 5.3.

State machine::

    STARTING ──ready──▶ RUNNING ──SIGTERM──▶ DRAINING ──in-flight=0──▶ STOPPING
                          ▲                     │
                          │                     ▼
                          └─── liveness fail ───┘  (k8s restart)

Each service constructs **one** :class:`Lifecycle`, registers ``on_drain``
+ ``on_cleanup`` hooks during startup, then hands the instance to its
ASGI handler / supervisor for the SIGTERM hookup (Stream B).

**In-flight counter**: ``Lifecycle.in_flight()`` is an async context
manager that increments on enter / decrements on exit (in ``finally``).
``graceful_shutdown`` waits for the counter to reach zero before
proceeding to cleanup, bounded by ``drain_timeout_s``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import StrEnum

logger = logging.getLogger("expert_work.lifecycle")

# Type alias for the hooks the operator registers.
Hook = Callable[[], Awaitable[None]]


class ShutdownState(StrEnum):
    """Lifecycle phases per subsystems/28 § 3.2.

    Transitions are linear and one-way: a service that reaches
    ``STOPPING`` cannot bounce back to ``RUNNING``. ``KILLED`` is set
    *externally* (SIGKILL from the orchestrator); we never set it
    from inside the process.
    """

    STARTING = "starting"
    RUNNING = "running"
    DRAINING = "draining"
    STOPPING = "stopping"


@dataclass
class Lifecycle:
    """Mutable lifecycle handle for one service process.

    Mutable on purpose — the state machine progresses in place so all
    consumers (health endpoints, ASGI middleware, observability) see the
    same snapshot via shared reference.
    """

    drain_timeout_s: float = 30.0
    """Max wall-clock seconds to wait for in-flight requests to drain.

    Default 30 s matches § 5.3. Services with long-tail requests
    (Orchestrator: single step LLM call ~60 s) should pass 120 s.
    """

    force_kill_after_s: float = 60.0
    """Belt-and-suspenders: the orchestrator (k8s / systemd) must SIGKILL
    after this many seconds even if cleanup hooks are still running.
    Surface here so it's documented + observable in metrics."""

    _state: ShutdownState = field(default=ShutdownState.STARTING)
    _started_at: float = field(default_factory=time.time)
    _drain_started_at: float | None = field(default=None)
    _drain_hooks: list[Hook] = field(default_factory=list)
    _cleanup_hooks: list[Hook] = field(default_factory=list)
    _in_flight: int = field(default=0)
    _in_flight_zero: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self) -> None:
        # Counter starts at zero, so the event is set on construction.
        self._in_flight_zero.set()

    # ------------------------------------------------------------------
    # State accessors (read-only public API)
    # ------------------------------------------------------------------

    @property
    def state(self) -> ShutdownState:
        return self._state

    @property
    def started_at(self) -> float:
        """Unix seconds when the lifecycle was constructed."""
        return self._started_at

    @property
    def drain_started_at(self) -> float | None:
        """Unix seconds when ``DRAINING`` began; ``None`` while RUNNING/STARTING."""
        return self._drain_started_at

    @property
    def in_flight(self) -> int:
        """Snapshot of the in-flight request counter."""
        return self._in_flight

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def mark_ready(self) -> None:
        """Promote ``STARTING`` → ``RUNNING``.

        Idempotent — re-calling from ``RUNNING`` is a no-op so a slow
        startup that lands twice (e.g., uvicorn worker restart) doesn't
        crash. Calling after ``DRAINING`` is a programming bug; we log
        loudly but don't raise to avoid masking the actual shutdown.
        """
        if self._state is ShutdownState.STARTING:
            self._state = ShutdownState.RUNNING
            logger.info("lifecycle.ready started_at=%.3f", self._started_at)
            return
        if self._state is not ShutdownState.RUNNING:
            logger.warning(
                "lifecycle.mark_ready_called_in_state state=%s — ignored",
                self._state.value,
            )

    def on_drain(self, hook: Hook) -> None:
        """Register a hook to run when ``DRAINING`` begins (before any
        in-flight wait). Typical: stop accepting new HTTP requests."""
        self._drain_hooks.append(hook)

    def on_cleanup(self, hook: Hook) -> None:
        """Register a hook to run after in-flight=0 (the ``STOPPING``
        phase). Typical: flush checkpoints, close DB / Redis pools."""
        self._cleanup_hooks.append(hook)

    # ------------------------------------------------------------------
    # In-flight tracking
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def track_in_flight(self) -> AsyncIterator[None]:
        """Increment the in-flight counter for the block's lifetime.

        Always wrap request handling so ``graceful_shutdown`` can wait
        for the counter to hit zero. The decrement is in ``finally`` so
        exceptions don't leak the count (a real cause of "drain never
        completes" outages — see subsystems/28 § 6).
        """
        self._in_flight += 1
        self._in_flight_zero.clear()
        try:
            yield
        finally:
            self._in_flight -= 1
            if self._in_flight == 0:
                self._in_flight_zero.set()

    # ------------------------------------------------------------------
    # Graceful shutdown
    # ------------------------------------------------------------------

    async def graceful_shutdown(self) -> None:
        """Execute the drain → cleanup → exit sequence.

        Safe to call from a SIGTERM handler (Stream B wires it). Hooks
        run sequentially in registration order; a hook that raises is
        logged but doesn't abort the rest of shutdown — losing one
        cleanup step is better than skipping all of them.
        """
        if self._state in (ShutdownState.DRAINING, ShutdownState.STOPPING):
            # Re-entry from a second SIGTERM. Caller already on the
            # shutdown path — nothing to do.
            logger.info("lifecycle.shutdown_reentry state=%s", self._state.value)
            return

        # ----- DRAINING -----
        self._state = ShutdownState.DRAINING
        self._drain_started_at = time.time()
        logger.info(
            "lifecycle.drain_started in_flight=%d drain_timeout_s=%.1f",
            self._in_flight,
            self.drain_timeout_s,
        )
        await self._run_hooks(self._drain_hooks, phase="drain")

        # Wait for in-flight counter to reach zero, bounded by timeout.
        timed_out = False
        try:
            async with asyncio.timeout(self.drain_timeout_s):
                await self._in_flight_zero.wait()
        except TimeoutError:
            timed_out = True
            logger.warning(
                "lifecycle.drain_timeout_exceeded in_flight=%d timeout_s=%.1f",
                self._in_flight,
                self.drain_timeout_s,
            )

        # ----- STOPPING -----
        self._state = ShutdownState.STOPPING
        await self._run_hooks(self._cleanup_hooks, phase="cleanup")

        logger.info(
            "lifecycle.shutdown_complete drain_timed_out=%s in_flight_at_exit=%d",
            timed_out,
            self._in_flight,
        )

    async def _run_hooks(self, hooks: list[Hook], *, phase: str) -> None:
        """Run hooks sequentially; log + swallow individual failures.

        We do **not** use ``asyncio.gather`` here — sequential order is
        operationally easier to debug (drain order is encoded in
        registration order). A failure logs and continues.
        """
        for index, hook in enumerate(hooks):
            try:
                await hook()
            except Exception as exc:
                logger.exception(
                    "lifecycle.%s_hook_failed index=%d hook=%s reason=%r",
                    phase,
                    index,
                    getattr(hook, "__qualname__", repr(hook)),
                    exc,
                )
