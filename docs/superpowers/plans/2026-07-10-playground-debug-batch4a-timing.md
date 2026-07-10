# 调试台 Batch 4a(实时每步/每工具耗时)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 Batch 3 的 StepTimeline 填上「每步 / 每工具耗时」—— node 走 SSE 消费循环的 superstep 间隔、工具复用 builder.py 已算好的 per-tool duration,纯前后端零 Langfuse。

**Architecture:** 后端 sse.py 在 astream 消费循环里给每帧 node 注入 `_duration_ms`(相邻 chunk 墙钟差),builder.py 把已有的 per-tool `duration_ms` 挂到 ToolMessage 的 `additional_kwargs`;前端 `parseTimeline`/`parseToolCalls` 读出成 `durationMs`,StepTimeline 用 `fmtDuration` 渲染。

**Tech Stack:** 后端 Python(orchestrator,uv workspace,pytest);前端 React + Vite + AntD 5 + react-i18next(vitest)。

**设计来源(权威):** `docs/superpowers/specs/2026-07-10-playground-debug-batch4a-timing-design.md`。

## Global Constraints

- **纯前后端,零 Langfuse / 零 facade / 零 trace_id**(那是 4b)。不新增 orchestrator span 埋点(父 spec §9)—— 只表面化已有测量 + sse.py 消费侧算 superstep 间隔。
- 计时粒度 = 每步 + 每工具(Q5=B)。工具口径 = builder.py 现有 `_elapsed_ms(started)` 原义:派发到完成、含 `before_tool_dispatch_chain` 中间件时间。
- 帧形状:node `_duration_ms` **塞进 node 的通道 dict**(`{node: {…, "_duration_ms": N}}`),**不塞帧顶层**(parseTimeline 把顶层 key 当 node 名遍历)。工具 duration 走 ToolMessage `additional_kwargs`。
- 后端验证:`uv run pytest <path>` + `uv run mypy`(root config,非 `cd services/… && python -m`)。
- 前端验证:`cd apps/admin-ui && pnpm typecheck`(=`tsc -b --noEmit`,非裸 `npx tsc`)+ `npx vitest run <path>`。类型禁 `any`;`unknown` 收窄;不注解 `JSX.Element` 返回;需 node 类型具名 `import type { ReactNode }`。
- i18n 三处 parity(en 类型+值 / zh 值)—— 本批只加一个键 `tl_duration`。
- 缺失计时 → 字段缺席/`null`,不报错、不 500、前端隐藏。
- 每 Task 末 commit,conventional commits。

---

## Task 1: 后端 —— node superstep 间隔注入 `_duration_ms`(sse.py)

**Files:**
- Modify: `services/orchestrator/src/orchestrator/sse.py`(astream 消费循环 `:414-437` 区)
- Test: `services/orchestrator/tests/test_sse.py`(扩,复用现成 `_ScriptedGraph`/`run_agent`/`_drain` 骨架)

**Interfaces:**
- Produces:`updates` 帧的每个 node 通道 dict 多一个 `"_duration_ms": int`(距上一帧墙钟毫秒;首 chunk = RUNNING→首 chunk ≈ TTFT)。

- [ ] **Step 1: 写失败测试**

`services/orchestrator/tests/test_sse.py` 追加(骨架照同文件 `test_run_agent_publishes_metadata_then_chunks_then_end`):

```python
@pytest.mark.asyncio
async def test_run_agent_stamps_node_duration_ms_on_updates() -> None:
    bridge = InMemoryStreamBridge()
    rm = RunManager()
    record = await _new_record(rm)
    graph = _ScriptedGraph(
        chunks=[
            {"agent": {"step_count": 1}},
            {"tools": {"step_count": 1}},
        ]
    )
    await run_agent(
        bridge=bridge,
        run_manager=rm,
        record=record,
        graph=graph,
        graph_input={"messages": []},
        config={},
    )
    events = await _drain(bridge, record.run_id)
    updates = [e for e in events if e.event == "updates"]
    assert updates, "expected updates frames"
    for e in updates:
        assert isinstance(e.data, dict)
        for node_val in e.data.values():
            assert isinstance(node_val, dict)
            assert "_duration_ms" in node_val
            assert isinstance(node_val["_duration_ms"], int)
            assert node_val["_duration_ms"] >= 0
    # metadata frame must NOT carry a node-level _duration_ms key.
    meta = [e for e in events if e.event == "metadata"][0]
    assert "_duration_ms" not in meta.data
```

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `cd services/orchestrator && uv run pytest tests/test_sse.py::test_run_agent_stamps_node_duration_ms_on_updates -v`
Expected: FAIL —— `_duration_ms` 不在 node 通道里。

