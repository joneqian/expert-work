"""Stream L.L6 — :func:`tools_node` parallel dispatch integration.

The scheduling logic itself is exhaustively unit-tested in
:mod:`test_tool_scheduling`. This file pins the end-to-end behaviour:
a batch of read-only tool calls actually runs concurrently (wall-clock
≈ single-call time), counters emit, and order is preserved when the
LLM gets the tool results back.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _SlowReadTool:
    """Read-only tool that sleeps ``sleep_s`` before returning. Records each
    call's ``(start, end)`` monotonic window in ``windows`` so a test can prove
    two calls in one batch *overlapped* in time — a concurrency check that is
    robust to scheduling overhead, unlike an absolute wall-clock bound."""

    name: str
    sleep_s: float
    is_read_only: bool = True
    windows: list[tuple[float, float]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.windows is None:
            self.windows = []

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=f"sleeps {self.sleep_s}s",
            is_read_only=self.is_read_only,
        )

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        del ctx
        start = time.monotonic()
        await asyncio.sleep(self.sleep_s)
        self.windows.append((start, time.monotonic()))
        return ToolResult(content=f"{self.name}:{args.get('q', '')}")


@dataclass
class _SerialWriteTool:
    """Write tool that sleeps and tracks invocation order. Used to
    prove conflicting writes serialise."""

    name: str
    sleep_s: float
    path_args: tuple[str, ...] = ()
    invocations: list[tuple[str, float]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.invocations is None:
            self.invocations = []

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=f"writes for {self.sleep_s}s",
            is_read_only=False,
            path_args=self.path_args,
        )

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        del ctx
        start = time.monotonic()
        self.invocations.append(("start", start))
        await asyncio.sleep(self.sleep_s)
        finish = time.monotonic()
        self.invocations.append(("end", finish))
        return ToolResult(content=f"{self.name}:{args}")


def _tc(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


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
            msg = f"scripted LLM ran out of responses at call {idx}"
            raise RuntimeError(msg)
        return self.responses[idx]


async def _run(
    llm: _ScriptedLLM,
    registry: ToolRegistry,
    *,
    max_steps: int = 5,
) -> AgentState:
    async with make_checkpointer("memory") as cp:
        runner = GraphRunner(checkpointer=cp)
        compiled = runner.compile(build_react_graph(llm_caller=llm, tool_registry=registry))
        cfg: RunnableConfig = {"configurable": {"thread_id": str(uuid4())}}
        return await compiled.ainvoke(
            {
                "messages": [HumanMessage(content="start")],
                "step_count": 0,
                "max_steps": max_steps,
            },
            config=cfg,
        )


# ---------------------------------------------------------------------------
# Parallelisation proven by wall-clock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_read_only_calls_run_concurrently() -> None:
    """The central L6 guarantee. Two reads dispatched in one batch run
    concurrently (via ``asyncio.gather``), so their execution windows
    overlap rather than running back-to-back."""
    sleep_s = 0.2
    llm = _ScriptedLLM(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    _tc("knowledge_search", {"q": "a"}, "tc-1"),
                    _tc("knowledge_search", {"q": "b"}, "tc-2"),
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    registry = ToolRegistry()
    read_tool = _SlowReadTool(name="knowledge_search", sleep_s=sleep_s)
    registry.register(read_tool)

    state = await _run(llm, registry)

    # Concurrency proven by overlapping windows, not absolute wall-clock: the
    # later-starting read began before the earlier one finished. Robust to CI
    # scheduling overhead — load delays both start and end together and cannot
    # make two truly-concurrent sleeps stop overlapping. Sequential execution
    # would give disjoint windows (second_start >= first_end).
    assert len(read_tool.windows) == 2
    (_, first_end), (second_start, _) = sorted(read_tool.windows)
    assert second_start < first_end, (
        f"reads did not overlap — ran sequentially: windows={read_tool.windows}"
    )
    # Both ToolMessages came back in original tool_call order.
    tool_msgs = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 2


@pytest.mark.asyncio
async def test_conflicting_writes_serialise_into_separate_stages() -> None:
    """Two writes to the same artifact name → wall-clock ≈ 2 *
    sleep_s; invocation timestamps prove the second one started AFTER
    the first one finished."""
    sleep_s = 0.15
    tool = _SerialWriteTool(name="save_artifact", sleep_s=sleep_s, path_args=("name",))
    llm = _ScriptedLLM(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    _tc("save_artifact", {"name": "report.md"}, "tc-1"),
                    _tc("save_artifact", {"name": "report.md"}, "tc-2"),
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    registry = ToolRegistry()
    registry.register(tool)

    started = time.monotonic()
    await _run(llm, registry)
    elapsed = time.monotonic() - started

    # Two serialised writes → total wall-clock ≥ 2 * sleep_s minus
    # scheduler overhead.
    assert elapsed >= sleep_s * 1.8
    # invocations: [start#1, end#1, start#2, end#2] — second started
    # after first ended.
    starts = [t for label, t in tool.invocations if label == "start"]
    ends = [t for label, t in tool.invocations if label == "end"]
    assert starts[1] >= ends[0]


@pytest.mark.asyncio
async def test_writes_to_different_paths_run_concurrently() -> None:
    """Two ``save_artifact`` calls on disjoint paths can race — the
    L6 conflict rule is per-path, not per-tool."""
    sleep_s = 0.2
    tool = _SerialWriteTool(name="save_artifact", sleep_s=sleep_s, path_args=("name",))
    llm = _ScriptedLLM(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    _tc("save_artifact", {"name": "a.md"}, "tc-1"),
                    _tc("save_artifact", {"name": "b.md"}, "tc-2"),
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    registry = ToolRegistry()
    registry.register(tool)

    await _run(llm, registry)

    # Concurrency proven by overlap, not wall-clock: with disjoint paths the
    # second write began before the first finished. Robust to CI overhead.
    starts = sorted(t for label, t in tool.invocations if label == "start")
    ends = sorted(t for label, t in tool.invocations if label == "end")
    assert starts[1] < ends[0], (
        f"writes to different paths did not overlap: invocations={tool.invocations}"
    )


# ---------------------------------------------------------------------------
# Result ordering — LLM sees ToolMessages in original order regardless
# of execution order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_messages_preserve_original_call_order() -> None:
    """The reducer (``add_messages``) appends in the order the
    ``tools`` node returns its list. The scheduler must collate
    results back into original tool_call order even though execution
    runs in stage order."""
    llm = _ScriptedLLM(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    _tc("knowledge_search", {"q": "first"}, "tc-1"),
                    _tc("save_artifact", {"name": "report.md"}, "tc-2"),
                    _tc("knowledge_search", {"q": "third"}, "tc-3"),
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    registry = ToolRegistry()
    registry.register(_SlowReadTool(name="knowledge_search", sleep_s=0.01))
    registry.register(_SerialWriteTool(name="save_artifact", sleep_s=0.01, path_args=("name",)))

    state = await _run(llm, registry)

    # Tool message ids match the original tool_call ids in order — proof
    # the collation respected ``call.index``.
    tool_msgs = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in tool_msgs] == ["tc-1", "tc-2", "tc-3"]


# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatched_and_stages_counters_emit() -> None:
    """``expert_work_tools_dispatched_total`` and ``expert_work_tools_stages_total``
    track the scheduler's batch shape — dividing one by the other gives
    the dashboard's average concurrency."""
    from prometheus_client import REGISTRY

    before_dispatched = REGISTRY.get_sample_value("expert_work_tools_dispatched_total") or 0.0
    before_stages = REGISTRY.get_sample_value("expert_work_tools_stages_total") or 0.0

    llm = _ScriptedLLM(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    _tc("knowledge_search", {"q": "a"}, "tc-1"),
                    _tc("knowledge_search", {"q": "b"}, "tc-2"),
                    _tc("knowledge_search", {"q": "c"}, "tc-3"),
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    registry = ToolRegistry()
    registry.register(_SlowReadTool(name="knowledge_search", sleep_s=0.01))

    await _run(llm, registry)

    after_dispatched = REGISTRY.get_sample_value("expert_work_tools_dispatched_total") or 0.0
    after_stages = REGISTRY.get_sample_value("expert_work_tools_stages_total") or 0.0

    # 3 dispatches in 1 stage → 3 dispatched, 1 stage.
    assert after_dispatched == before_dispatched + 3
    assert after_stages == before_stages + 1
