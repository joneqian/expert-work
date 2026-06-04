"""In-process agent execution runtime — control-plane ↔ orchestrator glue.

The control-plane runs the orchestrator as a library (in-process
monolith, STREAM-E-DESIGN § 2.6): an agent graph executes as a
background ``asyncio.Task`` in this process, streaming events to the
SSE client through a :class:`StreamBridge`.

:class:`AgentRuntime` bundles the three long-lived pieces a run needs —
the run-lifecycle registry, the SSE event bridge, and the
manifest→agent build path — behind one object held on ``app.state``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from control_plane.platform_embedding_config import PlatformEmbeddingConfigService
from control_plane.tenancy import TenantConfigNotConfiguredError, TenantConfigService
from control_plane.tenant_mcp_pool import TenantMcpPoolProvider
from control_plane.tenant_scope import bypass_rls_session
from helix_agent.common.credentials import CredentialsResolver, CredentialsResolverError
from helix_agent.common.skill_activity import SkillActivityRecorder
from helix_agent.persistence import ArtifactStore, KnowledgeStore
from helix_agent.persistence.skill import SkillStore
from helix_agent.persistence.token_usage_store import TokenUsageStore
from helix_agent.protocol import AgentSpec, ModelSpec, Provider, TenantPlan, Tool, tier_satisfies
from helix_agent.runtime.audit import DefaultSecretRedactor
from helix_agent.runtime.llm import InMemoryRedisCache, LLMResponseCache
from helix_agent.runtime.middleware import RecordingLangfuseClient
from helix_agent.runtime.runs import RunEventStore, RunManager, RunStore
from helix_agent.runtime.secret_store import SecretStore, parse_secret_ref
from helix_agent.runtime.storage import ObjectStore, ObjectStoreBackend, S3CompatibleConfig
from helix_agent.runtime.stream_bridge import InMemoryStreamBridge, StreamBridge
from orchestrator import (
    BuiltAgent,
    MemoryEnv,
    MiddlewareEnv,
    ToolEnv,
    build_agent,
    build_llm_router,
)
from orchestrator.agent_factory import (
    ProviderKeyResolver,
    SkillResolver,
    SubagentSpecResolver,
    _SkillLookupResult,
    detect_subagent_cycle,
)
from orchestrator.errors import AgentFactoryError
from orchestrator.llm import Embedder, HTTPEmbeddingClient, OpenAICompatibleEmbedder
from orchestrator.multimodal import ImageResolver, ObjectStoreImageResolver
from orchestrator.tools import (
    AllowlistProvider,
    HTTPSupervisorClient,
    HTTPTavilyClient,
    KnowledgeRetriever,
    LLMReranker,
    MCPClient,
    MCPServerConfig,
    MCPServerPool,
    Reranker,
    SseMCPClient,
    StdioMCPClient,
    StreamableHttpMCPClient,
    SupervisorClient,
    TavilyClient,
)


#: Builds a runnable agent from a manifest. The production builder
#: closes over a SecretStore + checkpointer and calls
#: :func:`orchestrator.build_agent`; integration tests substitute a
#: stub returning a :class:`BuiltAgent` over a fake-LLM graph — the
#: real builder wires HTTP provider clients, which a test must not hit.
class AgentBuilder(Protocol):
    """Builds a runnable agent from a manifest.

    Stream O (Mini-ADR O-14) — ``tenant_id`` lets the builder construct a
    per-tenant ``ToolEnv`` (today: the MCP server allowlist; ``None`` keeps
    the platform default). Optional + defaulted so test stubs that ignore
    tenancy still conform."""

    async def __call__(self, spec: AgentSpec, *, tenant_id: UUID | None = None) -> BuiltAgent:
        """Build the agent for ``spec``; ``tenant_id`` selects the per-tenant ToolEnv."""


logger = logging.getLogger(__name__)


@dataclass
class AgentRuntime:
    """The control-plane's in-process agent execution surface.

    Owns the run-lifecycle :class:`RunManager`, the SSE
    :class:`StreamBridge`, and the manifest→agent build path. Built
    agents are cached per ``(tenant_id, name, version)`` — a manifest
    compiles to a graph once, not once per run.
    """

    run_manager: RunManager
    stream_bridge: StreamBridge
    agent_builder: AgentBuilder
    #: Stream H.3 PR 3 (Mini-ADR H-7) — durable mirror of every SSE
    #: frame so RunDetail's Event stream panel can replay terminal runs
    #: past the bridge's 60-second cleanup window. ``None`` keeps SSE
    #: purely in-memory; production wiring passes an
    #: :class:`InMemoryRunEventStore` (dev) or :class:`SqlRunEventStore`.
    run_event_store: RunEventStore | None = None
    _cache: dict[tuple[UUID, str, str], BuiltAgent] = field(default_factory=dict, repr=False)

    async def get_agent(
        self,
        *,
        tenant_id: UUID,
        name: str,
        version: str,
        spec: AgentSpec,
    ) -> BuiltAgent:
        """Return the :class:`BuiltAgent` for a manifest, building on cache miss.

        ``spec`` is only consulted on a miss — the cache key is the
        manifest identity, so a redeployed manifest under a *new*
        version naturally gets a fresh build.
        """
        key = (tenant_id, name, version)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        built = await self.agent_builder(spec, tenant_id=tenant_id)
        self._cache[key] = built
        return built

    def invalidate_tenant(self, tenant_id: UUID) -> None:
        """Drop every cached built-agent for ``tenant_id``.

        Called when the tenant's MCP server registry changes so the next run
        rebuilds the agent against the refreshed tenant MCP pool (Stream V-D).
        The cache key is ``(tenant_id, name, version)``.
        """
        for key in [k for k in self._cache if k[0] == tenant_id]:
            del self._cache[key]


def make_provider_key_resolver(
    *, resolver: CredentialsResolver, tenant_id: UUID
) -> ProviderKeyResolver:
    """Bind a :class:`CredentialsResolver` + tenant to a provider→secret_ref
    getter for the agent build (Stream Q, Mini-ADR Q-5).

    Translates :class:`CredentialsResolverError` into
    :class:`AgentFactoryError` here (control-plane) so the orchestrator's
    ``build_llm_router`` stays free of any ``helix-common.credentials`` import.
    """

    async def _resolve(provider: str) -> str:
        try:
            return await resolver.resolve_provider(
                tenant_id=tenant_id, provider=cast(Provider, provider)
            )
        except CredentialsResolverError as exc:
            msg = f"no platform credential is configured for provider {provider!r}"
            raise AgentFactoryError(msg) from exc

    return _resolve


def make_skill_resolver(
    *, store: SkillStore, tenant_config_service: TenantConfigService
) -> SkillResolver:
    """Bind a :class:`SkillStore` to the agent build's skill resolver
    (Stream X, Mini-ADR X-4).

    Resolution follows tenant-first / platform-fallback semantics:

    * **Tenant-first (name-shadowing, R2)** — if the tenant owns a skill
      with this ``name`` it wins, *even if* the tenant's copy is draft /
      archived (a tenant name shadows the platform library; we never fall
      back to a platform skill of the same name).
    * **Platform-fallback (R3 tier gate)** — when the tenant has no skill
      of that name, read the platform (NULL-tenant) library under
      :func:`bypass_rls_session` (platform rows are invisible to the
      tenant-scoped session otherwise — the X-1/W-8 property). A platform
      skill the tenant's plan tier doesn't satisfy returns
      ``not_entitled`` so the loader names the required plan.

    The plan lookup runs outside ``bypass_rls_session`` — ``tenant_config``
    is its own RLS table and must read under the tenant's normal scope.
    """

    async def _resolve(tenant_id: Any, name: str, version: int | None) -> _SkillLookupResult:
        # Tenant-first (R2): a tenant skill of this name shadows the
        # platform library, draft / archived included → no fallback.
        tskill = await store.get_skill_by_name(tenant_id=tenant_id, name=name)
        if tskill is not None:
            if version is not None:
                pinned = await store.resolve_pinned(tenant_id=tenant_id, name=name, version=version)
                if pinned is not None:
                    return _SkillLookupResult.ok(pinned, skill=tskill)
                return _SkillLookupResult.version_not_found()
            active = await store.resolve_by_name(tenant_id=tenant_id, name=name)
            if active is not None:
                return _SkillLookupResult.ok(active, skill=tskill)
            return _SkillLookupResult.not_active(skill=tskill)

        # Platform-fallback. Resolve the tenant's plan first, under its own
        # RLS scope (tenant_config is a tenant-scoped table).
        try:
            plan = (await tenant_config_service.get(tenant_id=tenant_id)).plan
        except TenantConfigNotConfiguredError:
            plan = TenantPlan.FREE

        async with bypass_rls_session():
            pskill = await store.get_platform_skill_by_name(name=name)
            if pskill is None:
                return _SkillLookupResult.not_found()
            # R3 tier gate — an un-entitled platform skill is treated as a
            # distinct build-time error so the loader can name the plan.
            if not tier_satisfies(plan, pskill.required_tier):
                return _SkillLookupResult.not_entitled(required_tier=pskill.required_tier.value)
            if version is not None:
                pinned = await store.resolve_platform_pinned(name=name, version=version)
                if pinned is not None:
                    return _SkillLookupResult.ok(pinned, skill=pskill)
                return _SkillLookupResult.version_not_found()
            active = await store.resolve_platform_by_name(name=name)
            if active is not None:
                return _SkillLookupResult.ok(active, skill=pskill)
            return _SkillLookupResult.not_active(skill=pskill)

    return _resolve


def _declares_long_term(spec: AgentSpec) -> bool:
    """True when the manifest declares ``memory.long_term`` (Stream T)."""
    memory = spec.spec.memory
    return memory is not None and memory.long_term is not None


def make_agent_builder(
    secret_store: SecretStore,
    checkpointer: BaseCheckpointSaver[Any],
    *,
    tool_env: ToolEnv | None = None,
    middleware_env: MiddlewareEnv | None = None,
    memory_env: MemoryEnv | None = None,
    subagent_spec_resolver: SubagentSpecResolver | None = None,
    mcp_allowlist_provider: Callable[[UUID], Awaitable[Sequence[str]]] | None = None,
    tenant_mcp_pool_provider: TenantMcpPoolProvider | None = None,
    credentials_resolver: CredentialsResolver | None = None,
    platform_embedding_config_service: PlatformEmbeddingConfigService | None = None,
    skill_store: SkillStore | None = None,
    skill_activity_recorder: SkillActivityRecorder | None = None,
    tenant_config_service: TenantConfigService | None = None,
) -> AgentBuilder:
    """Production :data:`AgentBuilder` bound to a SecretStore + checkpointer.

    ``tool_env`` / ``middleware_env`` / ``memory_env`` inject the
    platform tool, middleware, and long-term-memory backends. Kept
    separate from :func:`make_agent_runtime` so the app lifespan can
    rebuild the builder once the durable checkpointer's connection
    context is open and the tenant-config service is ready.

    ``subagent_spec_resolver`` (Mini-ADR J-40) lets the top-level
    builder check the manifest's delegation graph for cycles before
    assembling any tools. When ``None`` (the default), the check is
    skipped — for unit tests + agents that declare no ``subagents``
    block. The app lifespan binds it to an ``AgentSpecStore`` synchronous
    resolver so a cycle in production is rejected at build time
    (``AgentFactoryError``) rather than blowing the depth cap at run
    time.

    ``platform_embedding_config_service`` (Stream T, PR B) hosts the
    build-time embedding gate. The dynamic embedder is never ``None`` (it
    resolves the live config per call), so the orchestrator's
    ``embedder is None`` gate can no longer fire; this builder checks the
    effective config instead — a manifest declaring ``memory.long_term``
    with platform embedding unconfigured is rejected here at build time.
    ``None`` skips the check (unit tests / the placeholder builder
    ``make_agent_runtime`` installs before the lifespan swap); the
    orchestrator gate stays as defense.
    """

    async def _build(spec: AgentSpec, *, tenant_id: UUID | None = None) -> BuiltAgent:
        # Stream T (PR B) — build-time embedding gate. A manifest that
        # declares long-term memory needs a configured platform embedder;
        # the dynamic embedder object is always present, so we check the
        # effective config rather than ``embedder is None``.
        if platform_embedding_config_service is not None and _declares_long_term(spec):
            if await platform_embedding_config_service.effective_embedding_config() is None:
                raise AgentFactoryError(
                    "manifest declares memory.long_term but platform embedding is not "
                    "configured — configure it in platform settings"
                )
        if subagent_spec_resolver is not None and spec.spec.subagents:
            detect_subagent_cycle(spec, resolve=subagent_spec_resolver)
        # Stream O (Mini-ADR O-14) — apply the tenant's MCP server allowlist
        # to a per-build ToolEnv. Empty / unconfigured → no restriction.
        build_tool_env = tool_env
        if (
            mcp_allowlist_provider is not None
            and tenant_id is not None
            and tool_env is not None
            and tool_env.mcp_pool is not None
        ):
            allowlist = await mcp_allowlist_provider(tenant_id)
            if allowlist:
                build_tool_env = replace(tool_env, mcp_allowlist=tuple(allowlist))
        # Stream V (Mini-ADR V-4) — attach the tenant's own remote MCP pool.
        if tenant_mcp_pool_provider is not None and tenant_id is not None:
            tenant_pool = await tenant_mcp_pool_provider(tenant_id)
            if tenant_pool.names():
                base_env = build_tool_env if build_tool_env is not None else ToolEnv()
                build_tool_env = replace(base_env, tenant_mcp_pool=tenant_pool)
        # Stream Q (Mini-ADR Q-5) / Stream Y-2 — resolve each model's key from
        # the tenant's platform-configured credential. Y-2: manifest api_key_ref
        # is ignored for agent builds, so this resolver is the ONLY key source.
        # Needs a tenant; preview/validation builds (tenant_id None) get no
        # resolver and therefore cannot build a runnable agent (by design).
        provider_key_resolver = (
            make_provider_key_resolver(resolver=credentials_resolver, tenant_id=tenant_id)
            if credentials_resolver is not None and tenant_id is not None
            else None
        )
        # Stream X (Mini-ADR X-4) — first wiring of skill resolution into the
        # agent build. Needs a tenant; preview / validation builds (tenant_id
        # None) keep today's behaviour (a skills manifest errors at build).
        skill_resolver = (
            make_skill_resolver(store=skill_store, tenant_config_service=tenant_config_service)
            if skill_store is not None
            and tenant_config_service is not None
            and tenant_id is not None
            else None
        )
        return await build_agent(
            spec,
            secret_store=secret_store,
            checkpointer=checkpointer,
            tool_env=build_tool_env,
            middleware_env=middleware_env,
            memory_env=memory_env,
            tenant_id=tenant_id,
            provider_key_resolver=provider_key_resolver,
            skill_resolver=skill_resolver,
            skill_activity_recorder=skill_activity_recorder,
        )

    return _build


# ---------------------------------------------------------------------------
# Stream O Mini-ADR O-9 — per-tenant credential-resolving callers
#
# embedder / reranker / web_search were platform singletons with the API key
# baked in at boot. These wrappers resolve the per-tenant secret_ref via
# :class:`CredentialsResolver` at call time (platform vs tenant mode), then
# build the concrete client. Resolution runs once per ``embed`` batch / once
# per rerank / once per search — frequency is low, so no caching is needed.
# The wrappers live here (control-plane glue) so the orchestrator package
# never imports helix-common.credentials; they implement the orchestrator
# protocols structurally.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvingEmbedder:
    """Per-tenant credential-resolving :class:`Embedder` (Mini-ADR O-9)."""

    resolver: CredentialsResolver
    secret_store: SecretStore
    provider: Provider
    model: str

    async def embed(self, texts: Sequence[str], *, tenant_id: UUID) -> list[tuple[float, ...]]:
        if not texts:
            return []
        secret_ref = await self.resolver.resolve_provider(
            tenant_id=tenant_id, provider=self.provider
        )
        api_key = await self.secret_store.get(parse_secret_ref(secret_ref))
        delegate = OpenAICompatibleEmbedder(
            client=HTTPEmbeddingClient(api_key=api_key), model=self.model
        )
        return await delegate.embed(texts, tenant_id=tenant_id)


@dataclass(frozen=True)
class ResolvingReranker:
    """Per-tenant credential-resolving :class:`Reranker` (Mini-ADR O-9).

    Rerank is an optional quality pass — if the platform has no credential
    for the rerank provider, degrade to the RRF-fused order rather than
    failing (Mini-ADR O-12)."""

    resolver: CredentialsResolver
    secret_store: SecretStore
    provider: Provider
    model: str

    async def rerank(
        self, *, query: str, documents: Sequence[str], top_k: int, tenant_id: UUID
    ) -> list[int]:
        if not documents:
            return []
        try:
            secret_ref = await self.resolver.resolve_provider(
                tenant_id=tenant_id, provider=self.provider
            )
        except CredentialsResolverError:
            logger.info(
                "knowledge.rerank_skipped — no credential for provider=%s tenant=%s",
                self.provider,
                tenant_id,
            )
            return list(range(len(documents)))[:top_k]
        model_spec = ModelSpec.model_validate(
            {"provider": self.provider, "name": self.model, "api_key_ref": secret_ref}
        )
        router = await build_llm_router(model_spec, secret_store=self.secret_store)
        return await LLMReranker(llm_caller=router).rerank(
            query=query, documents=documents, top_k=top_k, tenant_id=tenant_id
        )


@dataclass(frozen=True)
class DynamicResolvingEmbedder:
    """Embedder reading the live platform embedding config per call so an
    admin's change takes effect without restart (Stream T, Mini-ADR T-3)."""

    config_service: PlatformEmbeddingConfigService
    resolver: CredentialsResolver
    secret_store: SecretStore

    async def embed(self, texts: Sequence[str], *, tenant_id: UUID) -> list[tuple[float, ...]]:
        if not texts:
            return []
        cfg = await self.config_service.effective_embedding_config()
        if cfg is None:
            raise AgentFactoryError(
                "platform embedding is not configured — configure it in platform settings"
            )
        provider, model = cfg
        secret_ref = await self.resolver.resolve_provider(tenant_id=tenant_id, provider=provider)
        api_key = await self.secret_store.get(parse_secret_ref(secret_ref))
        delegate = OpenAICompatibleEmbedder(
            client=HTTPEmbeddingClient(api_key=api_key), model=model
        )
        return await delegate.embed(texts, tenant_id=tenant_id)