- [ ] **Step 3: 写实现**

在 `sse.py`,`ttft_started = time.monotonic()`(约 `:394`)之后、`with expert_work_span(... "run" ...):` 之前,加基线:

```python
        # Batch 4a — per-superstep wall-clock. Each ``updates`` chunk is one
        # node execution; the gap between successive chunk arrivals is that
        # step's coarse duration. Baseline = the same RUNNING mark used for
        # TTFT, so the first chunk's duration ≈ TTFT.
        last_frame_ts = ttft_started
```

在消费循环里,`jsonable_chunk = _to_jsonable(chunk)`(`:428`)之后、`await bridge.publish(...)`(`:429`)之前,注入:

```python
                        now = time.monotonic()
                        duration_ms = round((now - last_frame_ts) * 1000)
                        last_frame_ts = now
                        if isinstance(jsonable_chunk, dict):
                            for node_val in jsonable_chunk.values():
                                if isinstance(node_val, dict):
                                    node_val["_duration_ms"] = duration_ms
```

- [ ] **Step 4: 跑测试确认 PASS**

Run: `cd services/orchestrator && uv run pytest tests/test_sse.py -v`
Expected: PASS(新用例 + 原有全绿)。

- [ ] **Step 5: 类型检查** —— Run: `uv run mypy services/orchestrator/src/orchestrator/sse.py`(root config)→ clean。

- [ ] **Step 6: 提交**

```bash
git add services/orchestrator/src/orchestrator/sse.py services/orchestrator/tests/test_sse.py
git commit -m "feat(playground): SSE updates 帧注入 node superstep 耗时 _duration_ms"
```

---

## Task 2: 后端 —— 表面化 per-tool duration 到 ToolMessage(builder.py)

**Files:**
- Modify: `services/orchestrator/src/orchestrator/graph_builder/builder.py`(`_dispatch_tool` `:1955-2127`)
- Test: `services/orchestrator/tests/test_tool_audit.py`(扩,复用现成 `_dispatch_tool(...)` fixtures)

**Interfaces:**
- Consumes:无(独立后端改动,不依赖 Task 1)。
- Produces:`_dispatch_tool` 返回的 `ToolMessage`(tuple 第 0 项)其 `additional_kwargs["duration_ms"]` = `int`(= 现有 `_elapsed_ms(started)`,派发到完成含中间件时间)。成功 / 未知工具 / 中间件阻塞三条返回路径都带。

- [ ] **Step 1: 写失败测试**

`services/orchestrator/tests/test_tool_audit.py` 追加。**setup 照抄同文件 `test_success_emits_tool_call_success`(`:96`)与 `test_tool_error_emits_result_error`(`:120`)的 `_dispatch_tool(...)` 调用**(registry/ctx/audit_logger fixtures 同),只改断言为读 duration:

```python
@pytest.mark.asyncio
async def test_dispatch_stamps_duration_ms_on_success_message() -> None:
    # copy the exact _dispatch_tool(...) call + fixtures from
    # test_success_emits_tool_call_success above.
    msg, _, _, _ = await _dispatch_tool(  # ← same args as test_success_emits_tool_call_success
        ...
    )
    assert isinstance(msg.additional_kwargs.get("duration_ms"), int)
    assert msg.additional_kwargs["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_dispatch_stamps_duration_ms_on_error_message() -> None:
    # copy the exact _dispatch_tool(...) call + fixtures from
    # test_tool_error_emits_result_error above.
    msg, _, _, _ = await _dispatch_tool(  # ← same args as test_tool_error_emits_result_error
        ...
    )
    assert isinstance(msg.additional_kwargs.get("duration_ms"), int)
    assert msg.additional_kwargs["duration_ms"] >= 0
```

