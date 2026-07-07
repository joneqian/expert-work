"""Stream CM-2 — working-memory sliding-window wiring (graph-level).

Drives the window through the compiled graph at the ``agent_node`` entry:
a long over-threshold history is trimmed to first turn + recent N turns in
the prompt the LLM actually sees, tool-call pairs survive the cut, and the
checkpointed state still carries the full history (the trim is a per-turn
prompt view, never a history rewrite). A high-threshold window is a no-op.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from expert_work.runtime.checkpointer import make_checkpointer
from orchestrator import AgentState, GraphRunner, ToolRegistry, ToolSpec, build_react_graph
from orchestrator.context import WorkingWindow


@dataclass
class _ScriptedLLM:
    """Records every prompt it sees and replies with a no-tool-call message
    so the run ends after one agent step."""

    seen_prompts: list[list[BaseMessage]] = field(default_factory=list)

    async def __call__(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
    ) -> AIMessage:
        del tools
        self.seen_prompts.append(list(messages))
        return AIMessage(content="done")


def _long_history(n_turns: int, *, pad: int) -> list[BaseMessage]:
    """``n_turns`` user turns, each: Human → AI(tool_call) → Tool → AI.
    ``pad`` bytes per HumanMessage to push the estimate over threshold."""
    msgs: list[BaseMessage] = []
    for i in range(n_turns):
        call_id = f"call-{i}"
        msgs.append(HumanMessage(content=f"user-{i} " + "z" * pad))
        msgs.append(
            AIMessage(
                content="",
                tool_calls=[{"id": call_id, "name": "noop", "args": {}}],
            )
        )
        msgs.append(ToolMessage(content=f"result-{i}", tool_call_id=call_id))
        msgs.append(AIMessage(content=f"assistant-{i}"))
    return msgs


def _humans(messages: Sequence[BaseMessage]) -> list[str]:
    return [str(m.content) for m in messages if isinstance(m, HumanMessage)]


def _assert_pairs_intact(messages: Sequence[BaseMessage]) -> None:
    open_ids: set[str] = set()
    for m in messages:
        if isinstance(m, AIMessage) and m.tool_calls:
            open_ids.update(tc["id"] for tc in m.tool_calls)
        if isinstance(m, ToolMessage):
            assert m.tool_call_id in open_ids, f"dangling tool_result {m.tool_call_id}"
            open_ids.discard(m.tool_call_id)
    assert not open_ids, f"unanswered tool_use: {open_ids}"


async def _run(
    llm: _ScriptedLLM, window: WorkingWindow | None, history: list[BaseMessage]
) -> AgentState:
    async with make_checkpointer("memory") as cp:
        runner = GraphRunner(checkpointer=cp)
        compiled = runner.compile(
            build_react_graph(
                llm_caller=llm,
                tool_registry=ToolRegistry(),
                working_window=window,
            )
        )
        cfg: RunnableConfig = {"configurable": {"thread_id": str(uuid4())}}
        return await compiled.ainvoke(
            {"messages": history, "step_count": 0, "max_steps": 5},
            config=cfg,
        )


@pytest.mark.asyncio
async def test_window_trims_prompt_but_keeps_full_checkpoint() -> None:
    # 10 turns, ~800 chars each → well over a 1000*0.7 token threshold.
    history = _long_history(10, pad=800)
    llm = _ScriptedLLM()
    window = WorkingWindow(context_window=1000, threshold_pct=0.7, max_recent_turns=2)

    state = await _run(llm, window, history)

    # The LLM saw a trimmed prompt: first turn + most-recent 2 turns.
    prompt_users = _humans(llm.seen_prompts[0])
    assert prompt_users[0].startswith("user-0")
    assert prompt_users[-1].startswith("user-9")
    assert len(prompt_users) == 3
    _assert_pairs_intact(llm.seen_prompts[0])

    # The checkpointed history is untouched (all 10 user turns + the reply).
    final_users = _humans(state["messages"])
    assert len(final_users) == 10
    assert final_users[0].startswith("user-0")
    assert final_users[-1].startswith("user-9")


@pytest.mark.asyncio
async def test_window_noop_under_threshold() -> None:
    history = _long_history(10, pad=0)  # tiny → under threshold
    llm = _ScriptedLLM()
    window = WorkingWindow(context_window=100_000, threshold_pct=0.7, max_recent_turns=2)

    await _run(llm, window, history)

    # Under threshold → the full history reaches the LLM untrimmed.
    assert len(_humans(llm.seen_prompts[0])) == 10


@pytest.mark.asyncio
async def test_no_window_is_unchanged_path() -> None:
    history = _long_history(10, pad=800)
    llm = _ScriptedLLM()

    await _run(llm, None, history)

    # working_window=None → no trimming regardless of size.
    assert len(_humans(llm.seen_prompts[0])) == 10
