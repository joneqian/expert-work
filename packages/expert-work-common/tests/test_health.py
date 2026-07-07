"""Unit tests for :mod:`expert_work.common.health`."""

from __future__ import annotations

import asyncio

import pytest

from expert_work.common.health import (
    DefaultHealthProvider,
    DependencyCheck,
    HealthStatus,
    make_health_handlers,
)
from expert_work.common.lifecycle import Lifecycle, ShutdownState


def _provider(
    *,
    lifecycle: Lifecycle | None = None,
    dependencies: dict[str, DependencyCheck] | None = None,
    check_timeout_s: float = 5.0,
) -> DefaultHealthProvider:
    return DefaultHealthProvider(
        service="control-plane",
        version="0.0.0",
        lifecycle=lifecycle or Lifecycle(),
        dependencies=dependencies,
        check_timeout_s=check_timeout_s,
    )


def _ok_check(name: str = "postgres", *, critical: bool = True) -> DependencyCheck:
    async def _run() -> HealthStatus:
        return HealthStatus.OK

    return DependencyCheck(name=name, run=_run, critical=critical)


def _broken_check(name: str, *, critical: bool = True) -> DependencyCheck:
    async def _run() -> HealthStatus:
        return HealthStatus.NOT_READY

    return DependencyCheck(name=name, run=_run, critical=critical)


def _slow_check(name: str, *, delay_s: float, critical: bool = True) -> DependencyCheck:
    async def _run() -> HealthStatus:
        await asyncio.sleep(delay_s)
        return HealthStatus.OK

    return DependencyCheck(name=name, run=_run, critical=critical)


# ---------------------------------------------------------------------------
# live()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_is_ok_regardless_of_dependencies() -> None:
    """The cardinal rule (§ 5.2): live() must NOT consult deps."""
    lc = Lifecycle()
    lc.mark_ready()
    provider = _provider(lifecycle=lc, dependencies={"postgres": _broken_check("postgres")})
    report = await provider.live()
    assert report.status is HealthStatus.OK
    assert report.checks == {}


@pytest.mark.asyncio
async def test_live_ok_during_drain() -> None:
    """Liveness stays OK during DRAINING — only readiness flips."""
    lc = Lifecycle()
    lc.mark_ready()
    lc._state = ShutdownState.DRAINING  # simulate mid-shutdown
    provider = _provider(lifecycle=lc)
    report = await provider.live()
    assert report.status is HealthStatus.OK


# ---------------------------------------------------------------------------
# ready()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ready_returns_not_ready_in_starting() -> None:
    provider = _provider()
    report = await provider.ready()
    assert report.status is HealthStatus.NOT_READY


@pytest.mark.asyncio
async def test_ready_returns_not_ready_in_draining() -> None:
    lc = Lifecycle()
    lc.mark_ready()
    lc._state = ShutdownState.DRAINING
    provider = _provider(lifecycle=lc)
    report = await provider.ready()
    assert report.status is HealthStatus.NOT_READY


@pytest.mark.asyncio
async def test_ready_ok_when_all_deps_ok() -> None:
    lc = Lifecycle()
    lc.mark_ready()
    provider = _provider(
        lifecycle=lc, dependencies={"postgres": _ok_check(), "redis": _ok_check("redis")}
    )
    report = await provider.ready()
    assert report.status is HealthStatus.OK
    assert report.checks == {"postgres": HealthStatus.OK, "redis": HealthStatus.OK}


@pytest.mark.asyncio
async def test_ready_critical_dep_failure_maps_to_not_ready() -> None:
    lc = Lifecycle()
    lc.mark_ready()
    provider = _provider(
        lifecycle=lc,
        dependencies={
            "postgres": _broken_check("postgres", critical=True),
            "redis": _ok_check("redis", critical=False),
        },
    )
    report = await provider.ready()
    assert report.status is HealthStatus.NOT_READY


@pytest.mark.asyncio
async def test_ready_non_critical_dep_failure_maps_to_degraded() -> None:
    lc = Lifecycle()
    lc.mark_ready()
    provider = _provider(
        lifecycle=lc,
        dependencies={
            "postgres": _ok_check("postgres", critical=True),
            "redis": _broken_check("redis", critical=False),
        },
    )
    report = await provider.ready()
    assert report.status is HealthStatus.DEGRADED


@pytest.mark.asyncio
async def test_ready_check_timeout_treated_as_not_ready() -> None:
    lc = Lifecycle()
    lc.mark_ready()
    provider = _provider(
        lifecycle=lc,
        dependencies={"slow": _slow_check("slow", delay_s=1.0, critical=True)},
        check_timeout_s=0.05,
    )
    report = await provider.ready()
    assert report.status is HealthStatus.NOT_READY
    assert report.checks["slow"] is HealthStatus.NOT_READY


@pytest.mark.asyncio
async def test_ready_check_exception_treated_as_not_ready() -> None:
    lc = Lifecycle()
    lc.mark_ready()

    async def _explodes() -> HealthStatus:
        raise RuntimeError("connect refused")

    provider = _provider(
        lifecycle=lc,
        dependencies={"vault": DependencyCheck(name="vault", run=_explodes, critical=False)},
    )
    report = await provider.ready()
    # Non-critical failure → degraded.
    assert report.status is HealthStatus.DEGRADED
    assert report.checks["vault"] is HealthStatus.NOT_READY


# ---------------------------------------------------------------------------
# startup()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_not_ready_until_mark_ready_called() -> None:
    provider = _provider()
    assert (await provider.startup()).status is HealthStatus.NOT_READY


@pytest.mark.asyncio
async def test_startup_ok_after_mark_ready() -> None:
    lc = Lifecycle()
    lc.mark_ready()
    provider = _provider(lifecycle=lc)
    assert (await provider.startup()).status is HealthStatus.OK


# ---------------------------------------------------------------------------
# make_health_handlers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_make_health_handlers_returns_three_callables() -> None:
    lc = Lifecycle()
    lc.mark_ready()
    provider = _provider(lifecycle=lc)
    live, ready, startup = make_health_handlers(provider)

    assert (await live()).status is HealthStatus.OK
    assert (await ready()).status is HealthStatus.OK
    assert (await startup()).status is HealthStatus.OK