@dataclass(frozen=True)
class DynamicResolvingReranker:
    """Reranker reading the live platform rerank config per call; degrades to
    identity order when rerank is unconfigured (Stream T, Mini-ADR T-3)."""

    config_service: PlatformEmbeddingConfigService
    resolver: CredentialsResolver
    secret_store: SecretStore

    async def rerank(
        self, *, query: str, documents: Sequence[str], top_k: int, tenant_id: UUID
    ) -> list[int]:
        if not documents:
            return []
        cfg = await self.config_service.effective_rerank_config()
        if cfg is None:
            return list(range(len(documents)))[:top_k]
        provider, model = cfg
        try:
            secret_ref = await self.resolver.resolve_provider(
                tenant_id=tenant_id, provider=provider
            )
        except CredentialsResolverError:
            logger.info(
                "knowledge.rerank_skipped — no credential for provider=%s tenant=%s",
                provider,
                tenant_id,
            )
            return list(range(len(documents)))[:top_k]
        model_spec = ModelSpec.model_validate(
            {"provider": provider, "name": model, "api_key_ref": secret_ref}
        )
        router = await build_llm_router(model_spec, secret_store=self.secret_store)
        return await LLMReranker(llm_caller=router).rerank(
            query=query, documents=documents, top_k=top_k, tenant_id=tenant_id
        )


