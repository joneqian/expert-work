"""``ChildAgentBuilder`` wiring — Stream J.4 (sub-agent delegation).

The orchestrator's ``SubAgentTool`` delegates to a deployed sub-agent but
cannot resolve an ``agent_ref`` itself — the :class:`AgentSpecStore` lives
here in the control-plane. :func:`make_child_agent_builder` closes over
the spec store and the recursive ``build_agent`` path to produce the
:class:`ChildAgentBuilder` the orchestrator's ``ToolEnv`` carries.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from typing import Any
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver

from control_plane.runtime import make_provider_key_resolver, make_skill_resolver
from control_plane.tenancy import TenantConfigService
from control_plane.tenant_mcp_pool import TenantMcpPoolProvider
from helix_agent.common.credentials import CredentialsResolver
from helix_agent.common.skill_activity import SkillActivityRecorder
from helix_agent.persistence.agent_spec import AgentSpecStore
from helix_agent.persistence.skill import SkillStore
from helix_agent.runtime.secret_store import SecretStore
from orchestrator import BuiltAgent, MemoryEnv, MiddlewareEnv, ToolEnv, build_agent
from orchestrator.tools import ChildAgentBuilder

logger = logging.getLogger(__name__)


class SubAgentNotFoundError(Exception):
    """Raised when a ``SubAgentTool``'s ``agent_ref`` does not resolve to a
    deployed, non-deleted AgentSpec in the tenant.

    The orchestrator's tools node wraps it into a ``ToolMessage`` error
    (Mini-ADR E-12) — a dangling ``agent_ref`` fails that one delegation,
    not the whole parent run.
    """

    def __init__(self, *, tenant_id: UUID, name: str, version: str) -> None:
        super().__init__(
            f"sub-agent not found: tenant_id={tenant_id} name={name!r} version={version!r}"
        )
        self.tenant_id = tenant_id
        self.name = name
        self.version = version


def make_child_agent_builder(
    *,
    spec_store: AgentSpecStore,
    secret_store: SecretStore,
    checkpointer: BaseCheckpointSaver[Any],
    base_tool_env: ToolEnv,
    middleware_env: MiddlewareEnv | None = None,
    memory_env: MemoryEnv | None = None,
    credentials_resolver: CredentialsResolver | None = None,
    tenant_mcp_pool_provider: TenantMcpPoolProvider | None = None,
    skill_store: SkillStore | None = None,
    skill_activity_recorder: SkillActivityRecorder | None = None,
    tenant_config_service: TenantConfigService | None = None,
    register_invalidation: Callable[[Callable[[UUID], None]], None] | None = None,
) -> ChildAgentBuilder:
    """Build the :class:`ChildAgentBuilder` the orchestrator's ``ToolEnv`` carries.

    The returned callback resolves an ``agent_ref`` through ``spec_store``,
    recursively builds the sub-agent at ``subagent_depth=depth``, and
    caches the result keyed on ``(tenant_id, name, version, depth)``. The
    cache key includes ``depth`` because the same manifest builds a
    *different* graph at different depths — an agent built at
    ``MAX_SUBAGENT_DEPTH`` carries no further ``SubAgentTool``\\s.

    The sub-agent's own ``ToolEnv`` carries this same builder, so a child
    can delegate to a grandchild; the recursion is bounded by the
    build-time depth cap, not by this wiring.

    The returned callback raises :class:`SubAgentNotFoundError` for an
    unresolvable ``agent_ref`` — the orchestrator turns that into a tool
    error rather than crashing the parent run.
    """
    cache: dict[tuple[UUID, str, str, int], BuiltAgent] = {}

    async def _build(*, tenant_id: UUID, name: str, version: str, depth: int) -> BuiltAgent:
        key = (tenant_id, name, version, depth)
        cached = cache.get(key)
        if cached is not None:
            return cached
        record = await spec_store.get(tenant_id=tenant_id, name=name, version=version)
        if record is None:
            raise SubAgentNotFoundError(tenant_id=tenant_id, name=name, version=version)
        provider_key_resolver = (
            make_provider_key_resolver(resolver=credentials_resolver, tenant_id=tenant_id)
            if credentials_resolver is not None
            else None
        )
        # Stream X (Mini-ADR X-4) — sub-agents resolve skills too; a child
        # whose manifest declares skills would otherwise hard-fail at build.
        skill_resolver = (
            make_skill_resolver(store=skill_store, tenant_config_service=tenant_config_service)
            if skill_store is not None and tenant_config_service is not None
            else None
        )
        # Stream V (Mini-ADR V-4) — attach the tenant's own remote MCP pool
        # per-call so delegated sub-agents can also use tenant MCP servers.
        call_tool_env = child_tool_env
        if tenant_mcp_pool_provider is not None:
            tenant_pool = await tenant_mcp_pool_provider(tenant_id)
            if tenant_pool.names():
                call_tool_env = replace(child_tool_env, tenant_mcp_pool=tenant_pool)
        built = await build_agent(
            record.spec,
            secret_store=secret_store,
            checkpointer=checkpointer,
            tool_env=call_tool_env,
            middleware_env=middleware_env,
            memory_env=memory_env,
            subagent_depth=depth,
            tenant_id=tenant_id,
            provider_key_resolver=provider_key_resolver,
            skill_resolver=skill_resolver,
            skill_activity_recorder=skill_activity_recorder,
        )
        cache[key] = built
        logger.info(
            "control_plane.subagent.built name=%s version=%s depth=%d",
            name,
            version,
            depth,
        )
        return built

    def _invalidate_tenant(tenant_id: UUID) -> None:
        """Drop cached sub-agents for a tenant (Stream V-D, audit #1).

        Registered with the :class:`AgentRuntime` so a tenant's MCP registry
        change evicts stale delegated sub-agents (whose ``ToolEnv`` holds a
        now-closed tenant MCP pool), mirroring the top-level cache.
        """
        for key in [k for k in cache if k[0] == tenant_id]:
            del cache[key]

    if register_invalidation is not None:
        register_invalidation(_invalidate_tenant)

    # The sub-agent's ToolEnv carries _build itself so a child can in turn
    # delegate to a grandchild. Assigned after _build is defined; the
    # closure reads it only at call time, by which point it is bound.
    child_tool_env = replace(base_tool_env, child_agent_builder=_build)
    return _build
