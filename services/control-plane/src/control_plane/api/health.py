"""Health routes — wires Stream A.11 ``HealthReportProvider`` into FastAPI.

Per subsystems/28 § 3.1 the three probes are intentionally **distinct**:

* ``/healthz/live``    — never touches deps; restart-trigger when unhealthy
* ``/healthz/ready``   — aggregates dep checks + lifecycle state
* ``/healthz/startup`` — true once :func:`Lifecycle.mark_ready` is called

Status code mapping follows the k8s-friendly convention: ``OK`` /
``DEGRADED`` → ``200`` (still routable), ``NOT_READY`` → ``503``,
``UNHEALTHY`` → ``500`` so the kubelet restarts the pod.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from expert_work.common.health import HealthReport, HealthReportProvider, HealthStatus

ProbeFn = Callable[[], Awaitable[HealthReport]]


def _status_to_http(status: HealthStatus) -> int:
    if status in (HealthStatus.OK, HealthStatus.DEGRADED):
        return 200
    if status is HealthStatus.NOT_READY:
        return 503
    return 500  # UNHEALTHY


def _report_to_payload(report: HealthReport) -> dict[str, Any]:
    return {
        "status": report.status.value,
        "service": report.service,
        "version": report.version,
        "checks": {name: status.value for name, status in report.checks.items()},
    }


def _route(probe: ProbeFn) -> Callable[[], Awaitable[JSONResponse]]:
    async def handler() -> JSONResponse:
        report = await probe()
        return JSONResponse(_report_to_payload(report), status_code=_status_to_http(report.status))

    return handler


def build_health_router(provider: HealthReportProvider) -> APIRouter:
    """Return a FastAPI router exposing the three probes under ``/healthz``."""
    router = APIRouter(prefix="/healthz", tags=["health"])
    router.add_api_route("/live", _route(provider.live), methods=["GET"])
    router.add_api_route("/ready", _route(provider.ready), methods=["GET"])
    router.add_api_route("/startup", _route(provider.startup), methods=["GET"])
    return router
