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
    build_api_keys_router,
    build_health_router,
    build_metrics_router,
    build_role_bindings_router,
    build_runs_router,
    build_service_accounts_router,
    build_sessions_router,
)
from control_plane.audit import build_default_audit_logger
from control_plane.auth import (
    ApiKeyVerifier,
    HTTPJWKSProvider,
    JWTVerifier,
    MTLSVerifier,
    build_mtls_verifier,
)
from control_plane.manifest import ManifestLoader
from control_plane.middleware import (
    AuditContextMiddleware,
    AuthMiddleware,
    CancellationMiddleware,
    DeadlineMiddleware,
    InFlightMiddleware,
    ObservabilityMiddleware,
    RateLimitMiddleware,
    RLSContextMiddleware,
)
from control_plane.ratelimit import InProcessTokenBucketLimiter, RateLimiter
from control_plane.settings import Settings
from helix_agent.common.health import DefaultHealthProvider
from helix_agent.common.lifecycle import Lifecycle
from helix_agent.common.observability import init_logging, init_tracing
from helix_agent.persistence.agent_spec import AgentSpecStore, InMemoryAgentSpecStore
from helix_agent.persistence.auth import (
    ApiKeyStore,
    InMemoryApiKeyStore,
    InMemoryRoleBindingStore,
    InMemoryServiceAccountStore,
    RoleBindingStore,
    ServiceAccountStore,
)
from helix_agent.persistence.thread_meta import InMemoryThreadMetaStore, ThreadMetaStore
from helix_agent.runtime.audit.logger import AuditLogger

__all__ = ["create_app"]

logger = logging.getLogger("helix.control_plane")

_VERSION = "0.0.0"


def create_app(
    *,
    settings: Settings | None = None,
    lifecycle: Lifecycle | None = None,
    rate_limiter: RateLimiter | None = None,
    agent_spec_repo: AgentSpecStore | None = None,
    thread_meta_repo: ThreadMetaStore | None = None,
    audit_logger: AuditLogger | None = None,
    manifest_loader: ManifestLoader | None = None,
    jwt_verifier: JWTVerifier | None = None,
    mtls_verifier: MTLSVerifier | None = None,
    service_account_repo: ServiceAccountStore | None = None,
    api_key_repo: ApiKeyStore | None = None,
    role_binding_repo: RoleBindingStore | None = None,
    api_key_verifier: ApiKeyVerifier | None = None,
) -> FastAPI:
    """Build a configured FastAPI app.

    :param settings: Optional pre-built settings (tests use this).
    :param lifecycle: Optional pre-built :class:`Lifecycle`; default is a
        fresh instance owned by the app.
    :param rate_limiter: Optional pre-built limiter (tests inject a stub
        or a tuned bucket). Defaults to :class:`InProcessTokenBucketLimiter`
        sized from ``settings.rate_limit_*``.
    :param jwt_verifier: Optional pre-built JWT verifier. Tests provide a
        :class:`StaticJWKSProvider`-backed verifier to avoid HTTP calls to
        Keycloak; production wiring builds one from ``settings`` (C.1).
    """
    resolved_settings = settings or Settings()
    resolved_lifecycle = lifecycle or Lifecycle()
    resolved_limiter = rate_limiter or InProcessTokenBucketLimiter(
        capacity=resolved_settings.rate_limit_burst,
        refill_per_sec=resolved_settings.rate_limit_per_second,
    )
    resolved_repo = agent_spec_repo or InMemoryAgentSpecStore()
    resolved_threads = thread_meta_repo or InMemoryThreadMetaStore()
    resolved_audit = audit_logger or build_default_audit_logger()
    resolved_loader = manifest_loader or ManifestLoader()
    resolved_verifier = jwt_verifier or _build_default_jwt_verifier(resolved_settings)
    resolved_mtls = mtls_verifier or _build_default_mtls_verifier(resolved_settings)
    resolved_service_accounts = service_account_repo or InMemoryServiceAccountStore()
    resolved_api_keys = api_key_repo or InMemoryApiKeyStore()
    resolved_role_bindings = role_binding_repo or InMemoryRoleBindingStore()
    resolved_api_key_verifier = api_key_verifier or ApiKeyVerifier.from_store(resolved_api_keys)
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
    app.state.thread_meta_repo = resolved_threads
    app.state.audit_logger = resolved_audit
    app.state.manifest_loader = resolved_loader
    app.state.jwt_verifier = resolved_verifier
    app.state.mtls_verifier = resolved_mtls
    app.state.service_account_repo = resolved_service_accounts
    app.state.api_key_repo = resolved_api_keys
    app.state.role_binding_repo = resolved_role_bindings
    app.state.api_key_verifier = resolved_api_key_verifier

    # Starlette wraps middleware in *reverse* registration order: the
    # last call to ``add_middleware`` becomes the outermost layer. We
    # therefore register innermost first.
    #
    # Effective execution order (outermost → innermost):
    #   1. ObservabilityMiddleware  — open span, record timing
    #   2. AuthMiddleware           — verify JWT → request.state.principal (C.1)
    #   3. RLSContextMiddleware     — project principal.tenant_id → RLS ctxvar (C.4)
    #   4. AuditContextMiddleware   — project principal.tenant_id → log ctxvar
    #   5. RateLimitMiddleware      — 429 short-circuit (B.2)
    #   6. CancellationMiddleware   — mint CancelToken + disconnect poll
    #   7. DeadlineMiddleware       — consume CancelToken + seed
    #                                 DeadlineContext from header
    #   8. InFlightMiddleware       — Lifecycle.track_in_flight (drain)
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
    app.add_middleware(RLSContextMiddleware)
    app.add_middleware(
        AuthMiddleware,
        verifier=resolved_verifier,
        exempt_path_prefixes=tuple(resolved_settings.auth_exempt_path_prefixes),
        audit_logger=resolved_audit,
        mtls_verifier=resolved_mtls,
        mtls_header_name=resolved_settings.mtls_xfcc_header_name,
        api_key_verifier=resolved_api_key_verifier,
    )
    app.add_middleware(ObservabilityMiddleware)

    app.include_router(build_health_router(health_provider))
    app.include_router(build_metrics_router())
    app.include_router(build_agents_router())
    app.include_router(build_sessions_router())
    app.include_router(build_runs_router())
    app.include_router(build_service_accounts_router())
    app.include_router(build_api_keys_router())
    app.include_router(build_role_bindings_router())

    return app


def _build_default_jwt_verifier(settings: Settings) -> JWTVerifier:
    """Construct a Keycloak-backed JWT verifier from settings (C.1)."""
    provider = HTTPJWKSProvider(
        settings.resolve_jwks_uri(),
        cache_ttl_s=float(settings.oidc_jwks_cache_ttl_s),
    )
    return JWTVerifier(
        jwks_provider=provider,
        issuer=settings.oidc_issuer,
        audience=settings.oidc_audience,
        leeway_s=settings.oidc_jwt_leeway_s,
    )


def _build_default_mtls_verifier(settings: Settings) -> MTLSVerifier | None:
    """Construct an mTLS verifier when the feature is enabled (C.2)."""
    if not settings.mtls_enabled:
        return None
    return build_mtls_verifier(
        allowed_subjects=settings.mtls_allowed_service_subjects,
        system_tenant_id=settings.mtls_system_tenant_id,
        require_uri_san=settings.mtls_require_uri_san,
    )
