"""Unit tests for :func:`build_react_graph` (Stream E.6).

Uses an in-memory checkpointer + a scripted ``LLMCaller`` that returns
a predetermined sequence of ``AIMessage`` values. No real LLM call,
no middleware chain wired — this PR is about loop / dispatch /
error-wrap mechanics; middleware integration follows in E.11.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from expert_work.runtime.checkpointer import make_checkpointer
from orchestrator import (
    AgentState,
    GraphRunner,
    ToolContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    build_react_graph,
)

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


@dataclass
class _ScriptedLLM:
    """LLMCaller stub: returns ``responses[call_index]`` on each invocation."""

    responses: list[AIMessage]
    calls: int = 0

    async def __call__(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
    ) -> AIMessage:
        idx = self.calls
        self.calls += 1
        if idx >= len(self.responses):
            raise RuntimeError(f"scripted LLM ran out of responses at call {idx}")
        return self.responses[idx]


@dataclass
class _ScriptedTool:
    """Tool stub: returns ``result``, or raises ``exc`` if set."""

    name: str
    result: str = ""
    exc: Exception | None = None

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description=f"scripted {self.name}")

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        del ctx
        if self.exc is not None:
            raise self.exc
        return ToolResult(content=self.result)


def _tool_call(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    """Build the ``tool_calls`` entry LangChain expects on AIMessage."""
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


async def _run_graph(
    llm: _ScriptedLLM,
    registry: ToolRegistry,
    *,
    max_steps: int = 5,
    thread_id: str = "test-thread",
) -> AgentState:
    async with make_checkpointer("memory") as cp:
        runner = GraphRunner(checkpointer=cp)
        compiled = runner.compile(build_react_graph(llm_caller=llm, tool_registry=registry))
        cfg: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        result = await compiled.ainvoke(
            {
                "messages": [HumanMessage(content="start")],
                "step_count": 0,
                "max_steps": max_steps,
            },
            config=cfg,
        )
        return result


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_step_final_answer() -> None:
    """LLM returns text only on the first call → ReAct loop ends after one
    agent step."""
    llm = _ScriptedLLM(responses=[AIMessage(content="all done")])
    registry = ToolRegistry()
    state = await _run_graph(llm, registry)
    assert llm.calls == 1
    assert state["step_count"] == 1
    assert state["messages"][-1].content == "all done"


@pytest.mark.asyncio
async def test_three_step_loop_tool_tool_final() -> None:
    """tool_call → tool_call → final answer (3 LLM calls, 2 tool dispatches)."""
    llm = _ScriptedLLM(
        responses=[
            AIMessage(
                content="",
                tool_calls=[_tool_call("search", {"q": "python"}, "tc-1")],
            ),
            AIMessage(
                content="",
                tool_calls=[_tool_call("search", {"q": "go"}, "tc-2")],
            ),
            AIMessage(content="found 2 results"),
        ]
    )
    registry = ToolRegistry()
    registry.register(_ScriptedTool(name="search", result="result-body"))

    state = await _run_graph(llm, registry)
    assert llm.calls == 3
    assert state["step_count"] == 3

    tool_msgs = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 2
    assert all(m.content == "result-body" for m in tool_msgs)
    assert state["messages"][-1].content == "found 2 results"


# ---------------------------------------------------------------------------
# max_steps guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_steps_graceful_wrapup_when_llm_keeps_calling_tools() -> None:
    """LLM never finalises → at the budget the loop does ONE final tool-less
    wrap-up turn instead of raising, so the run ends cleanly without discarding
    work (hermes-agent #7915). The wrap-up response has its tool_calls stripped
    so the router goes to END."""
    llm = _ScriptedLLM(
        responses=[
            AIMessage(
                content="",
                tool_calls=[_tool_call("search", {"q": str(i)}, f"tc-{i}")],
            )
            for i in range(5)
        ]
    )
    registry = ToolRegistry()
    registry.register(_ScriptedTool(name="search", result="r"))

    # No exception — the run finalises gracefully.
    state = await _run_graph(llm, registry, max_steps=3)
    # 3 normal turns (each dispatched a tool) + 1 forced tool-less wrap-up turn.
    assert llm.calls == 4
    assert state["step_count"] == 4
    # The wrap-up turn is terminal: tool_calls stripped → loop ended at END.
    last = state["messages"][-1]
    assert isinstance(last, AIMessage)
    assert not last.tool_calls


@pytest.mark.asyncio
async def test_final_at_max_steps_runs_clean() -> None:
    """LLM returns final answer on step max_steps → no MaxStepsExceededError."""
    llm = _ScriptedLLM(
        responses=[
            AIMessage(
                content="",
                tool_calls=[_tool_call("search", {"q": "1"}, "tc-1")],
            ),
            AIMessage(
                content="",
                tool_calls=[_tool_call("search", {"q": "2"}, "tc-2")],
            ),
            AIMessage(content="done"),
        ]
    )
    registry = ToolRegistry()
    registry.register(_ScriptedTool(name="search", result="r"))
    state = await _run_graph(llm, registry, max_steps=3)
    assert state["step_count"] == 3
    assert state["messages"][-1].content == "done"


# ---------------------------------------------------------------------------
# Tool error wrapper (Mini-ADR E-12)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_exception_wrapped_into_toolmessage_error() -> None:
    """Tool raise → ToolMessage(content='[tool error] ...') injected; loop continues."""
    llm = _ScriptedLLM(
        responses=[
            AIMessage(
                content="",
                tool_calls=[_tool_call("search", {"q": "x"}, "tc-1")],
            ),
            AIMessage(content="ok, gave up on that tool"),
        ]
    )
    registry = ToolRegistry()
    registry.register(_ScriptedTool(name="search", exc=RuntimeError("connection refused")))

    state = await _run_graph(llm, registry)
    assert llm.calls == 2
    tool_msgs = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].content.startswith("[tool error] RuntimeError:")
    assert "connection refused" in tool_msgs[0].content
    assert tool_msgs[0].status == "error"
    assert state["messages"][-1].content == "ok, gave up on that tool"


@pytest.mark.asyncio
async def test_unknown_tool_wrapped_into_toolmessage_error() -> None:
    """LLM calls a tool that isn't registered → ToolMessage(error) + loop continues."""
    llm = _ScriptedLLM(
        responses=[
            AIMessage(
                content="",
                tool_calls=[_tool_call("ghost_tool", {}, "tc-1")],
            ),
            AIMessage(content="never mind"),
        ]
    )
    registry = ToolRegistry()

    state = await _run_graph(llm, registry)
    assert llm.calls == 2
    tool_msgs = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    assert "ToolNotFoundError" in tool_msgs[0].content
    assert "ghost_tool" in tool_msgs[0].content
    assert tool_msgs[0].status == "error"


@pytest.mark.asyncio
async def test_long_tool_error_truncated() -> None:
    """Multi-MB exception strings get capped before injection."""
    huge_msg = "x" * 5000
    llm = _ScriptedLLM(
        responses=[
            AIMessage(
                content="",
                tool_calls=[_tool_call("search", {"q": "x"}, "tc-1")],
            ),
            AIMessage(content="ok"),
        ]
    )
    registry = ToolRegistry()
    registry.register(_ScriptedTool(name="search", exc=RuntimeError(huge_msg)))

    state = await _run_graph(llm, registry)
    tool_msgs = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    assert "[truncated]" in tool_msgs[0].content
    # Truncation cap is well under the original 5000 chars.
    assert len(tool_msgs[0].content) < 1000


# ---------------------------------------------------------------------------
# Parallel tool_calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_tool_calls_produce_separate_toolmessages() -> None:
    """LLM emits multiple tool_calls in one AIMessage → one ToolMessage each, in order."""
    llm = _ScriptedLLM(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    _tool_call("search", {"q": "a"}, "tc-1"),
                    _tool_call("search", {"q": "b"}, "tc-2"),
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    registry = ToolRegistry()
    registry.register(_ScriptedTool(name="search", result="ok"))

    state = await _run_graph(llm, registry)
    tool_msgs = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in tool_msgs] == ["tc-1", "tc-2"]
    assert state["messages"][-1].content == "done"