> 实现者:把两个现成测试的 `_dispatch_tool(...)` 调用行连同其上方的 registry/ctx/audit_logger 构造整段复制进新用例(改函数名 + 断言即可)。断言只关心返回的 `ToolMessage.additional_kwargs["duration_ms"]`。

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `cd services/orchestrator && uv run pytest tests/test_tool_audit.py::test_dispatch_stamps_duration_ms_on_success_message tests/test_tool_audit.py::test_dispatch_stamps_duration_ms_on_error_message -v`
Expected: FAIL —— `additional_kwargs` 无 `duration_ms`。

- [ ] **Step 3: 写实现**

`_dispatch_tool` 现在每条返回路径各算一次 `_elapsed_ms(started)` 喂 `_emit_tool_audit(duration_ms=...)`。改成:各返回前把该值也挂到 ToolMessage 的 `additional_kwargs`。三处:

**(a) 成功路径**(`:2050` `return outcome` 之前)—— `outcome[0]` 是 ToolMessage,复用即将传给 audit 的同一 `_elapsed_ms(started)`:

```python
        duration_ms = _elapsed_ms(started)
        outcome[0].additional_kwargs["duration_ms"] = duration_ms
        _record_tool_metrics(name, started, "ok" if ok else "error")
        await _emit_tool_audit(
            audit_logger,
            ctx,
            ...
            duration_ms=duration_ms,   # ← 复用同一变量,替换原 _elapsed_ms(started)
            ...
        )
        return outcome
```

**(b) 未知工具路径**(`:2081` 的 `return (ToolMessage(...), {}, 0, ...)`)—— 构造时加 `additional_kwargs`,并让 audit 复用同值:

```python
        duration_ms = _elapsed_ms(started)
        await _emit_tool_audit(..., duration_ms=duration_ms, ...)  # 替换原 _elapsed_ms(started)
        ...
        return (
            ToolMessage(
                content=content,
                tool_call_id=call_id,
                status="error",
                name=name,
                additional_kwargs={"duration_ms": duration_ms},
            ),
            {},
            0,
            classify_tool_error(tool_name=name, error=exc, spec=None),
        )
```

**(c) 中间件阻塞路径**(`:2117` 的 `return (ToolMessage(...), {}, 0, ...)`)—— 同 (b):

```python
        duration_ms = _elapsed_ms(started)
        await _emit_tool_audit(..., duration_ms=duration_ms, ...)  # 替换原 _elapsed_ms(started)
        ...
        return (
            ToolMessage(
                content=_format_error(exc),
                tool_call_id=call_id,
                status="error",
                name=name,
                additional_kwargs={"duration_ms": duration_ms},
            ),
            {},
            0,
            classify_tool_error(tool_name=name, error=exc, blocked=True),
        )
```

> 注:每路径只调一次 `_elapsed_ms(started)` 存进 `duration_ms` 局部,audit 与 message 共用 —— 不重复测量、口径一致。

- [ ] **Step 4: 跑测试确认 PASS**

Run: `cd services/orchestrator && uv run pytest tests/test_tool_audit.py -v`
Expected: PASS(新用例 + 原有全绿,含 audit duration 断言不回归)。

- [ ] **Step 5: 类型检查** —— Run: `uv run mypy services/orchestrator/src/orchestrator/graph_builder/builder.py` → clean。

- [ ] **Step 6: 提交**

```bash
git add services/orchestrator/src/orchestrator/graph_builder/builder.py services/orchestrator/tests/test_tool_audit.py
git commit -m "feat(playground): per-tool duration_ms 挂到 ToolMessage additional_kwargs"
```

---

## Task 3: 前端 —— `fmtDuration` 格式化纯函数

