"""Control Plane FastAPI app factory — Stream B.1.

The factory is intentionally synchronous + side-effect-free so tests can
build many isolated apps without disturbing each other.

Wiring overview (see [STREAM-B-DESIGN § 2.2](../../../docs/streams/STREAM-B-DESIGN.md)):

* Synchronous fail-fast guard: ``settings.auth_mode == "prod"`` raises
  (ADR B-5) until C.1 OIDC middleware lands.
* ``lifespan`` initialises structured logging + OTel tracing and arms
  the :class:`Lifecycle` graceful-shutdown drain.
* Middleware stack (outermost → innermost):
    1. ``ObservabilityMiddleware``
    2. ``AuditContextMiddleware``
    3. ``InFlightMiddleware``
* Routes: ``/healthz/*`` + ``/metrics`` (B.1 surface). The
  ``/v1/agents`` / ``/v1/sessions`` / ``/v1/sessions/{id}/runs`` routers
  land in B.5 / B.6 / B.7.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from control_plane.api import build_health_router, build_metrics_router
from control_plane.middleware import (
    AuditContextMiddleware,
    InFlightMiddleware,
    ObservabilityMiddleware,
)
from control_plane.settings import Settings
from helix_agent.common.health import DefaultHealthProvider
from helix_agent.common.lifecycle import Lifecycle
from helix_agent.common.observability import init_logging, init_tracing

__all__ = ["ProdAuthModeNotReadyError", "create_app"]

logger = logging.getLogger("helix.control_plane")

_VERSION = "0.0.0"


class ProdAuthModeNotReadyError(RuntimeError):
    """Raised when ``HELIX_AGENT_AUTH_MODE=prod`` is requested before C.1 lands.

    ADR B-5 — the prod path needs OIDC/JWT validation which Stream C.1
    delivers. Until then we refuse to boot rather than serve traffic
    with the dev header-trust middleware.
    """


def create_app(
    *,
    settings: Settings | None = None,
    lifecycle: Lifecycle | None = None,
) -> FastAPI:
    """Build a configured FastAPI app.

    :param settings: Optional pre-built settings (tests use this).
    :param lifecycle: Optional pre-built :class:`Lifecycle`; default is a
        fresh instance owned by the app.
    :raises ProdAuthModeNotReadyError: if ``settings.auth_mode == "prod"``
        (ADR B-5 — waits for C.1).
    """
    resolved_settings = settings or Settings()
    if resolved_settings.auth_mode == "prod":
        msg = (
            "HELIX_AGENT_AUTH_MODE=prod is not yet supported; the prod "
            "path requires C.1 OIDC middleware. Run with auth_mode=dev "
            "until the C.1 PR lands."
        )
        raise ProdAuthModeNotReadyError(msg)

    resolved_lifecycle = lifecycle or Lifecycle()
    health_provider = DefaultHealthProvider(
        service=resolved_settings.service_name,
        version=_VERSION,
        lifecycle=resolved_lifecycle,
        dependencies=None,
        check_timeout_s=resolved_settings.health_check_timeout_s,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        init_logging(
            service=resolved_settings.service_name,
            env=resolved_settings.env,
            level=resolved_settings.log_level,
        )
        init_tracing(
            service_name=resolved_settings.service_name,
            env=resolved_settings.env,
            otlp_endpoint=resolved_settings.otlp_traces_endpoint,
        )
        resolved_lifecycle.mark_ready()
        logger.info(
            "control_plane.lifespan.ready",
            extra={"service": resolved_settings.service_name, "env": resolved_settings.env},
        )
        try:
            yield
        finally:
            await resolved_lifecycle.graceful_shutdown()
            logger.info("control_plane.lifespan.stopped")

    app = FastAPI(
        title="Helix-Agent Control Plane",
        version=_VERSION,
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.lifecycle = resolved_lifecycle
    app.state.health_provider = health_provider

    # Starlette wraps middleware in *reverse* registration order: the
    # last call to ``add_middleware`` becomes the outermost layer. We
    # therefore register innermost first.
    app.add_middleware(InFlightMiddleware, lifecycle=resolved_lifecycle)
    app.add_middleware(
        AuditContextMiddleware,
        default_tenant_id=resolved_settings.default_dev_tenant_id,
        default_actor_id=resolved_settings.default_dev_actor_id,
    )
    app.add_middleware(ObservabilityMiddleware)

    app.include_router(build_health_router(health_provider))
    app.include_router(build_metrics_router())

    return app
