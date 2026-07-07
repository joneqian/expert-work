"""Integration tests for cancellation + resume sanitize — Stream E.15.

Covers test matrix #30 (cancel in-flight LLM), #31 (cancel mid-tool),
#32 (no cross-run bleed at the graph level), and #41 (resume sanitize
of dangling tool_calls).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from expert_work.runtime.cancellation import (
    CANCELLATION_TOKEN_KEY,
    CancellationToken,
    RunCancelledError,
)
from expert_work.runtime.checkpointer import make_checkpointer
from orchestrator import (
    GraphRunner,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    build_react_graph,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _SlowLLM:
    """LLMCaller that blocks ``delay_s`` before replying."""

    delay_s: float = 5.0
    started: asyncio.Event = field(default_factory=asyncio.Event)
    completed: bool = False

    async def __call__(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
    ) -> AIMessage:
        del messages, tools
        self.started.set()
        await asyncio.sleep(self.delay_s)
        self.completed = True
        return AIMessage(content="too late", id="ai-late")


@dataclass
class _ToolCallThenDoneLLM:
    """First call → an AIMessage requesting ``tool_name``; then text."""

    tool_name: str

    calls: int = 0

    async def __call__(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
    ) -> AIMessage:
        del messages, tools
        idx = self.calls
        self.calls += 1
        if idx == 0:
            return AIMessage(
                content="",
                tool_calls=[
                    {"id": "tc-1", "name": self.tool_name, "args": {}, "type": "tool_call"}
                ],
                id="ai-1",
            )
        return AIMessage(content="finished", id="ai-2")


@dataclass
class _SlowTool:
    """Tool that blocks ``delay_s`` inside ``call``."""

    name: str = "slow_tool"
    delay_s: float = 5.0
    started: asyncio.Event = field(default_factory=asyncio.Event)
    completed: bool = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description="a slow tool")

    async def call(self, args: Mapping[str, Any], *, ctx: Any) -> ToolResult:
        del args, ctx
        self.started.set()
        await asyncio.sleep(self.delay_s)
        self.completed = True
        return ToolResult(content="late tool result")


@dataclass
class _EchoLLM:
    async def __call__(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
    ) -> AIMessage:
        del tools
        return AIMessage(content=f"echo: {messages[-1].content}", id="ai-echo")


def _config(token: CancellationToken | None = None) -> RunnableConfig:
    configurable: dict[str, Any] = {"thread_id": uuid4().hex}
    if token is not None:
        configurable[CANCELLATION_TOKEN_KEY] = token
    return {"configurable": configurable}


def _initial(prompt: str = "hi") -> dict[str, Any]:
    return {
        "messages": [HumanMessage(content=prompt)],
        "step_count": 0,
        "max_steps": 5,
    }


# ---------------------------------------------------------------------------
# Test matrix #30 — cancel an in-flight LLM call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_interrupts_inflight_llm_call() -> None:
    """A cancel mid-LLM-call aborts the run within a fraction of a
    second, not after the (5 s) call would have completed."""
    token = CancellationToken()
    llm = _SlowLLM(delay_s=5.0)

    async def _cancel_soon() -> None:
        await llm.started.wait()
        await asyncio.sleep(0.05)
        token.cancel()

    async with make_checkpointer("memory") as cp:
        runner = GraphRunner(checkpointer=cp)
        graph = runner.compile(build_react_graph(llm_caller=llm, tool_registry=ToolRegistry()))
        start = time.monotonic()
        with pytest.raises(RunCancelledError):
            await asyncio.gather(
                graph.ainvoke(_initial(), config=_config(token)),
                _cancel_soon(),
            )
        elapsed = time.monotonic() - start

    assert elapsed < 1.5, f"cancel took {elapsed:.3f}s — LLM was not interrupted"
    assert llm.completed is False


@pytest.mark.asyncio
async def test_cancel_before_run_stops_at_node_entry() -> None:
    """A token already cancelled before the graph starts trips the
    agent node's entry checkpoint — the LLM is never called."""
    token = CancellationToken()
    token.cancel()
    llm = _SlowLLM(delay_s=5.0)

    async with make_checkpointer("memory") as cp:
        runner = GraphRunner(checkpointer=cp)
        graph = runner.compile(build_react_graph(llm_caller=llm, tool_registry=ToolRegistry()))
        with pytest.raises(RunCancelledError):
            await graph.ainvoke(_initial(), config=_config(token))

    assert llm.started.is_set() is False


@pytest.mark.asyncio
async def test_no_token_in_config_runs_normally() -> None:
    """No token in config → a fresh never-cancelled token → the graph
    behaves exactly as pre-E.15."""
    async with make_checkpointer("memory") as cp:
        runner = GraphRunner(checkpointer=cp)
        graph = runner.compile(
            build_react_graph(llm_caller=_EchoLLM(), tool_registry=ToolRegistry())
        )
        final = await graph.ainvoke(_initial("ping"), config=_config())

    assert final["messages"][-1].content == "echo: ping"