@dataclass(frozen=True)
class ResolvingTavilyClient:
    """Per-tenant credential-resolving :class:`TavilyClient` (Mini-ADR O-9).

    A missing credential raises :class:`CredentialsResolverError` (a
    fail-fast, mirrored to a ``ToolMessage(status="error")`` by the ReAct
    tools node, Mini-ADR E-12) — web_search has no graceful degradation."""

    resolver: CredentialsResolver
    secret_store: SecretStore

    async def search(
        self, *, query: str, max_results: int, tenant_id: UUID | None
    ) -> Mapping[str, Any]:
        if tenant_id is None:
            msg = "web_search requires a tenant context to resolve its credential"
            raise CredentialsResolverError(msg, mode="platform", kind="tool", key="web_search")
        secret_ref = await self.resolver.resolve_tool(tenant_id=tenant_id, tool="web_search")
        api_key = await self.secret_store.get(parse_secret_ref(secret_ref))
        return await HTTPTavilyClient(api_key=api_key).search(
            query=query, max_results=max_results, tenant_id=tenant_id
        )


async def resolve_embedder(
    *,
    resolver: CredentialsResolver,
    secret_store: SecretStore,
    provider: Provider,
    model: str,
    supported_providers: Sequence[Provider],
) -> Embedder | None:
    """Build the per-tenant credential-resolving embedder for long-term
    memory (Stream J.3 + Mini-ADR O-9).

    ``provider`` not in ``supported_providers`` → ``None``: the deployment
    has no embedding credential at all (legacy or Stream O), so long-term
    memory is globally unavailable and an agent declaring
    ``memory.long_term`` fails at build time (the build-time gate is
    preserved). Per-tenant failures (tenant mode, missing key) surface at
    ``embed`` time instead (Mini-ADR O-11)."""
    if provider not in supported_providers:
        return None
    return ResolvingEmbedder(
        resolver=resolver, secret_store=secret_store, provider=provider, model=model
    )


