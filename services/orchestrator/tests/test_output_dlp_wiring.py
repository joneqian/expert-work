"""Stream 7.4 — outbound DLP wired into the react graph (agent_node)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from helix_agent.common.dlp import DLP_REPLACEMENT
from helix_agent.runtime.checkpointer import make_checkpointer
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


async def _run(final_text: str, *, output_dlp: bool) -> str:
    llm = _ScriptedLLM(responses=[AIMessage(content=final_text)])
    async with make_checkpointer("memory") as cp:
        runner = GraphRunner(checkpointer=cp)
        compiled = runner.compile(
            build_react_graph(
                llm_caller=llm,
                tool_registry=ToolRegistry(),
                output_dlp=output_dlp,
            )
        )
        cfg: RunnableConfig = {"configurable": {"thread_id": str(uuid4())}}
        final = await compiled.ainvoke(
            {"messages": [HumanMessage(content="hi")], "step_count": 0, "max_steps": 5},
            config=cfg,
        )
    return str(final["messages"][-1].content)


@pytest.mark.asyncio
async def test_pii_in_response_is_redacted() -> None:
    out = await _run("Sure, his email is bob@example.com.", output_dlp=True)
    assert "bob@example.com" not in out
    assert DLP_REPLACEMENT in out


@pytest.mark.asyncio
async def test_clean_response_passes_through() -> None:
    out = await _run("The deploy finished at 14:00.", output_dlp=True)
    assert out == "The deploy finished at 14:00."


@pytest.mark.asyncio
async def test_dlp_off_lets_pii_through() -> None:
    out = await _run("his email is bob@example.com", output_dlp=False)
    assert out == "his email is bob@example.com"
