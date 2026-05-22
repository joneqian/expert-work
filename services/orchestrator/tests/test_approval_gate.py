"""Tests for the J.8 approval gate — Stream J.8-step2 (Mini-ADR J-24).

Two layers:

* ``_approval`` helper units — ``find_approval_target`` /
  ``build_approval_request`` (pure functions, no graph).
* graph integration — a manifest-gated tool / an ``ask_for_approval``
  call pauses the run: ``tools_node`` writes ``pending_approval`` and
  the graph routes to END without dispatching.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from helix_agent.runtime.checkpointer import make_checkpointer
from orchestrator import (
    AgentState,
    GraphRunner,
    ToolContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    build_react_graph,
)
from orchestrator.graph_builder._approval import (
    build_approval_request,
    find_approval_target,
)
from orchestrator.tools.approval import ASK_FOR_APPROVAL_TOOL, AskForApprovalTool

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@dataclass
class _ScriptedLLM:
    responses: list[AIMessage]
    calls: int = 0

    async def __call__(
        self, *, messages: Sequence[BaseMessage], tools: Sequence[ToolSpec]
    ) -> AIMessage:
        idx = self.calls
        self.calls += 1
        if idx >= len(self.responses):
            raise RuntimeError(f"scripted LLM ran out at call {idx}")
        return self.responses[idx]


@dataclass
class _ScriptedTool:
    name: str
    result: str = "tool-ran"
    dispatched: int = 0

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description=f"scripted {self.name}")

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        del args, ctx
        self.dispatched += 1
        return ToolResult(content=self.result)


def _tool_call(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


async def _run(
    llm: _ScriptedLLM,
    registry: ToolRegistry,
    *,
    approval_required_tools: frozenset[str] = frozenset(),
) -> AgentState:
    async with make_checkpointer("memory") as cp:
        runner = GraphRunner(checkpointer=cp)
        compiled = runner.compile(
            build_react_graph(
                llm_caller=llm,
                tool_registry=registry,
                approval_required_tools=approval_required_tools,
            )
        )
        cfg: RunnableConfig = {"configurable": {"thread_id": "t", "run_id": "r-1"}}
        return await compiled.ainvoke(
            {
                "messages": [HumanMessage(content="start")],
                "step_count": 0,
                "max_steps": 5,
            },
            config=cfg,
        )


# ---------------------------------------------------------------------------
# _approval helper units
# ---------------------------------------------------------------------------


def test_find_approval_target_declarative_gate() -> None:
    calls = [
        _tool_call("safe_tool", {}, "tc-1"),
        _tool_call("send_email", {"to": "x"}, "tc-2"),
    ]
    target = find_approval_target(calls, frozenset({"send_email"}))
    assert target is not None
    assert target.index == 1
    assert target.is_agent_initiated is False


def test_find_approval_target_ask_for_approval() -> None:
    calls = [_tool_call(ASK_FOR_APPROVAL_TOOL, {"action_summary": "x"}, "tc-1")]
    target = find_approval_target(calls, frozenset())
    assert target is not None
    assert target.is_agent_initiated is True


def test_find_approval_target_none_when_nothing_gated() -> None:
    calls = [_tool_call("safe_tool", {}, "tc-1")]
    assert find_approval_target(calls, frozenset({"send_email"})) is None


def test_find_approval_target_returns_first_hit() -> None:
    """M0 pauses on the *first* gated call in the turn."""
    calls = [
        _tool_call("send_email", {}, "tc-1"),
        _tool_call(ASK_FOR_APPROVAL_TOOL, {}, "tc-2"),
    ]
    target = find_approval_target(calls, frozenset({"send_email"}))
    assert target is not None
    assert target.index == 0


def test_build_approval_request_policy_gate() -> None:
    calls = [_tool_call("send_email", {"to": "ops@x.com"}, "tc-1")]
    target = find_approval_target(calls, frozenset({"send_email"}))
    assert target is not None
    req = build_approval_request(target, thread_id="r-1", timeout_s=3600)
    assert req.reason_kind == "policy_gate"
    assert req.proposed_args == {"to": "ops@x.com"}
    assert "send_email" in req.action_summary
    assert (req.timeout_at - req.requested_at).total_seconds() == 3600
    assert req.request_id.startswith("approval:")


def test_build_approval_request_agent_initiated() -> None:
    calls = [
        _tool_call(
            ASK_FOR_APPROVAL_TOOL,
            {
                "reason_kind": "approach_choice",
                "action_summary": "delete the temp dir?",
                "proposed_args": {"path": "workdir/scratch"},
            },
            "tc-1",
        )
    ]
    target = find_approval_target(calls, frozenset())
    assert target is not None
    req = build_approval_request(target, thread_id="r-1", timeout_s=86400)
    assert req.reason_kind == "approach_choice"
    assert req.action_summary == "delete the temp dir?"
    assert req.proposed_args == {"path": "workdir/scratch"}


def test_build_approval_request_unknown_reason_kind_falls_back() -> None:
    """A bogus agent-supplied reason_kind degrades to risk_confirmation."""
    calls = [_tool_call(ASK_FOR_APPROVAL_TOOL, {"reason_kind": "bogus"}, "tc-1")]
    target = find_approval_target(calls, frozenset())
    assert target is not None
    req = build_approval_request(target, thread_id="r-1", timeout_s=86400)
    assert req.reason_kind == "risk_confirmation"


def test_request_id_is_stable_for_same_inputs() -> None:
    calls = [_tool_call("send_email", {"to": "x"}, "tc-1")]
    target = find_approval_target(calls, frozenset({"send_email"}))
    assert target is not None
    a = build_approval_request(target, thread_id="r-1", timeout_s=60)
    b = build_approval_request(target, thread_id="r-1", timeout_s=60)
    assert a.request_id == b.request_id


# ---------------------------------------------------------------------------
# ask_for_approval tool spec
# ---------------------------------------------------------------------------


def test_ask_for_approval_tool_spec() -> None:
    spec = AskForApprovalTool().spec
    assert spec.name == ASK_FOR_APPROVAL_TOOL
    assert "reason_kind" in spec.parameters["properties"]
    assert spec.parameters["required"] == ["reason_kind", "action_summary"]


# ---------------------------------------------------------------------------
# graph integration — the gate pauses the run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_declarative_gate_pauses_run() -> None:
    """A tool named in ``approval_required_tools`` pauses before dispatch."""
    llm = _ScriptedLLM(
        responses=[
            AIMessage(content="", tool_calls=[_tool_call("send_email", {"to": "x"}, "tc-1")]),
        ]
    )
    registry = ToolRegistry()
    email_tool = _ScriptedTool(name="send_email")
    registry.register(email_tool)

    state = await _run(llm, registry, approval_required_tools=frozenset({"send_email"}))

    pending = state.get("pending_approval")
    assert pending is not None
    assert pending.reason_kind == "policy_gate"
    # The gated tool was NOT dispatched — the run paused first.
    assert email_tool.dispatched == 0
    # Exactly one LLM call: agent → tools(pause) → END.
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_ungated_tool_runs_normally() -> None:
    """A tool NOT in the gate list dispatches + the loop continues."""
    llm = _ScriptedLLM(
        responses=[
            AIMessage(content="", tool_calls=[_tool_call("search", {"q": "x"}, "tc-1")]),
            AIMessage(content="done"),
        ]
    )
    registry = ToolRegistry()
    search_tool = _ScriptedTool(name="search")
    registry.register(search_tool)

    state = await _run(llm, registry, approval_required_tools=frozenset({"send_email"}))

    assert state.get("pending_approval") is None
    assert search_tool.dispatched == 1
    assert state["messages"][-1].content == "done"


@pytest.mark.asyncio
async def test_ask_for_approval_call_pauses_run() -> None:
    """An ``ask_for_approval`` call pauses even with no declarative gate."""
    llm = _ScriptedLLM(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    _tool_call(
                        ASK_FOR_APPROVAL_TOOL,
                        {"reason_kind": "risk_confirmation", "action_summary": "ok?"},
                        "tc-1",
                    )
                ],
            ),
        ]
    )
    registry = ToolRegistry()
    registry.register(AskForApprovalTool())

    state = await _run(llm, registry)

    pending = state.get("pending_approval")
    assert pending is not None
    assert pending.reason_kind == "risk_confirmation"
    assert pending.action_summary == "ok?"
