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

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from helix_agent.protocol import PROVIDER_CATALOG, Provider, Tool

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

    #: Persistence backend for the control-plane stores — agent_spec /
    #: thread_meta / audit / api_key / role_binding / service_account /
    #: tenant_quota / token_reservation / tenant_config / feedback
    #: (ADR B-6). ``memory`` (M0 dev / tests — state is lost on restart)
    #: or ``sql`` (durable — Postgres via ``db_dsn``). ``sql`` requires
    #: the schema to exist: run the Alembic migrations first (the
    #: ``migrate`` one-shot service in ``infra/docker-compose.yml``).
    store_backend: Literal["memory", "sql"] = "memory"

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

    # ------------------------------------------------------------------ run trigger
    #: Path to the ``.env``-style file the local-dev SecretStore reads
    #: (F.6 / ADR-0007). ``None`` → an empty store: agent runs fail at
    #: provider-key resolution with a clear error until a file is set.
    secret_store_env_file: str | None = None

    #: Stream Q (Mini-ADR Q-1) — which SecretStore backend the app wires.
    #: ``local_dev`` (default; reads ``secret_store_env_file``) /
    #: ``sql_encrypted`` (envelope-encrypted values in Postgres — admins paste
    #: keys via the web UI; requires ``store_backend == "sql"`` +
    #: ``secret_encryption_key``) / ``aliyun_kms`` (deploy-time follow-up).
    secret_store_backend: Literal["local_dev", "sql_encrypted", "aliyun_kms"] = "local_dev"  # noqa: S105 — backend selector enum value, not a password

    #: Stream Q (Mini-ADR Q-2) — base64-encoded 32-byte AES-256 KEK for the
    #: ``sql_encrypted`` backend. ``SecretStr`` so it never renders in logs /
    #: repr. Required (and length-validated at boot) when
    #: ``secret_store_backend == "sql_encrypted"``.
    secret_encryption_key: SecretStr | None = None

    # ------------------------------------------------------------------ checkpointer (E.1)
    #: LangGraph checkpointer backend. ``memory`` (M0 dev / tests — run
    #: state is lost on restart) or ``postgres`` (durable — a run's
    #: graph state survives a process restart). ``postgres`` requires
    #: ``checkpointer_dsn``; the app refuses to boot without it.
    checkpointer_backend: Literal["memory", "postgres"] = "memory"

    #: libpq connection string for the Postgres checkpointer. Use the
    #: sync-driver scheme (``postgresql://...``) — ``AsyncPostgresSaver``
    #: manages its own async pool. ``None`` is only valid when
    #: ``checkpointer_backend`` is ``memory``.
    checkpointer_dsn: str | None = None

    # ------------------------------------------------------------------ tools (E.7)
    #: ``secret://`` reference to the Tavily API key for the built-in
    #: ``web_search`` tool. ``None`` → ``web_search`` is unavailable; an
    #: agent that declares it fails at build time with a clear error.
    #:
    #: Stream O (Mini-ADR O-5) — **deprecated**: prefer
    #: ``platform_tool_credentials["web_search"]``. Removal scheduled
    #: M1 Q? after all callers migrate to :class:`CredentialsResolver`.
    tavily_api_key_ref: str | None = None

    #: Path to a JSON file listing the platform's MCP servers —
    #: ``[{"name", "command": [...], "env": {...}}]`` (STREAM-E-DESIGN
    #: Mini-ADR E-17). The orchestrator launches one subprocess per
    #: entry, so the list is operator-controlled — never tenant input.
    #: ``None`` → an empty MCP pool (no servers).
    mcp_servers_config_file: str | None = None

    #: Base URL of the Sandbox Supervisor's HTTP API — backs the built-in
    #: ``exec_python`` tool (Stream F). ``None`` → ``exec_python`` is
    #: unavailable; an agent that declares it fails at build time with a
    #: clear error.
    sandbox_supervisor_url: str | None = None

    #: ``secret://`` reference to the embedding API key — backs long-term
    #: memory (Stream J.3). ``None`` → no embedder; an agent that declares
    #: ``memory.long_term`` fails at build time with a clear error.
    #:
    #: Stream O (Mini-ADR O-5) — **deprecated**: prefer
    #: ``platform_provider_credentials[<embedding_provider>]``. Removal
    #: scheduled M1 Q? after the embedder migrates to
    #: :class:`CredentialsResolver`.
    embedding_api_key_ref: str | None = None

    #: Embedding model name for long-term memory (Stream J.3). The default
    #: is qwen DashScope's ``text-embedding-v4`` (1024-dim — matches
    #: ``HELIX_AGENT_EMBEDDING_DIM``).
    embedding_model: str = "text-embedding-v4"

    #: Stream O (Mini-ADR O-9) — the catalog provider whose credential the
    #: embedder resolves through :class:`CredentialsResolver`. The embedder
    #: previously carried only a model name + a hardcoded base URL; the
    #: provider id is what keys the per-tenant credential lookup. Default
    #: ``qwen`` matches the default ``embedding_model``.
    embedding_provider: Provider = "qwen"

    #: Knowledge-retrieval reranker (Stream J.5). ``rerank_api_key_ref``
    #: ``None`` → no reranker; hybrid search then returns the RRF-fused
    #: order without an LLM rerank pass. ``rerank_provider`` is an
    #: OpenAI-compatible provider id (``ModelSpec.provider``).
    #:
    #: Stream O (Mini-ADR O-5) — ``rerank_api_key_ref`` **deprecated**;
    #: prefer ``platform_provider_credentials[rerank_provider]``.
    #: Removal scheduled M1 Q? after the reranker migrates to
    #: :class:`CredentialsResolver`.
    rerank_api_key_ref: str | None = None
    rerank_provider: str = "qwen"
    rerank_model: str = "qwen-plus"

    # ------------------------------------------------------------------ object storage (J.6)
    #: Object-store backend for uploaded images (Stream J.6). ``"memory"``
    #: (default) keeps a fresh checkout / CI booting without MinIO;
    #: ``"s3-compatible"`` points at MinIO / OSS / S3 and requires the
    #: ``object_store_*`` fields below.
    object_store_backend: Literal["memory", "s3-compatible"] = "memory"
    object_store_endpoint_url: str | None = None
    object_store_region: str = "us-east-1"
    object_store_bucket: str = "helix-agent"
    #: ``secret://`` references to the S3 access / secret keys — required
    #: when ``object_store_backend`` is ``"s3-compatible"``.
    object_store_access_key_ref: str | None = None
    object_store_secret_key_ref: str | None = None

    # ------------------------------------------------------------------ multimodal input (J.6)
    #: Per-image upload size cap (default 10 MiB) and per-run image count
    #: cap — boundary limits enforced by the image upload endpoint.
    multimodal_max_image_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    multimodal_max_images_per_run: int = Field(default=8, gt=0)
    #: Accepted image content types for upload.
    multimodal_allowed_content_types: tuple[str, ...] = (
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/gif",
    )

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

    # ------------------------------------------------------ Keycloak Admin (Stream R)
    #: Toggle the real Keycloak Admin REST client. ``False`` (default) wires a
    #: ``FakeKeycloakAdminClient`` so dev/CI never depend on a live Keycloak;
    #: integration / prod set it ``True`` to provision real accounts.
    keycloak_enabled: bool = False

    #: Keycloak base URL for the Admin API + service-account token grant.
    keycloak_base_url: str = "http://keycloak:8080"

    #: Realm the Admin client manages (member accounts live here).
    keycloak_realm: str = "helix-agent"

    #: Confidential client whose service account holds ``manage-users`` and
    #: drives the Admin API (``helix-agent-api-internal`` already has
    #: ``serviceAccountsEnabled``).
    keycloak_admin_client_id: str = "helix-agent-api-internal"

    #: Vault name of the service-account client secret (Stream Q
    #: ``SqlEncryptedSecretStore``). The secret value never lives in settings.
    keycloak_admin_secret_name: str = "helix-agent/platform/keycloak/admin-client-secret"  # noqa: S105 — vault key NAME, not a secret value

    #: Lifespan (seconds) of the Keycloak ``execute-actions-email`` set-password
    #: link sent on member invite. Default 24h.
    keycloak_email_action_lifespan_s: int = Field(default=86400, gt=0)

    #: Path-prefix exemption list. Health + metrics are always allowed
    #: through; ``/v1/webhooks`` is exempt because an external webhook
    #: caller has no helix principal — that endpoint authenticates with
    #: a per-trigger secret instead (Stream J.10 / Mini-ADR J-42). Any
    #: other path requires a valid JWT in M0.
    auth_exempt_path_prefixes: list[str] = Field(
        default_factory=lambda: ["/healthz", "/metrics", "/v1/webhooks"],
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

    # ------------------------------------------------------------------ memory DLQ (K.K7)
    #: Stream K.K7 — how often the memory writeback DLQ worker scans
    #: for retry-ready rows. Per-row backoff schedule is owned by the
    #: worker itself (1 min → 5 → 30 → 2 h → 6 h); this is the cycle
    #: cadence, kept short so a freshly-enqueued row is picked up
    #: promptly.
    memory_dlq_worker_interval_s: int = Field(default=30, gt=0)

    # ------------------------------------------------------------------ trigger scheduler (J.10)
    #: Stream J.10 — how often the trigger scheduler sweeps the
    #: ``agent_trigger`` table for due cron triggers. Cron granularity
    #: is one minute, so 60s is the natural cadence.
    trigger_scheduler_interval_s: int = Field(default=60, gt=0)

    #: Max triggers fired per scheduler sweep — caps a single cycle's work.
    trigger_scheduler_batch_size: int = Field(default=100, gt=0)

    #: Stream J.10 (Mini-ADR J-26 (2)) — max cron triggers a tenant may
    #: register. Caps the scheduler's per-sweep work + a runaway client.
    max_cron_triggers_per_tenant: int = Field(default=100, gt=0)

    # ------------------------------------------------------------------ curation worker (J.12)
    #: Stream J.12 — how often the curation worker scans the L7 trajectory
    #: ObjectStore for new candidates. Curation is not latency-sensitive;
    #: a few minutes is plenty.
    curation_worker_interval_s: int = Field(default=300, gt=0)

    #: Stream SE (SE-6d) — the Layer B skill-evolution worker. OFF by default:
    #: it distils + replay-verifies candidate skills into DRAFTs, which only
    #: matter once the SE-7 governance gate can promote them. Enable per-env
    #: once governance + the SE-9 benchmark are in place.
    enable_skill_evolution_worker: bool = Field(default=False)
    #: Evolution sweeps run real replays (LLM + graph) — keep the cadence slow.
    skill_evolution_worker_interval_s: int = Field(default=600, gt=0)

    #: Max trajectories examined per curation sweep — caps a cycle's work.
    curation_worker_batch_size: int = Field(default=200, gt=0)

    #: Stream J.12 (Mini-ADR J-43) — max curated eval-dataset rows a
    #: tenant may hold. Checked at create + promote; "current count"
    #: ceiling, the table is the authoritative count (like cron quota).
    max_eval_dataset_rows_per_tenant: int = Field(default=1000, gt=0)

    # --------------------------------------------------------- skill curator (Sprint #4)
    #: Capability Uplift Sprint #4 (Mini-ADR U-26) — how often the
    #: Curator sweeps every tenant + applies the state-machine
    #: transitions. Default 86400 = once per day. Tests dial this down
    #: to seconds to verify state transitions.
    skill_curator_interval_s: int = Field(default=86_400, gt=0)

    #: TTL on the in-process activity throttle (Mini-ADR U-27). Default
    #: 3600 = each skill produces at most one ``last_used_at`` UPDATE
    #: per hour per replica. Tighter than the state machine cares about
    #: but loose enough to cap write amplification under high fan-out.
    skill_activity_throttle_s: int = Field(default=3_600, gt=0)

    # --------------------------------------------------- memory consolidator (Sprint #7)
    #: Capability Uplift Sprint #7 (Mini-ADR U-34) — MemoryConsolidator
    #: sweep cadence. Default 14400 = every 4 hours (4 sweeps / day).
    #: Tighter than SkillCurator's daily because fact accumulation
    #: happens on hours-to-days, not weeks.
    memory_consolidator_interval_s: int = Field(default=14_400, gt=0)

    #: Capability Uplift Sprint #7 (Mini-ADR U-39) — default aux model
    #: name used when an agent's manifest leaves
    #: ``policies.memory_consolidation.aux_model`` unset. Sonnet rather
    #: than Haiku because the anti-mislearn classification benefits from
    #: a stronger model; this is a cold path so the cost premium is
    #: bounded.
    memory_consolidator_default_aux_model: str = "claude-sonnet-4-6"

    #: Stream O — default provider for the consolidator's aux model when
    #: the manifest leaves ``policies.memory_consolidation.aux_model``
    #: unset. The :class:`LLMRouterAuxModelAdapter` uses this to build
    #: a single-provider :class:`LLMRouter` with the appropriate
    #: credential resolved via :class:`CredentialsResolver`.
    memory_consolidator_default_aux_provider: Provider = "anthropic"

    # ----------------------------------------------- credentials catalog (Stream O)
    #: Stream O (Mini-ADR O-1) — LLM providers the platform deployment
    #: opts in to. A provider not in this list is unavailable to all
    #: tenants regardless of agent manifest (publish-time gate, Mini-ADR
    #: O-4) and cannot be configured by tenants in ``tenant`` mode.
    #: Default is the empty list — every deployment must explicitly
    #: enable providers via env (e.g.
    #: ``HELIX_AGENT_SUPPORTED_PROVIDERS=anthropic,openai,qwen``) along
    #: with a matching ``platform_provider_credentials`` entry. The
    #: startup validator (Mini-ADR O-1) refuses to boot if the two
    #: don't match.
    supported_providers: list[Provider] = Field(default_factory=list)

    #: Stream O (Mini-ADR O-1) — platform-level secret_ref per supported
    #: provider. The startup validator rejects any non-empty difference
    #: between ``supported_providers`` and the keys of this dict, so a
    #: deployment that opts a provider in **must** supply its credential.
    platform_provider_credentials: dict[Provider, str] = Field(
        default_factory=dict,
        description="provider → secret:// URI; startup validates full coverage",
    )

    #: Stream O (Mini-ADR O-1) — external tools the platform deployment
    #: opts in to. Same opt-in + tenant-mode-whitelist semantics as
    #: ``supported_providers``. Default empty; operators opt in explicitly.
    supported_tools: list[Tool] = Field(default_factory=list)

    #: Stream O (Mini-ADR O-1) — platform-level secret_ref per supported
    #: tool.
    platform_tool_credentials: dict[Tool, str] = Field(default_factory=dict)

    # --- Stream O Mini-ADR O-10: legacy → effective catalog derivation ---
    # The embedder / reranker / web_search callers migrate to
    # CredentialsResolver in PR 2a. A deployment that has not opted into
    # Stream O (empty ``platform_provider_credentials``) but still sets the
    # legacy ``embedding_api_key_ref`` / ``rerank_api_key_ref`` /
    # ``tavily_api_key_ref`` keeps working: these properties gap-fill the
    # catalog from the legacy fields. Explicit Stream O config always wins;
    # legacy only fills providers/tools not already present. The resolver
    # is built from the ``effective_*`` view, the startup validator still
    # checks only the explicit fields.

    @property
    def effective_platform_provider_credentials(self) -> dict[Provider, str]:
        """Explicit ``platform_provider_credentials`` plus legacy
        embedding / rerank refs gap-filling absent providers."""
        merged = dict(self.platform_provider_credentials)
        if self.embedding_api_key_ref and self.embedding_provider not in merged:
            merged[self.embedding_provider] = self.embedding_api_key_ref
        if (
            self.rerank_api_key_ref
            and self.rerank_provider in PROVIDER_CATALOG
            and self.rerank_provider not in merged
        ):
            # ``rerank_provider in PROVIDER_CATALOG`` narrows str → Provider.
            merged[self.rerank_provider] = self.rerank_api_key_ref
        return merged

    @property
    def effective_supported_providers(self) -> list[Provider]:
        """Union of explicit ``supported_providers`` and any provider the
        legacy derivation added — order: explicit first, then derived."""
        derived = list(self.supported_providers)
        for provider in self.effective_platform_provider_credentials:
            if provider not in derived:
                derived.append(provider)
        return derived

    @property
    def effective_platform_tool_credentials(self) -> dict[Tool, str]:
        """Explicit ``platform_tool_credentials`` plus the legacy Tavily
        ref gap-filling ``web_search``."""
        merged = dict(self.platform_tool_credentials)
        if self.tavily_api_key_ref and "web_search" not in merged:
            merged["web_search"] = self.tavily_api_key_ref
        return merged

    @property
    def effective_supported_tools(self) -> list[Tool]:
        """Union of explicit ``supported_tools`` and any tool the legacy
        derivation added."""
        derived = list(self.supported_tools)
        for tool in self.effective_platform_tool_credentials:
            if tool not in derived:
                derived.append(tool)
        return derived

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

    #: Dedicated, tighter per-tenant bucket for the MCP probe-bearing
    #: endpoints (register / instantiate / test / list-tools). Each of those
    #: triggers a server-side outbound connection to a tenant-chosen URL, so
    #: they get a far smaller bucket than ordinary CRUD to blunt
    #: outbound-amplification / scanning (security audit #6). Gated by the
    #: same ``tenant_rate_limit_enabled`` toggle.
    mcp_probe_rate_limit_capacity: int = Field(default=10, gt=0)

    #: Refill rate (tokens / second) for the MCP probe bucket.
    mcp_probe_rate_limit_refill_per_sec: float = Field(default=0.5, gt=0)

    #: Public redirect URI for the per-user MCP OAuth callback (Stream MCP-OAUTH).
    #: Must be reachable by the user's browser and registered in each connector's
    #: OAuth app allowlist, e.g. ``https://app.example.com/v1/mcp-oauth/callback``.
    #: ``None`` (default) makes the OAuth initiate endpoint return 503.
    mcp_oauth_redirect_uri: str | None = None

    #: Path to the MCP connector catalog env-seed template (Stream MCP-OAUTH
    #: OA-5), e.g. ``configs/mcp-catalog-seed.json``. On startup each entry whose
    #: ``${VAR}`` placeholders all resolve from the environment is created (idem-
    #: potently); entries with an unset placeholder are skipped. ``None``
    #: (default) seeds nothing.
    mcp_catalog_seed_file: str | None = None

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
