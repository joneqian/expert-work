"""Stream CM-9 — limit-hit effort escalation (Mini-ADR CM-J4/J5).

The agent node serves a turn from the pre-built higher-effort caller
when either signal fires: the loop-detection middleware tripped on the
previous response (``escalate_next``), or the step budget crossed 75%
of ``max_steps``. No escalated caller wired → byte-for-byte the
existing path.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from helix_agent.protocol import ModelSpec
from helix_agent.runtime.checkpointer import make_checkpointer
from helix_agent.runtime.middleware import LoopDetectionMiddleware, MiddlewareChain
from orchestrator import AgentState, GraphRunner, ToolRegistry, build_react_graph
from orchestrator.agent_factory import _escalated_model
from orchestrator.tools.registry import ToolContext, ToolResult, ToolSpec


@dataclass
class _CountingLLM:
    """Scripted caller that records how many calls it served."""

    responses: list[AIMessage]
    label: str = "base"
    calls: int = 0

    async def __call__(
        self, *, messages: Sequence[BaseMessage], tools: Sequence[ToolSpec]
    ) -> AIMessage:
        del messages, tools
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
async def test_budget_signal_switches_to_escalated_caller() -> None:
    base = _CountingLLM(responses=[AIMessage(content="base done")])
    escalated = _CountingLLM(responses=[AIMessage(content="escalated done")], label="esc")
    graph = build_react_graph(
        llm_caller=base,
        escalated_llm_caller=escalated,
        tool_registry=ToolRegistry(),
    )
    # step_count 3 of max_steps 4 → 3*4 >= 4*3 → budget signal fires.
    state = await _invoke(
        graph,
        {"messages": [HumanMessage(content="go")], "step_count": 3, "max_steps": 4},
        "esc-budget",
    )
    assert escalated.calls == 1
    assert base.calls == 0
    assert str(state["messages"][-1].content) == "escalated done"


@pytest.mark.asyncio
async def test_loop_trip_arms_escalation_for_the_next_run() -> None:
    tool = _EchoTool()
    registry = ToolRegistry()
    registry.register(tool)
    # Three identical tool calls trip the loop middleware on the 3rd
    # response — it clears the tool_calls, so the run ends with the
    # reminder and ``escalate_next`` armed in the checkpoint. The NEXT
    # run's first agent step serves from the escalated caller.
    base = _CountingLLM(
        responses=[
            AIMessage(content="", tool_calls=[_tc("a")]),
            AIMessage(content="", tool_calls=[_tc("b")]),
            AIMessage(content="", tool_calls=[_tc("c")]),
        ]
    )
    escalated = _CountingLLM(responses=[AIMessage(content="escalated final")], label="esc")
    graph = build_react_graph(
        llm_caller=base,
        escalated_llm_caller=escalated,
        tool_registry=registry,
        after_llm_chain=MiddlewareChain.from_middlewares(
            "after_llm_call", [LoopDetectionMiddleware()]
        ),
    )
    async with make_checkpointer("memory") as cp:
        compiled = GraphRunner(checkpointer=cp).compile(graph)
        cfg: RunnableConfig = {"configurable": {"thread_id": "esc-loop"}}
        first = await compiled.ainvoke(
            {"messages": [HumanMessage(content="go")], "step_count": 0, "max_steps": 20},
            config=cfg,
        )
        # Loop tripped: run ended on the reminder with the signal armed.
        assert first.get("escalate_next") is True
        assert escalated.calls == 0

        second = await compiled.ainvoke(
            {"messages": [HumanMessage(content="try again")], "step_count": 0},
            config=cfg,
        )
    assert escalated.calls == 1
    assert str(second["messages"][-1].content) == "escalated final"
    # Consumed signal resets (the escalated turn had no loop trip).
    assert second.get("escalate_next") is False


@pytest.mark.asyncio
async def test_no_escalated_caller_keeps_base_path() -> None:
    base = _CountingLLM(responses=[AIMessage(content="done")])
    graph = build_react_graph(llm_caller=base, tool_registry=ToolRegistry())
    state = await _invoke(
        graph,
        {"messages": [HumanMessage(content="go")], "step_count": 3, "max_steps": 4},
        "esc-none",
    )
    assert base.calls == 1
    assert str(state["messages"][-1].content) == "done"


# ---------------------------------------------------------------------------
# factory — escalated ModelSpec derivation (CM-J4)
# ---------------------------------------------------------------------------


def _model(**overrides: Any) -> ModelSpec:
    return ModelSpec.model_validate(
        {"provider": "anthropic", "name": "claude-sonnet-4-6", **overrides}
    )


def test_escalation_disabled_when_compute_controls_untouched() -> None:
    assert _escalated_model(_model()) is None


def test_escalation_ladder_steps_one_level() -> None:
    assert _escalated_model(_model(adaptive_thinking=True)).effort == "medium"
    assert _escalated_model(_model(effort="low")).effort == "medium"
    assert _escalated_model(_model(effort="medium")).effort == "high"
    assert _escalated_model(_model(effort="high")).effort == "max"


def test_escalation_capped_at_max() -> None:
    assert _escalated_model(_model(effort="max")) is None


def test_escalation_requires_catalog_effort_support() -> None:
    assert _escalated_model(_model(name="claude-haiku-4-5", adaptive_thinking=True)) is None
    # Off-catalog models are not escalated either (capability unknown).
    assert _escalated_model(_model(name="claude-custom-gw", effort="low")) is None


# ---------------------------------------------------------------------------
# CM-10 — cross-vendor escalation (Mini-ADR CM-L6)
# ---------------------------------------------------------------------------


def _vendor(provider: str, name: str, **overrides: Any) -> ModelSpec:
    return ModelSpec.model_validate({"provider": provider, "name": name, **overrides})


def test_escalation_ladder_applies_to_effort_and_budget_vendors() -> None:
    openai = _escalated_model(_vendor("openai", "gpt-5.5", effort="low"))
    assert openai is not None and openai.effort == "medium"
    qwen = _escalated_model(_vendor("qwen", "qwen3.7-max", effort="high"))
    assert qwen is not None and qwen.effort == "max"
    doubao = _escalated_model(_vendor("doubao", "doubao-seed-2.0-pro", adaptive_thinking=True))
    assert doubao is not None and doubao.effort == "medium"
    # Untouched manifests on ladder vendors stay off (CM-9 conservative default).
    assert _escalated_model(_vendor("qwen", "qwen3.7-max")) is None


def test_toggle_vendors_escalate_by_turning_thinking_on() -> None:
    # Untouched manifest -> one hop: enable thinking.
    glm = _escalated_model(_vendor("glm", "glm-5.1"))
    assert glm is not None and glm.effort == "high"
    # Already thinking -> nowhere to go.
    assert _escalated_model(_vendor("glm", "glm-5.1", effort="low")) is None
    assert _escalated_model(_vendor("kimi", "kimi-k2.6", adaptive_thinking=True)) is None


def test_no_thinking_control_models_never_escalate() -> None:
    assert _escalated_model(_vendor("deepseek", "deepseek-reasoner", effort="low")) is None
    assert _escalated_model(_vendor("qwen", "custom-gateway", effort="low")) is None
