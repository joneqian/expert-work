# B2 PR1 后端 worker 事件桥 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** worker(spawn_worker / 静态 subagent)运行期的 start/update/end 事件桥进父 run 的 SSE 流 + RunEventStore,实时与回放同源可见。

**Architecture:** `run_child_to_result` 从 `ainvoke` 改为 `astream(stream_mode=["updates","values"])` 循环,每个 updates chunk 截断成 `worker` 帧,经 config 注入的异步 sink(compaction sink 同款模式)发布进父 bridge 并落库;sink 经 `_child_config` 向下透传,孙 worker 帧直达父 bridge。帧构建是纯函数,独立模块独立测。

**Tech Stack:** Python 3.12 / LangGraph astream / pytest-asyncio / dataclasses。

**Spec:** `docs/superpowers/specs/2026-07-19-worker-observability-design.md`(决策与帧格式的权威;冲突以 spec 为准)。

## Global Constraints

- 截断常量精确值:`WORKER_CONTENT_EXCERPT = 500`、`WORKER_ARGS_EXCERPT = 200`、`WORKER_RESULT_EXCERPT = 500`(字符;超限截断加 `…`)。
- SSE event 名精确值:`"worker"`;帧信封字段精确集:`worker_id / parent_worker_id / parent_tool_call_id / label / agent_ref / depth / kind / wseq / data`;`kind ∈ {"start","update","end"}`;`outcome ∈ {"success","max_steps","cancelled"}`。
- sink 全程 best-effort:发布/落库异常吞掉记 warning 日志,绝不影响 worker 本体执行。
- seq 竞态红线:`_publish_worker` 必须在任何 `await` 之前同步分配 seq(并发 worker ≤3 会交错)。
- 行为等价红线:cancel / MaxSteps / 最终结果语义与 ainvoke 版一致;现有测试原样通过(仅允许:桩加 `astream` 包装、显式断言 ainvoke 调用形状的 mock repoint)。
- 分层红线:`orchestrator.tools` 不得 import `orchestrator.graph_builder`(包环,见 `tools/approval.py:29` 先例)——sink key 定义在 `tools/_worker_events.py`。
- 不加新量控面(截断即量控);worker 无 token 帧(不桥)。
- 帧必须 JSON-safe(`json.dumps` 可序列化)——落 JSONB 与 SSE 皆依赖。
- 测试命令:`uv run pytest`(裸 pytest 缺包);CI mypy 精确命令见 Task 5;ruff 跑全库。
- 提交格式:`feat: ...` / `test: ...`,无 attribution。

---

## File Structure

| 文件 | 职责 |
|---|---|
| Create `services/orchestrator/src/orchestrator/tools/_worker_events.py` | 帧构建纯函数 + `WORKER_EVENT_SINK_KEY` + `WorkerEventSink` 类型(叶子模块,零 orchestrator 内部依赖) |
| Modify `services/orchestrator/src/orchestrator/tools/registry.py` | `ToolContext` 加 `worker_event_sink` / `tool_call_id` 字段 |
| Modify `services/orchestrator/src/orchestrator/graph_builder/builder.py` | `_tool_context` 读 sink;`_invoke_tool` 顶部 `replace(ctx, tool_call_id=call_id)` |
| Modify `services/orchestrator/src/orchestrator/tools/_child_run.py` | astream 化 + start/update/end 发布 + `_child_config` 透传 |
| Modify `services/orchestrator/src/orchestrator/sse.py` | `_publish_worker` sink 定义 + 注入 |
| Test Create `services/orchestrator/tests/test_worker_events.py` | 纯函数单测 |
| Test Modify `services/orchestrator/tests/test_tool_context.py` | sink 读取 + tool_call_id 注入 |
| Test Create `services/orchestrator/tests/test_worker_event_bridge.py` | `run_child_to_result` 帧集成 |
| Test Modify `services/orchestrator/tests/test_spawn_worker.py`、`test_subagent.py` | `_FakeGraph` 补 `astream` 包装 |
| Test Create `services/orchestrator/tests/test_sse_worker_events.py` | run_agent 注入 + 持久化 + 并发 seq |

---

### Task 1: `_worker_events.py` 帧构建纯函数

**Files:**
- Create: `services/orchestrator/src/orchestrator/tools/_worker_events.py`
- Test: `services/orchestrator/tests/test_worker_events.py`

