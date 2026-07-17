"""Regression guard: a streaming step's token-frame `step` must equal the
authoritative `step_count` the same node reports, or the frontend's synthetic
live card never reconciles away (dup + false "interrupted"; answer-step
typewriter suppressed on multi-step runs). Guards builder.py make_token_sink."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from expert_work.runtime.checkpointer import make_checkpointer
from orchestrator import GraphRunner, ToolRegistry, build_react_graph
from orchestrator.graph_builder._config import TOKEN_SINK_KEY
from orchestrator.llm.providers._streaming import LLMDelta


@dataclass
class _StreamingLLM:
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


@pytest.mark.asyncio
async def test_token_frame_step_matches_authoritative_step_count() -> None:
    token_frames: list[dict[str, Any]] = []

    async def capture(frame: dict[str, Any]) -> None:
        token_frames.append(frame)

    llm = _StreamingLLM(responses=[AIMessage(content="final answer")])
    async with make_checkpointer("memory") as cp:
        runner = GraphRunner(checkpointer=cp)
        compiled = runner.compile(build_react_graph(llm_caller=llm, tool_registry=ToolRegistry()))
        cfg: RunnableConfig = {"configurable": {"thread_id": str(uuid4()), TOKEN_SINK_KEY: capture}}
        auth_step_counts: list[Any] = []
        async for chunk in compiled.astream(
            {"messages": [HumanMessage(content="hi")], "step_count": 0, "max_steps": 5},
            config=cfg,
            stream_mode="updates",
        ):
            for _node, ch in chunk.items():
                if isinstance(ch, dict) and "messages" in ch:
                    auth_step_counts.append(ch.get("step_count"))

    assert token_frames, "fake LLM did not stream — test would be hollow"
    token_steps = {f["step"] for f in token_frames}
    auth_steps = set(auth_step_counts)
    assert token_steps == auth_steps, (
        f"token-frame step {token_steps} != authoritative step_count {auth_steps} "
        "— synthetic live card never reconciles (reconcile off-by-one)"
    )
