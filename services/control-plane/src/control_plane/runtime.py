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

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from control_plane.tenancy import TenantConfigNotConfiguredError, TenantConfigService
from helix_agent.protocol import AgentSpec
from helix_agent.runtime.llm import InMemoryRedisCache, LLMResponseCache
from helix_agent.runtime.middleware import RecordingLangfuseClient
from helix_agent.runtime.runs import RunManager
from helix_agent.runtime.secret_store import SecretStore
from helix_agent.runtime.stream_bridge import InMemoryStreamBridge, StreamBridge
from orchestrator import BuiltAgent, MiddlewareEnv, ToolEnv, build_agent
from orchestrator.tools import AllowlistProvider

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


def build_tool_env(tenant_config_service: TenantConfigService) -> ToolEnv:
    """Assemble the M0 :class:`ToolEnv`.

    Wires the HTTP tool's per-tenant allowlist. ``web_search`` (Tavily)
    and ``mcp`` are not wired yet — declaring those tools fails at
    build time until their follow-ups land.
    """
    return ToolEnv(allowlist_provider=_tenant_allowlist_provider(tenant_config_service))


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