**Interfaces:**
- Consumes: 无(叶子模块;仅 langchain_core.messages)
- Produces(后续 Task 依赖,签名精确):
  - `WORKER_EVENT_SINK_KEY = "worker_event_sink"`(str 常量)
  - `WorkerEventSink = Callable[[dict[str, Any]], Awaitable[None]]`
  - `@dataclass(frozen=True) class WorkerIdentity(worker_id: str, parent_worker_id: str | None, parent_tool_call_id: str | None, label: str, agent_ref: str, depth: int)`
  - `build_worker_start_frame(ident: WorkerIdentity, *, wseq: int, task: str, role: str | None, max_steps: int) -> dict[str, Any]`
  - `build_worker_update_frame(ident: WorkerIdentity, *, wseq: int, node: str, writes: Mapping[str, Any], duration_ms: int) -> dict[str, Any]`
  - `build_worker_end_frame(ident: WorkerIdentity, *, wseq: int, outcome: str, iteration_used: int, llm_call_count: int, wall_clock_ms: int) -> dict[str, Any]`
  - 常量 `WORKER_CONTENT_EXCERPT / WORKER_ARGS_EXCERPT / WORKER_RESULT_EXCERPT`

- [ ] **Step 1: 写失败测试**

```python
"""B2 worker 可观测性 — 帧构建纯函数单测."""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from orchestrator.tools._worker_events import (
    WORKER_ARGS_EXCERPT,
    WORKER_CONTENT_EXCERPT,
    WORKER_RESULT_EXCERPT,
    WorkerIdentity,
    build_worker_end_frame,
    build_worker_start_frame,
    build_worker_update_frame,
)

_IDENT = WorkerIdentity(
    worker_id="w-1",
    parent_worker_id=None,
    parent_tool_call_id="call-1",
    label="spawn_worker",
    agent_ref="dynamic:research",
    depth=1,
)


def test_start_frame_envelope_and_task_excerpt() -> None:
    frame = build_worker_start_frame(
        _IDENT, wseq=0, task="t" * 600, role="research", max_steps=32
    )
    assert frame["worker_id"] == "w-1"
    assert frame["parent_worker_id"] is None
    assert frame["parent_tool_call_id"] == "call-1"
    assert frame["label"] == "spawn_worker"
    assert frame["agent_ref"] == "dynamic:research"
    assert frame["depth"] == 1
    assert frame["kind"] == "start"
    assert frame["wseq"] == 0
    assert frame["data"]["role"] == "research"
    assert frame["data"]["max_steps"] == 32
    # 500 字 + "…"
    assert len(frame["data"]["task_excerpt"]) == WORKER_CONTENT_EXCERPT + 1
    assert frame["data"]["task_excerpt"].endswith("…")


def test_update_frame_summarizes_ai_and_tool_messages() -> None:
    writes = {
        "step_count": 3,
        "messages": [
            AIMessage(
                content="x" * 600,
                tool_calls=[
                    {
                        "name": "http_request",
                        "args": {"url": "https://e.com", "body": "b" * 300},
                        "id": "tc-1",
                    }
                ],
            ),
            ToolMessage(content="r" * 600, tool_call_id="tc-1", name="http_request"),
        ],
        "plan": {"goal": "dropped"},
    }
    frame = build_worker_update_frame(
        _IDENT, wseq=1, node="agent", writes=writes, duration_ms=42
    )
    data = frame["data"]
    assert frame["kind"] == "update"
    assert data["node"] == "agent"
    assert data["step_count"] == 3
    assert data["_duration_ms"] == 42
    assert "plan" not in data  # 非消息类 writes 丢弃
    ai, tool = data["messages"]
    assert ai["type"] == "ai"
    assert len(ai["content_excerpt"]) == WORKER_CONTENT_EXCERPT + 1
    assert ai["tool_calls"][0]["name"] == "http_request"
    assert len(ai["tool_calls"][0]["args_excerpt"]) == WORKER_ARGS_EXCERPT + 1
    assert tool["type"] == "tool"
    assert tool["name"] == "http_request"
    assert len(tool["tool_result_excerpt"]) == WORKER_RESULT_EXCERPT + 1


def test_update_frame_accepts_single_message_and_generic_type() -> None:
    frame = build_worker_update_frame(
        _IDENT, wseq=0, node="agent", writes={"messages": SystemMessage(content="hi")}, duration_ms=1
    )
    (msg,) = frame["data"]["messages"]
    assert msg["type"] == "system"
    assert msg["content_excerpt"] == "hi"


def test_update_frame_no_step_count_key_when_absent() -> None:
    frame = build_worker_update_frame(
        _IDENT, wseq=0, node="tools", writes={"messages": []}, duration_ms=5
    )
    assert "step_count" not in frame["data"]
    assert frame["data"]["messages"] == []


def test_end_frame_summary() -> None:
    frame = build_worker_end_frame(
        _IDENT, wseq=9, outcome="max_steps", iteration_used=32, llm_call_count=16, wall_clock_ms=1234
    )
    assert frame["kind"] == "end"
    assert frame["data"] == {
        "outcome": "max_steps",
        "iteration_used": 32,
        "llm_call_count": 16,
        "wall_clock_ms": 1234,
    }


def test_frames_are_json_safe() -> None:
    writes = {"messages": [AIMessage(content="ok")], "step_count": 1}
    for frame in (
        build_worker_start_frame(_IDENT, wseq=0, task="t", role=None, max_steps=8),
        build_worker_update_frame(_IDENT, wseq=1, node="agent", writes=writes, duration_ms=0),
        build_worker_end_frame(
            _IDENT, wseq=2, outcome="success", iteration_used=1, llm_call_count=1, wall_clock_ms=10
        ),
    ):
        json.dumps(frame)  # 不抛 = JSON-safe
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run pytest services/orchestrator/tests/test_worker_events.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator.tools._worker_events'`

