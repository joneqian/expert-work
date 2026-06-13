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
import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path

import httpx
from fastapi import FastAPI
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from control_plane.api import (
    build_agent_schema_router,
    build_agents_router,
    build_api_keys_router,
    build_approvals_router,
    build_artifacts_router,
    build_audit_router,
    build_billing_admin_router,
    build_curation_router,
    build_eval_dataset_router,
    build_feedback_router,
    build_health_router,
    build_knowledge_router,
    build_mcp_catalog_router,
    build_mcp_oauth_router,
    build_mcp_servers_router,
    build_me_router,
    build_members_router,
    build_memory_router,
    build_metrics_router,
    build_model_catalog_router,
    build_plan_router,
    build_platform_config_router,
    build_platform_embedding_config_router,
    build_platform_skills_router,
    build_quota_router,
    build_rate_card_router,
    build_role_bindings_router,
    build_runs_list_router,
    build_runs_router,
    build_sandboxes_router,
    build_service_accounts_router,
    build_sessions_router,
    build_skill_evolution_router,
    build_skills_router,
    build_tenant_config_router,
    build_tenant_quotas_router,
    build_tenants_router,
    build_triggers_router,
    build_uploads_router,
    build_usage_router,
    build_webhook_endpoints_router,
    build_webhooks_router,
)
from control_plane.api.model_catalog import PlatformConfiguredProviders
from control_plane.approval_metrics import ApprovalGaugeWorker
from control_plane.audit import TenantConfigPiiResolver, build_default_audit_logger
from control_plane.auth import (
    ApiKeyVerifier,
    HTTPJWKSProvider,
    JWTVerifier,
    MTLSVerifier,
    build_mtls_verifier,
)
from control_plane.aux_model_adapter import make_llm_router_aux_model
from control_plane.catalog_seed import CatalogSeedError, load_catalog_seed, seed_catalog
from control_plane.curation_worker import CurationWorker
from control_plane.encrypted_secret_store import (
    SqlEncryptedSecretStore,
    build_kek_from_b64,
)
from control_plane.feedback_consumer import FeedbackConsumerWorker
from control_plane.keycloak import (
    FakeKeycloakAdminClient,
    HttpKeycloakAdminClient,
    KeycloakAdminClient,
    ServiceAccountTokenProvider,
)
from control_plane.knowledge.ingestion import KnowledgeIngestionRunner
from control_plane.manifest import ManifestLoader
from control_plane.mcp_oauth import default_http_client
from control_plane.mcp_oauth_refresh import McpOAuthRefresher
from control_plane.memory import MemoryDLQWorker
from control_plane.memory_consolidator import (
    ConsolidatorAuxModel,
    MemoryConsolidator,
    make_consolidator_embedder,
    make_null_consolidator_aux_model,
)
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
from control_plane.platform_embedding_config import PlatformEmbeddingConfigService
from control_plane.platform_secrets import PlatformSecretsService
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
    DynamicResolvingEmbedder,
    DynamicResolvingReranker,
    _build_mcp_client,
    build_mcp_pool,
    build_middleware_env,
    build_supervisor_client,
    build_tool_env,
    make_agent_builder,
    make_agent_runtime,
    make_image_resolver,
    make_knowledge_retriever,
    make_mcp_allowlist_provider,
    resolve_object_store_config,
    resolve_web_search_client,
)
from control_plane.scheduler import TriggerScheduler
from control_plane.settings import Settings
from control_plane.skill_activity import ThrottledActivityRecorder
from control_plane.skill_curator import SkillCurator
from control_plane.skill_evolution_limits import CircuitBreaker
from control_plane.skill_evolution_worker import SkillEvolutionWorker
from control_plane.skill_rollback_monitor import RollbackMonitor
from control_plane.skill_run_usage_recorder import StoreSkillRunUsageRecorder
from control_plane.subagent_runtime import make_child_agent_builder
from control_plane.tenancy import TenantConfigService
from control_plane.tenant_mcp_pool import TenantMcpPoolService
from control_plane.tenant_secret_overlay import TenantOverlayCredentialsResolver
from control_plane.tenant_status import TenantStatusService
from control_plane.user_mcp_oauth_pool import UserMcpOAuthPoolService
from control_plane.webhook_delivery_worker import WebhookDeliveryWorker
from control_plane.workspace_lock import PgWorkspaceLock
from helix_agent.common.credentials import CredentialsResolver
from helix_agent.common.health import DefaultHealthProvider
from helix_agent.common.lifecycle import Lifecycle
from helix_agent.common.observability import init_logging, init_tracing
from helix_agent.common.uplift_metrics import record_legacy_credentials_fallback
from helix_agent.persistence.agent_spec import (
    AgentSpecStore,
    InMemoryAgentSpecStore,
    SqlAgentSpecStore,
)
from helix_agent.persistence.approval import (
    ApprovalStore,
    InMemoryApprovalStore,
    SqlApprovalStore,
)
from helix_agent.persistence.artifact import (
    ArtifactStore,
    InMemoryArtifactStore,
    SqlArtifactStore,
)
from helix_agent.persistence.audit_log import AuditLogStore, SqlAuditLogStore
from helix_agent.persistence.auth import (
    ApiKeyStore,
    InMemoryApiKeyStore,
    InMemoryRoleBindingStore,
    InMemoryServiceAccountStore,
    RoleBindingStore,
    ServiceAccountStore,
    SqlApiKeyStore,
    SqlRoleBindingStore,
    SqlServiceAccountStore,
)
from helix_agent.persistence.billing import (
    DbModelRateCardStore,
    DbTenantBillingLedgerStore,
    InMemoryModelRateCardStore,
    InMemoryTenantBillingLedgerStore,
    ModelRateCardStore,
    TenantBillingLedgerStore,
)
from helix_agent.persistence.curation import (
    CurationCandidateStore,
    EvalDatasetStore,
    InMemoryCurationCandidateStore,
    InMemoryEvalDatasetStore,
    SqlCurationCandidateStore,
    SqlEvalDatasetStore,
)
from helix_agent.persistence.database import (
    DatabaseConfig,
    create_async_engine_from_config,
    create_async_session_factory,
)
from helix_agent.persistence.feedback_store import (
    DbFeedbackStore,
    FeedbackStore,
    InMemoryFeedbackStore,
)
from helix_agent.persistence.image_upload import (
    ImageUploadStore,
    InMemoryImageUploadStore,
    SqlImageUploadStore,
)
from helix_agent.persistence.knowledge import (
    InMemoryKnowledgeStore,
    KnowledgeStore,
    SqlKnowledgeStore,
)
from helix_agent.persistence.mcp_connector_catalog import (
    InMemoryMcpConnectorCatalogStore,
    McpConnectorCatalogStore,
    SqlMcpConnectorCatalogStore,
)
from helix_agent.persistence.mcp_oauth_connection import (
    InMemoryMcpOAuthConnectionStore,
    McpOAuthConnectionStore,
    SqlMcpOAuthConnectionStore,
)
from helix_agent.persistence.memory import (
    InMemoryMemoryStore,
    InMemoryMemoryWritebackDLQ,
    MemoryStore,
    MemoryWritebackDLQ,
    SqlMemoryStore,
    SqlMemoryWritebackDLQ,
)
from helix_agent.persistence.platform_embedding_config import (
    InMemoryPlatformEmbeddingConfigStore,
    PlatformEmbeddingConfigStore,
    SqlPlatformEmbeddingConfigStore,
)
from helix_agent.persistence.platform_secrets import (
    InMemoryPlatformSecretStore,
    PlatformSecretStore,
    SqlPlatformSecretStore,
)
from helix_agent.persistence.quota import (
    InMemoryTenantQuotaStore,
    InMemoryTokenReservationStore,
    SqlTenantQuotaStore,
    SqlTokenReservationStore,
    TenantQuotaStore,
    TokenReservationStore,
)
from helix_agent.persistence.rls import build_rls_sessionmaker
from helix_agent.persistence.skill import (
    InMemorySkillStore,
    SkillStore,
    SqlSkillStore,
)
from helix_agent.persistence.tenant_config import (
    InMemoryTenantConfigStore,
    SqlTenantConfigStore,
    TenantConfigStore,
)
from helix_agent.persistence.tenant_mcp_server import (
    InMemoryTenantMcpServerStore,
    SqlTenantMcpServerStore,
    TenantMcpServerStore,
)
from helix_agent.persistence.tenant_member import (
    InMemoryTenantMemberStore,
    SqlTenantMemberStore,
    TenantMemberStore,
)
from helix_agent.persistence.tenant_user import (
    InMemoryTenantUserStore,
    SqlTenantUserStore,
    TenantUserStore,
)
from helix_agent.persistence.thread_meta import (
    InMemoryThreadMetaStore,
    SqlThreadMetaStore,
    ThreadMetaStore,
)
from helix_agent.persistence.token_usage_store import (
    DbTokenUsageStore,
    InMemoryTokenUsageStore,
    TokenUsageStore,
)
from helix_agent.persistence.trigger import (
    InMemoryTriggerRunStore,
    InMemoryTriggerStore,
    SqlTriggerRunStore,
    SqlTriggerStore,
    TriggerRunStore,
    TriggerStore,
)
from helix_agent.persistence.webhook import (
    InMemoryWebhookDeliveryStore,
    InMemoryWebhookEndpointStore,
    SqlWebhookDeliveryStore,
    SqlWebhookEndpointStore,
    WebhookDeliveryStore,
    WebhookEndpointStore,
)
from helix_agent.protocol import PROVIDER_CATALOG
from helix_agent.runtime.audit.logger import AuditLogger
from helix_agent.runtime.checkpointer import make_checkpointer
from helix_agent.runtime.middleware import make_langfuse_client
from helix_agent.runtime.runs import (
    InMemoryRunEventStore,
    InMemoryRunStore,
    RunEventStore,
    RunStore,
    SqlRunEventStore,
    SqlRunStore,
)
from helix_agent.runtime.secret_store import SecretStore, make_secret_store
from helix_agent.runtime.storage import make_object_store
from orchestrator import MemoryEnv
from orchestrator.trajectory import TrajectoryReader

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
    tenant_user_repo: TenantUserStore | None = None,
    feedback_repo: FeedbackStore | None = None,
    token_usage_repo: TokenUsageStore | None = None,
    curation_candidate_repo: CurationCandidateStore | None = None,
    eval_dataset_repo: EvalDatasetStore | None = None,
    artifact_repo: ArtifactStore | None = None,
    knowledge_repo: KnowledgeStore | None = None,
    image_upload_repo: ImageUploadStore | None = None,
    approval_repo: ApprovalStore | None = None,
    run_repo: RunStore | None = None,
    run_event_repo: RunEventStore | None = None,
    trigger_repo: TriggerStore | None = None,
    trigger_run_repo: TriggerRunStore | None = None,
    webhook_endpoint_repo: WebhookEndpointStore | None = None,
    webhook_delivery_repo: WebhookDeliveryStore | None = None,
    skill_repo: SkillStore | None = None,
    knowledge_ingestion_runner: KnowledgeIngestionRunner | None = None,
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
    enable_scheduler: bool = True,
    enable_curation_worker: bool = True,
    tenant_rate_limiter: RateLimiter | None = None,
    tenant_config_repo: TenantConfigStore | None = None,
    tenant_config_service: TenantConfigService | None = None,
    tenant_member_repo: TenantMemberStore | None = None,
    keycloak_admin_client: KeycloakAdminClient | None = None,
    platform_secret_store: PlatformSecretStore | None = None,
    secret_store: SecretStore | None = None,
    agent_runtime: AgentRuntime | None = None,
    memory_repo: MemoryStore | None = None,
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
    # Stream O (Mini-ADR O-1) — Platform Catalog startup validation.
    # Fail fast at create_app rather than at first LLM call so a
    # mis-configured deployment crashes the pipeline at boot.
    _validate_platform_catalog(resolved_settings)
    # Stream O (Mini-ADR O-10) — warn + meter when the effective catalog
    # is still gap-filled from a legacy ``*_api_key_ref`` field.
    _signal_legacy_credentials_derivation(resolved_settings)
    # ADR B-6: in ``sql`` mode every store is Postgres-backed off one
    # RLS-wrapped sessionmaker; the engine is disposed in ``lifespan``.
    # Injected repos still win, so unit tests keep their in-memory
    # stores untouched.
    sql_stores = (
        _build_sql_stores(resolved_settings) if resolved_settings.store_backend == "sql" else None
    )
    resolved_lifecycle = lifecycle or Lifecycle()
    resolved_limiter = rate_limiter or _build_default_gateway_limiter(resolved_settings)
    resolved_tenant_limiter = tenant_rate_limiter or _build_default_tenant_limiter(
        resolved_settings
    )
    # Dedicated tight bucket for MCP probe-bearing endpoints (audit #6).
    resolved_mcp_probe_limiter = (
        _build_mcp_probe_limiter(resolved_settings)
        if resolved_settings.tenant_rate_limit_enabled
        else None
    )
    resolved_repo = agent_spec_repo or (
        sql_stores.agent_spec if sql_stores else InMemoryAgentSpecStore()
    )
    resolved_threads = thread_meta_repo or (
        sql_stores.thread_meta if sql_stores else InMemoryThreadMetaStore()
    )
    resolved_tenant_users = tenant_user_repo or (
        sql_stores.tenant_user if sql_stores else InMemoryTenantUserStore()
    )
    # Stream J.3 — long-term memory store for the agent runtime.
    resolved_memory_store: MemoryStore = memory_repo or (
        sql_stores.memory if sql_stores else InMemoryMemoryStore()
    )
    # Stream K.K7 — DLQ used by the writeback path; defaults match the
    # memory store's backing (SQL when sql_stores is set, else in-memory).
    resolved_memory_dlq: MemoryWritebackDLQ = (
        sql_stores.memory_dlq if sql_stores else InMemoryMemoryWritebackDLQ()
    )
    # Stream J.9 — artifact registry backing save_artifact / list_artifacts
    # and the artifact API. The supervisor client backs artifact content
    # download (only the supervisor can read a per-user volume); it is
    # shared with the agent tool env.
    resolved_artifact_store: ArtifactStore = artifact_repo or (
        sql_stores.artifact if sql_stores else InMemoryArtifactStore()
    )
    # Stream J.5 — knowledge bases (RAG) backing the knowledge API + the
    # knowledge_search tool.
    resolved_knowledge_store: KnowledgeStore = knowledge_repo or (
        sql_stores.knowledge if sql_stores else InMemoryKnowledgeStore()
    )
    # Stream J.6.补强-3 (Mini-ADR J-32) — image upload registry.
    resolved_image_upload_store: ImageUploadStore = image_upload_repo or (
        sql_stores.image_upload if sql_stores else InMemoryImageUploadStore()
    )
    # Stream J.8 (Mini-ADR J-24) — paused-run approval registry.
    resolved_approval_store: ApprovalStore = approval_repo or (
        sql_stores.approval if sql_stores else InMemoryApprovalStore()
    )
    # Stream J.8 closeout follow-up (Mini-ADR J-41) — durable run
    # lifecycle store. The same instance is wired into the RunManager
    # (mirror writes) and read by GET .../runs/{id} as the fallback
    # once the in-memory record has expired.
    resolved_run_store: RunStore = run_repo or (
        sql_stores.run if sql_stores else InMemoryRunStore()
    )
    # Stream H.3 PR 3 (Mini-ADR H-7) — durable SSE event store.
    resolved_run_event_store: RunEventStore = run_event_repo or (
        sql_stores.run_event if sql_stores else InMemoryRunEventStore()
    )
    # Stream J.10 (Mini-ADR J-26 / J-42) — trigger registry + firing log.
    resolved_trigger_store: TriggerStore = trigger_repo or (
        sql_stores.trigger if sql_stores else InMemoryTriggerStore()
    )
    resolved_trigger_run_store: TriggerRunStore = trigger_run_repo or (
        sql_stores.trigger_run if sql_stores else InMemoryTriggerRunStore()
    )
    # HX-9 (STREAM-HX § 13) — outbound webhook hook registry + delivery queue.
    resolved_webhook_endpoint_store: WebhookEndpointStore = webhook_endpoint_repo or (
        sql_stores.webhook_endpoint if sql_stores else InMemoryWebhookEndpointStore()
    )
    resolved_webhook_delivery_store: WebhookDeliveryStore = webhook_delivery_repo or (
        sql_stores.webhook_delivery if sql_stores else InMemoryWebhookDeliveryStore()
    )
    # Stream J.12 (Mini-ADR J-43) — curation candidate + eval-dataset registries.
    resolved_curation_candidate_store: CurationCandidateStore = curation_candidate_repo or (
        sql_stores.curation_candidate if sql_stores else InMemoryCurationCandidateStore()
    )
    resolved_eval_dataset_store: EvalDatasetStore = eval_dataset_repo or (
        sql_stores.eval_dataset if sql_stores else InMemoryEvalDatasetStore()
    )
    # Stream J.7a (Mini-ADR J-23) — skill registry.
    resolved_skill_store: SkillStore = skill_repo or (
        sql_stores.skill if sql_stores else InMemorySkillStore()
    )
    resolved_supervisor_client = build_supervisor_client(resolved_settings.sandbox_supervisor_url)
    resolved_feedback = feedback_repo or (
        sql_stores.feedback if sql_stores else InMemoryFeedbackStore()
    )
    resolved_token_usage = token_usage_repo or (
        sql_stores.token_usage if sql_stores else InMemoryTokenUsageStore()
    )
    # In-process agent runtime (RunManager + StreamBridge + manifest→agent
    # build path). Backend per ``secret_store_backend`` (Stream Q); tests
    # inject a runtime whose builder returns a fake-LLM agent.
    resolved_secret_store = secret_store or _build_secret_store(resolved_settings, sql_stores)
    resolved_agent_runtime = agent_runtime or make_agent_runtime(
        resolved_secret_store,
        run_store=resolved_run_store,
        run_event_store=resolved_run_event_store,
    )
    # SE-7d-3b-ii — wire run-end skill_run_usage emission (feeds the rollback
    # monitor). Gated with the monitor: one flag controls the whole SE-7d data
    # path. Cheap — most runs bind zero distilled skills, so the emit is a no-op.
    if resolved_settings.enable_skill_rollback_monitor:
        resolved_agent_runtime.skill_run_usage_recorder = StoreSkillRunUsageRecorder(
            store=resolved_skill_store
        )
    # Late-bound PII resolver: lets the audit logger reference
    # tenant_config without forcing it to exist yet (D.2 cycle break).
    pii_resolver = TenantConfigPiiResolver()
    resolved_audit = audit_logger or build_default_audit_logger(
        store=sql_stores.audit_log if sql_stores else None,
        pii_fields_resolver=pii_resolver,
    )
    resolved_loader = manifest_loader or ManifestLoader()
    resolved_verifier = jwt_verifier or _build_default_jwt_verifier(resolved_settings)
    resolved_mtls = mtls_verifier or _build_default_mtls_verifier(resolved_settings)
    resolved_service_accounts = service_account_repo or (
        sql_stores.service_account if sql_stores else InMemoryServiceAccountStore()
    )
    resolved_api_keys = api_key_repo or (
        sql_stores.api_key if sql_stores else InMemoryApiKeyStore()
    )
    resolved_role_bindings = role_binding_repo or (
        sql_stores.role_binding if sql_stores else InMemoryRoleBindingStore()
    )
    resolved_api_key_verifier = api_key_verifier or ApiKeyVerifier.from_store(resolved_api_keys)
    resolved_tenant_quotas = tenant_quota_repo or (
        sql_stores.tenant_quota if sql_stores else InMemoryTenantQuotaStore()
    )
    resolved_reservations = token_reservation_repo or (
        sql_stores.token_reservation if sql_stores else InMemoryTokenReservationStore()
    )
    resolved_quota = quota_service or _build_default_quota_service(
        settings=resolved_settings,
        quota_store=resolved_tenant_quotas,
        reservation_store=resolved_reservations,
    )
    resolved_tenant_config_repo = tenant_config_repo or (
        sql_stores.tenant_config if sql_stores else InMemoryTenantConfigStore()
    )
    resolved_tenant_config_service = tenant_config_service or TenantConfigService(
        store=resolved_tenant_config_repo,
        audit_logger=resolved_audit,
        ttl_s=float(resolved_settings.tenant_config_cache_ttl_s),
    )
    # Stream U (PR E) — per-tenant suspended-status cache. AuthMiddleware reads
    # it on the hot path to 403 a suspended tenant's members; the
    # deactivate/activate endpoints call ``invalidate`` for immediate effect.
    resolved_tenant_status_service = TenantStatusService(
        store=resolved_tenant_config_repo,
        ttl_seconds=float(resolved_settings.tenant_config_cache_ttl_s),
    )
    # Stream R — member roster + Keycloak Admin client. The client is a Fake
    # unless ``keycloak_enabled`` (dev/CI never depend on a live Keycloak).
    resolved_tenant_member_repo = tenant_member_repo or (
        sql_stores.tenant_member if sql_stores else InMemoryTenantMemberStore()
    )
    # Stream V — tenant-registered remote MCP server registry.
    resolved_tenant_mcp_server_store = (
        sql_stores.tenant_mcp_server if sql_stores else InMemoryTenantMcpServerStore()
    )
    # Stream W — platform-curated MCP connector catalog (NULL-tenant rows).
    resolved_mcp_connector_catalog_store = (
        sql_stores.mcp_connector_catalog if sql_stores else InMemoryMcpConnectorCatalogStore()
    )
    # Stream MCP-OAUTH — per-user OAuth connections to hosted MCP connectors.
    resolved_mcp_oauth_connection_store = (
        sql_stores.mcp_oauth_connection if sql_stores else InMemoryMcpOAuthConnectionStore()
    )
    # Stream Y (Y-3) — platform-curated model rate card (NULL-tenant rows).
    resolved_model_rate_card_store = (
        sql_stores.model_rate_card if sql_stores else InMemoryModelRateCardStore()
    )
    # Stream Y (Y-4) — per-tenant billing ledger (tenant-scoped; Stream Z reads it).
    resolved_tenant_billing_ledger_store = (
        sql_stores.tenant_billing_ledger if sql_stores else InMemoryTenantBillingLedgerStore()
    )
    resolved_keycloak_admin_client = keycloak_admin_client or _build_keycloak_admin_client(
        resolved_settings, resolved_secret_store
    )
    # Stream P (Mini-ADR P-7/P-9) — platform credential overlay (DB wins over
    # env). The service is lazy (no DB read until first getter call), so it is
    # safe to build in the synchronous factory; lifespan wires its getters into
    # the CredentialsResolver.
    resolved_platform_secret_store = platform_secret_store or (
        sql_stores.platform_secret if sql_stores else InMemoryPlatformSecretStore()
    )
    resolved_platform_secrets_service = PlatformSecretsService(
        store=resolved_platform_secret_store,
        settings=resolved_settings,
        ttl_s=float(resolved_settings.tenant_config_cache_ttl_s),
    )
    # Stream T (PR B) — effective embedding/rerank config (DB-row wins, env
    # fallback), TTL-cached like PlatformSecretsService. The dynamic
    # embedder/reranker read it per call so an admin's change takes effect
    # without a restart; PR C's write endpoint calls ``invalidate`` for
    # immediate effect on the writing instance. Lazy (no DB read until the
    # first getter), so it is safe to build in the synchronous factory.
    resolved_platform_embedding_config_store = (
        sql_stores.platform_embedding_config
        if sql_stores
        else InMemoryPlatformEmbeddingConfigStore()
    )
    resolved_platform_embedding_config_service = PlatformEmbeddingConfigService(
        store=resolved_platform_embedding_config_store,
        settings=resolved_settings,
        ttl_seconds=float(resolved_settings.tenant_config_cache_ttl_s),
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
    # Stream J.10 — single-replica trigger scheduler (Mini-ADR J-18 / J-42).
    scheduler: TriggerScheduler | None = (
        TriggerScheduler(
            trigger_store=resolved_trigger_store,
            trigger_run_store=resolved_trigger_run_store,
            run_store=resolved_run_store,
            agent_spec_store=resolved_repo,
            thread_store=resolved_threads,
            runtime=resolved_agent_runtime,
            audit_logger=resolved_audit,
            approval_store=resolved_approval_store,
            interval_s=resolved_settings.trigger_scheduler_interval_s,
            batch_size=resolved_settings.trigger_scheduler_batch_size,
            # Capability Uplift Sprint #1 — fire-time scan reads
            # tenant_config.trigger_fire_scan_mode (Mini-ADR U-2 Layer B).
            tenant_config_store=resolved_tenant_config_repo,
        )
        if enable_scheduler
        else None
    )
    # Capability Uplift Sprint #4 (Mini-ADRs U-26 / U-27) — Curator
    # sweep + activity recorder. Curator is single-replica (same
    # rationale as the trigger scheduler); the recorder is process-
    # local (each replica throttles independently — Curator's day-scale
    # decisions don't notice the per-replica fuzz).
    skill_curator: SkillCurator | None = (
        SkillCurator(
            skill_store=resolved_skill_store,
            tenant_config_service=resolved_tenant_config_service,
            audit_logger=resolved_audit,
            interval_s=float(resolved_settings.skill_curator_interval_s),
        )
        if enable_scheduler
        else None
    )
    skill_activity_recorder = ThrottledActivityRecorder(
        resolved_skill_store,
        ttl_seconds=resolved_settings.skill_activity_throttle_s,
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
            # Stream MCP-OAUTH (OA-5) — env-seed the connector catalog before
            # serving: oauth2 connectors whose ${VAR} client_id placeholders
            # resolve from the environment are created idempotently.
            await _seed_mcp_catalog(resolved_settings, resolved_mcp_connector_catalog_store)
            # Wire the agent runtime's backends before serving: the
            # checkpointer (E.1) plus the tool / middleware env bundles
            # (PR1.5). No run starts before lifespan completes, so the
            # agent cache is still empty — swapping the builder is
            # race-free. An injected runtime (tests) is left untouched.
            curation_worker: CurationWorker | None = None
            skill_evolution_worker: SkillEvolutionWorker | None = None
            skill_rollback_monitor: RollbackMonitor | None = None
            feedback_consumer: FeedbackConsumerWorker | None = None
            webhook_delivery_worker: WebhookDeliveryWorker | None = None
            approval_gauge_worker: ApprovalGaugeWorker | None = None
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
                # Stream O Mini-ADR O-9/O-10 — one CredentialsResolver over
                # the effective (legacy-derived) catalog. The web_search /
                # embedder / reranker callers and the consolidator aux model
                # all resolve per-tenant credentials through it. Stream HX-8:
                # the overlay subclass resolves through the tenant-effective
                # view (per-tenant override rows win, disabled rows suppress).
                credentials_resolver: CredentialsResolver = TenantOverlayCredentialsResolver(
                    tenant_provider_view=(
                        resolved_platform_secrets_service.effective_provider_credentials_for
                    ),
                    tenant_tool_view=(
                        resolved_platform_secrets_service.effective_tool_credentials_for
                    ),
                    platform_provider_credentials=(
                        resolved_settings.effective_platform_provider_credentials
                    ),
                    platform_tool_credentials=(
                        resolved_settings.effective_platform_tool_credentials
                    ),
                    tenant_config_getter=resolved_tenant_config_service,
                    # Stream P (Mini-ADR P-9) — live merged view (env + DB
                    # overlay); these win over the static dicts above so a
                    # platform admin's runtime change takes effect within the
                    # service's TTL without a restart.
                    platform_provider_getter=(
                        resolved_platform_secrets_service.effective_provider_credentials
                    ),
                    platform_tool_getter=(
                        resolved_platform_secrets_service.effective_tool_credentials
                    ),
                )
                _app.state.credentials_resolver = credentials_resolver
                web_search_client = await resolve_web_search_client(
                    resolver=credentials_resolver,
                    secret_store=resolved_secret_store,
                    supported_tools=resolved_settings.effective_supported_tools,
                )
                mcp_pool = await stack.enter_async_context(
                    build_mcp_pool(
                        resolved_settings.mcp_servers_config_file,
                        secret_store=resolved_secret_store,
                    )
                )

                # Stream V (Mini-ADR V-4) — per-tenant remote MCP pool.
                # Built lazily on first agent build per tenant, cached,
                # and closed at shutdown via the exit stack callback.
                async def _tenant_mcp_client_factory(cfg):  # type: ignore[no-untyped-def]
                    return await _build_mcp_client(cfg, secret_store=resolved_secret_store)

                tenant_mcp_pool_service = TenantMcpPoolService(
                    store=resolved_tenant_mcp_server_store,
                    secret_store=resolved_secret_store,
                    client_factory=_tenant_mcp_client_factory,
                )
                stack.push_async_callback(tenant_mcp_pool_service.close_all)
                _app.state.tenant_mcp_pool_service = tenant_mcp_pool_service

                async def _tenant_mcp_pool_provider(tenant_id):  # type: ignore[no-untyped-def]
                    return await tenant_mcp_pool_service.get_or_build(tenant_id)

                # Stream MCP-OAUTH (OA-3b) — per-(tenant,user) OAuth MCP pool;
                # OA-6 refresher lazily renews near-expiry access tokens at build.
                mcp_oauth_refresher = McpOAuthRefresher(
                    oauth_store=resolved_mcp_oauth_connection_store,
                    catalog_store=resolved_mcp_connector_catalog_store,
                    secret_store=resolved_secret_store,
                    http_factory=default_http_client,
                )
                user_mcp_oauth_pool_service = UserMcpOAuthPoolService(
                    oauth_store=resolved_mcp_oauth_connection_store,
                    catalog_store=resolved_mcp_connector_catalog_store,
                    client_factory=_tenant_mcp_client_factory,
                    refresher=mcp_oauth_refresher,
                )
                stack.push_async_callback(user_mcp_oauth_pool_service.close_all)
                _app.state.user_mcp_oauth_pool_service = user_mcp_oauth_pool_service

                async def _user_mcp_oauth_pool_provider(tenant_id, user_id):  # type: ignore[no-untyped-def]
                    return await user_mcp_oauth_pool_service.get_or_build(tenant_id, user_id)

                # Stream J.6 — object store for uploaded images + the
                # image resolver both multimodal paths draw on (Path A
                # content blocks / Path B ask_image).
                object_store = await stack.enter_async_context(
                    make_object_store(
                        resolved_settings.object_store_backend,
                        await resolve_object_store_config(
                            backend=resolved_settings.object_store_backend,
                            endpoint_url=resolved_settings.object_store_endpoint_url,
                            region=resolved_settings.object_store_region,
                            bucket=resolved_settings.object_store_bucket,
                            access_key_ref=resolved_settings.object_store_access_key_ref,
                            secret_key_ref=resolved_settings.object_store_secret_key_ref,
                            secret_store=resolved_secret_store,
                        ),
                    )
                )
                _app.state.object_store = object_store
                # Stream J.12 — the curation worker reads the L7
                # trajectory ObjectStore, so it is constructed here
                # where the store exists (mirrors MemoryDLQWorker).
                if enable_curation_worker:
                    curation_worker = CurationWorker(
                        trajectory_reader=TrajectoryReader(object_store=object_store),
                        candidate_store=resolved_curation_candidate_store,
                        thread_store=resolved_threads,
                        feedback_store=resolved_feedback,
                        interval_s=resolved_settings.curation_worker_interval_s,
                        batch_size=resolved_settings.curation_worker_batch_size,
                    )
                image_resolver = make_image_resolver(object_store)
                # Stream J.3 + Stream T (PR B) — long-term memory backend for
                # the agent. The embedder reads the live platform embedding
                # config per call (DB-row wins, env fallback), so an admin's
                # config change takes effect without a restart (Mini-ADR T-3).
                # It is ALWAYS a valid object; if embedding is unconfigured the
                # ``embed`` call raises ``AgentFactoryError`` at use time. The
                # build-time "memory.long_term needs embedding" gate now lives
                # in ``make_agent_builder`` (it checks the effective config).
                embedder = DynamicResolvingEmbedder(
                    config_service=resolved_platform_embedding_config_service,
                    resolver=credentials_resolver,
                    secret_store=resolved_secret_store,
                )
                # Stream K.K6 — memory CRUD endpoint needs the embedder
                # for the PATCH path (re-embed on content change). GET /
                # DELETE work without it; the PATCH path surfaces the
                # embedding-unconfigured error at call time now that the
                # embedder object is always present.
                _app.state.embedder = embedder
                # Stream J.5 — the knowledge retriever backing knowledge_search:
                # hybrid recall + an optional (deployment-configured) LLM rerank.
                # The reranker likewise reads the live rerank config per call and
                # degrades to identity order when rerank is unconfigured.
                reranker = DynamicResolvingReranker(
                    config_service=resolved_platform_embedding_config_service,
                    resolver=credentials_resolver,
                    secret_store=resolved_secret_store,
                )
                knowledge_retriever = make_knowledge_retriever(
                    store=resolved_knowledge_store, embedder=embedder, reranker=reranker
                )
                base_tool_env = build_tool_env(
                    resolved_tenant_config_service,
                    web_search_client=web_search_client,
                    supervisor_client=resolved_supervisor_client,
                    mcp_pool=mcp_pool,
                    artifact_store=resolved_artifact_store,
                    knowledge_retriever=knowledge_retriever,
                    image_resolver=image_resolver,
                    # Stream TE-8 — cross-replica per-workspace write lock for
                    # write_file / bash. A raw (non-RLS) sessionmaker: the
                    # advisory lock touches no tenant tables.
                    workspace_lock=(
                        PgWorkspaceLock(create_async_session_factory(sql_stores.engine))
                        if sql_stores is not None
                        else None
                    ),
                )
                # Stream HX-7 — resolve the Langfuse client once: the
                # SDK adapter when the deployment configures an instance,
                # the M0 recording stub otherwise (Mini-ADR HX-G3).
                langfuse_client = make_langfuse_client(
                    host=resolved_settings.langfuse_host,
                    public_key=resolved_settings.langfuse_public_key,
                    secret_key=resolved_settings.langfuse_secret_key,
                    # Mini-ADR OBS-L1 — PII masking on by default (fail-safe).
                    pii_masking_enabled=resolved_settings.langfuse_pii_masking_enabled,
                )
                middleware_env = build_middleware_env(
                    token_usage_store=resolved_token_usage,
                    langfuse_client=langfuse_client,
                )
                memory_env = MemoryEnv(
                    store=resolved_memory_store,
                    embedder=embedder,
                    dlq=resolved_memory_dlq,  # K.K7 — failed writebacks land here
                    # Capability Uplift Sprint #6 (Mini-ADR U-5) — recall
                    # node reads tenant_config.memory_recall_mode.
                    tenant_config_store=resolved_tenant_config_repo,
                    # Stream CM-4 — the same DynamicResolvingReranker that backs
                    # knowledge_search reorders long-term memory recall too. It
                    # reads the live rerank config per call and degrades to the
                    # RRF order when rerank is unconfigured.
                    reranker=reranker,
                )
                # Stream J.4 — the ChildAgentBuilder lets a SubAgentTool
                # resolve an agent_ref and recursively build the sub-agent;
                # the top-level agent's ToolEnv carries it so delegation
                # works at depth 0.
                child_agent_builder = make_child_agent_builder(
                    spec_store=resolved_repo,
                    secret_store=resolved_secret_store,
                    checkpointer=checkpointer,
                    base_tool_env=base_tool_env,
                    middleware_env=middleware_env,
                    memory_env=memory_env,
                    # Stream Q (Mini-ADR Q-5) — sub-agents whose manifest omits
                    # api_key_ref resolve the chat-LLM key from platform creds too.
                    credentials_resolver=credentials_resolver,
                    # Stream V (Mini-ADR V-4) — tenant's own remote MCP pool.
                    tenant_mcp_pool_provider=_tenant_mcp_pool_provider,
                    # Stream X (Mini-ADR X-4) — sub-agents resolve skills too.
                    skill_store=resolved_skill_store,
                    skill_activity_recorder=skill_activity_recorder,
                    tenant_config_service=resolved_tenant_config_service,
                    # Stream V-D (audit #1) — evict cached sub-agents when a
                    # tenant's MCP registry changes, like the top-level cache.
                    register_invalidation=resolved_agent_runtime.register_invalidation_hook,
                )
                resolved_agent_runtime.agent_builder = make_agent_builder(
                    resolved_secret_store,
                    checkpointer,
                    tool_env=replace(base_tool_env, child_agent_builder=child_agent_builder),
                    middleware_env=middleware_env,
                    memory_env=memory_env,
                    # Stream O (Mini-ADR O-14) — per-tenant MCP server allowlist.
                    mcp_allowlist_provider=make_mcp_allowlist_provider(
                        resolved_tenant_config_service
                    ),
                    # Stream Q (Mini-ADR Q-5) — chat-LLM key from platform creds
                    # when the manifest model has no api_key_ref.
                    credentials_resolver=credentials_resolver,
                    # Stream T (PR B) — build-time embedding gate. The dynamic
                    # embedder is never None, so the orchestrator's
                    # ``embedder is None`` gate can't fire; the builder checks
                    # the effective config and rejects a memory.long_term
                    # manifest when platform embedding is unconfigured.
                    platform_embedding_config_service=(resolved_platform_embedding_config_service),
                    # Stream V (Mini-ADR V-4) — tenant's own remote MCP pool.
                    tenant_mcp_pool_provider=_tenant_mcp_pool_provider,
                    # Stream MCP-OAUTH (OA-3b) — caller's per-user OAuth MCP pool.
                    user_mcp_oauth_pool_provider=_user_mcp_oauth_pool_provider,
                    # Stream X (Mini-ADR X-4) — first wiring of skill resolution
                    # into the agent build: tenant-first / platform-fallback +
                    # plan-tier gate, plus bind-activity tracking.
                    skill_store=resolved_skill_store,
                    skill_activity_recorder=skill_activity_recorder,
                    tenant_config_service=resolved_tenant_config_service,
                    # Stream SE (SE-3b) — audit for in-session skill authoring.
                    audit_logger=resolved_audit,
                )
                # Stream MCP-OAUTH (OA-3b) — let get_agent decide per-user builds.
                resolved_agent_runtime.user_oauth_pool_provider = _user_mcp_oauth_pool_provider
                # Stream T (PR B) — these background workers (ingestion runner,
                # DLQ worker, consolidator) are ALWAYS started. ``embedder`` is a
                # ``DynamicResolvingEmbedder`` that resolves the live platform
                # embedding config at use-time, so it is never None; the previous
                # ``if embedder is not None`` guards were dead. The build-time
                # gate in ``make_agent_builder`` rejects a ``memory.long_term``
                # agent when embedding is unconfigured, so no memory data (hence
                # no DLQ rows) can exist without embedding configured — making it
                # safe to always start these workers. An ``embed`` call against an
                # unconfigured embedding raises ``AgentFactoryError``, which each
                # worker loop already catches and backs off on. We deliberately do
                # NOT gate on a startup config check: that would break "config
                # takes effect without a restart" (resolve-at-use-time is intended).
                #
                # Stream J.5 — the ingestion runner needs the embedder to embed
                # uploaded knowledge documents.
                _app.state.knowledge_ingestion_runner = KnowledgeIngestionRunner(
                    store=resolved_knowledge_store, embedder=embedder
                )
            if reaper is not None:
                reaper.start()
            if scheduler is not None:
                scheduler.start()
                _app.state.trigger_scheduler = scheduler
            if skill_curator is not None:
                skill_curator.start()
                _app.state.skill_curator = skill_curator
            if curation_worker is not None:
                curation_worker.start()
                _app.state.curation_worker = curation_worker
            # Stream K.K7 — the DLQ retry worker re-embeds failed writebacks
            # before re-attempting the store write. Always started (see the
            # always-on note above): the embedder is the always-present dynamic
            # embedder, and DLQ rows can only exist for a memory.long_term agent,
            # which the build-time gate already requires embedding for.
            memory_dlq_worker = MemoryDLQWorker(
                dlq=resolved_memory_dlq,
                memory_store=resolved_memory_store,
                embedder=embedder,
                interval_s=resolved_settings.memory_dlq_worker_interval_s,
            )
            memory_dlq_worker.start()
            _app.state.memory_dlq_worker = memory_dlq_worker
            # Capability Uplift Sprint #7 (Mini-ADRs U-34 / U-39) —
            # MemoryConsolidator. Started whenever the scheduler is enabled
            # (the always-present dynamic embedder embeds the consolidated
            # summary text — see the always-on note above).
            #
            # Stream O Mini-ADR O-6 — the aux model now flows through
            # the production :class:`LLMRouterAuxModelAdapter`, which
            # resolves credentials per-tenant via
            # :class:`CredentialsResolver` (platform / tenant mode).
            # The deployment falls back to the null adapter when the
            # default provider has no platform credential and no tenant
            # has opted into tenant mode for it (operator deploys with
            # supported_providers but no platform secret → can't reach
            # the LLM yet → ship the worker idle rather than crash).
            memory_consolidator: MemoryConsolidator | None = None
            if enable_scheduler:
                default_provider = resolved_settings.memory_consolidator_default_aux_provider
                # Reuse the CredentialsResolver built above (Mini-ADR O-9).
                aux_model: ConsolidatorAuxModel
                if default_provider in resolved_settings.effective_platform_provider_credentials:
                    aux_model = make_llm_router_aux_model(
                        resolver=credentials_resolver,
                        secret_store=resolved_secret_store,
                        default_provider=default_provider,
                        default_model=(resolved_settings.memory_consolidator_default_aux_model),
                    )
                else:
                    logger.warning(
                        "memory_consolidator.aux_model.fallback_to_null reason="
                        "platform_credential_missing provider=%s",
                        default_provider,
                    )
                    aux_model = make_null_consolidator_aux_model()
                memory_consolidator = MemoryConsolidator(
                    memory_store=resolved_memory_store,
                    tenant_config_service=resolved_tenant_config_service,
                    audit_logger=resolved_audit,
                    aux_model=aux_model,
                    embedder=make_consolidator_embedder(embedder),
                    interval_s=float(resolved_settings.memory_consolidator_interval_s),
                    default_aux_model_name=(
                        resolved_settings.memory_consolidator_default_aux_model
                    ),
                )
                memory_consolidator.start()
                _app.state.memory_consolidator = memory_consolidator

            # Stream SE (SE-6d) — Layer B skill-evolution worker (gated OFF by
            # default). Built lazily so the orchestrator graph import it needs
            # stays out of the module-import path.
            # SE-7 auto-promote channel breaker, shared by the promote gate
            # (SE-7c) and the rollback monitor (SE-7d) so a promote that later
            # rolls back trips the same channel (SE-A12). In-process state.
            skill_evo_breaker = CircuitBreaker(
                failure_threshold=0.5, min_samples=5, window=timedelta(hours=24)
            )
            if resolved_settings.enable_skill_evolution_worker:
                from control_plane.skill_evolution_wiring import build_evolution_worker

                se_provider = resolved_settings.memory_consolidator_default_aux_provider
                if se_provider in resolved_settings.effective_platform_provider_credentials:
                    skill_evolution_worker = build_evolution_worker(
                        aux_model=make_llm_router_aux_model(
                            resolver=credentials_resolver,
                            secret_store=resolved_secret_store,
                            default_provider=se_provider,
                            default_model=resolved_settings.memory_consolidator_default_aux_model,
                        ),
                        aux_default_model=resolved_settings.memory_consolidator_default_aux_model,
                        candidate_store=resolved_curation_candidate_store,
                        skill_store=resolved_skill_store,
                        eval_store=resolved_eval_dataset_store,
                        agent_spec_store=resolved_repo,
                        trajectory_reader=TrajectoryReader(object_store=object_store),
                        agent_builder=resolved_agent_runtime.agent_builder,
                        interval_s=resolved_settings.skill_evolution_worker_interval_s,
                        audit_logger=resolved_audit,
                        breaker=skill_evo_breaker,
                    )
                    skill_evolution_worker.start()
                    _app.state.skill_evolution_worker = skill_evolution_worker
                else:
                    logger.warning(
                        "skill_evolution_worker.aux_model.unavailable provider=%s", se_provider
                    )

            # Stream SE (SE-7d-3b-i) — regression-rollback monitor. DB-only (no
            # LLM/graph), so no provider gate; shares the auto-promote breaker.
            if resolved_settings.enable_skill_rollback_monitor:
                from control_plane.skill_rollback_gate import RollbackGate
                from control_plane.skill_rollback_monitor import RollbackMonitorConfig

                skill_rollback_monitor = RollbackMonitor(
                    skill_store=resolved_skill_store,
                    gate=RollbackGate(
                        skill_store=resolved_skill_store,
                        breaker=skill_evo_breaker,
                        audit_logger=resolved_audit,
                        # Stream HX-2 (Mini-ADR HX-B2) — user 👎 joins the
                        # rollback window as a demoted sample.
                        feedback_store=resolved_feedback,
                    ),
                    config=RollbackMonitorConfig(
                        window=timedelta(days=resolved_settings.skill_rollback_window_days)
                    ),
                    interval_s=resolved_settings.skill_rollback_monitor_interval_s,
                )
                skill_rollback_monitor.start()
                _app.state.skill_rollback_monitor = skill_rollback_monitor

            # Stream HX-2 — feedback consumer (user 👎 → memory review
            # flags). DB-only, no LLM/provider gate.
            if resolved_settings.enable_feedback_consumer:
                feedback_consumer = FeedbackConsumerWorker(
                    feedback_store=resolved_feedback,
                    thread_meta_store=resolved_threads,
                    memory_store=resolved_memory_store,
                    audit_logger=resolved_audit,
                    interval_s=resolved_settings.feedback_consumer_interval_s,
                )
                feedback_consumer.start()
                _app.state.feedback_consumer = feedback_consumer
            # HX-9 (STREAM-HX § 13) — outbound webhook delivery worker.
            if resolved_settings.enable_webhook_delivery:
                webhook_delivery_worker = WebhookDeliveryWorker(
                    delivery_store=resolved_webhook_delivery_store,
                    endpoint_store=resolved_webhook_endpoint_store,
                    secret_store=resolved_secret_store,
                    run_store=resolved_run_store,
                    approval_store=resolved_approval_store,
                    artifact_store=resolved_artifact_store,
                    thread_meta_store=resolved_threads,
                    interval_s=resolved_settings.webhook_delivery_interval_s,
                    per_tenant_concurrency=(
                        resolved_settings.webhook_delivery_per_tenant_concurrency
                    ),
                )
                webhook_delivery_worker.start()
                _app.state.webhook_delivery_worker = webhook_delivery_worker
            # Stream HX-4 (Mini-ADR HX-D2) — pending-approvals gauge.
            # Always-on: one COUNT(*) a minute, no settings knob.
            approval_gauge_worker = ApprovalGaugeWorker(approval_store=resolved_approval_store)
            approval_gauge_worker.start()
            _app.state.approval_gauge_worker = approval_gauge_worker
            resolved_lifecycle.mark_ready()
            logger.info(
                "control_plane.lifespan.ready",
                extra={"service": resolved_settings.service_name, "env": resolved_settings.env},
            )
            try:
                yield
            finally:
                if memory_consolidator is not None:
                    await memory_consolidator.stop()
                if memory_dlq_worker is not None:
                    await memory_dlq_worker.stop()
                if reaper is not None:
                    await reaper.stop()
                if scheduler is not None:
                    await scheduler.stop()
                if skill_curator is not None:
                    await skill_curator.stop()
                if curation_worker is not None:
                    await curation_worker.stop()
                if skill_evolution_worker is not None:
                    await skill_evolution_worker.stop()
                if skill_rollback_monitor is not None:
                    await skill_rollback_monitor.stop()
                if feedback_consumer is not None:
                    await feedback_consumer.stop()
                if webhook_delivery_worker is not None:
                    await webhook_delivery_worker.stop()
                if approval_gauge_worker is not None:
                    await approval_gauge_worker.stop()
                # Stream HX-7 — drain the Langfuse SDK's background queue
                # before the process exits; the recording stub has no
                # shutdown, hence the duck-typed lookup.
                langfuse_shutdown = getattr(langfuse_client, "shutdown", None)
                if callable(langfuse_shutdown):
                    langfuse_shutdown()
                ingestion_runner: KnowledgeIngestionRunner | None = getattr(
                    _app.state, "knowledge_ingestion_runner", None
                )
                if ingestion_runner is not None:
                    await ingestion_runner.aclose()
                await resolved_lifecycle.graceful_shutdown()
                # ADR B-6: release the pool after the drain so in-flight
                # requests keep their connections until they complete.
                if sql_stores is not None:
                    await sql_stores.engine.dispose()
                logger.info("control_plane.lifespan.stopped")

    app = FastAPI(
        title="Helix-Agent Control Plane",
        version=_VERSION,
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.lifecycle = resolved_lifecycle
    # ``AsyncEngine`` in ``sql`` mode, ``None`` in ``memory`` mode (ADR B-6).
    app.state.db_engine = sql_stores.engine if sql_stores else None
    app.state.health_provider = health_provider
    app.state.rate_limiter = resolved_limiter
    app.state.mcp_probe_limiter = resolved_mcp_probe_limiter
    app.state.tenant_rate_limiter = resolved_tenant_limiter
    app.state.agent_spec_repo = resolved_repo
    app.state.thread_meta_repo = resolved_threads
    app.state.tenant_user_repo = resolved_tenant_users
    app.state.feedback_store = resolved_feedback
    app.state.token_usage_store = resolved_token_usage
    app.state.artifact_store = resolved_artifact_store
    app.state.approval_store = resolved_approval_store
    app.state.run_store = resolved_run_store
    app.state.run_event_store = resolved_run_event_store
    app.state.trigger_store = resolved_trigger_store
    app.state.trigger_run_store = resolved_trigger_run_store
    app.state.webhook_endpoint_store = resolved_webhook_endpoint_store
    app.state.webhook_delivery_store = resolved_webhook_delivery_store
    app.state.curation_candidate_store = resolved_curation_candidate_store
    app.state.eval_dataset_store = resolved_eval_dataset_store
    app.state.knowledge_store = resolved_knowledge_store
    app.state.image_upload_store = resolved_image_upload_store
    app.state.skill_store = resolved_skill_store
    # Stream T (PR B) — PR C's write endpoint resolves the config service off
    # app.state to upsert the row + invalidate the cache for immediate effect.
    app.state.platform_embedding_config_service = resolved_platform_embedding_config_service
    # Capability Uplift Sprint #4 — exposed on app.state for callers that
    # need it directly. Stream X (Mini-ADR X-4) wires it through
    # ``make_agent_builder`` / ``make_child_agent_builder`` into
    # ``_load_skills`` + ``SkillViewTool`` for bind / view activity tracking.
    app.state.skill_activity_recorder = skill_activity_recorder
    # Stream J.6 — the object store is created in the lifespan (it goes on
    # the AsyncExitStack); the upload endpoint reads it from app.state.
    app.state.object_store = None
    # The ingestion runner is built in the lifespan (it needs the resolved
    # embedder); tests inject one. ``None`` → document upload returns 503.
    app.state.knowledge_ingestion_runner = knowledge_ingestion_runner
    app.state.supervisor_client = resolved_supervisor_client
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
    app.state.tenant_status_service = resolved_tenant_status_service
    # Stream R — member onboarding roster + Keycloak Admin client.
    app.state.tenant_member_repo = resolved_tenant_member_repo
    app.state.keycloak_admin_client = resolved_keycloak_admin_client
    # Stream V — tenant-registered remote MCP server registry + per-tenant
    # remote pool (V-D). The pool service is built in the lifespan (needs the
    # AsyncExitStack for shutdown); ``None`` until the lifespan sets it.
    app.state.tenant_mcp_server_store = resolved_tenant_mcp_server_store
    app.state.mcp_connector_catalog_store = resolved_mcp_connector_catalog_store
    app.state.mcp_oauth_connection_store = resolved_mcp_oauth_connection_store
    app.state.model_rate_card_store = resolved_model_rate_card_store
    app.state.tenant_billing_ledger_store = resolved_tenant_billing_ledger_store
    app.state.tenant_mcp_pool_service = None
    app.state.platform_secret_store = resolved_platform_secret_store
    app.state.platform_secrets_service = resolved_platform_secrets_service
    # Stream S (Mini-ADR S-4) — model catalog reads only usable providers
    # (configured + enabled) via the same service; thin adapter wraps it.
    app.state.model_catalog_providers = PlatformConfiguredProviders(
        resolved_platform_secrets_service
    )
    # Stream Q (PR C) — the SecretStore is exposed so the platform-config write
    # path can encrypt a pasted raw key via ``secret_store.put`` before storing
    # only the ``secret://`` ref in the catalog.
    app.state.secret_store = resolved_secret_store
    app.state.agent_runtime = resolved_agent_runtime
    # Stream K.K6 — memory CRUD endpoints. ``memory_repo`` is the store
    # already resolved above (SQL when ``store_backend == "sql"``, else
    # InMemory). In normal operation the lifespan sets ``app.state.embedder``
    # to the always-present ``DynamicResolvingEmbedder``; the PATCH path catches
    # the embedding-unconfigured ``AgentFactoryError`` at call time and returns a
    # 503. GET / DELETE never touch the embedder. The ``None`` fallback below
    # only applies when a runtime is injected (tests) and the lifespan wiring
    # block above was skipped.
    app.state.memory_repo = resolved_memory_store
    if not hasattr(app.state, "embedder"):
        app.state.embedder = None

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
        # Stream N — AuthMiddleware queries this store to populate
        # ``Principal.is_system_admin`` from a platform-scope role binding.
        role_binding_store=resolved_role_bindings,
        # Stream U (PR E) — 403 a suspended tenant's members (never system_admin).
        tenant_status=resolved_tenant_status_service,
    )
    app.add_middleware(ObservabilityMiddleware)

    app.include_router(build_health_router(health_provider))
    app.include_router(build_metrics_router())
    app.include_router(build_agent_schema_router())
    app.include_router(build_model_catalog_router())
    app.include_router(build_agents_router())
    app.include_router(build_sessions_router())
    app.include_router(build_runs_router())
    app.include_router(build_plan_router())
    app.include_router(build_runs_list_router())
    # Stream HX-7 — the cross-run approval queue + batch decide.
    app.include_router(build_approvals_router())
    app.include_router(build_feedback_router())
    app.include_router(build_artifacts_router())
    app.include_router(build_knowledge_router())
    app.include_router(build_me_router())
    app.include_router(build_memory_router())
    app.include_router(build_skills_router())
    app.include_router(build_skill_evolution_router())
    app.include_router(build_uploads_router())
    app.include_router(build_service_accounts_router())
    app.include_router(build_mcp_servers_router())
    app.include_router(build_mcp_catalog_router())
    app.include_router(build_mcp_oauth_router())
    app.include_router(build_rate_card_router())
    app.include_router(build_usage_router())
    app.include_router(build_billing_admin_router())
    app.include_router(build_platform_skills_router())
    app.include_router(build_api_keys_router())
    app.include_router(build_role_bindings_router())
    app.include_router(build_quota_router())
    app.include_router(build_tenants_router())
    app.include_router(build_members_router())
    app.include_router(build_sandboxes_router())
    app.include_router(build_platform_config_router())
    app.include_router(build_platform_embedding_config_router())
    app.include_router(build_tenant_quotas_router())
    app.include_router(build_tenant_config_router())
    app.include_router(build_triggers_router())
    app.include_router(build_webhooks_router())
    app.include_router(build_webhook_endpoints_router())
    app.include_router(build_audit_router())
    app.include_router(build_curation_router())
    app.include_router(build_eval_dataset_router())

    return app


@dataclass(frozen=True)
class _SqlStores:
    """Postgres-backed store bundle built when ``store_backend == "sql"``.

    Every store shares the one ``engine`` + RLS-wrapped sessionmaker;
    ``engine`` is disposed in the app's ``lifespan`` (ADR B-6).
    """

    engine: AsyncEngine
    #: Stream Q — exposed so the app can build a SqlEncryptedSecretStore over
    #: the same RLS-wrapped sessionmaker when ``secret_store_backend`` is set.
    session_factory: async_sessionmaker[AsyncSession]
    agent_spec: AgentSpecStore
    thread_meta: ThreadMetaStore
    tenant_user: TenantUserStore
    memory: MemoryStore
    memory_dlq: MemoryWritebackDLQ  # Stream K.K7
    knowledge: KnowledgeStore
    skill: SkillStore
    image_upload: ImageUploadStore  # Stream J.6.补强-3 (Mini-ADR J-32)
    artifact: ArtifactStore
    approval: ApprovalStore  # Stream J.8 (Mini-ADR J-24)
    run: RunStore  # Stream J.8 closeout follow-up (Mini-ADR J-41)
    run_event: RunEventStore  # Stream H.3 PR 3 (Mini-ADR H-7)
    trigger: TriggerStore  # Stream J.10 (Mini-ADR J-26 / J-42)
    trigger_run: TriggerRunStore  # Stream J.10
    webhook_endpoint: WebhookEndpointStore  # HX-9 (STREAM-HX § 13)
    webhook_delivery: WebhookDeliveryStore  # HX-9
    curation_candidate: CurationCandidateStore  # Stream J.12 (Mini-ADR J-43)
    eval_dataset: EvalDatasetStore  # Stream J.12 (Mini-ADR J-43)
    service_account: ServiceAccountStore
    api_key: ApiKeyStore
    role_binding: RoleBindingStore
    platform_secret: PlatformSecretStore
    platform_embedding_config: PlatformEmbeddingConfigStore  # Stream T (PR B)
    tenant_quota: TenantQuotaStore
    token_reservation: TokenReservationStore
    tenant_config: TenantConfigStore
    tenant_member: TenantMemberStore  # Stream R
    tenant_mcp_server: TenantMcpServerStore  # Stream V
    mcp_connector_catalog: McpConnectorCatalogStore  # Stream W
    mcp_oauth_connection: McpOAuthConnectionStore  # Stream MCP-OAUTH
    model_rate_card: ModelRateCardStore  # Stream Y (Y-3)
    tenant_billing_ledger: TenantBillingLedgerStore  # Stream Y (Y-4)
    feedback: FeedbackStore
    token_usage: TokenUsageStore
    audit_log: AuditLogStore


def _validate_platform_catalog(settings: Settings) -> None:
    """Stream O (Mini-ADR O-1) — Platform Catalog startup validation.

    Three checks, fail-fast on any:
    1. Every entry in ``supported_providers`` has a matching
       ``platform_provider_credentials`` entry.
    2. Every entry in ``supported_tools`` has a matching
       ``platform_tool_credentials`` entry.
    3. No extraneous credential entries (would point at a typo in env).

    Pydantic already validates that the Literal values are in the
    catalog; this checks the cross-field consistency.
    """
    supported_provs = set(settings.supported_providers)
    cred_provs = set(settings.platform_provider_credentials)
    missing_provs = supported_provs - cred_provs
    extra_provs = cred_provs - supported_provs
    supported_tools = set(settings.supported_tools)
    cred_tools = set(settings.platform_tool_credentials)
    missing_tools = supported_tools - cred_tools
    extra_tools = cred_tools - supported_tools
    if missing_provs or extra_provs or missing_tools or extra_tools:
        details = []
        if missing_provs:
            details.append(
                f"providers in supported_providers without credentials: {sorted(missing_provs)}"
            )
        if extra_provs:
            details.append(
                f"platform_provider_credentials with providers not in "
                f"supported_providers: {sorted(extra_provs)}"
            )
        if missing_tools:
            details.append(f"tools in supported_tools without credentials: {sorted(missing_tools)}")
        if extra_tools:
            details.append(
                f"platform_tool_credentials with tools not in supported_tools: "
                f"{sorted(extra_tools)}"
            )
        msg = "Platform Catalog mismatch — " + "; ".join(details)
        raise RuntimeError(msg)


def _signal_legacy_credentials_derivation(settings: Settings) -> None:
    """Stream O (Mini-ADR O-10) — emit a deprecation signal when the
    effective credentials catalog is gap-filled from a legacy
    ``*_api_key_ref`` field. A deployment that has fully migrated to
    ``platform_provider_credentials`` / ``platform_tool_credentials``
    triggers nothing here; the P3 ``LegacyCredentialsFallbackPresent``
    alert (Mini-ADR O-8) flags any that remain."""
    explicit_provs = set(settings.platform_provider_credentials)
    if settings.embedding_api_key_ref and settings.embedding_provider not in explicit_provs:
        logger.warning(
            "credentials.legacy_derivation role=embedding provider=%s — migrate to "
            "platform_provider_credentials",
            settings.embedding_provider,
        )
        record_legacy_credentials_fallback(role="embedding")
    if (
        settings.rerank_api_key_ref
        and settings.rerank_provider not in explicit_provs
        and settings.rerank_provider in PROVIDER_CATALOG
    ):
        logger.warning(
            "credentials.legacy_derivation role=rerank provider=%s — migrate to "
            "platform_provider_credentials",
            settings.rerank_provider,
        )
        record_legacy_credentials_fallback(role="rerank")
    if settings.tavily_api_key_ref and "web_search" not in settings.platform_tool_credentials:
        logger.warning(
            "credentials.legacy_derivation role=tavily — migrate to "
            "platform_tool_credentials[web_search]"
        )
        record_legacy_credentials_fallback(role="tavily")


async def _seed_mcp_catalog(settings: Settings, store: McpConnectorCatalogStore) -> None:
    """Env-seed the connector catalog from ``settings.mcp_catalog_seed_file``
    (Stream MCP-OAUTH OA-5). No-op when unset. A malformed template fails fast."""
    if not settings.mcp_catalog_seed_file:
        return
    path = Path(settings.mcp_catalog_seed_file)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"mcp_catalog_seed_file not readable: {path}") from exc
    try:
        entries, skipped = load_catalog_seed(raw, os.environ)
    except CatalogSeedError as exc:
        raise RuntimeError(f"invalid mcp_catalog_seed_file {path}: {exc}") from exc
    created, existing = await seed_catalog(store=store, entries=entries)
    logger.info(
        "control_plane.mcp_catalog.seeded created=%s existing=%s skipped_unset=%s",
        created,
        existing,
        skipped,
    )


def _build_secret_store(settings: Settings, sql_stores: _SqlStores | None) -> SecretStore:
    """Build the SecretStore for ``settings.secret_store_backend`` (Stream Q).

    ``sql_encrypted`` needs a Postgres session + a 32-byte KEK; it fails loud
    at boot if either is missing rather than silently degrading. Other backends
    delegate to :func:`make_secret_store`.
    """
    backend = settings.secret_store_backend
    if backend == "sql_encrypted":
        if sql_stores is None:
            msg = (
                "secret_store_backend='sql_encrypted' requires store_backend='sql' "
                "(it needs a Postgres session) — got in-memory mode"
            )
            raise RuntimeError(msg)
        if settings.secret_encryption_key is None:
            msg = (
                "secret_store_backend='sql_encrypted' requires "
                "HELIX_AGENT_SECRET_ENCRYPTION_KEY (base64 32-byte KEK)"
            )
            raise RuntimeError(msg)
        kek = build_kek_from_b64(settings.secret_encryption_key.get_secret_value())
        return SqlEncryptedSecretStore(sql_stores.session_factory, kek=kek)
    return make_secret_store(backend, env_file=settings.secret_store_env_file)


def _build_keycloak_admin_client(
    settings: Settings, secret_store: SecretStore
) -> KeycloakAdminClient:
    """Build the Keycloak Admin client for member provisioning (Stream R).

    Returns a :class:`FakeKeycloakAdminClient` unless ``keycloak_enabled`` so
    dev/CI run the full onboarding flow without a live Keycloak. When enabled,
    the service-account client secret is loaded lazily from the vault on each
    token refresh (never held in settings).
    """
    if not settings.keycloak_enabled:
        return FakeKeycloakAdminClient()

    http = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=30.0))

    async def _load_secret() -> str:
        return await secret_store.get(settings.keycloak_admin_secret_name)

    token_provider = ServiceAccountTokenProvider(
        base_url=settings.keycloak_base_url,
        realm=settings.keycloak_realm,
        client_id=settings.keycloak_admin_client_id,
        secret_loader=_load_secret,
        http=http,
    )
    return HttpKeycloakAdminClient(
        base_url=settings.keycloak_base_url,
        realm=settings.keycloak_realm,
        token_provider=token_provider,
        http=http,
    )


