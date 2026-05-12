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

from control_plane.api import (
    build_agents_router,
    build_health_router,
    build_metrics_router,
)
from control_plane.audit import build_default_audit_logger
from control_plane.manifest import ManifestLoader
from control_plane.middleware import (
    AuditContextMiddleware,
    CancellationMiddleware,
    DeadlineMiddleware,
    InFlightMiddleware,
    ObservabilityMiddleware,
    RateLimitMiddleware,
)
from control_plane.ratelimit import InProcessTokenBucketLimiter, RateLimiter
from control_plane.settings import Settings
from helix_agent.common.health import DefaultHealthProvider
from helix_agent.common.lifecycle import Lifecycle
from helix_agent.common.observability import init_logging, init_tracing
from helix_agent.persistence.agent_spec import AgentSpecStore, InMemoryAgentSpecStore
from helix_agent.runtime.audit.logger import AuditLogger

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
    rate_limiter: RateLimiter | None = None,
    agent_spec_repo: AgentSpecStore | None = None,
    audit_logger: AuditLogger | None = None,
    manifest_loader: ManifestLoader | None = None,
) -> FastAPI:
    """Build a configured FastAPI app.

    :param settings: Optional pre-built settings (tests use this).
    :param lifecycle: Optional pre-built :class:`Lifecycle`; default is a
        fresh instance owned by the app.
    :param rate_limiter: Optional pre-built limiter (tests inject a stub
        or a tuned bucket). Defaults to :class:`InProcessTokenBucketLimiter`
        sized from ``settings.rate_limit_*``.
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
    resolved_limiter = rate_limiter or InProcessTokenBucketLimiter(
        capacity=resolved_settings.rate_limit_burst,
        refill_per_sec=resolved_settings.rate_limit_per_second,
    )
    resolved_repo = agent_spec_repo or InMemoryAgentSpecStore()
    resolved_audit = audit_logger or build_default_audit_logger()
    resolved_loader = manifest_loader or ManifestLoader()
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
    app.state.rate_limiter = resolved_limiter
    app.state.agent_spec_repo = resolved_repo
    app.state.audit_logger = resolved_audit
    app.state.manifest_loader = resolved_loader

    # Starlette wraps middleware in *reverse* registration order: the
    # last call to ``add_middleware`` becomes the outermost layer. We
    # therefore register innermost first.
    #
    # Effective execution order (outermost → innermost):
    #   1. ObservabilityMiddleware  — open span, record timing
    #   2. AuditContextMiddleware   — set tenant / actor ctxvar
    #   3. RateLimitMiddleware      — 429 short-circuit (B.2)
    #   4. CancellationMiddleware   — mint CancelToken + disconnect poll
    #   5. DeadlineMiddleware       — consume CancelToken + seed
    #                                 DeadlineContext from header
    #   6. InFlightMiddleware       — Lifecycle.track_in_flight (drain)
    app.add_middleware(InFlightMiddleware, lifecycle=resolved_lifecycle)
    app.add_middleware(DeadlineMiddleware)
    app.add_middleware(
        CancellationMiddleware,
        poll_interval_s=resolved_settings.cancellation_poll_interval_s,
    )
    app.add_middleware(
        RateLimitMiddleware,
        limiter=resolved_limiter,
        enabled=resolved_settings.rate_limit_enabled,
    )
    app.add_middleware(
        AuditContextMiddleware,
        default_tenant_id=resolved_settings.default_dev_tenant_id,
        default_actor_id=resolved_settings.default_dev_actor_id,
    )
    app.add_middleware(ObservabilityMiddleware)

    app.include_router(build_health_router(health_provider))
    app.include_router(build_metrics_router())
    app.include_router(build_agents_router())

    return app