- [ ] **Step 3: 实现**

```python
"""B2 worker 可观测性 — worker 帧构建纯函数 + sink 契约.

spec: docs/superpowers/specs/2026-07-19-worker-observability-design.md

sink key / 类型定义在本模块(而非 ``graph_builder/_config``,那是其余
sink 的家):``orchestrator.tools`` 是 ``graph_builder`` 的下层,反向
import 会成包环(``tools/approval.py`` 同款先例)。``sse.py`` /
``builder.py`` 从这里向下 import。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

#: config["configurable"] key —— run_agent 注入的异步 worker 帧 sink
#: (镜像 COMPACTION_SINK_KEY 的注入模式)。
WORKER_EVENT_SINK_KEY = "worker_event_sink"  # noqa: S105 — config key, not a credential

#: 一个 worker 帧(信封 dict)送进父 run bridge + 事件库的异步回调。
WorkerEventSink = Callable[[dict[str, Any]], Awaitable[None]]

WORKER_CONTENT_EXCERPT = 500
WORKER_ARGS_EXCERPT = 200
WORKER_RESULT_EXCERPT = 500


@dataclass(frozen=True)
class WorkerIdentity:
    """一个 child run 的帧信封身份 — 每帧原样携带."""

    worker_id: str
    parent_worker_id: str | None
    parent_tool_call_id: str | None
    label: str
    agent_ref: str
    depth: int


def build_worker_start_frame(
    ident: WorkerIdentity, *, wseq: int, task: str, role: str | None, max_steps: int
) -> dict[str, Any]:
    return _envelope(
        ident,
        kind="start",
        wseq=wseq,
        data={
            "task_excerpt": _excerpt(task, WORKER_CONTENT_EXCERPT),
            "role": role,
            "max_steps": max_steps,
        },
    )


def build_worker_update_frame(
    ident: WorkerIdentity, *, wseq: int, node: str, writes: Mapping[str, Any], duration_ms: int
) -> dict[str, Any]:
    data: dict[str, Any] = {"node": node, "_duration_ms": duration_ms}
    step_raw = writes.get("step_count")
    if isinstance(step_raw, int):
        data["step_count"] = step_raw
    data["messages"] = [_summarize_message(m) for m in _messages_of(writes)]
    return _envelope(ident, kind="update", wseq=wseq, data=data)


def build_worker_end_frame(
    ident: WorkerIdentity,
    *,
    wseq: int,
    outcome: str,
    iteration_used: int,
    llm_call_count: int,
    wall_clock_ms: int,
) -> dict[str, Any]:
    return _envelope(
        ident,
        kind="end",
        wseq=wseq,
        data={
            "outcome": outcome,
            "iteration_used": iteration_used,
            "llm_call_count": llm_call_count,
            "wall_clock_ms": wall_clock_ms,
        },
    )


def _envelope(
    ident: WorkerIdentity, *, kind: str, wseq: int, data: dict[str, Any]
) -> dict[str, Any]:
    return {
        "worker_id": ident.worker_id,
        "parent_worker_id": ident.parent_worker_id,
        "parent_tool_call_id": ident.parent_tool_call_id,
        "label": ident.label,
        "agent_ref": ident.agent_ref,
        "depth": ident.depth,
        "kind": kind,
        "wseq": wseq,
        "data": data,
    }


def _excerpt(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def _text(content: Any) -> str:
    return content if isinstance(content, str) else str(content)


def _messages_of(writes: Mapping[str, Any]) -> list[BaseMessage]:
    raw = writes.get("messages")
    if isinstance(raw, BaseMessage):
        return [raw]
    if isinstance(raw, Sequence) and not isinstance(raw, str | bytes):
        return [m for m in raw if isinstance(m, BaseMessage)]
    return []


def _summarize_message(msg: BaseMessage) -> dict[str, Any]:
    if isinstance(msg, AIMessage):
        summary: dict[str, Any] = {
            "type": "ai",
            "content_excerpt": _excerpt(_text(msg.content), WORKER_CONTENT_EXCERPT),
        }
        calls = [
            {
                "name": str(call.get("name", "")),
                "args_excerpt": _excerpt(
                    json.dumps(call.get("args") or {}, ensure_ascii=False, default=str),
                    WORKER_ARGS_EXCERPT,
                ),
            }
            for call in (msg.tool_calls or [])
        ]
        if calls:
            summary["tool_calls"] = calls
        return summary
    if isinstance(msg, ToolMessage):
        return {
            "type": "tool",
            "name": msg.name or "",
            "tool_result_excerpt": _excerpt(_text(msg.content), WORKER_RESULT_EXCERPT),
        }
    return {
        "type": msg.type,
        "content_excerpt": _excerpt(_text(msg.content), WORKER_CONTENT_EXCERPT),
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest services/orchestrator/tests/test_worker_events.py -q`
Expected: PASS(6 tests)