def _build_sql_stores(settings: Settings) -> _SqlStores:
    """Build the Postgres-backed store bundle from ``settings.db_*`` (ADR B-6).

    One engine, one ``build_rls_sessionmaker``-wrapped sessionmaker,
    shared by every store. ``create_async_engine`` is lazy — no
    connection opens here; the engine is disposed in ``lifespan``.
    """
    engine = create_async_engine_from_config(
        DatabaseConfig(
            dsn=settings.db_dsn,
            pgbouncer_mode=settings.db_pgbouncer_mode,
            echo_sql=settings.db_echo,
        )
    )
    session_factory = build_rls_sessionmaker(create_async_session_factory(engine))
    return _SqlStores(
        engine=engine,
        session_factory=session_factory,
        agent_spec=SqlAgentSpecStore(session_factory),
        thread_meta=SqlThreadMetaStore(session_factory),
        tenant_user=SqlTenantUserStore(session_factory),
        memory=SqlMemoryStore(session_factory),
        memory_dlq=SqlMemoryWritebackDLQ(session_factory),
        knowledge=SqlKnowledgeStore(session_factory),
        skill=SqlSkillStore(session_factory),
        image_upload=SqlImageUploadStore(session_factory),
        artifact=SqlArtifactStore(session_factory),
        approval=SqlApprovalStore(session_factory),
        run=SqlRunStore(session_factory),
        run_event=SqlRunEventStore(session_factory),
        trigger=SqlTriggerStore(session_factory),
        trigger_run=SqlTriggerRunStore(session_factory),
        webhook_endpoint=SqlWebhookEndpointStore(session_factory),
        webhook_delivery=SqlWebhookDeliveryStore(session_factory),
        curation_candidate=SqlCurationCandidateStore(session_factory),
        eval_dataset=SqlEvalDatasetStore(session_factory),
        service_account=SqlServiceAccountStore(session_factory),
        api_key=SqlApiKeyStore(session_factory),
        role_binding=SqlRoleBindingStore(session_factory),
        tenant_quota=SqlTenantQuotaStore(session_factory),
        token_reservation=SqlTokenReservationStore(session_factory),
        platform_secret=SqlPlatformSecretStore(session_factory),
        platform_embedding_config=SqlPlatformEmbeddingConfigStore(session_factory),
        tenant_config=SqlTenantConfigStore(session_factory),
        tenant_member=SqlTenantMemberStore(session_factory),
        tenant_mcp_server=SqlTenantMcpServerStore(session_factory),
        mcp_connector_catalog=SqlMcpConnectorCatalogStore(session_factory),
        mcp_oauth_connection=SqlMcpOAuthConnectionStore(session_factory),
        model_rate_card=DbModelRateCardStore(session_factory),
        tenant_billing_ledger=DbTenantBillingLedgerStore(session_factory),
        feedback=DbFeedbackStore(session_factory),
        token_usage=DbTokenUsageStore(session_factory),
        audit_log=SqlAuditLogStore(session_factory),
    )


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


def _build_mcp_probe_limiter(settings: Settings) -> RateLimiter:
    """Tight per-tenant limiter for MCP probe-bearing endpoints (audit #6).

    Mirrors the tenant-tier impl selection (Redis multi-replica / in-process
    single-replica) but with a much smaller bucket — these endpoints each open
    a server-side outbound connection to a tenant-chosen URL.
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
            capacity=settings.mcp_probe_rate_limit_capacity,
            refill_per_sec=settings.mcp_probe_rate_limit_refill_per_sec,
        )
    return InProcessTokenBucketLimiter(
        capacity=settings.mcp_probe_rate_limit_capacity,
        refill_per_sec=settings.mcp_probe_rate_limit_refill_per_sec,
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
