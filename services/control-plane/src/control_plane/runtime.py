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
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from control_plane.tenancy import TenantConfigNotConfiguredError, TenantConfigService
from helix_agent.protocol import AgentSpec
from helix_agent.runtime.llm import InMemoryRedisCache, LLMResponseCache
from helix_agent.runtime.middleware import RecordingLangfuseClient
from helix_agent.runtime.runs import RunManager
from helix_agent.runtime.secret_store import SecretStore, parse_secret_ref
from helix_agent.runtime.stream_bridge import InMemoryStreamBridge, StreamBridge
from orchestrator import BuiltAgent, MiddlewareEnv, ToolEnv, build_agent
from orchestrator.tools import (
    AllowlistProvider,
    HTTPTavilyClient,
    MCPClient,
    MCPServerConfig,
    MCPServerPool,
    StdioMCPClient,
    TavilyClient,
)

#: Builds a runnable agent from a manifest. The production builder
#: closes over a SecretStore + checkpointer and calls
#: :func:`orchestrator.build_agent`; integration tests substitute a
#: stub returning a :class:`BuiltAgent` over a fake-LLM graph — the
#: real builder wires HTTP provider clients, which a test must not hit.
AgentBuilder = Callable[[AgentSpec], Awaitable[BuiltAgent]]


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
        built = await self.agent_builder(spec)
        self._cache[key] = built
        return built


def make_agent_builder(
    secret_store: SecretStore,
    checkpointer: BaseCheckpointSaver[Any],
    *,
    tool_env: ToolEnv | None = None,
    middleware_env: MiddlewareEnv | None = None,
) -> AgentBuilder:
    """Production :data:`AgentBuilder` bound to a SecretStore + checkpointer.

    ``tool_env`` / ``middleware_env`` inject the platform tool and
    middleware backends. Kept separate from :func:`make_agent_runtime`
    so the app lifespan can rebuild the builder once the durable
    checkpointer's connection context is open and the tenant-config
    service is ready.
    """

    async def _build(spec: AgentSpec) -> BuiltAgent:
        return await build_agent(
            spec,
            secret_store=secret_store,
            checkpointer=checkpointer,
            tool_env=tool_env,
            middleware_env=middleware_env,
        )

    return _build


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


async def resolve_web_search_client(
    *,
    api_key_ref: str | None,
    secret_store: SecretStore,
) -> TavilyClient | None:
    """Resolve the Tavily API key behind ``api_key_ref`` into a web-search
    client. ``None`` ref → ``None`` — ``web_search`` is then unavailable
    and an agent declaring it fails at build time."""
    if api_key_ref is None:
        return None
    api_key = await secret_store.get(parse_secret_ref(api_key_ref))
    return HTTPTavilyClient(api_key=api_key)


#: Builds an :class:`MCPClient` from a server config. The default
#: launches a real stdio subprocess; tests inject a recording client.
McpClientFactory = Callable[[MCPServerConfig], Awaitable[MCPClient]]


def _load_mcp_server_configs(path: str) -> list[MCPServerConfig]:
    """Parse the platform MCP-server JSON file (Mini-ADR E-17).

    Shape: ``[{"name": str, "command": [str, ...], "env": {str: str}}]``.
    A malformed entry raises at boot — fail-fast on misconfiguration.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        msg = f"mcp_servers_config_file must contain a JSON array: {path}"
        raise ValueError(msg)
    return [
        MCPServerConfig(
            name=entry["name"],
            command=entry["command"],
            env=entry.get("env", {}),
        )
        for entry in raw
    ]


async def _default_mcp_client(config: MCPServerConfig) -> MCPClient:
    client = StdioMCPClient(config=config)
    await client.start()
    return client


@asynccontextmanager
async def build_mcp_pool(
    config_file: str | None,
    *,
    client_factory: McpClientFactory = _default_mcp_client,
) -> AsyncIterator[MCPServerPool]:
    """Yield an :class:`MCPServerPool` of the platform's MCP servers.

    The pool launches one subprocess per entry in ``config_file``
    (Mini-ADR E-17 — operator-controlled, never tenant input). ``None``
    yields an empty pool. The pool — and every subprocess — is torn
    down on exit, including when a mid-startup failure aborts boot.
    """
    pool = MCPServerPool()
    try:
        if config_file:
            for config in _load_mcp_server_configs(config_file):
                await pool.add(config.name, await client_factory(config))
        yield pool
    finally:
        await pool.close_all()


def build_tool_env(
    tenant_config_service: TenantConfigService,
    *,
    web_search_client: TavilyClient | None = None,
    mcp_pool: MCPServerPool | None = None,
) -> ToolEnv:
    """Assemble the M0 :class:`ToolEnv`.

    Wires the HTTP tool's per-tenant allowlist, and — when supplied —
    the ``web_search`` Tavily client and the ``mcp`` server pool.
    """
    return ToolEnv(
        allowlist_provider=_tenant_allowlist_provider(tenant_config_service),
        web_search_client=web_search_client,
        mcp_pool=mcp_pool,
    )


def build_middleware_env() -> MiddlewareEnv:
    """Assemble the M0 :class:`MiddlewareEnv`.

    Single-instance defaults: an in-process response cache and the
    span-recording Langfuse client (the SDK-backed Langfuse adapter is
    M1). The PII redactor is not wired yet — see its follow-up.
    """
    return MiddlewareEnv(
        response_cache=LLMResponseCache(redis=InMemoryRedisCache()),
        langfuse_client=RecordingLangfuseClient(),
    )


def make_agent_runtime(secret_store: SecretStore) -> AgentRuntime:
    """Build the production :class:`AgentRuntime` with an in-memory checkpointer.

    :class:`InMemorySaver` has no async setup / teardown, so it is safe
    to construct here (outside a lifespan context). When
    ``settings.checkpointer_backend`` is ``postgres`` the app lifespan
    opens the durable checkpointer's connection context and swaps
    ``agent_builder`` before any run starts — see ``control_plane.app``.
    """
    return AgentRuntime(
        run_manager=RunManager(),
        stream_bridge=InMemoryStreamBridge(),
        agent_builder=make_agent_builder(secret_store, InMemorySaver()),
    )
