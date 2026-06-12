"""Unit tests for :class:`FindToolsTool` — Stream TE-6 (tool RAG meta-tool)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pytest

from orchestrator import (
    FindToolsTool,
    ToolContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)


@dataclass
class _DummyTool:
    spec: ToolSpec

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        del ctx
        return ToolResult(content=f"called with {dict(args)}")


def _deferred(name: str, description: str) -> _DummyTool:
    return _DummyTool(spec=ToolSpec(name=name, description=description))


def _registry_with_deferred() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_deferred("active", "an active tool"))
    registry.register(_deferred("github_issue", "create a github issue"), deferred=True)
    registry.register(_deferred("github_pr", "open a github pull request"), deferred=True)
    return registry


def test_spec_has_query_parameter() -> None:
    tool = FindToolsTool(registry=ToolRegistry())
    spec = tool.spec
    assert spec.name == "find_tools"
    assert spec.parameters["required"] == ["query"]
    assert "query" in spec.parameters["properties"]
    assert spec.is_read_only is False


@pytest.mark.asyncio
async def test_call_returns_matches_and_promotes() -> None:
    tool = FindToolsTool(registry=_registry_with_deferred())
    result = await tool.call({"query": "github"}, ctx=ToolContext())
    assert "github_issue" in result.content
    assert "github_pr" in result.content
    # HX-12: results are relevance-ranked, so compare as a set.
    assert set(result.state_updates["promoted_tools"]) == {"github_issue", "github_pr"}


@pytest.mark.asyncio
async def test_call_empty_query_raises() -> None:
    tool = FindToolsTool(registry=_registry_with_deferred())
    with pytest.raises(ValueError, match="non-empty"):
        await tool.call({"query": "   "}, ctx=ToolContext())
    with pytest.raises(ValueError, match="non-empty"):
        await tool.call({}, ctx=ToolContext())


@pytest.mark.asyncio
async def test_call_no_match_returns_guidance_and_empty_promotion() -> None:
    tool = FindToolsTool(registry=_registry_with_deferred())
    result = await tool.call({"query": "zzqx"}, ctx=ToolContext())
    # HX-12: zero hits return guidance, not a dead-end placeholder.
    assert "No matching tools found" in result.content
    assert "select:" in result.content
    assert result.state_updates["promoted_tools"] == []


@pytest.mark.asyncio
async def test_call_select_syntax() -> None:
    tool = FindToolsTool(registry=_registry_with_deferred())
    result = await tool.call({"query": "select:github_pr"}, ctx=ToolContext())
    assert result.state_updates["promoted_tools"] == ["github_pr"]


# ─── Stream HX-12 — ranked retrieval + listing hardening ──────────────


@pytest.mark.asyncio
async def test_natural_language_query_ranks_best_match_first() -> None:
    registry = ToolRegistry()
    registry.register(
        _deferred("calendar_create_event", "Create a new event on the user's calendar"),
        deferred=True,
    )
    registry.register(
        _deferred("calendar_list", "List upcoming calendar entries"),
        deferred=True,
    )
    registry.register(
        _deferred("send_email", "Send an email message"),
        deferred=True,
    )
    tool = FindToolsTool(registry=registry)
    result = await tool.call({"query": "create a calendar event"}, ctx=ToolContext())
    promoted = result.state_updates["promoted_tools"]
    assert promoted[0] == "calendar_create_event"
    assert "send_email" not in promoted


@pytest.mark.asyncio
async def test_chinese_query_matches_chinese_description() -> None:
    registry = ToolRegistry()
    registry.register(_deferred("doc_search", "搜索公司内部文档并返回摘要"), deferred=True)
    registry.register(_deferred("send_email", "发送电子邮件"), deferred=True)
    tool = FindToolsTool(registry=registry)
    result = await tool.call({"query": "搜索文档"}, ctx=ToolContext())
    promoted = result.state_updates["promoted_tools"]
    assert promoted and promoted[0] == "doc_search"


@pytest.mark.asyncio
async def test_listing_shows_source_and_truncates_description() -> None:
    registry = ToolRegistry()
    long_desc = "word " * 200  # 1000 chars
    registry.register(
        _deferred("github_issue", long_desc),
        deferred=True,
        source="mcp:github",
    )
    tool = FindToolsTool(registry=registry)
    result = await tool.call({"query": "github issue"}, ctx=ToolContext())
    assert "[mcp:github]" in result.content
    assert "…" in result.content
    # listing line is bounded; the full description never appears verbatim
    assert long_desc not in result.content


@pytest.mark.asyncio
async def test_regex_query_still_works_when_bm25_misses() -> None:
    registry = ToolRegistry()
    registry.register(_deferred("alpha_beta", "does things"), deferred=True)
    tool = FindToolsTool(registry=registry)
    # ``^alpha`` has zero lexical overlap as tokens but matches as regex.
    result = await tool.call({"query": "^alpha"}, ctx=ToolContext())
    assert result.state_updates["promoted_tools"] == ["alpha_beta"]
