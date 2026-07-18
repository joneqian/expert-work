"""Unit tests for per-tool Prometheus metrics (Stream TE-3).

``_dispatch_tool`` increments ``expert_work_tool_call_total{tool,outcome}`` and
observes ``expert_work_tool_latency_seconds{tool}`` on every dispatch, with
``outcome`` in {ok, error, blocked}. Metrics are unconditional (no tenant
needed). Each test uses a unique tool name so the global REGISTRY counters
don't collide across tests.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import pytest
from prometheus_client import REGISTRY

from orchestrator import ToolContext, ToolRegistry, ToolResult, ToolSpec
from orchestrator.graph_builder.builder import _dispatch_tool

pytestmark = pytest.mark.asyncio


class _EchoTool:
    def __init__(self, spec: ToolSpec) -> None:
        self.spec = spec

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        del args, ctx
        return ToolResult(content="ok")


class _BoomTool:
    def __init__(self, spec: ToolSpec) -> None:
        self.spec = spec

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        del args, ctx
        raise ValueError("boom")


class _BlockingChain:
    async def invoke(self, ctx: Any, terminal: Any) -> None:
        del ctx, terminal
        raise PermissionError("blocked")


def _registry(*tools: Any) -> ToolRegistry:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return reg


def _ctx() -> ToolContext:
    return ToolContext(tenant_id=uuid4(), run_id=uuid4())


def _call(name: str) -> dict[str, Any]:
    return {"name": name, "id": "c1", "args": {}}


def _calls(tool: str, outcome: str) -> float:
    return (
        REGISTRY.get_sample_value(
            "expert_work_tool_call_total", labels={"tool": tool, "outcome": outcome}
        )
        or 0.0
    )


def _latency_count(tool: str) -> float:
    return (
        REGISTRY.get_sample_value("expert_work_tool_latency_seconds_count", labels={"tool": tool})
        or 0.0
    )


async def test_success_increments_ok_and_latency() -> None:
    name = "metric_ok_tool"
    before_ok = _calls(name, "ok")
    before_lat = _latency_count(name)
    await _dispatch_tool(
        _call(name),
        _registry(_EchoTool(ToolSpec(name=name, description="d"))),
        _ctx(),
        before_tool_dispatch_chain=None,
        audit_logger=None,
    )
    assert _calls(name, "ok") == before_ok + 1
    assert _latency_count(name) == before_lat + 1


async def test_tool_error_increments_error_outcome() -> None:
    name = "metric_err_tool"
    before = _calls(name, "error")
    await _dispatch_tool(
        _call(name),
        _registry(_BoomTool(ToolSpec(name=name, description="d"))),
        _ctx(),
        before_tool_dispatch_chain=None,
        audit_logger=None,
    )
    assert _calls(name, "error") == before + 1


async def test_unknown_tool_increments_error_outcome() -> None:
    name = "metric_ghost_tool"
    before = _calls(name, "error")
    await _dispatch_tool(
        _call(name),
        _registry(),  # empty → ToolNotFoundError
        _ctx(),
        before_tool_dispatch_chain=None,
        audit_logger=None,
    )
    assert _calls(name, "error") == before + 1


async def test_middleware_block_increments_blocked_outcome() -> None:
    name = "metric_blocked_tool"
    before = _calls(name, "blocked")
    await _dispatch_tool(
        _call(name),
        _registry(_EchoTool(ToolSpec(name=name, description="d"))),
        _ctx(),
        before_tool_dispatch_chain=_BlockingChain(),
        audit_logger=None,
    )
    assert _calls(name, "blocked") == before + 1


async def test_mcp_tool_label_collapses_to_server() -> None:
    # MCP tool names (mcp__<server>__<tool>) are externally defined → the metric
    # label collapses to mcp:<server> to bound cardinality. Two different
    # tools on the same server share one series.
    server_label = "mcp:gh"
    before = _calls(server_label, "ok")
    for tool_name in ("mcp__gh__create_issue", "mcp__gh__list_repos"):
        await _dispatch_tool(
            _call(tool_name),
            _registry(_EchoTool(ToolSpec(name=tool_name, description="d"))),
            _ctx(),
            before_tool_dispatch_chain=None,
            audit_logger=None,
        )
    # both dispatches counted under the single collapsed server label ...
    assert _calls(server_label, "ok") == before + 2
    # ... and the per-tool labels were never created
    assert _calls("mcp__gh__create_issue", "ok") == 0.0


async def test_metrics_emit_without_audit_logger_or_tenant() -> None:
    # Metrics are unconditional — they fire even when audit would be skipped.
    name = "metric_no_tenant_tool"
    before = _calls(name, "ok")
    await _dispatch_tool(
        _call(name),
        _registry(_EchoTool(ToolSpec(name=name, description="d"))),
        ToolContext(tenant_id=None, run_id=None),  # audit would skip
        before_tool_dispatch_chain=None,
        audit_logger=None,
    )
    assert _calls(name, "ok") == before + 1
