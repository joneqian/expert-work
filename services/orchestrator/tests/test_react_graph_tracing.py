"""10.1 — connected-trace child spans inside the react graph.

``agent_node`` emits one ``expert_work.orchestrator.llm_call`` span per provider
call and ``tools_node`` one ``expert_work.orchestrator.tool_call`` span per
dispatch. Driving a minimal graph under an in-memory exporter proves both
land (the session root span itself is wired in ``run_agent`` — covered in
``test_sse.py``).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from expert_work.common.observability import init_tracing
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


@pytest.fixture
def exporter() -> Iterator[InMemorySpanExporter]:
    exp = InMemorySpanExporter()
    init_tracing(
        service_name="test-orchestrator-tracing",
        env="test",
        span_processor=SimpleSpanProcessor(exp),
    )
    exp.clear()
    yield exp
    exp.clear()


@dataclass
class _EchoTool:
    name: str = "echo"
    is_read_only: bool = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description="echoes", is_read_only=self.is_read_only)

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        del ctx
        return ToolResult(content=f"{self.name}:{args.get('q', '')}")


@dataclass
class _ScriptedLLM:
    responses: list[AIMessage]
    calls: int = 0

    async def __call__(
        self, *, messages: Sequence[BaseMessage], tools: Sequence[ToolSpec]
    ) -> AIMessage:
        del messages, tools
        idx = self.calls
        self.calls += 1
        return self.responses[idx]


def _tc(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


async def _run(llm: _ScriptedLLM, registry: ToolRegistry) -> AgentState:
    async with make_checkpointer("memory") as cp:
        runner = GraphRunner(checkpointer=cp)
        compiled = runner.compile(build_react_graph(llm_caller=llm, tool_registry=registry))
        cfg: RunnableConfig = {"configurable": {"thread_id": str(uuid4())}}
        return await compiled.ainvoke(
            {"messages": [HumanMessage(content="start")], "step_count": 0, "max_steps": 5},
            config=cfg,
        )


@pytest.mark.asyncio
async def test_llm_and_tool_calls_emit_child_spans(exporter: InMemorySpanExporter) -> None:
    llm = _ScriptedLLM(
        responses=[
            AIMessage(content="", tool_calls=[_tc("echo", {"q": "hi"}, "tc-1")]),
            AIMessage(content="done"),
        ]
    )
    registry = ToolRegistry()
    registry.register(_EchoTool())

    await _run(llm, registry)

    names = [s.name for s in exporter.get_finished_spans()]
    # Two agent_node turns → two llm_call spans; one dispatch → one tool_call.
    assert names.count("expert_work.orchestrator.llm_call") == 2
    assert names.count("expert_work.orchestrator.tool_call") == 1

    tool_span = next(
        s for s in exporter.get_finished_spans() if s.name == "expert_work.orchestrator.tool_call"
    )
    assert tool_span.attributes is not None
    assert tool_span.attributes["tool"] == "echo"