# ---------------------------------------------------------------------------
# Test matrix #31 — cancel mid-tool-dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_interrupts_inflight_tool_call() -> None:
    """A cancel while a slow tool is running aborts it promptly — the
    cancellation is not swallowed into a ToolMessage."""
    token = CancellationToken()
    slow_tool = _SlowTool(delay_s=5.0)
    registry = ToolRegistry()
    registry.register(slow_tool)
    llm = _ToolCallThenDoneLLM(tool_name=slow_tool.name)

    async def _cancel_soon() -> None:
        await slow_tool.started.wait()
        await asyncio.sleep(0.05)
        token.cancel()

    async with make_checkpointer("memory") as cp:
        runner = GraphRunner(checkpointer=cp)
        graph = runner.compile(build_react_graph(llm_caller=llm, tool_registry=registry))
        start = time.monotonic()
        with pytest.raises(RunCancelledError):
            await asyncio.gather(
                graph.ainvoke(_initial(), config=_config(token)),
                _cancel_soon(),
            )
        elapsed = time.monotonic() - start

    assert elapsed < 1.5, f"cancel took {elapsed:.3f}s — tool was not interrupted"
    assert slow_tool.completed is False


# ---------------------------------------------------------------------------
# Test matrix #32 — cancellation does not bleed across runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancelled_run_does_not_affect_next_run() -> None:
    """One run cancelled mid-flight; a second run on the same graph with
    its own token completes normally."""
    registry = ToolRegistry()

    async with make_checkpointer("memory") as cp:
        runner = GraphRunner(checkpointer=cp)

        # Run 1 — cancelled.
        token_1 = CancellationToken()
        slow = _SlowLLM(delay_s=5.0)
        graph_1 = runner.compile(build_react_graph(llm_caller=slow, tool_registry=registry))

        async def _cancel_soon() -> None:
            await slow.started.wait()
            await asyncio.sleep(0.05)
            token_1.cancel()

        with pytest.raises(RunCancelledError):
            await asyncio.gather(
                graph_1.ainvoke(_initial(), config=_config(token_1)),
                _cancel_soon(),
            )

        # Run 2 — fresh token, normal completion.
        graph_2 = runner.compile(build_react_graph(llm_caller=_EchoLLM(), tool_registry=registry))
        final = await graph_2.ainvoke(_initial("second"), config=_config(CancellationToken()))

    assert final["messages"][-1].content == "echo: second"


# ---------------------------------------------------------------------------
# Test matrix #41 — resume sanitize of dangling tool_calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sanitize_thread_injects_placeholders_for_orphans() -> None:
    """A checkpoint with an AIMessage whose tool_calls were never
    dispatched (cancelled mid-window) is repaired with one placeholder
    ToolMessage per orphan."""
    async with make_checkpointer("memory") as cp:
        runner = GraphRunner(checkpointer=cp)
        graph = runner.compile(
            build_react_graph(llm_caller=_EchoLLM(), tool_registry=ToolRegistry())
        )
        config = _config()

        orphan = AIMessage(
            content="",
            tool_calls=[
                {"id": "T1", "name": "a", "args": {}, "type": "tool_call"},
                {"id": "T2", "name": "b", "args": {}, "type": "tool_call"},
            ],
            id="ai-orphan",
        )
        await graph.aupdate_state(
            config, {"messages": [HumanMessage(content="hi"), orphan]}, as_node="agent"
        )

        injected = await runner.sanitize_thread(graph, config)
        assert injected == 2

        snapshot = await graph.aget_state(config)
        messages = snapshot.values["messages"]
        tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
        assert {m.tool_call_id for m in tool_messages} == {"T1", "T2"}
        assert all(m.status == "error" for m in tool_messages)

        # Idempotent — a second pass finds nothing left to repair.
        assert await runner.sanitize_thread(graph, config) == 0


@pytest.mark.asyncio
async def test_sanitize_thread_noop_on_valid_history() -> None:
    """A thread whose tool_calls are all answered needs no repair."""
    async with make_checkpointer("memory") as cp:
        runner = GraphRunner(checkpointer=cp)
        graph = runner.compile(
            build_react_graph(llm_caller=_EchoLLM(), tool_registry=ToolRegistry())
        )
        config = _config()

        ai = AIMessage(
            content="",
            tool_calls=[{"id": "T1", "name": "a", "args": {}, "type": "tool_call"}],
            id="ai-1",
        )
        answered = ToolMessage(content="ok", tool_call_id="T1")
        await graph.aupdate_state(
            config, {"messages": [HumanMessage(content="hi"), ai, answered]}, as_node="agent"
        )

        assert await runner.sanitize_thread(graph, config) == 0


@pytest.mark.asyncio
async def test_sanitize_thread_noop_on_fresh_thread() -> None:
    """A thread with no checkpoint at all sanitises to zero."""
    async with make_checkpointer("memory") as cp:
        runner = GraphRunner(checkpointer=cp)
        graph = runner.compile(
            build_react_graph(llm_caller=_EchoLLM(), tool_registry=ToolRegistry())
        )
        assert await runner.sanitize_thread(graph, _config()) == 0
