"""PI-2 — output screening wired into the react graph (agent_node)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from expert_work.common.output_screen import REFUSAL_TEXT
from expert_work.runtime.checkpointer import make_checkpointer
from orchestrator import GraphRunner, ToolRegistry, build_react_graph


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


async def _run(final_text: str, *, output_screen: bool) -> str:
    llm = _ScriptedLLM(responses=[AIMessage(content=final_text)])
    async with make_checkpointer("memory") as cp:
        runner = GraphRunner(checkpointer=cp)
        compiled = runner.compile(
            build_react_graph(
                llm_caller=llm,
                tool_registry=ToolRegistry(),
                output_screen=output_screen,
            )
        )
        cfg: RunnableConfig = {"configurable": {"thread_id": str(uuid4())}}
        final = await compiled.ainvoke(
            {"messages": [HumanMessage(content="hi")], "step_count": 0, "max_steps": 5},
            config=cfg,
        )
    return str(final["messages"][-1].content)


@pytest.mark.asyncio
async def test_leaking_response_is_blocked() -> None:
    # split literal so push protection sees no contiguous provider token
    leak = "Sure, the key is sk-" + "ant-api03-AbCdEf012345678901234567"
    out = await _run(leak, output_screen=True)
    assert out == REFUSAL_TEXT
    assert "sk-ant" not in out


@pytest.mark.asyncio
async def test_clean_response_passes_through() -> None:
    out = await _run("The deploy finished at 14:00.", output_screen=True)
    assert out == "The deploy finished at 14:00."


@pytest.mark.asyncio
async def test_screening_off_lets_leak_through() -> None:
    leak = "the key is sk-" + "ant-api03-AbCdEf012345678901234567"
    out = await _run(leak, output_screen=False)
    assert out == leak
