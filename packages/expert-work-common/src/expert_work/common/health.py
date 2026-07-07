"""Health probes — Stream A.11.

Design: subsystems/28-reliability-primitives § 3.1 + § 4.1 + § 5.2.

Three orthogonal probes per :class:`HealthReportProvider`:

* ``live()`` — am I alive? Must **not** touch dependencies (DB / Redis /
  Vault); otherwise a transient dep blip restarts the whole cluster.
* ``ready()`` — can I serve a request? Aggregates dep checks; lifecycle
  state ``DRAINING`` / ``STOPPING`` returns ``NOT_READY`` (LB-detachable).
* ``startup()`` — has the boot sequence finished? k8s uses this to delay
  liveness probes through slow cold starts (migrations, warmup).

The module is **framework-agnostic** — Stream B mounts these as FastAPI
routes (or aiohttp / Starlette / whatever).
:func:`make_health_handlers` returns three async callables you wrap with
the HTTP framework of your choice.

Implementations live with each service:

- :class:`DefaultHealthProvider` here covers the 80% case (lifecycle
  state + a list of injected dep checks).
- A service with custom semantics (Sandbox Supervisor checks the Docker
  daemon shape, not just liveness) writes its own
  :class:`HealthReportProvider` and ignores ``DefaultHealthProvider``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from expert_work.common.lifecycle import Lifecycle, ShutdownState

logger = logging.getLogger("expert_work.health")


class HealthStatus(StrEnum):
    """Outcome of a probe per subsystems/28 § 3.1."""

    OK = "ok"
    DEGRADED = "degraded"
    """Some non-critical deps unavailable; still serving.

    Examples per § 5.2 dependency-degradation table:
    - Redis down + Control Plane → degraded (limit-rate falls back to memory)
    - OpenAI down + Orchestrator → degraded (fallback to other provider)
    """

    NOT_READY = "not_ready"
    """Starting up, draining, or a critical dep is missing.

    Examples:
    - Postgres unreachable from Control Plane / Orchestrator
    - Service in ``DRAINING`` / ``STOPPING`` state (LB must detach)
    """

    UNHEALTHY = "unhealthy"
    """Self-failure that warrants a restart. Liveness probes return
    ``500`` for this — k8s SIGTERMs the pod."""


@dataclass(frozen=True)
class HealthReport:
    """One probe result.

    ``checks`` carries the **per-dependency** status so the operator can
    see "DB ok, Vault not_ready" at a glance. The top-level ``status``
    is the worst across critical deps (per ``degraded`` vs ``not_ready``
    rules in subsystems/28 § 5.2).
    """

    status: HealthStatus
    service: str
    version: str
    checks: dict[str, HealthStatus] = field(default_factory=dict)
    started_at: float = 0.0
    drain_started_at: float | None = None


class HealthReportProvider(Protocol):
    """The interface ASGI handlers consume."""

    async def live(self) -> HealthReport:
        """Self-only liveness — no dependency I/O."""

    async def ready(self) -> HealthReport:
        """Readiness — aggregates dep checks + lifecycle state."""

    async def startup(self) -> HealthReport:
        """Startup probe — returns ``OK`` once boot sequence is complete."""


@dataclass(frozen=True)
class DependencyCheck:
    """One dependency probe registered with :class:`DefaultHealthProvider`.

    ``critical=True`` means a failure flips overall ``ready`` status to
    ``NOT_READY``; ``critical=False`` only flips to ``DEGRADED``. The
    map of which deps are critical lives in subsystems/28 § 5.2.

    The probe ``run`` should be cheap (~ms): a TCP ping, a ``SELECT 1``,
    a HEAD request. Long-running checks belong in a separate background
    task that updates a gauge; ``ready`` reads the gauge.
    """

    name: str
    run: Callable[[], Awaitable[HealthStatus]]
    critical: bool = True


class DefaultHealthProvider:
    """The 80%-case provider: lifecycle state + a list of dep checks.

    ``live()`` is structurally dependency-free — it ignores any deps
    registered. This guards against the subsystems/28 § 5.2 anti-pattern
    where a liveness probe queries the DB and the whole cluster cycles
    on a transient DB hiccup.
    """

    def __init__(
        self,
        *,
        service: str,
        version: str,
        lifecycle: Lifecycle,
        dependencies: Mapping[str, DependencyCheck] | None = None,
        check_timeout_s: float = 5.0,
    ) -> None:
        self._service = service
        self._version = version
        self._lifecycle = lifecycle
        self._dependencies = dict(dependencies or {})
        self._check_timeout_s = check_timeout_s

    # ------------------------------------------------------------------
    # Probes
    # ------------------------------------------------------------------

    async def live(self) -> HealthReport:
        """Liveness — pure process check; never touches deps.

        Returns ``UNHEALTHY`` only when the lifecycle state machine is
        in an impossible state (defensive; should never fire).
        """
        status = (
            HealthStatus.OK
            if self._lifecycle.state
            in (
                ShutdownState.STARTING,
                ShutdownState.RUNNING,
                ShutdownState.DRAINING,
                ShutdownState.STOPPING,
            )
            else HealthStatus.UNHEALTHY
        )
        return HealthReport(
            status=status,
            service=self._service,
            version=self._version,
            started_at=self._lifecycle.started_at,
            drain_started_at=self._lifecycle.drain_started_at,
        )

    async def ready(self) -> HealthReport:
        """Readiness — aggregates lifecycle state + dep checks.

        Lifecycle non-RUNNING states short-circuit to ``NOT_READY``; the
        LB must detach the node immediately so no new traffic lands
        during a deploy.
        """
        if self._lifecycle.state in (ShutdownState.DRAINING, ShutdownState.STOPPING):
            return HealthReport(
                status=HealthStatus.NOT_READY,
                service=self._service,
                version=self._version,
                started_at=self._lifecycle.started_at,
                drain_started_at=self._lifecycle.drain_started_at,
            )
        if self._lifecycle.state is ShutdownState.STARTING:
            return HealthReport(
                status=HealthStatus.NOT_READY,
                service=self._service,
                version=self._version,
                started_at=self._lifecycle.started_at,
            )

        # RUNNING: poll every dep concurrently; aggregate.
        check_results = await self._run_checks()
        aggregate = _aggregate_status(self._dependencies, check_results)
        return HealthReport(
            status=aggregate,
            service=self._service,
            version=self._version,
            checks=check_results,
            started_at=self._lifecycle.started_at,
            drain_started_at=self._lifecycle.drain_started_at,
        )

    async def startup(self) -> HealthReport:
        """Startup probe — ``OK`` once :meth:`Lifecycle.mark_ready` was called."""
        if self._lifecycle.state is ShutdownState.STARTING:
            return HealthReport(
                status=HealthStatus.NOT_READY,
                service=self._service,
                version=self._version,
                started_at=self._lifecycle.started_at,
            )
        return HealthReport(
            status=HealthStatus.OK,
            service=self._service,
            version=self._version,
            started_at=self._lifecycle.started_at,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _run_checks(self) -> dict[str, HealthStatus]:
        """Run all dep checks concurrently with per-check timeout.

        A timeout or exception both surface as ``NOT_READY`` for the
        offending dep — the aggregator then folds critical/non-critical
        rules into the top-level status.
        """
        names = list(self._dependencies.keys())
        coros = [self._run_one(name) for name in names]
        statuses = await asyncio.gather(*coros, return_exceptions=False)
        return dict(zip(names, statuses, strict=True))

    async def _run_one(self, name: str) -> HealthStatus:
        check = self._dependencies[name]
        try:
            async with asyncio.timeout(self._check_timeout_s):
                return await check.run()
        except TimeoutError:
            logger.warning(
                "health.check_timeout dep=%s timeout_s=%.1f", name, self._check_timeout_s
            )
            return HealthStatus.NOT_READY
        except Exception as exc:
            logger.warning("health.check_failed dep=%s reason=%r", name, exc)
            return HealthStatus.NOT_READY


def _aggregate_status(
    dependencies: Mapping[str, DependencyCheck],
    results: Mapping[str, HealthStatus],
) -> HealthStatus:
    """Fold per-dep statuses into a single ``ready`` outcome.

    Rules per subsystems/28 § 5.2:

    - Any **critical** dep ≠ OK → ``NOT_READY``
    - Any non-critical dep ≠ OK → ``DEGRADED``
    - Else → ``OK``
    """
    has_non_critical_failure = False
    for name, status in results.items():
        if status is HealthStatus.OK:
            continue
        if dependencies[name].critical:
            return HealthStatus.NOT_READY
        has_non_critical_failure = True
    return HealthStatus.DEGRADED if has_non_critical_failure else HealthStatus.OK


def make_health_handlers(
    provider: HealthReportProvider,
) -> tuple[
    Callable[[], Awaitable[HealthReport]],
    Callable[[], Awaitable[HealthReport]],
    Callable[[], Awaitable[HealthReport]],
]:
    """Return ``(live, ready, startup)`` async callables.

    Stream B's FastAPI handler wraps each one as a route under
    ``/healthz/{live,ready,startup}`` and sets the HTTP status code
    from the :class:`HealthStatus` value (``OK``/``DEGRADED`` → 200,
    everything else → 503; ``UNHEALTHY`` on ``/live`` → 500).

    Keeping this module HTTP-framework-agnostic means the same
    primitive can power a non-HTTP supervisor process (e.g., a
    background eval runner) that exposes health via a unix socket.
    """
    return provider.live, provider.ready, provider.startup
