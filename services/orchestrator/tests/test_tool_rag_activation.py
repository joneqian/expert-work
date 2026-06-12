"""Stream TE-6b — tool RAG activation policy (always-defer-MCP).

Verifies the assembler's deer-flow-style policy: MCP tools register
deferred (absent from the per-turn LLM bind), ``find_tools`` is
auto-registered active when (and only when) something is deferred, and a
no-MCP agent's tool surface is byte-identical to pre-TE-6.
"""

from __future__ import annotations

import pytest

from helix_agent.protocol import BuiltinToolSpec, MCPToolSpec
from orchestrator.tools import (
    MCPServerPool,
    MCPToolDef,
    RecordingMCPClient,
    ToolEnv,
    ToolRegistry,
    build_tool_registry,
    register_mcp_tools,
)


def _tool_def(name: str) -> MCPToolDef:
    return MCPToolDef(
        name=name,
        description=f"{name} via MCP",
        input_schema={"type": "object", "properties": {}},
    )


def _active(registry: ToolRegistry) -> set[str]:
    return {s.name for s in registry.specs()}


def _all(registry: ToolRegistry) -> set[str]:
    return {s.name for s in registry.all_specs()}


class _RecordingTavily:
    """Minimal Tavily stub so the web_search builtin assembles."""

    async def search(self, *args: object, **kwargs: object) -> list[dict[str, object]]:
        del args, kwargs
        return []


# --- register_mcp_tools(deferred=) -----------------------------------------


@pytest.mark.asyncio
async def test_register_mcp_tools_deferred_excludes_from_bind() -> None:
    client = RecordingMCPClient(tools=(_tool_def("read_file"), _tool_def("list_dir")))
    registry = ToolRegistry()
    await register_mcp_tools(server_name="fs", client=client, registry=registry, deferred=True)

    # Absent from the LLM bind (specs) but present in all_specs + dispatchable.
    assert _active(registry) == set()
    assert _all(registry) == {"mcp:fs.read_file", "mcp:fs.list_dir"}
    assert registry.has_deferred()
    assert registry.get_required("mcp:fs.read_file") is not None


# --- build_tool_registry policy --------------------------------------------


async def _registry_with_mcp() -> ToolRegistry:
    pool = MCPServerPool()
    await pool.add("fs", RecordingMCPClient(tools=(_tool_def("read_file"), _tool_def("list_dir"))))
    env = ToolEnv(mcp_pool=pool)
    return await build_tool_registry([MCPToolSpec()], tool_env=env)


@pytest.mark.asyncio
async def test_build_defers_mcp_tools_and_autoregisters_find_tools() -> None:
    registry = await _registry_with_mcp()

    # MCP tools deferred (not in bind), find_tools auto-added and ACTIVE.
    assert "mcp:fs.read_file" not in _active(registry)
    assert "mcp:fs.read_file" in _all(registry)
    assert "find_tools" in _active(registry)  # discovery entry point always reachable

    # find_tools can surface the deferred MCP tools.
    found = registry.search("read_file")
    # HX-12 ranked retrieval: best match first; sibling tools sharing a
    # token (e.g. "file") may follow.
    assert found and found[0].name == "mcp:fs.read_file"


@pytest.mark.asyncio
async def test_find_tools_not_added_without_deferred_tools() -> None:
    # A no-MCP agent: only an active builtin, nothing deferred → no find_tools,
    # tool surface identical to pre-TE-6 (zero behaviour change).
    env = ToolEnv(web_search_client=_RecordingTavily())
    registry = await build_tool_registry([BuiltinToolSpec(name="web_search")], tool_env=env)
    assert registry.has_deferred() is False
    assert "find_tools" not in _all(registry)
    assert _active(registry) == {"web_search"}
