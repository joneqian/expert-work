"""B2 — run_child_to_result 的 worker 帧集成测试."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from expert_work.runtime.cancellation import RunCancelledError
from orchestrator.agent_factory import BuiltAgent
from orchestrator.errors import MaxStepsExceededError
from orchestrator.tools._child_run import run_child_to_result
from orchestrator.tools.registry import ToolContext


class _StreamingGraph:
    """吐 updates chunk 再吐最终 values 的脚本图."""

    def __init__(
        self, updates: list[Any], final: dict[str, Any], raise_with: BaseException | None = None
    ) -> None:
        self.updates = updates
        self.final = final
        self.raise_with = raise_with

    async def astream(
        self, state: Any, config: Any = None, *, stream_mode: Any = None
    ) -> AsyncIterator[Any]:
        del state, config, stream_mode
        for chunk in self.updates:
            yield ("updates", chunk)
        if self.raise_with is not None:
            raise self.raise_with
        yield ("values", self.final)

    async def aget_state(self, config: Any) -> Any:
        del config

        @dataclass
        class _Snap:
            values: dict[str, Any]

        return _Snap(values={"messages": [], "step_count": 1})


def _built(graph: Any, *, system_prompt: str = "worker prompt", max_steps: int = 5) -> BuiltAgent:
    # 同 test_spawn_worker.py:64 的 _built 惯例 — 最小 BuiltAgent 构造。
    return BuiltAgent(graph=graph, system_prompt=system_prompt, max_steps=max_steps)


def _collecting_ctx(frames: list[dict[str, Any]], *, run_id: Any = None) -> ToolContext:
    async def _sink(frame: dict[str, Any]) -> None:
        frames.append(frame)

    return ToolContext(
        tenant_id=uuid4(),
        run_id=run_id or uuid4(),
        worker_event_sink=_sink,
        tool_call_id="call-7",
    )


_FINAL = {"messages": [AIMessage(content="done")], "step_count": 2}
_UPDATES = [
    {"agent": {"messages": [AIMessage(content="thinking")], "step_count": 1}},
    {"tools": {"messages": []}},
]


@pytest.mark.asyncio
async def test_frames_start_updates_end_with_monotonic_wseq() -> None:
    frames: list[dict[str, Any]] = []
    result = await run_child_to_result(
        child=_built(_StreamingGraph(_UPDATES, _FINAL)),
        task="do the thing",
        ctx=_collecting_ctx(frames),
        child_depth=1,
        label="spawn_worker",
        agent_ref="dynamic:research",
        trajectory_recorder=None,
        trajectory_metadata={},
        extra_meta={"dynamic": True, "role": "research"},
    )
    assert result.content == "done"
    kinds = [f["kind"] for f in frames]
    assert kinds == ["start", "update", "update", "end"]
    assert [f["wseq"] for f in frames] == [0, 1, 2, 3]
    assert frames[0]["parent_tool_call_id"] == "call-7"
    assert frames[0]["parent_worker_id"] is None  # depth=1
    assert frames[0]["data"]["role"] == "research"
    assert frames[1]["data"]["node"] == "agent"
    assert frames[-1]["data"]["outcome"] == "success"
    assert frames[-1]["data"]["iteration_used"] == 2


@pytest.mark.asyncio
async def test_depth2_frames_carry_parent_worker_id() -> None:
    frames: list[dict[str, Any]] = []
    parent_worker_run = uuid4()
    await run_child_to_result(
        child=_built(_StreamingGraph([], _FINAL)),
        task="t",
        ctx=_collecting_ctx(frames, run_id=parent_worker_run),
        child_depth=2,
        label="spawn_worker",
        agent_ref="dynamic:general",
        trajectory_recorder=None,
        trajectory_metadata={},
    )
    assert frames[0]["parent_worker_id"] == str(parent_worker_run)
    assert frames[0]["depth"] == 2


@pytest.mark.asyncio
async def test_cancel_emits_end_cancelled_then_reraises() -> None:
    frames: list[dict[str, Any]] = []
    with pytest.raises(RunCancelledError):
        await run_child_to_result(
            child=_built(_StreamingGraph(_UPDATES[:1], _FINAL, raise_with=RunCancelledError())),
            task="t",
            ctx=_collecting_ctx(frames),
            child_depth=1,
            label="spawn_worker",
            agent_ref="dynamic:general",
            trajectory_recorder=None,
            trajectory_metadata={},
        )
    assert frames[-1]["kind"] == "end"
    assert frames[-1]["data"]["outcome"] == "cancelled"


@pytest.mark.asyncio
async def test_max_steps_emits_end_max_steps_partial_result() -> None:
    frames: list[dict[str, Any]] = []
    result = await run_child_to_result(
        child=_built(
            _StreamingGraph(
                _UPDATES[:1], _FINAL, raise_with=MaxStepsExceededError(step_count=8, max_steps=5)
            )
        ),
        task="t",
        ctx=_collecting_ctx(frames),
        child_depth=1,
        label="spawn_worker",
        agent_ref="dynamic:general",
        trajectory_recorder=None,
        trajectory_metadata={},
    )
    assert frames[-1]["data"]["outcome"] == "max_steps"
    assert "step limit" in str(result.content)


@pytest.mark.asyncio
async def test_sink_failure_does_not_break_child_run() -> None:
    async def _boom(frame: dict[str, Any]) -> None:
        raise RuntimeError("sink down")

    ctx = ToolContext(tenant_id=uuid4(), run_id=uuid4(), worker_event_sink=_boom)
    result = await run_child_to_result(
        child=_built(_StreamingGraph(_UPDATES, _FINAL)),
        task="t",
        ctx=ctx,
        child_depth=1,
        label="spawn_worker",
        agent_ref="dynamic:general",
        trajectory_recorder=None,
        trajectory_metadata={},
    )
    assert result.content == "done"


@pytest.mark.asyncio
async def test_no_sink_no_frames_still_works() -> None:
    result = await run_child_to_result(
        child=_built(_StreamingGraph(_UPDATES, _FINAL)),
        task="t",
        ctx=ToolContext(tenant_id=uuid4(), run_id=uuid4()),
        child_depth=1,
        label="spawn_worker",
        agent_ref="dynamic:general",
        trajectory_recorder=None,
        trajectory_metadata={},
    )
    assert result.content == "done"
