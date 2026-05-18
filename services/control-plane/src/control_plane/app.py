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
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from langgraph.checkpoint.memory import InMemorySaver

from control_plane.api import (
    build_agents_router,
    build_api_keys_router,
    build_feedback_router,
    build_health_router,
    build_metrics_router,
    build_quota_router,
    build_role_bindings_router,
    build_runs_router,
    build_service_accounts_router,
    build_sessions_router,
    build_tenant_config_router,
    build_tenant_quotas_router,
)
from control_plane.audit import TenantConfigPiiResolver, build_default_audit_logger
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
    TenantRateLimitMiddleware,
)
from control_plane.quota import (
    InMemoryQuotaService,
    QuotaService,
    RedisQuotaService,
    ReservationReaper,
)
from control_plane.ratelimit import (
    InProcessTokenBucketLimiter,
    RateLimiter,
    RedisTokenBucketLimiter,
)
from control_plane.runtime import (
    AgentRuntime,
    build_mcp_pool,
    build_middleware_env,
    build_supervisor_client,
    build_tool_env,
    make_agent_builder,
    make_agent_runtime,
    resolve_web_search_client,
)
from control_plane.settings import Settings
from control_plane.tenancy import TenantConfigService
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
from helix_agent.persistence.feedback_store import FeedbackStore, InMemoryFeedbackStore
from helix_agent.persistence.quota import (
    InMemoryTenantQuotaStore,
    InMemoryTokenReservationStore,
    TenantQuotaStore,
    TokenReservationStore,
)
from helix_agent.persistence.tenant_config import (
    InMemoryTenantConfigStore,
    TenantConfigStore,
)
from helix_agent.persistence.thread_meta import InMemoryThreadMetaStore, ThreadMetaStore
from helix_agent.runtime.audit.logger import AuditLogger
from helix_agent.runtime.checkpointer import make_checkpointer
from helix_agent.runtime.secret_store import make_secret_store

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
    feedback_repo: FeedbackStore | None = None,
    audit_logger: AuditLogger | None = None,
    manifest_loader: ManifestLoader | None = None,
    jwt_verifier: JWTVerifier | None = None,
    mtls_verifier: MTLSVerifier | None = None,
    service_account_repo: ServiceAccountStore | None = None,
    api_key_repo: ApiKeyStore | None = None,
    role_binding_repo: RoleBindingStore | None = None,
    api_key_verifier: ApiKeyVerifier | None = None,
    tenant_quota_repo: TenantQuotaStore | None = None,
    token_reservation_repo: TokenReservationStore | None = None,
    quota_service: QuotaService | None = None,
    enable_reaper: bool = True,
    tenant_rate_limiter: RateLimiter | None = None,
    tenant_config_repo: TenantConfigStore | None = None,
    tenant_config_service: TenantConfigService | None = None,
    agent_runtime: AgentRuntime | None = None,
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
    resolved_limiter = rate_limiter or _build_default_gateway_limiter(resolved_settings)
    resolved_tenant_limiter = tenant_rate_limiter or _build_default_tenant_limiter(
        resolved_settings
    )
    resolved_repo = agent_spec_repo or InMemoryAgentSpecStore()
    resolved_threads = thread_meta_repo or InMemoryThreadMetaStore()
    resolved_feedback = feedback_repo or InMemoryFeedbackStore()
    # In-process agent runtime (RunManager + StreamBridge + manifest→agent
    # build path). Default wires a local-dev SecretStore; tests inject a
    # runtime whose builder returns a fake-LLM agent.
    resolved_secret_store = make_secret_store(
        "local_dev", env_file=resolved_settings.secret_store_env_file
    )
    resolved_agent_runtime = agent_runtime or make_agent_runtime(resolved_secret_store)
    # Late-bound PII resolver: lets the audit logger reference
    # tenant_config without forcing it to exist yet (D.2 cycle break).
    pii_resolver = TenantConfigPiiResolver()
    resolved_audit = audit_logger or build_default_audit_logger(
        pii_fields_resolver=pii_resolver,
    )
    resolved_loader = manifest_loader or ManifestLoader()
    resolved_verifier = jwt_verifier or _build_default_jwt_verifier(resolved_settings)
    resolved_mtls = mtls_verifier or _build_default_mtls_verifier(resolved_settings)
    resolved_service_accounts = service_account_repo or InMemoryServiceAccountStore()
    resolved_api_keys = api_key_repo or InMemoryApiKeyStore()
    resolved_role_bindings = role_binding_repo or InMemoryRoleBindingStore()
    resolved_api_key_verifier = api_key_verifier or ApiKeyVerifier.from_store(resolved_api_keys)
    resolved_tenant_quotas = tenant_quota_repo or InMemoryTenantQuotaStore()
    resolved_reservations = token_reservation_repo or InMemoryTokenReservationStore()
    resolved_quota = quota_service or _build_default_quota_service(
        settings=resolved_settings,
        quota_store=resolved_tenant_quotas,
        reservation_store=resolved_reservations,
    )
    resolved_tenant_config_repo = tenant_config_repo or InMemoryTenantConfigStore()
    resolved_tenant_config_service = tenant_config_service or TenantConfigService(
        store=resolved_tenant_config_repo,
        audit_logger=resolved_audit,
        ttl_s=float(resolved_settings.tenant_config_cache_ttl_s),
    )
    # Complete the D.2 cycle: TenantAwareRedactor → resolver → service.
    pii_resolver.bind(resolved_tenant_config_service)
    reaper: ReservationReaper | None = (
        ReservationReaper(
            reservation_store=resolved_reservations,
            max_age_s=resolved_settings.quota_reservation_max_age_s,
            interval_s=resolved_settings.quota_reaper_interval_s,
            batch_size=resolved_settings.quota_reaper_batch_size,
        )
        if enable_reaper
        else None
    )
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
        async with AsyncExitStack() as stack:
            # Wire the agent runtime's backends before serving: the
            # checkpointer (E.1) plus the tool / middleware env bundles
            # (PR1.5). No run starts before lifespan completes, so the
            # agent cache is still empty — swapping the builder is
            # race-free. An injected runtime (tests) is left untouched.
            if agent_runtime is None:
                if resolved_settings.checkpointer_backend == "postgres":
                    if not resolved_settings.checkpointer_dsn:
                        msg = "checkpointer_backend='postgres' requires checkpointer_dsn"
                        raise RuntimeError(msg)
                    checkpointer = await stack.enter_async_context(
                        make_checkpointer("postgres", resolved_settings.checkpointer_dsn)
                    )
                    logger.info("control_plane.checkpointer.postgres_ready")
                else:
                    checkpointer = InMemorySaver()
                web_search_client = await resolve_web_search_client(
                    api_key_ref=resolved_settings.tavily_api_key_ref,
                    secret_store=resolved_secret_store,
                )
                mcp_pool = await stack.enter_async_context(
                    build_mcp_pool(resolved_settings.mcp_servers_config_file)
                )
                resolved_agent_runtime.agent_builder = make_agent_builder(
                    resolved_secret_store,
                    checkpointer,
                    tool_env=build_tool_env(
                        resolved_tenant_config_service,
                        web_search_client=web_search_client,
                        supervisor_client=build_supervisor_client(
                            resolved_settings.sandbox_supervisor_url
                        ),
                        mcp_pool=mcp_pool,
                    ),
                    middleware_env=build_middleware_env(),
                )
            if reaper is not None:
                reaper.start()
            resolved_lifecycle.mark_ready()
            logger.info(
                "control_plane.lifespan.ready",
                extra={"service": resolved_settings.service_name, "env": resolved_settings.env},
            )
            try:
                yield
            finally:
                if reaper is not None:
                    await reaper.stop()
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
    app.state.tenant_rate_limiter = resolved_tenant_limiter
    app.state.agent_spec_repo = resolved_repo
    app.state.thread_meta_repo = resolved_threads
    app.state.feedback_store = resolved_feedback
    app.state.audit_logger = resolved_audit
    app.state.manifest_loader = resolved_loader
    app.state.jwt_verifier = resolved_verifier
    app.state.mtls_verifier = resolved_mtls
    app.state.service_account_repo = resolved_service_accounts
    app.state.api_key_repo = resolved_api_keys
    app.state.role_binding_repo = resolved_role_bindings
    app.state.api_key_verifier = resolved_api_key_verifier
    app.state.tenant_quota_repo = resolved_tenant_quotas
    app.state.token_reservation_repo = resolved_reservations
    app.state.quota_service = resolved_quota
    app.state.quota_reaper = reaper
    app.state.tenant_config_repo = resolved_tenant_config_repo
    app.state.tenant_config_service = resolved_tenant_config_service
    app.state.agent_runtime = resolved_agent_runtime

    # Starlette wraps middleware in *reverse* registration order: the
    # last call to ``add_middleware`` becomes the outermost layer. We
    # therefore register innermost first.
    #
    # Effective execution order (outermost → innermost):
    #   1. ObservabilityMiddleware  — open span, record timing
    #   2. AuthMiddleware           — verify JWT → request.state.principal (C.1)
    #   3. TenantRateLimitMiddleware — per-tenant bucket (C.6)
    #   4. RLSContextMiddleware     — project principal.tenant_id → RLS ctxvar (C.4)
    #   5. AuditContextMiddleware   — project principal.tenant_id → log ctxvar
    #   6. RateLimitMiddleware      — per-IP / per-API-key bucket (B.2)
    #   7. CancellationMiddleware   — mint CancelToken + disconnect poll
    #   8. DeadlineMiddleware       — consume CancelToken + seed
    #                                 DeadlineContext from header
    #   9. InFlightMiddleware       — Lifecycle.track_in_flight (drain)
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
        TenantRateLimitMiddleware,
        limiter=resolved_tenant_limiter,
        audit_logger=resolved_audit,
        enabled=resolved_settings.tenant_rate_limit_enabled,
        exempt_path_prefixes=tuple(resolved_settings.auth_exempt_path_prefixes),
        audit_sample_every=resolved_settings.tenant_rate_limit_audit_sample_every,
    )
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
    app.include_router(build_feedback_router())
    app.include_router(build_service_accounts_router())
    app.include_router(build_api_keys_router())
    app.include_router(build_role_bindings_router())
    app.include_router(build_quota_router())
    app.include_router(build_tenant_quotas_router())
    app.include_router(build_tenant_config_router())

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


