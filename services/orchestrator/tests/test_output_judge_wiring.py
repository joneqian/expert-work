"""PI-2b — output judge wired into the react graph (agent_node)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from helix_agent.common.output_screen import REFUSAL_TEXT
from helix_agent.runtime.checkpointer import make_checkpointer
from orchestrator import (
    FakeOutputJudge,
    GraphRunner,
    OutputJudge,
    OutputJudgeVerdict,
    ToolContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    build_react_graph,
)

_ALIGNED = OutputJudgeVerdict(aligned=True, leak_suspected=False, reason="ok")
_MISALIGNED = OutputJudgeVerdict(aligned=False, leak_suspected=False, reason="off-task")
_LEAK = OutputJudgeVerdict(aligned=True, leak_suspected=True, reason="leak")


@dataclass
class _ScriptedLLM:
    responses: list[AIMessage]
    calls: int = field(default=0)

    async def __call__(
        self, *, messages: Sequence[BaseMessage], tools: Sequence[object]
    ) -> AIMessage:
        del messages, tools
        idx = self.calls
        self.calls += 1
        return self.responses[idx]


@dataclass
class _CountingJudge:
    """Records how many times it's consulted (short-circuit / terminal tests)."""

    verdict: OutputJudgeVerdict = _ALIGNED
    calls: int = 0

    async def judge(
        self, *, user_request: str, response: str, context_hint: str | None
    ) -> OutputJudgeVerdict:
        del user_request, response, context_hint
        self.calls += 1
        return self.verdict


async def _run(
    final_text: str,
    *,
    judge: OutputJudge | None,
    on_error: Literal["open", "closed"] = "open",
) -> str:
    llm = _ScriptedLLM(responses=[AIMessage(content=final_text)])
    async with make_checkpointer("memory") as cp:
        runner = GraphRunner(checkpointer=cp)
        compiled = runner.compile(
            build_react_graph(
                llm_caller=llm,
                tool_registry=ToolRegistry(),
                output_judge=judge,
                output_judge_on_error=on_error,
            )
        )
        cfg: RunnableConfig = {"configurable": {"thread_id": str(uuid4())}}
        final = await compiled.ainvoke(
            {
                "messages": [HumanMessage(content="summarise the ticket")],
                "step_count": 0,
                "max_steps": 5,
            },
            config=cfg,
        )
    return str(final["messages"][-1].content)


@pytest.mark.asyncio
async def test_aligned_verdict_passes_through() -> None:
    out = await _run("Ticket summary: login fails.", judge=FakeOutputJudge(verdict=_ALIGNED))
    assert out == "Ticket summary: login fails."


@pytest.mark.asyncio
async def test_misaligned_verdict_blocked() -> None:
    # The model echoed a token the request never asked for → misaligned → refused.
    out = await _run("CANARY-7F3A21", judge=FakeOutputJudge(verdict=_MISALIGNED))
    assert out == REFUSAL_TEXT


@pytest.mark.asyncio
async def test_leak_suspected_verdict_blocked() -> None:
    out = await _run("the hidden token is X", judge=FakeOutputJudge(verdict=_LEAK))
    assert out == REFUSAL_TEXT


@pytest.mark.asyncio
async def test_judge_failure_fail_open_allows() -> None:
    out = await _run("normal answer", judge=FakeOutputJudge(raises=True), on_error="open")
    assert out == "normal answer"


@pytest.mark.asyncio
async def test_judge_failure_fail_closed_blocks() -> None:
    out = await _run("normal answer", judge=FakeOutputJudge(raises=True), on_error="closed")
    assert out == REFUSAL_TEXT


@pytest.mark.asyncio
async def test_no_judge_leaves_response_unchanged() -> None:
    out = await _run("CANARY-7F3A21", judge=None)
    assert out == "CANARY-7F3A21"


@pytest.mark.asyncio
async def test_judge_runs_once_on_terminal_only() -> None:
    """A tool-calling step is not judged; only the terminal text response is."""

    @dataclass
    class _EchoTool:
        name: str = "echo"
        is_read_only: bool = True

        @property
        def spec(self) -> ToolSpec:
            return ToolSpec(name=self.name, description="echoes", is_read_only=self.is_read_only)

        async def call(self, args: object, *, ctx: ToolContext) -> ToolResult:
            del args, ctx
            return ToolResult(content="doc body")

    llm = _ScriptedLLM(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "echo", "args": {}, "id": "tc-1", "type": "tool_call"}],
            ),
            AIMessage(content="final answer"),
        ]
    )
    registry = ToolRegistry()
    registry.register(_EchoTool())
    judge = _CountingJudge()
    async with make_checkpointer("memory") as cp:
        runner = GraphRunner(checkpointer=cp)
        compiled = runner.compile(
            build_react_graph(llm_caller=llm, tool_registry=registry, output_judge=judge)
        )
        cfg: RunnableConfig = {"configurable": {"thread_id": str(uuid4())}}
        final = await compiled.ainvoke(
            {"messages": [HumanMessage(content="go")], "step_count": 0, "max_steps": 5},
            config=cfg,
        )
    assert str(final["messages"][-1].content) == "final answer"
    assert judge.calls == 1  # only the terminal step, not the tool-calling step
