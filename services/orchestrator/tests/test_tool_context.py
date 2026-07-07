"""Unit tests for ``_build_tool_context`` — Stream J.4 (cancellation token)."""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from expert_work.runtime.cancellation import CANCELLATION_TOKEN_KEY, CancellationToken
from orchestrator.graph_builder.builder import _build_tool_context


def test_build_tool_context_carries_run_cancellation_token() -> None:
    # The run's CancellationToken in config["configurable"] reaches the
    # ToolContext — so a tool (J.4 SubAgentTool) can thread it into work
    # it spawns.
    token = CancellationToken()
    config: RunnableConfig = {"configurable": {CANCELLATION_TOKEN_KEY: token}}

    ctx = _build_tool_context(config)

    assert ctx.cancellation_token is token


def test_build_tool_context_supplies_fresh_token_when_absent() -> None:
    # No token in config (dev / unit-test path) → a fresh, never-cancelled
    # token, so ``ctx.cancellation_token`` is always populated.
    ctx = _build_tool_context({"configurable": {}})

    assert isinstance(ctx.cancellation_token, CancellationToken)
    assert not ctx.cancellation_token.cancelled()