**Files:**
- Create: `apps/admin-ui/src/pages/agent_detail/playground/duration_format.ts`
- Test: `apps/admin-ui/src/pages/agent_detail/playground/__tests__/duration_format.test.ts`

**Interfaces:**
- Produces:`export function fmtDuration(ms: number): string`。

- [ ] **Step 1: 写失败测试**

```typescript
import { describe, expect, it } from "vitest";

import { fmtDuration } from "../duration_format";

describe("fmtDuration", () => {
  it("renders sub-second as integer ms", () => {
    expect(fmtDuration(0)).toBe("0ms");
    expect(fmtDuration(820)).toBe("820ms");
    expect(fmtDuration(999)).toBe("999ms");
  });
  it("renders seconds with one decimal at/above 1s", () => {
    expect(fmtDuration(1000)).toBe("1.0s");
    expect(fmtDuration(1200)).toBe("1.2s");
    expect(fmtDuration(59900)).toBe("59.9s");
  });
  it("renders minutes+seconds at/above 60s", () => {
    expect(fmtDuration(60000)).toBe("1m0s");
    expect(fmtDuration(62000)).toBe("1m2s");
  });
  it("carries rounding that would hit 60s into the next minute", () => {
    expect(fmtDuration(119500)).toBe("2m0s"); // 1m + round(59.5)=60 → 2m0s
  });
});
```

- [ ] **Step 2: FAIL** —— Run: `cd apps/admin-ui && npx vitest run src/pages/agent_detail/playground/__tests__/duration_format.test.ts` → 模块不存在。

- [ ] **Step 3: 写实现**

`apps/admin-ui/src/pages/agent_detail/playground/duration_format.ts`:

```typescript
/**
 * Human-readable per-step / per-tool duration. Adaptive units:
 * sub-second → integer ms; < 1min → seconds (1 decimal); else minutes+seconds.
 * Batch 4a — fills the StepTimeline duration slot Batch 3 left empty.
 */
export function fmtDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  let minutes = Math.floor(ms / 60000);
  let seconds = Math.round((ms % 60000) / 1000);
  if (seconds === 60) {
    minutes += 1;
    seconds = 0;
  }
  return `${minutes}m${seconds}s`;
}
```

- [ ] **Step 4: PASS + typecheck** —— Run: `cd apps/admin-ui && npx vitest run src/pages/agent_detail/playground/__tests__/duration_format.test.ts && pnpm typecheck` → PASS + exit 0。

- [ ] **Step 5: 提交**

```bash
git add apps/admin-ui/src/pages/agent_detail/playground/duration_format.ts apps/admin-ui/src/pages/agent_detail/playground/__tests__/duration_format.test.ts
git commit -m "feat(playground): fmtDuration 自适应耗时格式化"
```

---

## Task 4: 前端 —— 解析器读出 `durationMs`(timeline.ts + tool_timeline.ts)

**Files:**
- Modify: `apps/admin-ui/src/api/timeline.ts`(`AgentStep`/`AuxNodeItem` 接口 + `parseTimeline` 各 push)
- Modify: `apps/admin-ui/src/api/tool_timeline.ts`(`ToolCallEntry` 接口 + `parseToolCalls` 结果分支)
- Test: `apps/admin-ui/src/api/__tests__/timeline.test.ts`、`apps/admin-ui/src/api/__tests__/tool_timeline.test.ts`(各扩)

**Interfaces:**
- Consumes:Task 1 的 node `_duration_ms`(通道内)、Task 2 的 ToolMessage `additional_kwargs.duration_ms`。
- Produces:`AgentStep.durationMs: number | null`、`AuxNodeItem.durationMs: number | null`、`ToolCallEntry.durationMs: number | null`。

- [ ] **Step 1: 写失败测试(timeline.ts)**

`apps/admin-ui/src/api/__tests__/timeline.test.ts` 追加:

```typescript
it("reads node _duration_ms into agent step and aux node (null when absent)", () => {
  const events = [
    upd("agent", {
      step_count: 1,
      _duration_ms: 1200,
      messages: [{ type: "ai", content: "hi" }],
    }, "t1"),
    upd("memory_recall", {
      _duration_ms: 300,
      recalled_memories: [{ id: "m1", kind: "fact", content: "x", importance: 0.5, confidence: 0.5 }],
    }, "t2"),
    upd("agent", { step_count: 2, messages: [{ type: "ai", content: "no-dur" }] }, "t3"),
  ];
  const items = parseTimeline(events);
  const steps = items.filter((i) => i.kind === "agent");
  const mem = items.find((i) => i.kind === "memory_recall");
  expect(steps[0].kind === "agent" && steps[0].durationMs).toBe(1200);
  expect(steps[1].kind === "agent" && steps[1].durationMs).toBe(null);
  expect(mem && mem.kind === "memory_recall" && mem.durationMs).toBe(300);
});
```

- [ ] **Step 2: 写失败测试(tool_timeline.ts)**

`apps/admin-ui/src/api/__tests__/tool_timeline.test.ts` 追加(骨架照该文件现有 parseToolCalls 用例的 `updates` 帧构造):

```typescript
it("reads per-tool duration_ms from the tool result additional_kwargs", () => {
  const events = [
    ev("updates", { agent: { messages: [
      { type: "ai", content: "", tool_calls: [{ id: "c1", name: "exec_python", args: {} }] },
    ] } }, "t1"),
    ev("updates", { tools: { messages: [
      { type: "tool", tool_call_id: "c1", name: "exec_python", content: "ok", status: "success",
        additional_kwargs: { duration_ms: 840 } },
    ] } }, "t2"),
  ];
  const entries = parseToolCalls(events);
  expect(entries[0].durationMs).toBe(840);
});

it("leaves durationMs null when the tool result carries no duration", () => {
  const events = [
    ev("updates", { tools: { messages: [
      { type: "tool", tool_call_id: "c2", name: "web_search", content: "ok", status: "success" },
    ] } }, "t1"),
  ];
  expect(parseToolCalls(events)[0].durationMs).toBe(null);
});
```

> 若 `tool_timeline.test.ts` 尚无 `ev(...)` 帮手,照 `timeline.test.ts` 的 `ev(event, data, receivedAt)` 定义补一个本地帮手(返回 `{ id: null, event, data, rawData: "", receivedAt }`)。

- [ ] **Step 3: FAIL** —— Run: `cd apps/admin-ui && npx vitest run src/api/__tests__/timeline.test.ts src/api/__tests__/tool_timeline.test.ts` → 新用例失败(`durationMs` 不存在)。

- [ ] **Step 4: 写实现 —— timeline.ts**

接口加字段:`AgentStep` 与 `AuxNodeItem` 各 `+ durationMs: number | null;`。

在文件顶部帮手区(`textOf` 附近)加:

```typescript
function durationOf(ch: Record<string, unknown>): number | null {
  const v = ch._duration_ms;
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}
```

`parseTimeline` 里:
- AgentStep 的 `push({ kind: "agent", ... })` 加 `durationMs: durationOf(ch),`。
- 每个 aux push(`memory_recall` / `planner` / `reflect` / `memory_writeback`)各加 `durationMs: durationOf(ch),`。

- [ ] **Step 5: 写实现 —— tool_timeline.ts**

`ToolCallEntry` 接口加 `durationMs: number | null;`。两个 `ensure(...)` 的 init 对象各加 `durationMs: null,`(call 侧 `:220`、result 侧 `:245`)。

在 result 分支(`m.type === "tool"` 那块,填 `entry.status`/`entry.resultPreview` 之后)加:

```typescript
        const ak = m.additional_kwargs;
        const durRaw =
          ak !== null && typeof ak === "object"
            ? (ak as Record<string, unknown>).duration_ms
            : undefined;
        if (typeof durRaw === "number" && Number.isFinite(durRaw)) {
          entry.durationMs = durRaw;
        }
```

- [ ] **Step 6: PASS + typecheck** —— Run: `cd apps/admin-ui && npx vitest run src/api/__tests__/timeline.test.ts src/api/__tests__/tool_timeline.test.ts && pnpm typecheck` → PASS + exit 0。

