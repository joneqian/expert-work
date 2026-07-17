"""Regression guard: a streaming step's token-frame `step` must equal the
authoritative `step_count` the same node reports, or the frontend's synthetic
live card never reconciles away (dup + false "interrupted"; answer-step
typewriter suppressed on multi-step runs). Guards builder.py make_token_sink.

Covers both a single direct-answer step and a two-step tool round (the
multi-step case where the off-by-one wrongly suppresses the answer step's
live card)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from expert_work.runtime.checkpointer import make_checkpointer
from orchestrator import (
    GraphRunner,
    ToolContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    build_react_graph,
)
from orchestrator.graph_builder._config import TOKEN_SINK_KEY
from orchestrator.llm.providers._streaming import LLMDelta


@dataclass
class _StreamingLLM:
    """Scripted caller that fires ``on_delta`` (so the TokenSink publishes token
    frames) then returns ``responses[call_index]``."""

    responses: list[AIMessage]
    calls: int = field(default=0)

    async def __call__(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[object],
        on_delta: Callable[[LLMDelta], Awaitable[None]] | None = None,
    ) -> AIMessage:
        del messages, tools
        idx = self.calls
        self.calls += 1
        if on_delta is not None:
            await on_delta(LLMDelta(reasoning="thinking..."))
            await on_delta(LLMDelta(content="answer"))
        return self.responses[idx]


@dataclass
class _ScriptedTool:
    """Tool stub the tools node can execute so the graph loops back for a
    second agent step."""

    name: str
    result: str = ""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description=f"scripted {self.name}")

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        del args, ctx
        return ToolResult(content=self.result)


def _tool_call(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    """Build the ``tool_calls`` entry LangChain expects on an AIMessage."""
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


async def _run_and_collect(
    responses: list[AIMessage], registry: ToolRegistry
) -> tuple[set[int], set[int]]:
    """Drive the REAL react graph with a streaming fake LLM; return
    ``(distinct token-frame steps, distinct authoritative agent step_counts)``.

    Token frames are captured via the exact ``TOKEN_SINK_KEY`` hook production
    injects (``_config.py``); authoritative step_counts are read from the
    message-bearing ``updates`` the frontend parses into ``AgentStep``. Only the
    agent node bumps ``step_count`` (an int); the tools node's message-bearing
    update leaves it unset, so those are skipped.
    """
    token_frames: list[dict[str, Any]] = []

    async def capture(frame: dict[str, Any]) -> None:
        token_frames.append(frame)

    llm = _StreamingLLM(responses=responses)
    async with make_checkpointer("memory") as cp:
        runner = GraphRunner(checkpointer=cp)
        compiled = runner.compile(build_react_graph(llm_caller=llm, tool_registry=registry))
        cfg: RunnableConfig = {"configurable": {"thread_id": str(uuid4()), TOKEN_SINK_KEY: capture}}
        auth_step_counts: set[int] = set()
        async for chunk in compiled.astream(
            {"messages": [HumanMessage(content="hi")], "step_count": 0, "max_steps": 5},
            config=cfg,
            stream_mode="updates",
        ):
            for _node, ch in chunk.items():
                if isinstance(ch, dict) and isinstance(ch.get("step_count"), int):
                    auth_step_counts.add(ch["step_count"])

    assert token_frames, "fake LLM did not stream — test would be hollow"
    return {f["step"] for f in token_frames}, auth_step_counts


@pytest.mark.asyncio
async def test_token_frame_step_matches_step_count_direct_answer() -> None:
    token_steps, auth_steps = await _run_and_collect(
        [AIMessage(content="final answer")], ToolRegistry()
    )
    assert token_steps == auth_steps, (
        f"token-frame step {token_steps} != authoritative step_count {auth_steps} "
        "— synthetic live card never reconciles (reconcile off-by-one)"
    )


@pytest.mark.asyncio
async def test_token_frame_step_matches_step_count_across_tool_round() -> None:
    # Two agent steps: step 0 calls a tool, step 1 answers. Each step's token
    # frames must carry that step's authoritative step_count ({1, 2}) — the
    # multi-step case where the off-by-one wrongly SUPPRESSES the answer step's
    # live card (its token step collides with the prior step's settled card).
    registry = ToolRegistry()
    registry.register(_ScriptedTool(name="search", result="hits"))
    token_steps, auth_steps = await _run_and_collect(
        [
            AIMessage(content="", tool_calls=[_tool_call("search", {"q": "x"}, "tc-1")]),
            AIMessage(content="final answer"),
        ],
        registry,
    )
    assert token_steps == {1, 2}, f"expected token steps {{1, 2}}, got {token_steps}"
    assert token_steps == auth_steps, (
        f"token-frame step {token_steps} != authoritative step_count {auth_steps} "
        "— answer-step live card suppressed (reconcile off-by-one, multi-step)"
    )
