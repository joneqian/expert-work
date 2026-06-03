"""Unit tests for :func:`build_tool_registry` — manifest ``tools:`` → registry."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

import pytest
from langchain_core.messages import AIMessage, BaseMessage

from helix_agent.persistence import InMemoryKnowledgeStore
from helix_agent.protocol import (
    BuiltinToolSpec,
    HTTPToolSpec,
    KnowledgeSpec,
    MCPToolSpec,
    ModelSpec,
    SubAgentSpec,
    VisionSpec,
)
from orchestrator import AgentFactoryError
from orchestrator.llm import FakeEmbedder
from orchestrator.multimodal import InMemoryImageResolver
from orchestrator.tools import (
    MAX_SUBAGENT_DEPTH,
    AskImageTool,
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
async def test_builtin_ask_for_approval_assembled() -> None:
    """Stream J.8 — ``ask_for_approval`` is a zero-dependency builtin."""
    from orchestrator.tools.approval import AskForApprovalTool

    registry = await build_tool_registry(
        [BuiltinToolSpec(name="ask_for_approval")],
        tool_env=ToolEnv(),
    )
    tool = registry.get("ask_for_approval")
    assert isinstance(tool, AskForApprovalTool)


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


async def _two_server_pool() -> MCPServerPool:
    pool = MCPServerPool()
    await pool.add(
        "gitlab",
        RecordingMCPClient(tools=(MCPToolDef(name="read_pr", description="", input_schema={}),)),
    )
    await pool.add(
        "linear",
        RecordingMCPClient(
            tools=(MCPToolDef(name="list_issues", description="", input_schema={}),)
        ),
    )
    return pool


@pytest.mark.asyncio
async def test_mcp_server_allowlist_empty_sees_all_servers() -> None:
    # Stream O Mini-ADR O-14 — empty allowlist (default) = no restriction.
    pool = await _two_server_pool()
    registry = await build_tool_registry([MCPToolSpec()], tool_env=ToolEnv(mcp_pool=pool))
    assert len(registry) == 2


@pytest.mark.asyncio
async def test_mcp_server_allowlist_hides_unlisted_servers() -> None:
    # A non-empty allowlist restricts the agent to the listed server names;
    # ``linear`` is hidden even though it is in the platform pool.
    pool = await _two_server_pool()
    registry = await build_tool_registry(
        [MCPToolSpec()],
        tool_env=ToolEnv(mcp_pool=pool, mcp_allowlist=("gitlab",)),
    )
    assert len(registry) == 1
    assert registry.get("mcp:gitlab.read_pr") is not None
    assert registry.get("mcp:linear.list_issues") is None


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


# ---------------------------------------------------------------------------
# vision — ask_image activation (Stream J.6 Path B)
# ---------------------------------------------------------------------------


async def _stub_vl_caller(*, messages: Sequence[BaseMessage], tools: Sequence[Any]) -> AIMessage:
    """Conforms to :class:`LLMCaller`; never invoked by assembly tests."""
    raise AssertionError("VL caller must not be called during assembly")


def _vision_spec() -> VisionSpec:
    return VisionSpec(model=ModelSpec(provider="qwen", name="qwen-vl-max"))


@pytest.mark.asyncio
async def test_vision_block_activates_ask_image_tool() -> None:
    env = ToolEnv(image_resolver=InMemoryImageResolver())
    registry = await build_tool_registry(
        [],
        tool_env=env,
        vision=_vision_spec(),
        vl_caller=_stub_vl_caller,
    )
    tool = registry.get("ask_image")
    assert isinstance(tool, AskImageTool)


@pytest.mark.asyncio
async def test_vision_block_without_image_resolver_raises() -> None:
    with pytest.raises(AgentFactoryError, match="image resolver"):
        await build_tool_registry(
            [],
            tool_env=ToolEnv(),
            vision=_vision_spec(),
            vl_caller=_stub_vl_caller,
        )


@pytest.mark.asyncio
async def test_vision_block_without_vl_caller_raises() -> None:
    env = ToolEnv(image_resolver=InMemoryImageResolver())
    with pytest.raises(AgentFactoryError, match="VL llm_caller"):
        await build_tool_registry([], tool_env=env, vision=_vision_spec(), vl_caller=None)


@pytest.mark.asyncio
async def test_no_vision_block_registers_no_ask_image_tool() -> None:
    env = ToolEnv(image_resolver=InMemoryImageResolver())
    registry = await build_tool_registry([], tool_env=env)
    assert registry.get("ask_image") is None


# ---------------------------------------------------------------------------
# tenant_mcp_pool — Stream V-D union registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_pool_tools_registered_alongside_platform() -> None:
    platform = MCPServerPool()
    await platform.add(
        "ops",
        RecordingMCPClient(tools=(MCPToolDef(name="deploy", description="", input_schema={}),)),
    )
    tenant = MCPServerPool()
    await tenant.add(
        "github",
        RecordingMCPClient(
            tools=(MCPToolDef(name="create_issue", description="", input_schema={}),)
        ),
    )
    registry = await build_tool_registry(
        [MCPToolSpec()], tool_env=ToolEnv(mcp_pool=platform, tenant_mcp_pool=tenant)
    )
    assert registry.get("mcp:ops.deploy") is not None
    assert registry.get("mcp:github.create_issue") is not None


@pytest.mark.asyncio
async def test_tenant_pool_not_filtered_by_platform_allowlist() -> None:
    # The allowlist gates the PLATFORM pool only; tenant servers are the
    # tenant's own and are always visible.
    platform = MCPServerPool()
    await platform.add(
        "ops",
        RecordingMCPClient(tools=(MCPToolDef(name="deploy", description="", input_schema={}),)),
    )
    tenant = MCPServerPool()
    await tenant.add(
        "github",
        RecordingMCPClient(
            tools=(MCPToolDef(name="create_issue", description="", input_schema={}),)
        ),
    )
    registry = await build_tool_registry(
        [MCPToolSpec()],
        tool_env=ToolEnv(mcp_pool=platform, tenant_mcp_pool=tenant, mcp_allowlist=("ops",)),
    )
    assert registry.get("mcp:ops.deploy") is not None  # platform, on allowlist
    assert registry.get("mcp:github.create_issue") is not None  # tenant, not gated


@pytest.mark.asyncio
async def test_allow_tools_filters_tenant_pool_too() -> None:
    tenant = MCPServerPool()
    await tenant.add(
        "github",
        RecordingMCPClient(
            tools=(
                MCPToolDef(name="create_issue", description="", input_schema={}),
                MCPToolDef(name="delete_repo", description="", input_schema={}),
            )
        ),
    )
    registry = await build_tool_registry(
        [MCPToolSpec(allow_tools=["create_issue"])],
        tool_env=ToolEnv(tenant_mcp_pool=tenant),
    )
    assert registry.get("mcp:github.create_issue") is not None
    assert registry.get("mcp:github.delete_repo") is None


@pytest.mark.asyncio
async def test_tenant_pool_only_no_platform_pool_ok() -> None:
    # mcp tool declared with only a tenant pool (no platform pool) must work.
    tenant = MCPServerPool()
    await tenant.add(
        "github",
        RecordingMCPClient(
            tools=(MCPToolDef(name="create_issue", description="", input_schema={}),)
        ),
    )
    registry = await build_tool_registry([MCPToolSpec()], tool_env=ToolEnv(tenant_mcp_pool=tenant))
    assert registry.get("mcp:github.create_issue") is not None


@pytest.mark.asyncio
async def test_name_collision_platform_wins() -> None:
    platform = MCPServerPool()
    await platform.add(
        "github",
        RecordingMCPClient(
            tools=(MCPToolDef(name="from_platform", description="", input_schema={}),)
        ),
    )
    tenant = MCPServerPool()
    await tenant.add(
        "github",
        RecordingMCPClient(
            tools=(MCPToolDef(name="from_tenant", description="", input_schema={}),)
        ),
    )
    registry = await build_tool_registry(
        [MCPToolSpec()], tool_env=ToolEnv(mcp_pool=platform, tenant_mcp_pool=tenant)
    )
    # platform registered first; the colliding tenant server is skipped.
    assert registry.get("mcp:github.from_platform") is not None
    assert registry.get("mcp:github.from_tenant") is None


@pytest.mark.asyncio
async def test_mcp_declared_but_no_pools_raises() -> None:
    with pytest.raises(AgentFactoryError, match="MCP server pool"):
        await build_tool_registry([MCPToolSpec()], tool_env=ToolEnv())


@pytest.mark.asyncio
async def test_servers_filter_restricts_to_named_servers() -> None:
    pool = MCPServerPool()
    await pool.add(
        "github",
        RecordingMCPClient(tools=(MCPToolDef(name="gh", description="", input_schema={}),)),
    )
    await pool.add(
        "linear",
        RecordingMCPClient(tools=(MCPToolDef(name="li", description="", input_schema={}),)),
    )
    registry = await build_tool_registry(
        [MCPToolSpec(servers=["github"])], tool_env=ToolEnv(mcp_pool=pool)
    )
    assert registry.get("mcp:github.gh") is not None
    assert registry.get("mcp:linear.li") is None


@pytest.mark.asyncio
async def test_empty_servers_means_all() -> None:
    pool = MCPServerPool()
    await pool.add(
        "github",
        RecordingMCPClient(tools=(MCPToolDef(name="gh", description="", input_schema={}),)),
    )
    await pool.add(
        "linear",
        RecordingMCPClient(tools=(MCPToolDef(name="li", description="", input_schema={}),)),
    )
    registry = await build_tool_registry([MCPToolSpec()], tool_env=ToolEnv(mcp_pool=pool))
    assert registry.get("mcp:github.gh") is not None
    assert registry.get("mcp:linear.li") is not None


@pytest.mark.asyncio
async def test_servers_filter_applies_to_tenant_pool() -> None:
    tenant = MCPServerPool()
    await tenant.add(
        "github",
        RecordingMCPClient(tools=(MCPToolDef(name="gh", description="", input_schema={}),)),
    )
    await tenant.add(
        "postgres",
        RecordingMCPClient(tools=(MCPToolDef(name="pg", description="", input_schema={}),)),
    )
    registry = await build_tool_registry(
        [MCPToolSpec(servers=["github"])], tool_env=ToolEnv(tenant_mcp_pool=tenant)
    )
    assert registry.get("mcp:github.gh") is not None
    assert registry.get("mcp:postgres.pg") is None


@pytest.mark.asyncio
async def test_servers_filter_composes_with_platform_and_tenant() -> None:
    platform = MCPServerPool()
    await platform.add(
        "ops",
        RecordingMCPClient(tools=(MCPToolDef(name="deploy", description="", input_schema={}),)),
    )
    tenant = MCPServerPool()
    await tenant.add(
        "github",
        RecordingMCPClient(tools=(MCPToolDef(name="gh", description="", input_schema={}),)),
    )
    # select only the tenant's github; the platform ops server is excluded.
    registry = await build_tool_registry(
        [MCPToolSpec(servers=["github"])],
        tool_env=ToolEnv(mcp_pool=platform, tenant_mcp_pool=tenant),
    )
    assert registry.get("mcp:github.gh") is not None
    assert registry.get("mcp:ops.deploy") is None


@pytest.mark.asyncio
async def test_platform_reserves_name_even_when_all_its_tools_filtered() -> None:
    # Platform reserves the server NAME unconditionally even when allow_tools
    # filters out every one of its tools for this build.  A tenant server with
    # the same name must NOT be registered — otherwise a tenant could shadow a
    # platform server by crafting allow_tools to exclude all platform tools.
    platform = MCPServerPool()
    await platform.add(
        "github",
        RecordingMCPClient(
            tools=(MCPToolDef(name="platform_tool", description="", input_schema={}),)
        ),
    )
    tenant = MCPServerPool()
    await tenant.add(
        "github",
        RecordingMCPClient(
            tools=(MCPToolDef(name="tenant_tool", description="", input_schema={}),)
        ),
    )
    # allow_tools excludes the platform server's only tool; tenant has a matching name.
    registry = await build_tool_registry(
        [MCPToolSpec(allow_tools=["tenant_tool"])],
        tool_env=ToolEnv(mcp_pool=platform, tenant_mcp_pool=tenant),
    )
    # Platform reserved "github" → tenant's mcp:github.tenant_tool is NOT registered.
    assert registry.get("mcp:github.tenant_tool") is None
    assert registry.get("mcp:github.platform_tool") is None  # filtered out by allow_tools