- [ ] **Step 5: Commit**

```bash
git add services/orchestrator/src/orchestrator/tools/_worker_events.py services/orchestrator/tests/test_worker_events.py
git commit -m "feat: B2 worker 帧构建纯函数 + sink 契约(_worker_events.py)"
```

---

### Task 2: ToolContext 字段 + builder 接线

**Files:**
- Modify: `services/orchestrator/src/orchestrator/tools/registry.py:153-197`(ToolContext)
- Modify: `services/orchestrator/src/orchestrator/graph_builder/builder.py:2546`(`_tool_context` 返回)+ `builder.py:2595-2605`(`_invoke_tool` 顶部)
- Test: `services/orchestrator/tests/test_tool_context.py`(追加)

**Interfaces:**
- Consumes: Task 1 的 `WORKER_EVENT_SINK_KEY`(builder.py import 自 `orchestrator.tools._worker_events`)。
- Produces: `ToolContext.worker_event_sink: Callable[[dict[str, Any]], Awaitable[None]] | None = None`、`ToolContext.tool_call_id: str | None = None`;`_invoke_tool` 内每次 dispatch 的 ctx 带 `tool_call_id`。

- [ ] **Step 1: 写失败测试**

追加到 `services/orchestrator/tests/test_tool_context.py`(沿用该文件现有 import/fixture 风格;`_tool_context` 与 `_invoke_tool` 从 `orchestrator.graph_builder.builder` import):

```python
@pytest.mark.asyncio
async def test_tool_context_reads_worker_event_sink_from_config() -> None:
    async def _sink(frame: dict[str, Any]) -> None:  # noqa: ARG001
        return None

    config = {"configurable": {WORKER_EVENT_SINK_KEY: _sink}}
    ctx = _tool_context(config)
    assert ctx.worker_event_sink is _sink


def test_tool_context_worker_event_sink_defaults_none() -> None:
    ctx = _tool_context({"configurable": {}})
    assert ctx.worker_event_sink is None
    assert ctx.tool_call_id is None


def test_tool_context_ignores_non_callable_worker_sink() -> None:
    ctx = _tool_context({"configurable": {WORKER_EVENT_SINK_KEY: "not-callable"}})
    assert ctx.worker_event_sink is None


@pytest.mark.asyncio
async def test_invoke_tool_threads_tool_call_id_into_ctx() -> None:
    seen: dict[str, Any] = {}

    async def _call(args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        seen["tool_call_id"] = ctx.tool_call_id
        return ToolResult(content="ok")

    tool = _make_tool(name="probe", call=_call)  # 按文件内现有工具构造惯例
    await _invoke_tool(tool, {}, "call-42", ToolContext())
    assert seen["tool_call_id"] == "call-42"
```

注:`_tool_context` 的实际入参形状(RunnableConfig)与 `_make_tool` 构造惯例以 `test_tool_context.py` 现有测试为准 —— 保持断言语义,适配文件内 helper。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest services/orchestrator/tests/test_tool_context.py -q`
Expected: 新增 4 个 FAIL(`worker_event_sink` 字段不存在 / `tool_call_id` 为 None)

- [ ] **Step 3: 实现**

`registry.py` — `ToolContext` 末尾(`worker_spawn_budget` 字段后)追加;文件头部补 import(`from collections.abc import Awaitable, Callable` 并入既有 import 行):

```python
    #: B2 worker 可观测性 — async sink publishing ``worker`` SSE frames into
    #: the parent run's bridge + event store. Injected per-run by
    #: ``sse.run_agent`` via ``WORKER_EVENT_SINK_KEY``; ``None`` when unwired
    #: (tests / eval) — child runs then emit no frames.
    worker_event_sink: Callable[[dict[str, Any]], Awaitable[None]] | None = None
    #: B2 — id of the tool_call this invocation serves (AIMessage
    #: ``tool_calls[].id``), set per-dispatch in ``_invoke_tool`` via
    #: ``dataclasses.replace``. Worker frames carry it so the frontend can
    #: attach the worker sub-timeline to the pending tool card.
    tool_call_id: str | None = None