async def resolve_reranker(
    *,
    resolver: CredentialsResolver,
    secret_store: SecretStore,
    provider: Provider,
    model: str,
    supported_providers: Sequence[Provider],
) -> Reranker | None:
    """Build the per-tenant credential-resolving reranker (Stream J.5 +
    Mini-ADR O-9). ``provider`` not in ``supported_providers`` → ``None``
    (no rerank pass; hybrid search returns the RRF-fused order)."""
    if provider not in supported_providers:
        return None
    return ResolvingReranker(
        resolver=resolver, secret_store=secret_store, provider=provider, model=model
    )


def _tenant_allowlist_provider(service: TenantConfigService) -> AllowlistProvider:
    """An :data:`AllowlistProvider` reading ``http_tool_allowlist`` from
    the per-tenant config. A header-less / un-configured tenant yields
    an empty allowlist — the HTTP tool then blocks every URL."""

    async def _provider(tenant_id: UUID | None) -> Sequence[str]:
        if tenant_id is None:
            return []
        try:
            record = await service.get(tenant_id=tenant_id)
        except TenantConfigNotConfiguredError:
            return []
        return record.http_tool_allowlist

    return _provider


def make_mcp_allowlist_provider(
    service: TenantConfigService,
) -> Callable[[UUID], Awaitable[Sequence[str]]]:
    """Stream O (Mini-ADR O-14) — reads ``tenant_config.mcp_allowlist`` for the
    agent builder. Empty / unconfigured → no restriction (agent sees every
    platform MCP server); non-empty restricts to the listed server names."""

    async def _provider(tenant_id: UUID) -> Sequence[str]:
        try:
            record = await service.get(tenant_id=tenant_id)
        except TenantConfigNotConfiguredError:
            return []
        return record.mcp_allowlist

    return _provider


