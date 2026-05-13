"""Control Plane settings — Stream B.1.

Pydantic v2 ``BaseSettings`` with ``HELIX_AGENT_`` env prefix. The default
values point at the local ``infra/docker-compose.yml`` PgBouncer so a
fresh checkout boots without env tweaks.

ADR B-5 (STREAM-B-DESIGN.md § 3): ``auth_mode`` is the gate between
header-trust dev mode and the prod-mode startup guard that refuses to
boot until C.1 OIDC middleware lands.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Default tenant UUID assigned to header-less dev requests. ``00...00`` is
#: deliberately the nil UUID so it sticks out in audit_log dumps.
DEFAULT_DEV_TENANT_ID: UUID = UUID("00000000-0000-0000-0000-000000000000")

#: Default actor id for dev requests with no ``X-Helix-Actor`` header.
DEFAULT_DEV_ACTOR_ID: str = "anonymous"


class Settings(BaseSettings):
    """Resolved runtime settings; cheap to construct in tests."""

    model_config = SettingsConfigDict(
        env_prefix="HELIX_AGENT_",
        case_sensitive=False,
        # Service file overrides come from environments/<env>.yaml; this
        # only honours real env vars. Keep it strict so typos surface.
        extra="ignore",
    )

    # ------------------------------------------------------------------ identity
    service_name: str = "control_plane"
    env: Literal["dev", "staging", "prod"] = "dev"
    log_level: str = "INFO"

    # ------------------------------------------------------------------ auth (B-5)
    auth_mode: Literal["dev", "prod"] = "dev"
    default_dev_tenant_id: UUID = DEFAULT_DEV_TENANT_ID
    default_dev_actor_id: str = DEFAULT_DEV_ACTOR_ID

    # ------------------------------------------------------------------ database
    db_dsn: str = "postgresql+asyncpg://helix_agent:helix_agent_dev@localhost:6432/helix_agent_dev"
    db_pgbouncer_mode: bool = True
    db_echo: bool = False

    # ------------------------------------------------------------------ otel
    otlp_traces_endpoint: str | None = None

    # ------------------------------------------------------------------ runtime guards
    # ADR B-1: in-process rate-limiter assumes a single replica. Override
    # to ``False`` only after C.6 ships the Redis backend.
    single_instance: bool = True

    health_check_timeout_s: float = Field(default=5.0, gt=0)

    # ------------------------------------------------------------------ rate limit (B.2)
    # Gateway-tier limiter (subsystems/16 § 5.1, layer 1). Per-tenant
    # (layer 2, C.6) and per-provider (layer 3, E.6) tiers stack on top.
    rate_limit_enabled: bool = True
    rate_limit_burst: int = Field(default=60, gt=0)
    rate_limit_per_second: float = Field(default=20.0, gt=0)

    # ------------------------------------------------------------------ cancellation (B.3)
    # ADR B-2: 50 ms cadence + 50 ms scheduler drift + 100 ms handler
    # deadline_check ≈ 200 ms detection budget (verification gate #3).
    cancellation_poll_interval_s: float = Field(default=0.05, gt=0)

    # ------------------------------------------------------------------ run trigger (B.7)
    # M0 fake-SSE inter-token delay. Stream E replaces the generator and
    # this knob disappears; the field stays for now so tests can drive
    # the loop deterministically (``0.0`` = no delay).
    run_fake_token_delay_s: float = Field(default=0.005, ge=0)

    # ------------------------------------------------------------------ auth (C.1)
    # OIDC issuer used to validate the ``iss`` JWT claim and to derive
    # the JWKS endpoint when ``oidc_jwks_uri`` is not set. The default
    # points at the Keycloak service in ``infra/docker-compose.yml``.
    oidc_issuer: str = "http://keycloak:8080/realms/helix-agent"

    #: Accepted JWT ``aud`` values. Anthropic / Keycloak service-account
    #: tokens typically carry the client id here.
    oidc_audience: list[str] = Field(default_factory=lambda: ["helix-agent-api-internal"])

    #: Optional explicit JWKS URI. ``None`` → derived from ``oidc_issuer``
    #: + Keycloak's well-known ``/protocol/openid-connect/certs`` path.
    oidc_jwks_uri: str | None = None

    #: Per-process TTL for the JWKS cache (seconds). Keycloak default key
    #: rotation cadence is daily; 300s gives bounded propagation delay
    #: without hammering Keycloak.
    oidc_jwks_cache_ttl_s: int = Field(default=300, gt=0)

    #: Leeway when validating ``exp`` (seconds). Clock skew tolerance.
    oidc_jwt_leeway_s: int = Field(default=30, ge=0)

    #: Path-prefix exemption list. Health + metrics are always allowed
    #: through; any other path requires a valid JWT in M0.
    auth_exempt_path_prefixes: list[str] = Field(
        default_factory=lambda: ["/healthz", "/metrics"],
    )

    # ------------------------------------------------------------------ mTLS (C.2)
    #: Toggle the mTLS auth branch. When ``False``, ``AuthMiddleware``
    #: only accepts JWTs. Useful for environments where the proxy hasn't
    #: been configured to forward XFCC yet.
    mtls_enabled: bool = True

    #: Header forwarded by the reverse proxy (nginx / Envoy / Istio).
    #: Lowercase per Starlette header normalisation.
    mtls_xfcc_header_name: str = "x-forwarded-client-cert"

    #: CN allowlist for service certificates. Empty list means **no
    #: service is allowed in** — explicit opt-in only.
    mtls_allowed_service_subjects: list[str] = Field(
        default_factory=lambda: ["orchestrator", "sandbox-supervisor"],
    )

    #: Sentinel tenant id assigned to mTLS-authenticated principals.
    #: Internal handlers (e.g. ``/v1/quota/*``) must read the *target*
    #: tenant from the request body — mTLS only proves the caller, not
    #: the tenant they are calling for.
    mtls_system_tenant_id: UUID = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")

    #: Reject XFCC elements without a SPIFFE-style ``URI`` SAN. Off by
    #: default — Keycloak / dev certs typically don't set one.
    mtls_require_uri_san: bool = False

    # ------------------------------------------------------------------ quota (C.5)
    #: Redis DSN for the quota engine (subsystems/16 § 3.2). When unset,
    #: ``create_app`` falls back to the in-memory quota service so unit
    #: tests stay fast. Set to ``redis://redis:6379/0`` in dev /
    #: production.
    quota_redis_url: str | None = None

    #: Default per-tenant QPS limit applied when no ``tenant_quota`` row
    #: exists for the ``qps`` dimension. ``None`` means "unlimited"
    #: (M0 dev default — production sets a value through
    #: ``tenant_quota``).
    quota_default_qps_limit: int | None = None

    #: Burst capacity for the default ``qps`` bucket. Ignored when
    #: ``quota_default_qps_limit`` is None.
    quota_default_qps_burst: int = Field(default=120, gt=0)

    #: Reservation timeout (seconds). After this many seconds a
    #: reservation in ``RESERVED`` state is auto-released by the reaper
    #: (subsystems/16 § 5.4 — 30 minutes).
    quota_reservation_max_age_s: int = Field(default=30 * 60, gt=0)

    #: How often the reaper scans for expired reservations.
    quota_reaper_interval_s: int = Field(default=10 * 60, gt=0)

    #: Number of reservations to release per reaper cycle. Caps the
    #: blast radius of a misconfiguration that leaks reservations en
    #: masse.
    quota_reaper_batch_size: int = Field(default=100, gt=0)

    # ------------------------------------------------------------------ tenant rate limit (C.6)
    #: Per-tenant request bucket capacity (tokens). Drained one token
    #: per authenticated request.
    tenant_rate_limit_capacity: int = Field(default=200, gt=0)

    #: Refill rate (tokens / second) for the per-tenant bucket.
    tenant_rate_limit_refill_per_sec: float = Field(default=50.0, gt=0)

    #: Toggle for the per-tenant rate-limit middleware. ``False`` lets
    #: dev / single-tenant load tests run without the limit kicking in.
    tenant_rate_limit_enabled: bool = True

    #: Sample interval for ``quota:rate_limit_denied`` audit emissions.
    #: ``1`` audits every denial; default samples 1 in 100 per
    #: subsystems/16 § 8 (caps log volume under sustained throttle
    #: storms).
    tenant_rate_limit_audit_sample_every: int = Field(default=100, ge=1)

    #: When the runtime has a Redis URL (``quota_redis_url``) and
    #: ``single_instance`` is ``False``, the gateway / tenant limiter
    #: use Redis (multi-replica safe). On the default single-instance
    #: dev setup the in-process limiter is used so tests don't need a
    #: Redis container.

    # ------------------------------------------------------------------ tenant config (C.7)
    #: ``TenantConfigService`` in-memory cache TTL (seconds). Bounded
    #: by STREAM-C-DESIGN § 2.8 at 60s — keeps the hot path off the
    #: database without making admin edits invisible for too long.
    tenant_config_cache_ttl_s: int = Field(default=60, gt=0)

    def resolve_jwks_uri(self) -> str:
        """Return the explicit JWKS URI or derive it from the issuer."""
        if self.oidc_jwks_uri:
            return self.oidc_jwks_uri
        return self.oidc_issuer.rstrip("/") + "/protocol/openid-connect/certs"