```

`builder.py` — `_tool_context`(:2546 的 `return ToolContext(` 前)追加读取,返回加字段;import 行追加 `from orchestrator.tools._worker_events import WORKER_EVENT_SINK_KEY`:

```python
    # B2 — worker 事件 sink(镜像 worker_spawn_budget 的 config 读取)。
    sink_raw = configurable.get(WORKER_EVENT_SINK_KEY)
    worker_event_sink = sink_raw if callable(sink_raw) else None
    return ToolContext(
        tenant_id=tenant_id,
        run_id=run_id,
        user_id=user_id,
        oauth_user_id=oauth_user_id,
        cancellation_token=cancellation_token(config),
        plan=plan,
        deadline_at=deadline_at,
        worker_spawn_budget=worker_spawn_budget,
        worker_event_sink=worker_event_sink,
    )
```

`builder.py` — `_invoke_tool`(:2595)函数体第一行加(`from dataclasses import replace` 并入文件头 import):

```python
    # B2 — 让工具知道自己服务的 tool_call id(worker 帧挂前端工具卡用)。
    ctx = replace(ctx, tool_call_id=call_id)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest services/orchestrator/tests/test_tool_context.py -q`
Expected: PASS(含既有测试)

- [ ] **Step 5: Commit**

```bash
git add services/orchestrator/src/orchestrator/tools/registry.py services/orchestrator/src/orchestrator/graph_builder/builder.py services/orchestrator/tests/test_tool_context.py
git commit -m "feat: B2 ToolContext 携带 worker 事件 sink + per-dispatch tool_call_id"
```

---

### Task 3: `run_child_to_result` astream 化 + 帧发布 + 透传

**Files:**
- Modify: `services/orchestrator/src/orchestrator/tools/_child_run.py:88-222`(主函数)+ `:339-`(`_child_config`)+ 文件头 import
- Test Create: `services/orchestrator/tests/test_worker_event_bridge.py`
- Test Modify: `services/orchestrator/tests/test_spawn_worker.py:26-31`、`services/orchestrator/tests/test_subagent.py:46-54`(`_FakeGraph` 补 astream;`:411` `_StatefulGraph`、`:200` `_SlowGraph` 同款)

**Interfaces:**
- Consumes: Task 1 全部 builders + `WorkerIdentity` + `WORKER_EVENT_SINK_KEY` + `WorkerEventSink`;Task 2 的 `ctx.worker_event_sink` / `ctx.tool_call_id`。
- Produces: worker 帧在 child 运行期经 sink 发出(start → update×N → end);`_child_config` 透传 sink。

- [ ] **Step 1: 更新既有测试桩(astream 包装,保持脚本行为)**

`test_spawn_worker.py` `_FakeGraph` 与 `test_subagent.py` `_FakeGraph` / `_StatefulGraph` / `_SlowGraph`,各加(文件头补 `from collections.abc import AsyncIterator`):

```python
    async def astream(
        self, state: Any, config: Any = None, *, stream_mode: Any = None
    ) -> AsyncIterator[Any]:
        del stream_mode
        result = await self.ainvoke(state, config)
        yield ("values", result)
```

语义:脚本仍在 `ainvoke` 里(录调用/抛异常都保留);astream 只包一层。ainvoke 抛异常时不 yield values → 主代码走 `_fetch_partial` 兜底 = 原语义。**若个别测试断言 `ainvoke` 被直接调用的次数/形状,repoint 到 astream 路径(桩内 ainvoke 仍被调,计数通常不变)。**

- [ ] **Step 2: 写失败测试(新文件)**

```python
"""B2 — run_child_to_result 的 worker 帧集成测试."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from expert_work.runtime.cancellation import RunCancelledError
from orchestrator.errors import MaxStepsExceededError
from orchestrator.tools._child_run import run_child_to_result
from orchestrator.tools.registry import ToolContext


class _StreamingGraph:
    """吐 updates chunk 再吐最终 values 的脚本图."""

    def __init__(self, updates: list[Any], final: dict[str, Any], raise_with: BaseException | None = None) -> None:
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

        class _Snap:
            values = {"messages": [], "step_count": 1}

        return _Snap()


def _built(graph: Any) -> Any:
    # 按 test_spawn_worker.py 的 _built 惯例构造 BuiltAgent(system_prompt/max_steps 等)
    from tests.test_spawn_worker import _built as _built_helper  # 若不可直接复用,内联同款构造

    return _built_helper(graph)


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
        child=_built(_StreamingGraph(_UPDATES[:1], _FINAL, raise_with=MaxStepsExceededError(8))),
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
```

注:`MaxStepsExceededError` 构造签名以 `orchestrator/errors.py` 实际为准;`_built` 复用/内联 `test_spawn_worker.py:64` 惯例(BuiltAgent 字段以该 helper 为准)。

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run pytest services/orchestrator/tests/test_worker_event_bridge.py -q`
Expected: FAIL — 无帧发出(`frames == []`)/ astream 未被调用

- [ ] **Step 4: 实现**

`_child_run.py` 文件头 import 追加:

```python
from orchestrator.tools._worker_events import (
    WORKER_EVENT_SINK_KEY,
    WorkerEventSink,
    WorkerIdentity,
    build_worker_end_frame,
    build_worker_start_frame,
    build_worker_update_frame,
)
```

`run_child_to_result` 主体改造(:101-152 区段;三个 `_build_tool_result` return 分支 `:154-222` 不动):

```python
    started_at = datetime.now(UTC)
    start_monotonic = time.monotonic()
    result: Any = None
    raised_max_steps = False

    # B2 worker 可观测性 — 帧身份 + 局部序。sink 为 None(未接线:eval /
    # 单测)时零帧零开销。depth>1 说明"发起方自己就是 worker",其
    # ctx.run_id 即父 worker 的 sub_run_id。
    sink = ctx.worker_event_sink
    role_raw = (extra_meta or {}).get("role")
    ident = WorkerIdentity(
        worker_id=str(sub_run_id),
        parent_worker_id=str(ctx.run_id) if child_depth > 1 and ctx.run_id else None,
        parent_tool_call_id=ctx.tool_call_id,
        label=label,
        agent_ref=agent_ref,
        depth=child_depth,
    )
    wseq = 0
    if sink is not None:
        await _emit_worker_frame(
            sink,
            build_worker_start_frame(
                ident,
                wseq=wseq,
                task=task,
                role=str(role_raw) if role_raw else None,
                max_steps=child.max_steps,
            ),
        )
        wseq += 1

    try:
        # B2 — ainvoke → astream:同一 compiled graph、同一 config,
        # updates chunk 逐个截断成 worker 帧;最后一个 values chunk 即
        # ainvoke 的返回值(LangGraph 语义),异常时缺失 → 下方
        # _fetch_partial 兜底(原语义)。
        last_chunk = time.monotonic()
        async for part in child.graph.astream(
            child_input, child_config, stream_mode=["updates", "values"]
        ):
            mode, chunk = part
            if mode == "values":
                result = chunk
                continue
            now = time.monotonic()
            duration_ms = int((now - last_chunk) * 1000)
            last_chunk = now
            if sink is None or not isinstance(chunk, Mapping):
                continue
            for node, writes in chunk.items():
                await _emit_worker_frame(
                    sink,
                    build_worker_update_frame(
                        ident,
                        wseq=wseq,
                        node=str(node),
                        writes=writes if isinstance(writes, Mapping) else {},
                        duration_ms=duration_ms,
                    ),
                )
                wseq += 1
        outcome: TrajectoryOutcome = "success"
    except MaxStepsExceededError:
        outcome = "max_steps"
        raised_max_steps = True
        logger.info("child_run.max_steps label=%s agent_ref=%s", label, agent_ref)
    except RunCancelledError:
        partial_msgs, partial_steps = await _fetch_partial(child.graph, child_config, label=label)
        _dispatch_trajectory(
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            sub_thread_id=sub_thread_id,
            sub_run_id=sub_run_id,
            outcome="cancelled",
            messages=partial_msgs,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            step_count=partial_steps,
            recorder=trajectory_recorder,
            metadata=trajectory_metadata,
        )
        if sink is not None:
            await _emit_worker_frame(
                sink,
                build_worker_end_frame(
                    ident,
                    wseq=wseq,
                    outcome="cancelled",
                    iteration_used=partial_steps,
                    llm_call_count=sum(1 for m in partial_msgs if isinstance(m, AIMessage)),
                    wall_clock_ms=int((time.monotonic() - start_monotonic) * 1000),
                ),
            )
        raise
```

紧接现有 `:130-138`(wall_clock_ms / messages / step_count / llm_call_count 计算,不动)之后、`_dispatch_trajectory`(:140)之前插入:

```python
    if sink is not None:
        await _emit_worker_frame(
            sink,
            build_worker_end_frame(
                ident,
                wseq=wseq,
                outcome="max_steps" if raised_max_steps else "success",
                iteration_used=step_count,
                llm_call_count=llm_call_count,
                wall_clock_ms=wall_clock_ms,
            ),
        )
```

模块级新增(`_fetch_partial` 旁):

```python
async def _emit_worker_frame(sink: WorkerEventSink, frame: dict[str, Any]) -> None:
    """Best-effort — 桥接故障绝不影响 worker 本体执行(spec 红线)."""
    try:
        await sink(frame)
    except Exception as exc:
        logger.warning(
            "child_run.worker_frame_failed kind=%s err=%s",
            frame.get("kind", "?"),
            type(exc).__name__,
        )
```

`_child_config`(:339)`configurable` 组装里追加(孙 worker 透传):

```python
    # B2 — 向下透传 worker 事件 sink,孙 worker 帧直达父 run bridge。
    if ctx.worker_event_sink is not None:
        configurable[WORKER_EVENT_SINK_KEY] = ctx.worker_event_sink
```

- [ ] **Step 5: 跑测试确认通过 + 等价性回归**

Run: `uv run pytest services/orchestrator/tests/test_worker_event_bridge.py services/orchestrator/tests/test_spawn_worker.py services/orchestrator/tests/test_subagent.py -q`
Expected: 全 PASS。若 max-steps 既有测试因"values chunk 先到导致不再调 aget_state"失败:改桩(异常路径不 yield values),不改主代码语义。

- [ ] **Step 6: Commit**

```bash
git add services/orchestrator/src/orchestrator/tools/_child_run.py services/orchestrator/tests/test_worker_event_bridge.py services/orchestrator/tests/test_spawn_worker.py services/orchestrator/tests/test_subagent.py
git commit -m "feat: B2 run_child_to_result astream 化 + worker start/update/end 帧发布与透传"
```

---

### Task 4: sse.py `_publish_worker` sink + 注入

**Files:**
- Modify: `services/orchestrator/src/orchestrator/sse.py:376-383`(`_publish_token` 之后、sink 注入区)+ `:86` import 行
- Test Create: `services/orchestrator/tests/test_sse_worker_events.py`

**Interfaces:**
- Consumes: Task 1 `WORKER_EVENT_SINK_KEY`。
- Produces: 每个 run 的 `configurable[WORKER_EVENT_SINK_KEY]` = `_publish_worker`(publish `"worker"` 帧 + 落 RunEventStore,seq 同步分配)。

- [ ] **Step 1: 写失败测试(新文件;harness 照抄 `test_sse_trajectory.py:1-95` 的 stub/fixture 惯例)**

```python
"""B2 — run_agent 注入 worker sink:发布 + 持久化 + 并发 seq."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest

from expert_work.runtime.runs import DisconnectMode, RunManager, RunRecord
from expert_work.runtime.runs.event_store import InMemoryRunEventStore
from expert_work.runtime.stream_bridge import END_SENTINEL, InMemoryStreamBridge
from orchestrator.sse import run_agent
from orchestrator.tools._worker_events import WORKER_EVENT_SINK_KEY


async def _new_record(rm: RunManager) -> RunRecord:
    return await rm.create(
        run_id=uuid4(), thread_id=uuid4(), tenant_id=uuid4(),
        on_disconnect=DisconnectMode.CANCEL,
    )


class _WorkerGraph:
    """astream 期间经注入的 sink 发 worker 帧(模拟 child run 桥接)."""

    def __init__(self, frames: list[dict[str, Any]], *, concurrent: bool = False) -> None:
        self.frames = frames
        self.concurrent = concurrent

    async def astream(
        self, input: Any, config: Any = None, *, stream_mode: Any = None
    ) -> AsyncIterator[Any]:
        del input, stream_mode
        sink = config["configurable"][WORKER_EVENT_SINK_KEY]
        if self.concurrent:
            await asyncio.gather(*(sink(f) for f in self.frames))
        else:
            for frame in self.frames:
                await sink(frame)
        yield {"agent": {"step_count": 1}}


@pytest.mark.asyncio
async def test_worker_frames_published_and_persisted_with_monotonic_seq() -> None:
    bridge = InMemoryStreamBridge()
    rm = RunManager()
    record = await _new_record(rm)
    store = InMemoryRunEventStore()
    frames = [
        {"worker_id": "w1", "kind": "start", "wseq": 0},
        {"worker_id": "w1", "kind": "end", "wseq": 1},
    ]

    await run_agent(
        bridge=bridge, run_manager=rm, record=record,
        graph=_WorkerGraph(frames), graph_input={},
        config={"configurable": {"thread_id": str(record.thread_id)}},
        event_store=store,
    )

    events = await store.list(run_id=record.run_id, limit=500)
    worker_rows = [e for e in events if e.event_name == "worker"]
    assert [r.data["kind"] for r in worker_rows] == ["start", "end"]
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)  # 无重复


