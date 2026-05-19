"""Unit tests for :func:`build_tool_registry` — manifest ``tools:`` → registry."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

import pytest

from helix_agent.persistence import InMemoryKnowledgeStore
from helix_agent.protocol import (
    BuiltinToolSpec,
    HTTPToolSpec,
    KnowledgeSpec,
    MCPToolSpec,
    SubAgentSpec,
)
from orchestrator import AgentFactoryError
from orchestrator.llm import FakeEmbedder
from orchestrator.tools import (
    MAX_SUBAGENT_DEPTH,
    HTTPTool,
    KnowledgeRetriever,
    KnowledgeSearchTool,
    MCPServerPool,
    MCPToolDef,
    RecordingMCPClient,
    RecordingTavilyClient,
    SubAgentTool,
    ToolEnv,
    WebSearchTool,
    build_tool_registry,
)


def _knowledge_retriever() -> KnowledgeRetriever:
    return KnowledgeRetriever(store=InMemoryKnowledgeStore(), embedder=FakeEmbedder())


class _StubChildBuilder:
    """Conforms to ``ChildAgentBuilder``. Assembly only *registers*
    SubAgentTools — it never invokes the builder — so the body is unused."""

    async def __call__(self, *, tenant_id: Any, name: str, version: str, depth: int) -> Any:
        raise AssertionError("child builder must not be called during assembly")


_SUBAGENTS = [
    SubAgentSpec(name="researcher", agent_ref="deep-researcher@1.0.0", description="research"),
    SubAgentSpec(name="writer", agent_ref="doc-writer@2.0.0", description="drafting"),
]


async def _allowlist(_tenant: UUID | None) -> Sequence[str]:
    return ["https://api.github.com/*"]


async def _seeded_pool() -> MCPServerPool:
    pool = MCPServerPool()
    client = RecordingMCPClient(
        tools=(
            MCPToolDef(name="read_pr", description="read a PR", input_schema={}),
            MCPToolDef(name="post_comment", description="comment", input_schema={}),
        )
    )
    await pool.add("gitlab", client)
    return pool


@pytest.mark.asyncio
async def test_empty_tools_builds_empty_registry() -> None:
    registry = await build_tool_registry([], tool_env=ToolEnv())
    assert len(registry) == 0


@pytest.mark.asyncio
async def test_builtin_web_search_assembled() -> None:
    env = ToolEnv(web_search_client=RecordingTavilyClient())
    registry = await build_tool_registry(
        [BuiltinToolSpec(name="web_search", config={"max_results": 7})],
        tool_env=env,
    )
    tool = registry.get("web_search")
    assert isinstance(tool, WebSearchTool)
    assert tool.default_max_results == 7


@pytest.mark.asyncio
async def test_builtin_unknown_name_raises() -> None:
    with pytest.raises(AgentFactoryError, match="unknown builtin"):
        await build_tool_registry(
            [BuiltinToolSpec(name="nonsense")],
            tool_env=ToolEnv(web_search_client=RecordingTavilyClient()),
        )


@pytest.mark.asyncio
async def test_builtin_web_search_missing_client_raises() -> None:
    with pytest.raises(AgentFactoryError, match="Tavily client"):
        await build_tool_registry([BuiltinToolSpec(name="web_search")], tool_env=ToolEnv())


@pytest.mark.asyncio
async def test_http_tool_assembled() -> None:
    registry = await build_tool_registry(
        [HTTPToolSpec()], tool_env=ToolEnv(allowlist_provider=_allowlist)
    )
    assert isinstance(registry.get("http"), HTTPTool)


@pytest.mark.asyncio
async def test_http_tool_missing_allowlist_raises() -> None:
    with pytest.raises(AgentFactoryError, match="allowlist provider"):
        await build_tool_registry([HTTPToolSpec()], tool_env=ToolEnv())


@pytest.mark.asyncio
async def test_mcp_tools_assembled_from_pool() -> None:
    pool = await _seeded_pool()
    registry = await build_tool_registry([MCPToolSpec()], tool_env=ToolEnv(mcp_pool=pool))
    # Both server-advertised tools register, namespaced ``mcp:<server>.<tool>``.
    assert len(registry) == 2


@pytest.mark.asyncio
async def test_mcp_allow_tools_filters() -> None:
    pool = await _seeded_pool()
    registry = await build_tool_registry(
        [MCPToolSpec(allow_tools=["read_pr"])], tool_env=ToolEnv(mcp_pool=pool)
    )
    assert len(registry) == 1


@pytest.mark.asyncio
async def test_mcp_missing_pool_raises() -> None:
    with pytest.raises(AgentFactoryError, match="MCP server pool"):
        await build_tool_registry([MCPToolSpec()], tool_env=ToolEnv())


@pytest.mark.asyncio
async def test_multiple_tools_all_registered() -> None:
    env = ToolEnv(
        web_search_client=RecordingTavilyClient(),
        allowlist_provider=_allowlist,
    )
    registry = await build_tool_registry(
        [BuiltinToolSpec(name="web_search"), HTTPToolSpec()], tool_env=env
    )
    assert registry.get("web_search") is not None
    assert registry.get("http") is not None


# ---------------------------------------------------------------------------
# subagents — agent-as-tool delegation (Stream J.4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subagents_assembled_into_named_tools() -> None:
    env = ToolEnv(child_agent_builder=_StubChildBuilder())
    registry = await build_tool_registry([], tool_env=env, subagents=_SUBAGENTS)
    researcher = registry.get("researcher")
    writer = registry.get("writer")
    assert isinstance(researcher, SubAgentTool)
    assert isinstance(writer, SubAgentTool)
    # Top-level agent builds at depth 0 → its children build at depth 1.
    assert researcher.child_depth == 1


@pytest.mark.asyncio
async def test_subagent_child_depth_is_parent_depth_plus_one() -> None:
    env = ToolEnv(child_agent_builder=_StubChildBuilder())
    registry = await build_tool_registry(
        [], tool_env=env, subagents=_SUBAGENTS[:1], subagent_depth=1
    )
    tool = registry.get("researcher")
    assert isinstance(tool, SubAgentTool)
    assert tool.child_depth == 2


@pytest.mark.asyncio
async def test_subagents_without_builder_raises() -> None:
    with pytest.raises(AgentFactoryError, match="sub-agent builder"):
        await build_tool_registry([], tool_env=ToolEnv(), subagents=_SUBAGENTS)


@pytest.mark.asyncio
async def test_subagents_not_registered_at_depth_cap() -> None:
    # At MAX_SUBAGENT_DEPTH nothing registers — the structural recursion
    # guard. The agent still builds; it just cannot delegate further.
    env = ToolEnv(child_agent_builder=_StubChildBuilder())
    registry = await build_tool_registry(
        [], tool_env=env, subagents=_SUBAGENTS, subagent_depth=MAX_SUBAGENT_DEPTH
    )
    assert len(registry) == 0


@pytest.mark.asyncio
async def test_depth_cap_skips_missing_builder_check() -> None:
    # At the cap nothing is registered, so a missing builder is not an
    # error — the un-buildable check only fires when tools would register.
    registry = await build_tool_registry(
        [], tool_env=ToolEnv(), subagents=_SUBAGENTS, subagent_depth=MAX_SUBAGENT_DEPTH
    )
    assert len(registry) == 0


@pytest.mark.asyncio
async def test_no_subagents_with_empty_builder_is_fine() -> None:
    # No subagents declared → the missing-builder check never fires.
    registry = await build_tool_registry([], tool_env=ToolEnv())
    assert len(registry) == 0


# ---------------------------------------------------------------------------
# knowledge — knowledge_search activation (Stream J.5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_knowledge_block_activates_search_tool() -> None:
    env = ToolEnv(knowledge_retriever=_knowledge_retriever())
    registry = await build_tool_registry(
        [], tool_env=env, knowledge=KnowledgeSpec(knowledge_base_refs=["hr", "eng"])
    )
    tool = registry.get("knowledge_search")
    assert isinstance(tool, KnowledgeSearchTool)
    assert tool.knowledge_base_refs == ("hr", "eng")


@pytest.mark.asyncio
async def test_knowledge_block_without_retriever_raises() -> None:
    with pytest.raises(AgentFactoryError, match="knowledge retriever"):
        await build_tool_registry(
            [], tool_env=ToolEnv(), knowledge=KnowledgeSpec(knowledge_base_refs=["hr"])
        )


@pytest.mark.asyncio
async def test_no_knowledge_block_registers_no_search_tool() -> None:
    env = ToolEnv(knowledge_retriever=_knowledge_retriever())
    registry = await build_tool_registry([], tool_env=env)
    assert registry.get("knowledge_search") is None
