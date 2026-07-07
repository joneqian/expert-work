"""Stream L.L5 — iteration budget refund tests.

Covers the ``ToolResult.refund_iterations`` → ``tools_node`` →
``agent_node`` round-trip:

* ``ToolResult.refund_iterations`` rejects negative values at
  construction (a tool cannot reverse the polarity and consume
  budget through this channel)
* The tools node accumulates ``refund_iterations`` across the batch
  into ``step_count_refund_pending``
* The agent node subtracts the pending refund from ``step_count``
  before bumping (clamped at zero — never produces negative)
* ``update_plan`` calls do not advance the user-visible step count
  (the K.K8 closure of J.1 doesn't penalise replan housekeeping)

See [STREAM-L-DESIGN § 3.L5](../../../../docs/streams/STREAM-L-DESIGN.md)
+ Mini-ADR L-5.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from expert_work.protocol import Plan, PlanStep
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
from orchestrator.tools.update_plan import UpdatePlanTool

# ---------------------------------------------------------------------------
# ToolResult validation
# ---------------------------------------------------------------------------


def test_tool_result_rejects_negative_refund() -> None:
    """A tool cannot consume the agent's budget through this channel —
    ToolResult.__post_init__ raises on negative refund_iterations."""
    with pytest.raises(ValueError, match="refund_iterations must be >= 0"):
        ToolResult(content="x", refund_iterations=-1)


def test_tool_result_accepts_zero_refund_by_default() -> None:
    """Default ``refund_iterations=0`` is the no-refund path; the vast
    majority of tools never touch this field."""
    result = ToolResult(content="x")
    assert result.refund_iterations == 0


def test_tool_result_accepts_positive_refund() -> None:
    result = ToolResult(content="x", refund_iterations=3)
    assert result.refund_iterations == 3


# ---------------------------------------------------------------------------
# Test helpers (mirror test_react_graph.py)
# ---------------------------------------------------------------------------


@dataclass
class _ScriptedLLM:
    responses: list[AIMessage]
    calls: int = 0

    async def __call__(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
    ) -> AIMessage:
        idx = self.calls
        self.calls += 1
        if idx >= len(self.responses):
            raise RuntimeError(f"scripted LLM ran out at call {idx}")
        return self.responses[idx]


@dataclass
class _RefundTool:
    """Scripted tool that returns a successful ToolResult with the given
    ``refund``. Lets a single test exercise the refund path without
    standing up the full plan_execute graph."""

    name: str
    refund: int
    payload: str = "ok"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description=f"refunds {self.refund}")

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        del args, ctx
        return ToolResult(content=self.payload, refund_iterations=self.refund)


def _tc(name: str, call_id: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "args": args or {}, "id": call_id, "type": "tool_call"}


async def _run(
    llm: _ScriptedLLM,
    registry: ToolRegistry,
    *,
    max_steps: int = 5,
    thread_id: str = "refund-test",
) -> AgentState:
    async with make_checkpointer("memory") as cp:
        runner = GraphRunner(checkpointer=cp)
        compiled = runner.compile(build_react_graph(llm_caller=llm, tool_registry=registry))
        cfg: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        return await compiled.ainvoke(
            {
                "messages": [HumanMessage(content="start")],
                "step_count": 0,
                "max_steps": max_steps,
            },
            config=cfg,
        )


# ---------------------------------------------------------------------------
# tools_node accumulation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_refunding_tool_does_not_advance_step_count() -> None:
    """One tool refunding 1 iteration → effective step_count after the
    agent→tools→agent loop is unchanged from the pre-tool step."""
    llm = _ScriptedLLM(
        responses=[
            AIMessage(
                content="",
                tool_calls=[_tc("refund_one", "tc-1")],
            ),
            AIMessage(content="done"),
        ]
    )
    registry = ToolRegistry()
    registry.register(_RefundTool(name="refund_one", refund=1))

    state = await _run(llm, registry, thread_id="single-refund")
    # Without refund: step_count would be 2 (first agent, then second agent
    # after tools). With a refund of 1, the second agent enters with
    # step_count - refund = 1 - 1 = 0, increments to 1.
    assert state["step_count"] == 1
    assert llm.calls == 2


@pytest.mark.asyncio
async def test_tools_node_accumulates_refund_across_batch() -> None:
    """Two refunding tool calls in one batch → refund_pending = 2; the
    next agent step consumes the accumulated total. We need step_count
    to be high enough that the refund actually subtracts (clamp would
    otherwise mask accumulation — see
    ``test_step_count_never_goes_negative``)."""
    # Build a chain: agent → tools(noop) → agent → tools(refund x2) →
    # agent. By the second tools batch the step_count is 2, so a
    # refund of 2 is fully consumed (no clamping). Final step_count =
    # 2 - 2 + 1 = 1.
    llm = _ScriptedLLM(
        responses=[
            AIMessage(content="", tool_calls=[_tc("noop", "tc-0")]),
            AIMessage(
                content="",
                tool_calls=[
                    _tc("refund_one", "tc-1"),
                    _tc("refund_one", "tc-2"),
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    registry = ToolRegistry()
    registry.register(_RefundTool(name="refund_one", refund=1))
    registry.register(_RefundTool(name="noop", refund=0))

    state = await _run(llm, registry, thread_id="batch-refund")
    # 3 agent calls. Refund=2 fully consumed at the third entry
    # (step_count=2 going in → 2-2 = 0 → +1 = 1).
    assert state["step_count"] == 1
    assert llm.calls == 3


# ---------------------------------------------------------------------------
# agent_node consumption + invariants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step_count_never_goes_negative() -> None:
    """A refund larger than the current step_count clamps to 0 — the
    agent_node can never push the budget into nonsense negative
    territory even if a tool returns a giant refund."""
    llm = _ScriptedLLM(
        responses=[
            AIMessage(
                content="",
                tool_calls=[_tc("huge_refund", "tc-1")],
            ),
            AIMessage(content="done"),
        ]
    )
    registry = ToolRegistry()
    # First agent ran (step_count=1); tool refunds 50. Pre-clamp:
    # 1 - 50 = -49 → clamp to 0 → +1 → final = 1.
    registry.register(_RefundTool(name="huge_refund", refund=50))

    state = await _run(llm, registry, thread_id="huge-refund")
    assert state["step_count"] == 1


@pytest.mark.asyncio
async def test_refund_saves_agent_from_max_steps_cap() -> None:
    """A refund mid-run pushes the next agent step *under* the cap so
    the loop continues — proves consumption happens before the
    max_steps guard, not after."""
    llm = _ScriptedLLM(
        responses=[
            AIMessage(
                content="",
                tool_calls=[_tc("noop", "tc-1")],
            ),
            AIMessage(
                content="",
                tool_calls=[_tc("refund_one", "tc-2")],
            ),
            AIMessage(content="done"),
        ]
    )
    registry = ToolRegistry()
    registry.register(_RefundTool(name="noop", refund=0))
    registry.register(_RefundTool(name="refund_one", refund=1))

    # max_steps=2: without refund, third agent would trip
    # MaxStepsExceeded. With the refund, second agent's effective
    # step_count drops back from 2 to 1, the third runs fine.
    state = await _run(llm, registry, max_steps=2, thread_id="save-from-max")
    assert state["messages"][-1].content == "done"
    assert llm.calls == 3


@pytest.mark.asyncio
async def test_refund_pending_resets_after_consumption() -> None:
    """``agent_node`` writes ``step_count_refund_pending=0`` on its
    return dict — pending must not survive into the *next* turn."""
    llm = _ScriptedLLM(
        responses=[
            AIMessage(
                content="",
                tool_calls=[_tc("refund_one", "tc-1")],
            ),
            AIMessage(
                content="",
                tool_calls=[_tc("noop", "tc-2")],
            ),
            AIMessage(content="done"),
        ]
    )
    registry = ToolRegistry()
    registry.register(_RefundTool(name="refund_one", refund=1))
    registry.register(_RefundTool(name="noop", refund=0))

    state = await _run(llm, registry, thread_id="reset-after")
    # 3 agent calls; refund_one fires once (refund=1); final = 3 - 1 = 2.
    assert state["step_count"] == 2
    # The pending channel must be cleared at the end (last write came
    # from agent_node returning step_count_refund_pending=0).
    assert state.get("step_count_refund_pending", 0) == 0


# ---------------------------------------------------------------------------
# update_plan + L5 (K.K8 closure)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_plan_returns_refund_of_one() -> None:
    """``UpdatePlanTool`` declares ``refund_iterations=1`` so plan
    revisions don't burn user-visible budget. Direct unit test of the
    tool — proof that the call site (registry.py) honours the
    contract."""
    tool = UpdatePlanTool()
    initial = Plan(
        goal="Original goal",
        steps=(PlanStep(id="1", description="old step"),),
    )
    result = await tool.call(
        {"steps": ["new step"], "reason": "revised mid-run"},
        ctx=ToolContext(plan=initial),
    )
    assert result.refund_iterations == 1


@pytest.mark.asyncio
async def test_update_plan_call_does_not_increment_user_visible_step_count() -> None:
    """End-to-end: a turn where the only tool call is ``update_plan``
    leaves the user-visible step_count unchanged from before the
    call. K.K8 + L5 together — the agent can replan as many times as
    it wants without exhausting its iteration budget on housekeeping."""
    initial = Plan(
        goal="Ship the agent",
        steps=(PlanStep(id="1", description="draft"),),
    )
    llm = _ScriptedLLM(
        responses=[
            AIMessage(
                content="",
                tool_calls=[_tc("update_plan", "tc-1", {"steps": ["new"], "reason": "revise"})],
            ),
            AIMessage(content="done"),
        ]
    )
    registry = ToolRegistry()
    registry.register(UpdatePlanTool())

    async with make_checkpointer("memory") as cp:
        runner = GraphRunner(checkpointer=cp)
        compiled = runner.compile(build_react_graph(llm_caller=llm, tool_registry=registry))
        cfg: RunnableConfig = {"configurable": {"thread_id": "k8-l5-closure"}}
        state = await compiled.ainvoke(
            {
                "messages": [HumanMessage(content="start")],
                "step_count": 0,
                "max_steps": 5,
                "plan": initial,
            },
            config=cfg,
        )

    # 2 agent calls happened: pre-tool (step_count → 1), post-tool
    # (step_count → 1 - 1 + 1 = 1). update_plan's housekeeping call did
    # not advance the user-visible count past 1.
    assert state["step_count"] == 1
    # And the plan was actually revised (K.K8 state_updates path
    # continues to work after the L5 changes).
    new_plan = state["plan"]
    assert new_plan is not None
    assert [s.description for s in new_plan.steps] == ["new"]
    # The conversation ended with the final answer.
    final = state["messages"][-1]
    assert isinstance(final, AIMessage)
    assert final.content == "done"


# ---------------------------------------------------------------------------
# Error paths refund nothing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_error_does_not_refund() -> None:
    """When a tool raises, the dispatch wrapper returns refund=0 — only
    successful ToolResults can refund."""

    @dataclass
    class _RaisingTool:
        name: str

        @property
        def spec(self) -> ToolSpec:
            return ToolSpec(name=self.name, description="raises")

        async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
            del args, ctx
            msg = "boom"
            raise RuntimeError(msg)

    llm = _ScriptedLLM(
        responses=[
            AIMessage(
                content="",
                tool_calls=[_tc("boom", "tc-1")],
            ),
            AIMessage(content="done"),
        ]
    )
    registry = ToolRegistry()
    registry.register(_RaisingTool(name="boom"))

    state = await _run(llm, registry, thread_id="error-no-refund")
    # No refund on error → step_count advances normally (2 agent calls).
    assert state["step_count"] == 2
    # And the LLM saw an error ToolMessage so the run completed cleanly.
    tool_msgs = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    assert "boom" in str(tool_msgs[0].content)