@pytest.mark.asyncio
async def test_concurrent_worker_frames_do_not_collide_on_seq() -> None:
    bridge = InMemoryStreamBridge()
    rm = RunManager()
    record = await _new_record(rm)
    store = InMemoryRunEventStore()  # append 对重复 (run_id, seq) 直接 raise
    frames = [{"worker_id": w, "kind": k, "wseq": i}
              for i, (w, k) in enumerate([("a", "start"), ("b", "start"), ("a", "end"), ("b", "end")])]

    await run_agent(
        bridge=bridge, run_manager=rm, record=record,
        graph=_WorkerGraph(frames, concurrent=True), graph_input={},
        config={"configurable": {"thread_id": str(record.thread_id)}},
        event_store=store,
    )

    events = await store.list(run_id=record.run_id, limit=500)
    worker_rows = [e for e in events if e.event_name == "worker"]
    assert len(worker_rows) == 4
    assert len({e.seq for e in events}) == len(events)
```

注:`run_agent` 若测试后需要 drain bridge 防订阅泄漏,照抄 `test_sse_trajectory.py:78` 的 `_drain` 惯例(该文件其他测试没 drain 也过就不加)。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest services/orchestrator/tests/test_sse_worker_events.py -q`
Expected: FAIL — `KeyError: 'worker_event_sink'`(configurable 里没注入)