- [ ] **Step 7: 提交**

```bash
git add apps/admin-ui/src/api/timeline.ts apps/admin-ui/src/api/tool_timeline.ts apps/admin-ui/src/api/__tests__/timeline.test.ts apps/admin-ui/src/api/__tests__/tool_timeline.test.ts
git commit -m "feat(playground): parseTimeline/parseToolCalls 读出 durationMs"
```

---

## Task 5: 前端 —— StepTimeline 渲染耗时 + i18n

把 `durationMs`(Task 4)用 `fmtDuration`(Task 3)渲进 StepTimeline:agent 步头、工具卡头、aux 行各显耗时;`null` 隐藏。加**单个** i18n aria-label 键。

**Files:**
- Modify: `apps/admin-ui/src/pages/agent_detail/playground/StepTimeline.tsx`
- Modify: `apps/admin-ui/src/i18n/locales/{en,zh-CN}.ts`
- Test: `apps/admin-ui/src/pages/agent_detail/playground/__tests__/StepTimeline.test.tsx`(扩)

**Interfaces:**
- Consumes:`AgentStep.durationMs`/`AuxNodeItem.durationMs`/`ToolCallEntry.durationMs`(Task 4)、`fmtDuration`(Task 3)。

- [ ] **Step 1: 加 i18n 键(en 类型+值 / zh 值)** —— 键 `tl_duration`:en 值 `"step duration"`、zh 值 `"本步耗时"`。三处同增(`en.ts` 的 `TranslationKeys` 接口 + `en.ts` 值 + `zh-CN.ts` 值),照 Batch 3 `tl_*` 键的落点风格。

- [ ] **Step 2: 写失败测试** —— `StepTimeline.test.tsx` 追加:一个 `items` 含带 `durationMs` 的 agent 步(嵌一个带 `durationMs` 的工具)+ 一个 `durationMs: null` 的步。断言:
  - 有 duration 的 agent 步渲出 `fmtDuration` 串(如 `"1.2s"`),且该 `<span>` 带 `aria-label`(值 = `t("playground.tl_duration")` 解析结果,jsdom 下按 en = `"step duration"`;断言用 `getByLabelText` 或查 `aria-label` 属性,locale-agnostic 照 Batch 3 先例)。
  - 嵌套工具卡渲出其 `fmtDuration`(如 `"840ms"`)。
  - `durationMs: null` 的步不渲染耗时元素(查该步内无耗时文本 / 无 `tl_duration` aria-label)。

  测试构造 `items` 照 Batch 3 `StepTimeline.test.tsx` 现有 agent-step/tool fixture,补 `durationMs` 字段(`ToolCallEntry` 需带 `durationMs`)。

- [ ] **Step 3: FAIL** —— Run: `cd apps/admin-ui && npx vitest run src/pages/agent_detail/playground/__tests__/StepTimeline.test.tsx`。

- [ ] **Step 4: 写实现** —— 在 StepTimeline.tsx:
  - `import { fmtDuration } from "./duration_format";`。
  - **agent 步头**(Batch 3 的 `步骤号·node·model·finish·token` meta 行,即 `step-meta` 区):追加耗时元素 —— `item.durationMs !== null` 时渲 `<span aria-label={t("playground.tl_duration")}>{fmtDuration(item.durationMs)}</span>`,样式随 meta 行现有 token/finish 元素(`var(--ew-text-tertiary)` 一类),`null` 不渲。
  - **工具卡头**:StepTimeline 复用的 `ToolCallCard`(来自 `components/ToolTimeline.tsx`)—— 在 StepTimeline 侧渲工具时,若 `tool.durationMs !== null`,在工具卡状态徽章旁渲同款 `<span aria-label={t("playground.tl_duration")}>{fmtDuration(tool.durationMs)}</span>`。**注:** 若 `ToolCallCard` 内部不便加,则在 StepTimeline 包工具卡的容器里加耗时元素,不改 `ToolCallCard` 渲染(保 ToolTimeline 复用不回归);实现者按实际结构择一,勿改 ToolTimeline 的 3 个回归测试所断言的输出。
  - **aux 行**(`AuxNodeRow`):摘要行末尾同款渲 `item.durationMs`(`null` 隐藏)。
  - 用具名 `import type { ReactNode }`,不注解 `JSX.Element`。样式 `var(--ew-*)` 不硬编码 hex。