def _build_default_gateway_limiter(settings: Settings) -> RateLimiter:
    """Pick the gateway-tier limiter (B.2) impl based on Settings.

    Single-instance dev / unit tests stay on the in-process bucket so
    they don't need a Redis container. Multi-replica deploys
    (``single_instance=False``) plus a configured Redis URL get the
    Lua-backed implementation that survives horizontal scale-out.
    """
    if not settings.single_instance and settings.quota_redis_url:
        import redis.asyncio as redis_async

        client = redis_async.from_url(
            settings.quota_redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        return RedisTokenBucketLimiter(
            redis_client=client,
            capacity=settings.rate_limit_burst,
            refill_per_sec=settings.rate_limit_per_second,
        )
    return InProcessTokenBucketLimiter(
        capacity=settings.rate_limit_burst,
        refill_per_sec=settings.rate_limit_per_second,
    )


def _build_default_tenant_limiter(settings: Settings) -> RateLimiter:
    """Pick the tenant-tier limiter (C.6) impl based on Settings."""
    if not settings.single_instance and settings.quota_redis_url:
        import redis.asyncio as redis_async

        client = redis_async.from_url(
            settings.quota_redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        return RedisTokenBucketLimiter(
            redis_client=client,
            capacity=settings.tenant_rate_limit_capacity,
            refill_per_sec=settings.tenant_rate_limit_refill_per_sec,
        )
    return InProcessTokenBucketLimiter(
        capacity=settings.tenant_rate_limit_capacity,
        refill_per_sec=settings.tenant_rate_limit_refill_per_sec,
    )


def _build_default_quota_service(
    *,
    settings: Settings,
    quota_store: TenantQuotaStore,
    reservation_store: TokenReservationStore,
) -> QuotaService:
    """Pick the quota implementation based on Settings.quota_redis_url.

    Tests + dev (no redis URL) get the InMemoryQuotaService. Prod
    points ``HELIX_AGENT_QUOTA_REDIS_URL`` at the deployed Redis and
    we wire the Lua-backed implementation.
    """
    if settings.quota_redis_url:
        # Local import keeps redis-py off the import path for tests
        # that never touch this branch.
        import redis.asyncio as redis_async

        client = redis_async.from_url(
            settings.quota_redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        return RedisQuotaService(
            redis_client=client,
            quota_store=quota_store,
            reservation_store=reservation_store,
            default_qps_limit=settings.quota_default_qps_limit,
            default_qps_burst=settings.quota_default_qps_burst,
        )
    return InMemoryQuotaService(
        quota_store=quota_store,
        reservation_store=reservation_store,
        default_qps_limit=settings.quota_default_qps_limit,
        default_qps_burst=settings.quota_default_qps_burst,
    )
