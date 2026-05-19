"""Assemble a :class:`ToolRegistry` from a manifest's ``tools:`` block.

STREAM-E-DESIGN Mini-ADR E-14: the manifest declares tools as a
``type``-discriminated union (:data:`helix_agent.protocol.ToolSpecEntry`).
:func:`build_tool_registry` maps each declaration to a concrete adapter
and registers it.

Platform runtime deps — the Tavily client, the per-tenant HTTP
allowlist provider, the MCP server pool — are *not* in the manifest
(they are tenant-/platform-scoped, Mini-ADR E-14). They are injected
via :class:`ToolEnv`. A manifest that declares a tool whose backing
dep is absent from the ``ToolEnv`` raises :class:`AgentFactoryError`,
so the failure surfaces at build time, not on the first tool call.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from helix_agent.persistence import ArtifactStore
from helix_agent.protocol import (
    BuiltinToolSpec,
    HTTPToolSpec,
    MCPToolSpec,
    ToolSpecEntry,
)
from orchestrator.errors import AgentFactoryError
from orchestrator.tools.artifact import ListArtifactsTool, SaveArtifactTool
from orchestrator.tools.http import AllowlistProvider, HTTPTool
from orchestrator.tools.mcp import MCPServerPool, register_mcp_tools
from orchestrator.tools.registry import ToolRegistry
from orchestrator.tools.sandbox import ExecPythonTool, SupervisorClient
from orchestrator.tools.web_search import DEFAULT_MAX_RESULTS, TavilyClient, WebSearchTool

#: Built-in tool names the platform ships in M0.
KNOWN_BUILTINS = frozenset({"web_search", "exec_python", "save_artifact", "list_artifacts"})


@dataclass(frozen=True)
class ToolEnv:
    """Platform runtime deps the assembler draws on.

    Each field backs one tool kind. A field left ``None`` means that
    tool is not available in this deployment — declaring it in a
    manifest raises :class:`AgentFactoryError`. An empty ``ToolEnv()``
    therefore builds a pure-LLM agent and nothing else.
    """

    web_search_client: TavilyClient | None = None
    allowlist_provider: AllowlistProvider | None = None
    mcp_pool: MCPServerPool | None = None
    #: Sandbox Supervisor client backing the ``exec_python`` builtin (F.4).
    supervisor_client: SupervisorClient | None = None
    #: Artifact registry backing the ``save_artifact`` / ``list_artifacts``
    #: builtins (Stream J.9).
    artifact_store: ArtifactStore | None = None


async def build_tool_registry(
    tool_specs: Sequence[ToolSpecEntry],
    *,
    tool_env: ToolEnv,
    persistent_workspace: bool = False,
) -> ToolRegistry:
    """Build a :class:`ToolRegistry` from a manifest's ``tools:`` entries.

    ``persistent_workspace`` comes from the manifest's
    ``sandbox.filesystem`` block (Stream J.15) — it makes the
    ``exec_python`` builtin acquire against the run user's persistent
    workspace volume.

    :raises AgentFactoryError: an entry names an unknown builtin, or
        declares a tool whose ``ToolEnv`` dependency is not configured.
    """
    registry = ToolRegistry()
    for entry in tool_specs:
        if isinstance(entry, BuiltinToolSpec):
            _register_builtin(registry, entry, tool_env, persistent_workspace)
        elif isinstance(entry, HTTPToolSpec):
            _register_http(registry, tool_env)
        elif isinstance(entry, MCPToolSpec):
            await _register_mcp(registry, entry, tool_env)
    return registry


def _register_builtin(
    registry: ToolRegistry,
    entry: BuiltinToolSpec,
    env: ToolEnv,
    persistent_workspace: bool,
) -> None:
    if entry.name not in KNOWN_BUILTINS:
        raise AgentFactoryError(
            f"unknown builtin tool {entry.name!r} (known: {sorted(KNOWN_BUILTINS)})"
        )
    if entry.name == "web_search":
        _register_web_search(registry, entry, env)
    elif entry.name == "exec_python":
        _register_exec_python(registry, env, persistent_workspace)
    elif entry.name == "save_artifact":
        registry.register(SaveArtifactTool(store=_require_artifact_store(env, "save_artifact")))
    elif entry.name == "list_artifacts":
        registry.register(ListArtifactsTool(store=_require_artifact_store(env, "list_artifacts")))


def _register_web_search(registry: ToolRegistry, entry: BuiltinToolSpec, env: ToolEnv) -> None:
    if env.web_search_client is None:
        raise AgentFactoryError(
            "builtin 'web_search' declared but no Tavily client is "
            "configured (ToolEnv.web_search_client)"
        )
    max_results = int(entry.config.get("max_results", DEFAULT_MAX_RESULTS))
    registry.register(WebSearchTool(client=env.web_search_client, default_max_results=max_results))


def _register_exec_python(registry: ToolRegistry, env: ToolEnv, persistent_workspace: bool) -> None:
    if env.supervisor_client is None:
        raise AgentFactoryError(
            "builtin 'exec_python' declared but no Sandbox Supervisor client "
            "is configured (ToolEnv.supervisor_client)"
        )
    registry.register(
        ExecPythonTool(
            client=env.supervisor_client,
            persistent_workspace=persistent_workspace,
        )
    )


def _require_artifact_store(env: ToolEnv, tool_name: str) -> ArtifactStore:
    if env.artifact_store is None:
        raise AgentFactoryError(
            f"builtin {tool_name!r} declared but no artifact store is "
            "configured (ToolEnv.artifact_store)"
        )
    return env.artifact_store


def _register_http(registry: ToolRegistry, env: ToolEnv) -> None:
    if env.allowlist_provider is None:
        raise AgentFactoryError(
            "'http' tool declared but no allowlist provider is "
            "configured (ToolEnv.allowlist_provider)"
        )
    registry.register(HTTPTool(allowlist_provider=env.allowlist_provider))


async def _register_mcp(registry: ToolRegistry, entry: MCPToolSpec, env: ToolEnv) -> None:
    if env.mcp_pool is None:
        raise AgentFactoryError(
            "'mcp' tool declared but no MCP server pool is configured (ToolEnv.mcp_pool)"
        )
    allow = set(entry.allow_tools) or None
    for server_name in env.mcp_pool.names():
        client = env.mcp_pool.get(server_name)
        if client is None:  # pragma: no cover - name came from names()
            continue
        await register_mcp_tools(
            server_name=server_name,
            client=client,
            registry=registry,
            allow_tools=allow,
        )