- [ ] **Step 3: 实现**

`sse.py:86` import 行区追加:

```python
from orchestrator.tools._worker_events import WORKER_EVENT_SINK_KEY
```

`sse.py` `_publish_token`(:376-379)之后插入,注入区(:382-383)加一行:

```python
    # B2 worker 可观测性 — worker 事件 sink。child run(spawn_worker /
    # 静态 subagent)的 start/update/end 帧经此进父 run 的 bridge + 事件
    # 库,实时与回放同源。与 _publish_compaction 的关键差异:并发
    # worker(≤dynamic_worker_max_concurrent)会交错 await 本函数,seq
    # 必须在任何 await 之前同步分配,否则两帧读到同一 event_seq 撞
    # (run_id, seq) 主键。best-effort 由发送端(_emit_worker_frame)兜。
    async def _publish_worker(frame: dict[str, Any]) -> None:
        nonlocal event_seq
        seq = event_seq
        event_seq += 1
        await bridge.publish(run_id, "worker", frame)
        await _persist_event(
            event_store, run_id=run_id, seq=seq, event_name="worker", data=frame
        )

    effective_config["configurable"][COMPACTION_SINK_KEY] = _publish_compaction
    effective_config["configurable"][TOKEN_SINK_KEY] = _publish_token
    effective_config["configurable"][WORKER_EVENT_SINK_KEY] = _publish_worker
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest services/orchestrator/tests/test_sse_worker_events.py -q`
Expected: PASS(2 tests)

