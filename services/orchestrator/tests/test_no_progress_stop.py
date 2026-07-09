"""No-progress stop — graceful wrap-up when the agent is stuck in a loop.

The loop-detection middleware already flags identical tool-call repeats and
arms ``escalate_next`` (one higher-effort turn to break out). This extends
that signal: agent_node accumulates a ``no_progress_streak`` across
consecutive loop trips and, once it reaches the manifest's
``policies.max_no_progress`` (0 = off), forces the same tool-less graceful
wrap-up ``max_steps`` uses — stopping ~N steps early instead of grinding to
``max_steps`` while making no progress.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from expert_work.runtime.checkpointer import make_checkpointer
from expert_work.runtime.middleware import LoopDetectionMiddleware, MiddlewareChain
from orchestrator import AgentState, GraphRunner, ToolRegistry, build_react_graph
from orchestrator.tools.registry import ToolContext, ToolResult, ToolSpec


@dataclass
class _ScriptedLLM:
    responses: list[AIMessage]
    calls: int = 0
    seen_tool_counts: list[int] = field(default_factory=list)

    async def __call__(
        self, *, messages: Sequence[BaseMessage], tools: Sequence[ToolSpec]
    ) -> AIMessage:
        del messages
        self.seen_tool_counts.append(len(list(tools)))
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


@dataclass
class _EchoTool:
    name: str = "probe"
    dispatched: int = 0

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description="probe")

    async def call(self, args: dict[str, Any], *, ctx: ToolContext) -> ToolResult:
        del args, ctx
        self.dispatched += 1
        return ToolResult(content="ok")


def _tc(call_id: str) -> dict[str, Any]:
    return {"name": "probe", "args": {"q": "same"}, "id": call_id, "type": "tool_call"}


async def _invoke(graph, payload: dict[str, Any], thread_id: str) -> AgentState:
    async with make_checkpointer("memory") as cp:
        compiled = GraphRunner(checkpointer=cp).compile(graph)
        cfg: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        return await compiled.ainvoke(payload, config=cfg)


@pytest.mark.asyncio
async def test_streak_at_threshold_forces_toolless_wrapup() -> None:
    """streak >= max_no_progress → tool-less wrap-up turn, no tool dispatch.

    ``budget_exhausted`` forces ``tools=[]`` on the wrap-up turn, so the
    scripted LLM sees zero tools and the registered tool never dispatches.
    """
    tool = _EchoTool()
    registry = ToolRegistry()
    registry.register(tool)
    llm = _ScriptedLLM(responses=[AIMessage(content="here is what I have")])
    graph = build_react_graph(llm_caller=llm, tool_registry=registry)
    await _invoke(
        graph,
        {
            "messages": [HumanMessage(content="go")],
            "step_count": 0,
            "max_steps": 20,
            "no_progress_streak": 2,
            "max_no_progress": 2,
        },
        "nps-stuck",
    )
    assert llm.seen_tool_counts == [0]
    assert tool.dispatched == 0


@pytest.mark.asyncio
async def test_below_threshold_runs_normally() -> None:
    """streak < max_no_progress → no wrap-up, tools still offered."""
    tool = _EchoTool()
    registry = ToolRegistry()
    registry.register(tool)
    llm = _ScriptedLLM(responses=[AIMessage(content="done")])
    graph = build_react_graph(llm_caller=llm, tool_registry=registry)
    await _invoke(
        graph,
        {
            "messages": [HumanMessage(content="go")],
            "step_count": 0,
            "max_steps": 20,
            "no_progress_streak": 1,
            "max_no_progress": 2,
        },
        "nps-ok",
    )
    assert llm.seen_tool_counts == [1]


@pytest.mark.asyncio
async def test_disabled_when_max_no_progress_zero() -> None:
    """max_no_progress == 0 (default/off) never wraps up on streak."""
    registry = ToolRegistry()
    registry.register(_EchoTool())
    llm = _ScriptedLLM(responses=[AIMessage(content="done")])
    graph = build_react_graph(llm_caller=llm, tool_registry=registry)
    await _invoke(
        graph,
        {
            "messages": [HumanMessage(content="go")],
            "step_count": 0,
            "max_steps": 20,
            "no_progress_streak": 5,
            "max_no_progress": 0,
        },
        "nps-off",
    )
    assert llm.seen_tool_counts == [1]


@pytest.mark.asyncio
async def test_streak_accumulates_on_loop_and_resets_on_progress() -> None:
    """Loop trip increments the streak; a clean turn resets it to 0."""
    registry = ToolRegistry()
    registry.register(_EchoTool())
    # Three identical tool calls trip the loop middleware on the 3rd response.
    looping = _ScriptedLLM(
        responses=[
            AIMessage(content="", tool_calls=[_tc("a")]),
            AIMessage(content="", tool_calls=[_tc("b")]),
            AIMessage(content="", tool_calls=[_tc("c")]),
        ]
    )
    graph = build_react_graph(
        llm_caller=looping,
        tool_registry=registry,
        after_llm_chain=MiddlewareChain.from_middlewares(
            "after_llm_call", [LoopDetectionMiddleware()]
        ),
    )
    async with make_checkpointer("memory") as cp:
        compiled = GraphRunner(checkpointer=cp).compile(graph)
        cfg: RunnableConfig = {"configurable": {"thread_id": "nps-accum"}}
        first = await compiled.ainvoke(
            {
                "messages": [HumanMessage(content="go")],
                "step_count": 0,
                "max_steps": 20,
                "max_no_progress": 3,
            },
            config=cfg,
        )
        # Loop tripped this run → streak armed to 1.
        assert first.get("no_progress_streak") == 1

        # A clean run (no loop) resets the streak.
        clean = _ScriptedLLM(responses=[AIMessage(content="finally done")])
        graph2 = build_react_graph(
            llm_caller=clean,
            tool_registry=registry,
            after_llm_chain=MiddlewareChain.from_middlewares(
                "after_llm_call", [LoopDetectionMiddleware()]
            ),
        )
        compiled2 = GraphRunner(checkpointer=cp).compile(graph2)
        second = await compiled2.ainvoke(
            {"messages": [HumanMessage(content="try again")], "step_count": 0},
            config=cfg,
        )
    assert second.get("no_progress_streak") == 0
