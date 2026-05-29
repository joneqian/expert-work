"""``ChildAgentBuilder`` wiring — Stream J.4 (sub-agent delegation).

The orchestrator's ``SubAgentTool`` delegates to a deployed sub-agent but
cannot resolve an ``agent_ref`` itself — the :class:`AgentSpecStore` lives
here in the control-plane. :func:`make_child_agent_builder` closes over
the spec store and the recursive ``build_agent`` path to produce the
:class:`ChildAgentBuilder` the orchestrator's ``ToolEnv`` carries.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver

from control_plane.runtime import make_provider_key_resolver
from helix_agent.common.credentials import CredentialsResolver
from helix_agent.persistence.agent_spec import AgentSpecStore
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
        built = await build_agent(
            record.spec,
            secret_store=secret_store,
            checkpointer=checkpointer,
            tool_env=child_tool_env,
            middleware_env=middleware_env,
            memory_env=memory_env,
            subagent_depth=depth,
            tenant_id=tenant_id,
            provider_key_resolver=provider_key_resolver,
        )
        cache[key] = built
        logger.info(
            "control_plane.subagent.built name=%s version=%s depth=%d",
            name,
            version,
            depth,
        )
        return built

    # The sub-agent's ToolEnv carries _build itself so a child can in turn
    # delegate to a grandchild. Assigned after _build is defined; the
    # closure reads it only at call time, by which point it is bound.
    child_tool_env = replace(base_tool_env, child_agent_builder=_build)
    return _build