async def resolve_web_search_client(
    *,
    resolver: CredentialsResolver,
    secret_store: SecretStore,
    supported_tools: Sequence[Tool],
) -> TavilyClient | None:
    """Build the per-tenant credential-resolving web-search client (Stream
    E.7 + Mini-ADR O-9). ``web_search`` not in ``supported_tools`` →
    ``None``: the deployment has no Tavily credential at all, so an agent
    declaring ``web_search`` fails at build time (gate preserved)."""
    if "web_search" not in supported_tools:
        return None
    return ResolvingTavilyClient(resolver=resolver, secret_store=secret_store)


#: Builds an :class:`MCPClient` from a server config. The default
#: dispatches on ``config.transport``; tests inject a recording client.
McpClientFactory = Callable[[MCPServerConfig], Awaitable[MCPClient]]


def _load_mcp_server_configs(path: str) -> list[MCPServerConfig]:
    """Parse the platform MCP-server JSON file (Mini-ADR E-17 + U-10).

    Backward-compatible shapes:

    - stdio (legacy default): ``{"name", "command": [...], "env": {...}}``
    - stdio (explicit): ``{"name", "transport": "stdio", "command": [...]}``
    - sse / streamable_http (new in Capability Uplift Sprint #5):
      ``{"name", "transport": "sse" | "streamable_http", "url": str,
         "headers": {...}, "auth_type": "none" | "bearer" | "oauth2",
         "auth_config": {...}, "timeout_s": float, "retry_max": int}``

    Unknown keys raise — fail-fast on operator typos.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        msg = f"mcp_servers_config_file must contain a JSON array: {path}"
        raise ValueError(msg)
    return [_build_mcp_server_config(entry) for entry in raw]


def _build_mcp_server_config(entry: dict[str, Any]) -> MCPServerConfig:
    """Promote one JSON entry to a typed :class:`MCPServerConfig`.

    Validation (transport/url/auth pairing) happens in the dataclass
    ``__post_init__``; this helper only does the dict→kwarg lift +
    backward-compatible transport defaulting.
    """
    transport = entry.get("transport", "stdio")
    kwargs: dict[str, Any] = {"name": entry["name"], "transport": transport}
    if transport == "stdio":
        kwargs["command"] = entry["command"]
        if "env" in entry:
            kwargs["env"] = entry["env"]
    else:
        kwargs["url"] = entry.get("url")
        if "headers" in entry:
            kwargs["headers"] = entry["headers"]
    for opt in ("auth_type", "auth_config", "timeout_s", "retry_max"):
        if opt in entry:
            kwargs[opt] = entry[opt]
    return MCPServerConfig(**kwargs)


async def _build_mcp_client(
    config: MCPServerConfig,
    *,
    secret_store: SecretStore | None,
) -> MCPClient:
    """Build the right :class:`MCPClient` for ``config.transport``.

    OAuth2 servers fail fast (Mini-ADR U-12) — the schema accepts the
    config but the flow ships in a follow-up sprint. Bearer auth
    resolves ``auth_config["token_ref"]`` through the
    :class:`SecretStore` (Mini-ADR U-11) and injects an
    ``Authorization`` header without storing the value on the config.
    """
    if config.transport == "stdio":
        client: MCPClient = StdioMCPClient(config=config)
        await client.start()  # type: ignore[attr-defined]
        return client

    if config.auth_type == "oauth2":
        from orchestrator.tools.mcp import MCPOAuthNotImplementedError

        msg = (
            f"mcp server {config.name!r}: oauth2 auth flow not implemented "
            "in this release — see Mini-ADR L.L8-MCP. Switch to "
            'auth_type="bearer" with a token_ref or remove the server.'
        )
        raise MCPOAuthNotImplementedError(msg)

    resolved_headers = dict(config.headers)
    if config.auth_type == "bearer":
        if secret_store is None:
            msg = (
                f"mcp server {config.name!r}: bearer auth needs a "
                "SecretStore but none was provided to build_mcp_pool"
            )
            raise RuntimeError(msg)
        token_ref = config.auth_config["token_ref"]
        token = await secret_store.get(parse_secret_ref(token_ref))
        resolved_headers["Authorization"] = f"Bearer {token}"

    remote: SseMCPClient | StreamableHttpMCPClient
    if config.transport == "sse":
        remote = SseMCPClient(config=config, resolved_headers=resolved_headers)
    else:
        remote = StreamableHttpMCPClient(config=config, resolved_headers=resolved_headers)
    await remote.start()
    return remote


@asynccontextmanager
async def build_mcp_pool(
    config_file: str | None,
    *,
    secret_store: SecretStore | None = None,
    client_factory: McpClientFactory | None = None,
) -> AsyncIterator[MCPServerPool]:
    """Yield an :class:`MCPServerPool` of the platform's MCP servers.

    The pool launches each entry in ``config_file`` (Mini-ADR E-17 —
    operator-controlled, never tenant input). ``None`` yields an empty
    pool. The pool — and every connection / subprocess — is torn down
    on exit, including when a mid-startup failure aborts boot.

    ``client_factory`` overrides the default transport-dispatching
    factory (used by tests injecting a :class:`RecordingMCPClient`).
    ``secret_store`` is required for bearer-auth remote servers but
    unused for stdio / unauthenticated remotes.
    """
    if client_factory is None:

        async def _factory(cfg: MCPServerConfig) -> MCPClient:
            return await _build_mcp_client(cfg, secret_store=secret_store)

        client_factory = _factory

    pool = MCPServerPool()
    try:
        if config_file:
            for config in _load_mcp_server_configs(config_file):
                await pool.add(config.name, await client_factory(config))
        yield pool
    finally:
        await pool.close_all()


def build_supervisor_client(url: str | None) -> SupervisorClient | None:
    """Build the Sandbox Supervisor HTTP client from its base URL.

    ``None`` → the ``exec_python`` tool is unavailable; an agent that
    declares it fails at build time with a clear error.
    """
    if url is None:
        return None
    return HTTPSupervisorClient(base_url=url)


def build_tool_env(
    tenant_config_service: TenantConfigService,
    *,
    web_search_client: TavilyClient | None = None,
    supervisor_client: SupervisorClient | None = None,
    mcp_pool: MCPServerPool | None = None,
    artifact_store: ArtifactStore | None = None,
    knowledge_retriever: KnowledgeRetriever | None = None,
    image_resolver: ImageResolver | None = None,
) -> ToolEnv:
    """Assemble the M0 :class:`ToolEnv`.

    Wires the HTTP tool's per-tenant allowlist, and — when supplied —
    the ``web_search`` Tavily client, the ``exec_python`` Sandbox
    Supervisor client, the ``mcp`` server pool, the J.9 artifact store
    backing ``save_artifact`` / ``list_artifacts``, the J.5 knowledge
    retriever backing ``knowledge_search``, and the J.6 image resolver
    backing multimodal input.
    """
    return ToolEnv(
        allowlist_provider=_tenant_allowlist_provider(tenant_config_service),
        web_search_client=web_search_client,
        supervisor_client=supervisor_client,
        mcp_pool=mcp_pool,
        artifact_store=artifact_store,
        knowledge_retriever=knowledge_retriever,
        image_resolver=image_resolver,
    )


def make_knowledge_retriever(
    *,
    store: KnowledgeStore,
    embedder: Embedder | None,
    reranker: Reranker | None,
) -> KnowledgeRetriever | None:
    """Build the :class:`KnowledgeRetriever` backing ``knowledge_search``.

    ``None`` when no embedder is configured — ``knowledge_search`` is
    then unavailable, like long-term memory without an embedder.
    """
    if embedder is None:
        return None
    return KnowledgeRetriever(store=store, embedder=embedder, reranker=reranker)


async def resolve_object_store_config(
    *,
    backend: ObjectStoreBackend,
    endpoint_url: str | None,
    region: str,
    bucket: str,
    access_key_ref: str | None,
    secret_key_ref: str | None,
    secret_store: SecretStore,
) -> S3CompatibleConfig | None:
    """Build the S3 config for ``make_object_store`` (Stream J.6).

    ``None`` for the in-memory backend (no config needed). For
    ``s3-compatible`` the endpoint URL + both ``secret://`` key
    references are required; the keys are resolved through the
    SecretStore. Missing fields fail fast with a clear error.
    """
    if backend != "s3-compatible":
        return None
    if not (endpoint_url and access_key_ref and secret_key_ref):
        msg = (
            "object_store_backend='s3-compatible' requires object_store_endpoint_url "
            "+ object_store_access_key_ref + object_store_secret_key_ref"
        )
        raise RuntimeError(msg)
    return S3CompatibleConfig(
        endpoint_url=endpoint_url,
        region=region,
        bucket=bucket,
        access_key=await secret_store.get(parse_secret_ref(access_key_ref)),
        secret_key=await secret_store.get(parse_secret_ref(secret_key_ref)),
    )


def make_image_resolver(store: ObjectStore) -> ImageResolver:
    """Build the J.6 image resolver over an object store — backs both
    multimodal paths (Path A content blocks + Path B ``ask_image``)."""
    return ObjectStoreImageResolver(store=store)


def build_middleware_env(
    *,
    token_usage_store: TokenUsageStore | None = None,
) -> MiddlewareEnv:
    """Assemble the M0 :class:`MiddlewareEnv`.

    Single-instance defaults: an in-process response cache, the
    span-recording Langfuse client (the SDK-backed Langfuse adapter is
    M1), and the global-pattern secret redactor for the E.5 PII
    middleware. Per-tenant ``pii_fields`` mask only dict-shaped audit
    details (D.2); free-text LLM messages use the global patterns, so
    ``redact_text`` is a plain sync call — no per-tenant lookup.

    Stream G.9 — when ``token_usage_store`` is supplied,
    :class:`TokenUsageMiddleware` lands on the ``after_llm_call`` chain
    of every agent built from this env. ``app.py`` wires the SQL store;
    tests can leave it unset to keep the chain shape unchanged.
    """
    secret_redactor = DefaultSecretRedactor()

    def _redact_text(text: str, _tenant_id: UUID | None) -> str:
        return secret_redactor.redact_text(text)

    return MiddlewareEnv(
        response_cache=LLMResponseCache(redis=InMemoryRedisCache()),
        langfuse_client=RecordingLangfuseClient(),
        redact_text=_redact_text,
        token_usage_store=token_usage_store,
    )


def make_agent_runtime(
    secret_store: SecretStore,
    *,
    run_store: RunStore | None = None,
    run_event_store: RunEventStore | None = None,
) -> AgentRuntime:
    """Build the production :class:`AgentRuntime` with an in-memory checkpointer.

    :class:`InMemorySaver` has no async setup / teardown, so it is safe
    to construct here (outside a lifespan context). When
    ``settings.checkpointer_backend`` is ``postgres`` the app lifespan
    opens the durable checkpointer's connection context and swaps
    ``agent_builder`` before any run starts — see ``control_plane.app``.

    ``run_store`` (Mini-ADR J-41) is the durable ``agent_run`` mirror
    the :class:`RunManager` writes every create / status transition to;
    ``None`` keeps the registry purely in-memory.

    ``run_event_store`` (Stream H.3 PR 3 — Mini-ADR H-7) durable-mirrors
    every SSE frame; ``None`` keeps SSE purely in-memory (replay endpoint
    will fall through to live attach only).
    """
    return AgentRuntime(
        run_manager=RunManager(store=run_store),
        stream_bridge=InMemoryStreamBridge(),
        agent_builder=make_agent_builder(secret_store, InMemorySaver()),
        run_event_store=run_event_store,
    )