- [ ] **Step 5: Commit**

```bash
git add services/orchestrator/src/orchestrator/sse.py services/orchestrator/tests/test_sse_worker_events.py
git commit -m "feat: B2 run_agent 注入 worker 事件 sink(发布+持久化,seq 同步分配防并发碰撞)"
```

---

### Task 5: 整链验证

**Files:** 无新改动(只跑验证;发现问题按 systematic-debugging 修并单独 commit)

**Interfaces:** 消费前四个 Task 的全部产物。

- [ ] **Step 1: 全量 orchestrator 测试**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run pytest services/orchestrator/tests -q`
Expected: 全 PASS(等价性红线:除 Task 3 声明的桩改动外零测试语义变更)

- [ ] **Step 2: control-plane 测试(run_agent 调用方保险)**

Run: `uv run pytest services/control-plane/tests -q`
Expected: 全 PASS(run_agent 签名未变,应零波及;若 BuiltAgent/SimpleNamespace 桩类失败按报错补桩字段)

- [ ] **Step 3: Lint + 类型(CI 同款)**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy packages services/audit-backup-worker/src services/billing-rollup-job/src services/event-log-archive-job/src services/orchestrator/src services/retention-cleanup-job/src
```
Expected: 三者零错误(mypy 命令与 CI 完全一致,勿单文件跑——假阳)

- [ ] **Step 4: Commit(如有修复)**

```bash
git add -A && git commit -m "test: B2 PR1 整链验证修复"
```
(零修复则跳过)