- [ ] **Step 5: PASS + typecheck + 全量** —— Run: `cd apps/admin-ui && npx vitest run && pnpm typecheck` → 全绿 + exit 0。特别确认 `ToolTimeline` 回归 3/3 不变(若走了改 `ToolCallCard` 的路,回归须仍绿)。

- [ ] **Step 6: 提交**

```bash
git add apps/admin-ui/src/pages/agent_detail/playground/StepTimeline.tsx apps/admin-ui/src/i18n/locales/en.ts apps/admin-ui/src/i18n/locales/zh-CN.ts apps/admin-ui/src/pages/agent_detail/playground/__tests__/StepTimeline.test.tsx
git commit -m "feat(playground): StepTimeline 渲染每步/每工具耗时 + tl_duration aria 键"
```

---

## 验收(Batch 4a 整体)

- [ ] 后端:`cd services/orchestrator && uv run pytest tests/test_sse.py tests/test_tool_audit.py -v` 全绿;`uv run mypy services/orchestrator/src` clean。
- [ ] 前端:`cd apps/admin-ui && npx vitest run` 全绿;`pnpm typecheck` exit 0;`ToolTimeline` 回归 3/3 不变。
- [ ] `grep -rn "tool-timeline\|tool-call-card\|step-timeline\|step-card\|node-row" apps/admin-ui/e2e/` —— 本批不改 testid,仅加耗时文本;确认无 e2e 因此失效。
- [ ] 手动冒烟:跑一条含工具/多步的 run → StepTimeline 每步头显耗时、工具卡各显 per-tool ms、run 进行中即实时可见;无计时数据的步不显耗时(不占位)。
- [ ] 回归:StepTimeline 其余渲染(Batch 3)、RunDetail 的 ToolTimeline、raw 事件视图不变。

## 依赖与顺序

后端契约(Task 1 node 帧、Task 2 工具帧)是前端解析(Task 4)的数据源;`fmtDuration`(Task 3)纯前端独立;渲染(Task 5)依赖 T3+T4。序:**T1 → T2 → T3 → T4 → T5**(T1/T2 独立可互换;T3 可任意时点)。

## Self-Review(计划 vs spec 4a)

- **item 12 node 级(superstep 间隔)** → T1(sse.py 注入)+ T4(parseTimeline 读)+ T5(渲染)。✅
- **item 12 工具级(复用 builder 已算)** → T2(挂 additional_kwargs)+ T4(parseToolCalls 读)+ T5(工具卡渲染)。✅
- **帧形状(通道内非顶层)** → T1 注入进 node dict;T4 从 `ch._duration_ms` 读。✅
- **格式自适应 + 边界** → T3 `fmtDuration` + 边界测试(999/1000/60000/进位)。✅
- **单 aria 键三处 parity** → T5 Step 1。✅
- **null 隐藏 / 缺失不报错** → T1/T2 缺失不注入;T4 `durationOf`/守卫返 null;T5 `null` 不渲。✅
- **不碰 Langfuse/trace_id、不新增埋点** → 仅 sse.py 消费侧 + builder 表面化已有值。✅
- **Batch 1-3 教训** → pnpm typecheck / ReactNode 具名 / uv run pytest+mypy / ToolTimeline 回归 / e2e grep,全在 Global Constraints + 各 Task。✅
- **无 placeholder**:T1/T3/T4 完整代码;T2 impl 完整、测试指向同文件现成 fixture 复制(具体 pointer 非「similar to Task N」);T5 渲染转写指向 Batch 3 现有 StepTimeline 结构(现存组件,非 placeholder)。✅
