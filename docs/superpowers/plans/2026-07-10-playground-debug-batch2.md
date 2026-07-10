# 调试台 Batch 2(P1:结构化埋没通道)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 agent 执行轨迹里"为什么这么走"的埋没通道结构化渲染到调试台:AgentState 通道(recalled_memories / tool_failures / reflections / subagent_invocations / 标量信号)、retry / compaction 事件、per-step token 粒度。

**Architecture:** 一个小后端序列化修复(`_to_jsonable` 支持 pydantic + dataclass,让 4 个通道从 repr 字符串变真 JSON)+ 前端解析器(纯函数 TDD)+ 渲染组件。复用现成 `PlanPanel`(REST 自取)与 `parseCompactionEvents`;抽出共享 `EventCard` / `CompactionCard`。

**Tech Stack:** 后端 orchestrator Python(pytest);前端 React + Vite + AntD 5 + react-i18next(vitest + @testing-library/react)。

## Global Constraints

- **序列化前提(T1)是 T2/T6 的基础**:4 个通道(recalled_memories/tool_failures/reflections/subagent_invocations)修复前是 Python repr 字符串;T1 修复后是 JSON。前端解析器按**修复后的 JSON 形状**(pydantic `model_dump(mode="json")` / dataclass `asdict` 的 snake_case 字段名)写测试 fixture。
- 前端 i18n:所有用户可见文案走 i18n,`en.ts`(**类型接口 + 值**)+ `zh-CN.ts`(值)三处同增(en.ts 有 `TranslationKeys` 接口,漏一处 tsc 挂)。
- 前端类型:导出函数带显式入参/返回类型;外部/不可信数据用 `unknown` 再收窄,禁 `any`。就地填充累加器沿用现有文件风格。
- 前端验证:**用 `pnpm typecheck`(=`tsc -b --noEmit`)不是裸 `npx tsc`** —— Batch 1 踩过坑(裸 tsc 漏掉全局 JSX 命名空间/build-mode 错)。测试 `cd apps/admin-ui && npx vitest run <path>` + `pnpm typecheck`。
- 后端:PEP8 + 类型注解;pytest。测试从 `services/orchestrator` 跑(照该服务现有 pytest 惯例)。
- 样式令牌用 CSS 变量(`var(--ew-*)`),不硬编码色值。
- 提交:每 Task 末尾 commit,conventional commits。属性签名全局已禁用。

**数据形状参考(勘查所得;snake_case = 修复后 on-wire):**
- 帧封套 `{node: {channel: value}}`。node 名:`agent`/`tools`/`memory_recall`/`planner`/`reflect` 等。
- `recalled_memories`(memory_recall 节点,list[MemoryItem]):`id`/`kind`("fact"|"episodic")/`content`/`importance`/`confidence`(+其它忽略)。
- `tool_failures`(tools 节点,list[ClassifiedToolError],**每步被 agent 节点重置为 []**):`tool_name`/`error_class`/`summary`/`retryable`/`advice`/`path`。
- `reflections`(reflect 节点,**append reducer** → 每帧是增量):`verdict`("accept"|"revise")/`critique`/`run_id`。
- `subagent_invocations`(tools 节点,**append reducer** → 增量):`task_id`/`sub_thread_id`/`name`/`agent_ref`/`child_depth`/`status`/`result_excerpt`/`error`/`started_at`/`finished_at`/`iteration_used`/`llm_call_count`/`wall_clock_ms`。
- 标量(agent 节点,干净 JSON):`no_progress_streak`(int)/`escalate_next`(bool)/`step_count_refund_pending`(int)。
- `retry` 事件 payload:`{attempt, error_class, backoff_s}`。
- `compaction` 事件 payload:`{passes, tokens_before, tokens_after, summary_chars}`(已有 `parseCompactionEvents`)。
- per-step token:每 AI message 的 `usage_metadata`;`step_count` 在 **node 级**(与 `messages` 同在 agent 节点 dict),`messagesOf` 会抹掉 node 关联 → per-step 解析须走 `Object.entries(evt.data)`。

**不在 Batch 2 范围:** 事件过滤/搜索、step 时间线布局(Batch 3);SSE duration / trace-facade / Langfuse(Batch 4)。

---

## Task 1: 后端 `_to_jsonable` 支持 pydantic + dataclass 序列化

**Files:**
- Modify: `services/orchestrator/src/orchestrator/sse.py`(`_to_jsonable` + imports)
- Test: `services/orchestrator/tests/test_sse.py`

**Interfaces:**
- Produces:修复后 `_to_jsonable` 对 pydantic `BaseModel` → `model_dump(mode="json")`,对 dataclass 实例 → `dataclasses.asdict` 再递归。4 个通道在 `updates` 帧里变真 JSON(嵌套 dict/list)。

- [ ] **Step 1: 写失败测试**

在 `services/orchestrator/tests/test_sse.py` 末尾追加(直接测 `_to_jsonable`,它是模块级函数可导入):

```python
import dataclasses

from orchestrator.sse import _to_jsonable


def test_to_jsonable_serializes_pydantic_basemodel() -> None:
    from pydantic import BaseModel

    class _Step(BaseModel):
        id: str
        status: str

    class _Plan(BaseModel):
        goal: str
        steps: tuple[_Step, ...]

    plan = _Plan(goal="ship it", steps=(_Step(id="1", status="pending"),))
    out = _to_jsonable({"planner": {"plan": plan}})
    assert out == {
        "planner": {"plan": {"goal": "ship it", "steps": [{"id": "1", "status": "pending"}]}}
    }


def test_to_jsonable_serializes_dataclass() -> None:
    @dataclasses.dataclass(frozen=True)
    class _Err:
        tool_name: str
        error_class: str
        retryable: bool

    err = _Err(tool_name="exec_python", error_class="transient", retryable=True)
    out = _to_jsonable({"tools": {"tool_failures": [err]}})
    assert out == {
        "tools": {
            "tool_failures": [
                {"tool_name": "exec_python", "error_class": "transient", "retryable": True}
            ]
        }
    }
```

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `cd services/orchestrator && python -m pytest tests/test_sse.py::test_to_jsonable_serializes_pydantic_basemodel tests/test_sse.py::test_to_jsonable_serializes_dataclass -v`
Expected: FAIL —— pydantic model / dataclass 被 `str(value)` 兜底成 repr 字符串,`==` dict 断言不成立。

- [ ] **Step 3: 改实现**

`sse.py` 顶部 imports 区加(与其它 stdlib / 三方 import 归位):

```python
import dataclasses
from pydantic import BaseModel
```

`_to_jsonable` 里,在 `datetime` 分支之后、`Mapping` 分支之前插入两个分支(**必须在 `BaseMessage` 分支之后** —— BaseMessage 是 pydantic 子类,已在前面早截,顺序不能颠倒):

```python
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, BaseModel):
        # pydantic v2 → canonical JSON-mode dict (UUID/datetime already
        # stringified); recurse for any residual non-JSON leaf.
        return _to_jsonable(value.model_dump(mode="json"))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        # frozen dataclasses (e.g. ClassifiedToolError) → dict; recurse so
        # nested UUID/datetime leaves get stringified.
        return _to_jsonable(dataclasses.asdict(value))
    if isinstance(value, Mapping):
```

同时更新 `_to_jsonable` 的 docstring 转换清单,加两行说明 pydantic/dataclass(在现有 `BaseMessage` 行下补):

```python
    - :class:`pydantic.BaseModel` → ``model_dump(mode="json")``.
    - dataclass instance → ``dataclasses.asdict`` (recursed).
```

- [ ] **Step 4: 跑测试确认 PASS**

Run: `cd services/orchestrator && python -m pytest tests/test_sse.py -v`
Expected: PASS(含新增 2 条 + 既有 `test_run_agent_serializes_base_messages` 等回归不变)。

- [ ] **Step 5: mypy(该服务 CI 同款)**

Run: `cd services/orchestrator && python -m mypy src/orchestrator/sse.py`
Expected: 无新增错误(若有既有 baseline 错误,确认非本次引入)。

- [ ] **Step 6: 提交**

```bash
git add services/orchestrator/src/orchestrator/sse.py services/orchestrator/tests/test_sse.py
git commit -m "fix(orchestrator): _to_jsonable 结构化序列化 pydantic + dataclass

recalled_memories/tool_failures/reflections/subagent_invocations 之前
经 str() 兜底成 repr 字符串(非 JSON),原始事件视图与前端解析都无法用。
加 BaseModel(model_dump)+ dataclass(asdict)分支。BaseMessage 分支在前
不受影响。"
```

---

## Task 2: 前端 `agent_state.ts` —— AgentState 通道解析器

**Files:**
- Create: `apps/admin-ui/src/api/agent_state.ts`
- Test: `apps/admin-ui/src/api/__tests__/agent_state.test.ts`

**Interfaces:**
- Consumes: `SseEvent`(`api/sessions`);Task 1 修复后的 JSON 帧形状。
- Produces:
  ```ts
  export interface RecalledMemory { id: string; kind: string; content: string; importance: number; confidence: number }
  export interface ToolFailure { toolName: string; errorClass: string; summary: string; retryable: boolean; advice: string }
  export interface AgentReflection { verdict: string; critique: string }
  export interface SubagentInvocation { taskId: string; name: string; agentRef: string; status: string; iterationUsed: number; llmCallCount: number; wallClockMs: number; resultExcerpt: string; error: string | null }
  export interface AgentSignals { noProgressStreak: number | null; escalateNext: boolean | null; stepCountRefundPending: number | null }
  export interface AgentStateView { recalledMemories: RecalledMemory[]; toolFailures: ToolFailure[]; reflections: AgentReflection[]; subagentInvocations: SubagentInvocation[]; signals: AgentSignals }
  export function parseAgentState(events: readonly SseEvent[]): AgentStateView
  ```
- 语义:`recalledMemories` 取最后一个非空帧(memory_recall 只设一次);`toolFailures` **累计**所有非空帧(每步被重置,是失败日志);`reflections` / `subagentInvocations` **累计**(append reducer 增量),`subagentInvocations` 按 `taskId` 去重保留最后(running→completed);`signals` 每标量取最后一次出现值。

- [ ] **Step 1: 写失败测试**

`apps/admin-ui/src/api/__tests__/agent_state.test.ts`:

```typescript
import { describe, expect, it } from "vitest";

import type { SseEvent } from "../sessions";
import { parseAgentState } from "../agent_state";

function updates(node: string, channels: Record<string, unknown>): SseEvent {
  return { id: null, event: "updates", data: { [node]: channels }, rawData: "", receivedAt: "" };
}

describe("parseAgentState", () => {
  it("takes the last non-empty recalled_memories", () => {
    const events = [
      updates("memory_recall", {
        recalled_memories: [
          { id: "m1", kind: "fact", content: "user likes tea", importance: 0.6, confidence: 0.9 },
        ],
      }),
    ];
    const { recalledMemories } = parseAgentState(events);
    expect(recalledMemories).toEqual([
      { id: "m1", kind: "fact", content: "user likes tea", importance: 0.6, confidence: 0.9 },
    ]);
  });

  it("accumulates non-empty tool_failures across steps (reset in between)", () => {
    const events = [
      updates("tools", {
        tool_failures: [
          { tool_name: "exec_python", error_class: "transient", summary: "boom", retryable: true, advice: "retry" },
        ],
      }),
      updates("agent", { tool_failures: [] }), // reset — must not wipe the log
      updates("tools", {
        tool_failures: [
          { tool_name: "web_search", error_class: "invalid_arguments", summary: "bad q", retryable: false, advice: "fix args" },
        ],
      }),
    ];
    const { toolFailures } = parseAgentState(events);
    expect(toolFailures.map((f) => f.toolName)).toEqual(["exec_python", "web_search"]);
    expect(toolFailures[0].errorClass).toBe("transient");
  });

  it("accumulates reflections and dedupes subagent_invocations by taskId (last wins)", () => {
    const events = [
      updates("reflect", { reflections: [{ verdict: "revise", critique: "missed a case" }] }),
      updates("tools", {
        subagent_invocations: [
          { task_id: "t1", name: "researcher", agent_ref: "researcher@1", status: "running",
            result_excerpt: "", error: null, iteration_used: 0, llm_call_count: 0, wall_clock_ms: 0 },
        ],
      }),
      updates("tools", {
        subagent_invocations: [
          { task_id: "t1", name: "researcher", agent_ref: "researcher@1", status: "completed",
            result_excerpt: "done", error: null, iteration_used: 3, llm_call_count: 5, wall_clock_ms: 1200 },
        ],
      }),
    ];
    const { reflections, subagentInvocations } = parseAgentState(events);
    expect(reflections).toEqual([{ verdict: "revise", critique: "missed a case" }]);
    expect(subagentInvocations).toHaveLength(1);
    expect(subagentInvocations[0].status).toBe("completed");
    expect(subagentInvocations[0].wallClockMs).toBe(1200);
  });

  it("takes the latest scalar signals", () => {
    const events = [
      updates("agent", { no_progress_streak: 1, escalate_next: false, step_count_refund_pending: 0 }),
      updates("agent", { no_progress_streak: 2, escalate_next: true, step_count_refund_pending: 1 }),
    ];
    const { signals } = parseAgentState(events);
    expect(signals).toEqual({ noProgressStreak: 2, escalateNext: true, stepCountRefundPending: 1 });
  });

  it("returns empty view for no updates frames", () => {
    const view = parseAgentState([]);
    expect(view.recalledMemories).toEqual([]);
    expect(view.signals).toEqual({ noProgressStreak: null, escalateNext: null, stepCountRefundPending: null });
  });
});
```

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `cd apps/admin-ui && npx vitest run src/api/__tests__/agent_state.test.ts`
Expected: FAIL —— `../agent_state` 模块不存在。

- [ ] **Step 3: 写实现**

`apps/admin-ui/src/api/agent_state.ts`:

```typescript
/**
 * AgentState channel parser — distills the debug-relevant AgentState channels
 * out of a turn's SSE ``updates`` frames (``{node: {channel: value}}``).
 *
 * Requires the backend ``_to_jsonable`` fix (pydantic/dataclass → JSON); before
 * it these channels arrive as Python repr strings and cannot be parsed.
 */
import type { SseEvent } from "./sessions";

export interface RecalledMemory {
  id: string;
  kind: string;
  content: string;
  importance: number;
  confidence: number;
}
export interface ToolFailure {
  toolName: string;
  errorClass: string;
  summary: string;
  retryable: boolean;
  advice: string;
}
export interface AgentReflection {
  verdict: string;
  critique: string;
}
export interface SubagentInvocation {
  taskId: string;
  name: string;
  agentRef: string;
  status: string;
  iterationUsed: number;
  llmCallCount: number;
  wallClockMs: number;
  resultExcerpt: string;
  error: string | null;
}
export interface AgentSignals {
  noProgressStreak: number | null;
  escalateNext: boolean | null;
  stepCountRefundPending: number | null;
}
export interface AgentStateView {
  recalledMemories: RecalledMemory[];
  toolFailures: ToolFailure[];
  reflections: AgentReflection[];
  subagentInvocations: SubagentInvocation[];
  signals: AgentSignals;
}

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}
function num(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? v : 0;
}
function asObjArray(v: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(v)) return [];
  return v.filter((x): x is Record<string, unknown> => x !== null && typeof x === "object");
}

export function parseAgentState(events: readonly SseEvent[]): AgentStateView {
  let recalledMemories: RecalledMemory[] = [];
  const toolFailures: ToolFailure[] = [];
  const reflections: AgentReflection[] = [];
  const subagentByTask = new Map<string, SubagentInvocation>();
  const subagentOrder: string[] = [];
  const signals: AgentSignals = {
    noProgressStreak: null,
    escalateNext: null,
    stepCountRefundPending: null,
  };

  for (const evt of events) {
    if (evt.event !== "updates" || evt.data === null || typeof evt.data !== "object") continue;
    for (const node of Object.values(evt.data as Record<string, unknown>)) {
      if (node === null || typeof node !== "object") continue;
      const ch = node as Record<string, unknown>;

      if ("recalled_memories" in ch) {
        const mems = asObjArray(ch.recalled_memories).map((m) => ({
          id: str(m.id),
          kind: str(m.kind),
          content: str(m.content),
          importance: num(m.importance),
          confidence: num(m.confidence),
        }));
        if (mems.length > 0) recalledMemories = mems; // last non-empty wins
      }

      if ("tool_failures" in ch) {
        for (const f of asObjArray(ch.tool_failures)) {
          toolFailures.push({
            toolName: str(f.tool_name),
            errorClass: str(f.error_class),
            summary: str(f.summary),
            retryable: f.retryable === true,
            advice: str(f.advice),
          });
        }
      }

      if ("reflections" in ch) {
        for (const r of asObjArray(ch.reflections)) {
          reflections.push({ verdict: str(r.verdict), critique: str(r.critique) });
        }
      }

      if ("subagent_invocations" in ch) {
        for (const s of asObjArray(ch.subagent_invocations)) {
          const taskId = str(s.task_id);
          if (taskId === "") continue;
          if (!subagentByTask.has(taskId)) subagentOrder.push(taskId);
          subagentByTask.set(taskId, {
            taskId,
            name: str(s.name),
            agentRef: str(s.agent_ref),
            status: str(s.status),
            iterationUsed: num(s.iteration_used),
            llmCallCount: num(s.llm_call_count),
            wallClockMs: num(s.wall_clock_ms),
            resultExcerpt: str(s.result_excerpt),
            error: typeof s.error === "string" ? s.error : null,
          });
        }
      }

      if (typeof ch.no_progress_streak === "number") signals.noProgressStreak = ch.no_progress_streak;
      if (typeof ch.escalate_next === "boolean") signals.escalateNext = ch.escalate_next;
      if (typeof ch.step_count_refund_pending === "number") {
        signals.stepCountRefundPending = ch.step_count_refund_pending;
      }
    }
  }

  return {
    recalledMemories,
    toolFailures,
    reflections,
    subagentInvocations: subagentOrder.map((id) => subagentByTask.get(id) as SubagentInvocation),
    signals,
  };
}
```

- [ ] **Step 4: 跑测试确认 PASS**

Run: `cd apps/admin-ui && npx vitest run src/api/__tests__/agent_state.test.ts`
Expected: PASS(5 用例)。

- [ ] **Step 5: 类型检查**

Run: `cd apps/admin-ui && pnpm typecheck`
Expected: exit 0。

- [ ] **Step 6: 提交**

```bash
git add apps/admin-ui/src/api/agent_state.ts apps/admin-ui/src/api/__tests__/agent_state.test.ts
git commit -m "feat(playground): AgentState 通道解析器(记忆/失败/反思/子agent/信号)"
```

---

## Task 3: 前端 retry 事件解析器

**Files:**
- Modify: `apps/admin-ui/src/api/tool_timeline.ts`(挨着 `parseCompactionEvents`)
- Test: `apps/admin-ui/src/api/__tests__/tool_timeline.test.ts`

**Interfaces:**
- Produces:
  ```ts
  export interface RetryEntry { receivedAt: string; attempt: number; errorClass: string; backoffS: number }
  export function parseRetryEvents(events: readonly SseEvent[]): RetryEntry[]
  ```

- [ ] **Step 1: 写失败测试**

`tool_timeline.test.ts` 顶部 import 加 `parseRetryEvents`,文件末尾追加:

```typescript
describe("parseRetryEvents", () => {
  it("parses retry frames in order, skipping malformed ones", () => {
    const events = [
      { id: null, event: "retry", data: { attempt: 1, error_class: "TimeoutError", backoff_s: 2.5 }, rawData: "", receivedAt: "t1" },
      { id: null, event: "updates", data: {}, rawData: "", receivedAt: "t2" },
      { id: null, event: "retry", data: { attempt: 2 }, rawData: "", receivedAt: "t3" }, // malformed → skip
    ];
    expect(parseRetryEvents(events)).toEqual([
      { receivedAt: "t1", attempt: 1, errorClass: "TimeoutError", backoffS: 2.5 },
    ]);
  });
});
```

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `cd apps/admin-ui && npx vitest run src/api/__tests__/tool_timeline.test.ts`
Expected: FAIL —— `parseRetryEvents` 未导出。

- [ ] **Step 3: 写实现**

`tool_timeline.ts` 里 `parseCompactionEvents` 之后追加:

```typescript
/** One transient-retry event (sse.py retry publish): the run hit a retryable
 *  error and backed off before re-attempting the astream loop. */
export interface RetryEntry {
  receivedAt: string;
  attempt: number;
  errorClass: string;
  backoffS: number;
}

/** Extract ordered retry events (``event: "retry"`` with
 *  ``{attempt, error_class, backoff_s}``). Malformed frames are skipped. */
export function parseRetryEvents(events: readonly SseEvent[]): RetryEntry[] {
  const out: RetryEntry[] = [];
  for (const evt of events) {
    if (evt.event !== "retry" || evt.data === null || typeof evt.data !== "object") continue;
    const d = evt.data as Record<string, unknown>;
    if (typeof d.attempt !== "number" || typeof d.error_class !== "string") continue;
    out.push({
      receivedAt: evt.receivedAt,
      attempt: d.attempt,
      errorClass: d.error_class,
      backoffS: typeof d.backoff_s === "number" ? d.backoff_s : 0,
    });
  }
  return out;
}
```

- [ ] **Step 4: 跑测试确认 PASS + 类型检查**

Run: `cd apps/admin-ui && npx vitest run src/api/__tests__/tool_timeline.test.ts && pnpm typecheck`
Expected: PASS + exit 0。

- [ ] **Step 5: 提交**

```bash
git add apps/admin-ui/src/api/tool_timeline.ts apps/admin-ui/src/api/__tests__/tool_timeline.test.ts
git commit -m "feat(playground): retry 事件解析器 parseRetryEvents"
```

---

## Task 4: 前端 per-step token 粒度

**Files:**
- Modify: `apps/admin-ui/src/api/turn_summary.ts`
- Test: `apps/admin-ui/src/api/__tests__/turn_summary.test.ts`

**Interfaces:**
- Consumes: `TurnUsage`(已存在)
- Produces:`TurnSummary` 新增 `perStepUsage: StepUsage[]`;`export interface StepUsage { node: string; stepCount: number | null; usage: TurnUsage }` —— 每条 AI message 一行,保留其所属 node 的 `step_count`。summed `usage` 不变。

- [ ] **Step 1: 写失败测试**

`turn_summary.test.ts` 追加:

```typescript
it("emits per-step usage rows keyed by node + step_count without dropping the sum", () => {
  const events: SseEvent[] = [
    {
      id: "a", event: "updates",
      data: { agent: { step_count: 1, messages: [
        { type: "ai", content: "", usage_metadata: { input_tokens: 100, output_tokens: 10, total_tokens: 110 } },
      ] } },
      rawData: "", receivedAt: "2026-07-10T00:00:00Z",
    },
    {
      id: "b", event: "updates",
      data: { agent: { step_count: 2, messages: [
        { type: "ai", content: "done", usage_metadata: { input_tokens: 50, output_tokens: 5, total_tokens: 55 } },
      ] } },
      rawData: "", receivedAt: "2026-07-10T00:00:01Z",
    },
  ];
  const s = summarizeTurn(events);
  expect(s.perStepUsage).toHaveLength(2);
  expect(s.perStepUsage[0]).toEqual({
    node: "agent", stepCount: 1,
    usage: { inputTokens: 100, outputTokens: 10, totalTokens: 110, cacheReadTokens: 0, cacheCreationTokens: 0, reasoningTokens: 0 },
  });
  expect(s.perStepUsage[1].stepCount).toBe(2);
  // summed usage still intact
  expect(s.usage?.totalTokens).toBe(165);
});
```

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `cd apps/admin-ui && npx vitest run src/api/__tests__/turn_summary.test.ts`
Expected: FAIL —— `perStepUsage` 不存在。

- [ ] **Step 3: 改实现**

`turn_summary.ts`:加 `StepUsage` 接口(在 `TurnSummary` 前)+ 抽一个 `usageFromMetadata` helper 复用求和逻辑。

在 `TurnUsage` 接口后加:

```typescript
export interface StepUsage {
  node: string;
  stepCount: number | null;
  usage: TurnUsage;
}
```

`TurnSummary` 接口 `modelName` 后加:

```typescript
  /** Per-AI-message usage, each tagged with its owning node + step_count. */
  perStepUsage: StepUsage[];
}
```

抽一个把单条 `usage_metadata` 转 `TurnUsage` 的 helper(放在 `summarizeTurn` 前),供求和与 per-step 共用:

```typescript
function usageFromMetadata(um: Record<string, unknown>): TurnUsage {
  const itd = um.input_token_details;
  const otd = um.output_token_details;
  const d = (v: unknown): Record<string, unknown> =>
    v !== null && typeof v === "object" ? (v as Record<string, unknown>) : {};
  return {
    inputTokens: asInt(um.input_tokens),
    outputTokens: asInt(um.output_tokens),
    totalTokens: asInt(um.total_tokens),
    cacheReadTokens: asInt(d(itd).cache_read),
    cacheCreationTokens: asInt(d(itd).cache_creation),
    reasoningTokens: asInt(d(otd).reasoning),
  };
}
```

在 `summarizeTurn` 里:声明 `const perStepUsage: StepUsage[] = [];`(在 `usage` 累加器旁)。在**现有** `for (const evt of events)` 循环里,把"按 node 读 step_count"的块(现有 `Object.values(...)` 遍历)改为 `Object.entries(...)`,并在其中对该 node 的 AI messages 收 per-step usage —— 保留现有 summed 逻辑不动,新增 per-step 收集:

在现有 `for (const evt of events) { if (evt.event !== "updates") continue; ... }` 内,step_count 遍历块替换为:

```typescript
    if (evt.data !== null && typeof evt.data === "object") {
      for (const [nodeName, node] of Object.entries(evt.data as Record<string, unknown>)) {
        if (node === null || typeof node !== "object") continue;
        const n = node as Record<string, unknown>;
        const sc = typeof n.step_count === "number" ? n.step_count : null;
        if (sc !== null && (stepCount === null || sc > stepCount)) stepCount = sc;
        const msgs = Array.isArray(n.messages) ? n.messages : [];
        for (const m of msgs) {
          if (m === null || typeof m !== "object") continue;
          const mm = m as Record<string, unknown>;
          if (mm.type !== "ai") continue;
          const um = mm.usage_metadata;
          if (um !== null && typeof um === "object") {
            perStepUsage.push({
              node: nodeName,
              stepCount: sc,
              usage: usageFromMetadata(um as Record<string, unknown>),
            });
          }
        }
      }
    }
```

> 注:现有的 `for (const m of messagesOf(evt.data))` 求和块**保留不动**(它负责 finalText/reasoning/finishReason/modelName + summed usage);上面新块只额外收 per-step。为避免 summed usage 与 per-step 的取数逻辑漂移,可选:把求和块里的 `usage.inputTokens += asInt(u.input_tokens)` 等改为 `const su = usageFromMetadata(u); usage.inputTokens += su.inputTokens; ...`。若改,保证 summed 结果与改前一致(现有 turn_summary 测试兜底)。

return 语句加 `perStepUsage`:

```typescript
  return { finalText, reasoning, usage: reported ? usage : null, stepCount, latencyMs, finishReason, modelName, perStepUsage };
```

- [ ] **Step 4: 跑测试确认 PASS + 类型检查**

Run: `cd apps/admin-ui && npx vitest run src/api/__tests__/turn_summary.test.ts && pnpm typecheck`
Expected: PASS(含既有求和/finish/model 用例回归)+ exit 0。

- [ ] **Step 5: 提交**

```bash
git add apps/admin-ui/src/api/turn_summary.ts apps/admin-ui/src/api/__tests__/turn_summary.test.ts
git commit -m "feat(playground): summarizeTurn 保留 per-step token 粒度"
```

---

## Task 5: 抽出共享 `EventCard` + `CompactionCard`(去重 + 复用前提)

把两处重复的 `EventCard` 与 `EventStreamPanel` 私有的 `CompactionCard`/`CompactionSummaryList` 抽到共享组件,供 Playground 复用(Batch 1 遗留的 EventCard 去重也在此了结)。

**Files:**
- Create: `apps/admin-ui/src/components/EventCard.tsx`
- Create: `apps/admin-ui/src/components/CompactionCard.tsx`
- Modify: `apps/admin-ui/src/pages/run_detail/EventStreamPanel.tsx`(改用共享组件,删本地副本)
- Modify: `apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx`(改用共享 `EventCard`,删本地副本)

**Interfaces:**
- Produces:`export function EventCard({ evt }: { evt: SseEvent }): JSX`(合并两处逻辑,`data-testid` 用统一 `event-card-${evt.event}`,内部 EVENT_COLOR 自带);`export function CompactionSummaryList({ items }: { items: readonly CompactionSummary[] })` + `export function CompactionCard({ item }: { item: CompactionSummary })`。

- [ ] **Step 1: 创建共享 `EventCard.tsx`**

以 `EventStreamPanel.tsx:248-298` 的 `EventCard` 为基(它多显 `evt.id`,是超集),搬进 `components/EventCard.tsx`,导出,并把 `EVENT_COLOR` 一并搬入(从 `EventStreamPanel.tsx:33-39`;补 Playground 侧独有的 key 如需)。用 `data-testid={`event-card-${evt.event}`}`。完整代码照 `EventStreamPanel.tsx:248-298`(含 `EVENT_COLOR`、`CopyButton` import)。

> 说明:Playground 侧 `EventCard`(`PlaygroundTab.tsx`)显示 `toLocaleTimeString()`,RunDetail 侧显示 `evt.id`。合并版**同时**渲染两者(有则显):时间戳(`evt.receivedAt` 有则显)+ `evt.id`(非 null 显)。保留两处既有 `data-testid` 兼容性:统一为 `event-card-${evt.event}`,并更新引用它们的测试。

- [ ] **Step 2: 创建共享 `CompactionCard.tsx`**

把 `EventStreamPanel.tsx:197-246` 的 `CompactionSummaryList` + `CompactionCard` 原样搬进 `components/CompactionCard.tsx`,导出两者(代码见上文勘查引用,逐字搬,i18n key 不变)。import `CompactionSummary`(from `../api/tool_timeline`)、`Tag`/`Typography`、`useTranslation`。

- [ ] **Step 3: 改 `EventStreamPanel.tsx` 用共享组件**

删本地 `EventCard`(:248-298)、`CompactionSummaryList`/`CompactionCard`(:197-246)、本地 `EVENT_COLOR`(:33-39);顶部 import 改为:

```typescript
import { EventCard } from "../../components/EventCard";
import { CompactionSummaryList } from "../../components/CompactionCard";
```

其余用法(`<CompactionSummaryList items=.../>`、`<EventCard evt=.../>`)不变。

- [ ] **Step 4: 改 `PlaygroundTab.tsx` 用共享 `EventCard`**

删本地 `EventCard`(约 `:2016-2065` 区,Batch 1 后行号可能微移 —— 按函数名定位)+ 本地 `EVENT_COLOR`;顶部加 `import { EventCard } from "../../components/EventCard";`。

- [ ] **Step 5: 更新受影响测试的 testid**

grep 两处旧 testid(`playground-event-`、`event-stream-event-`)在 `*.test.tsx` 的引用,改为统一 `event-card-`。

Run: `cd apps/admin-ui && grep -rn "playground-event-\|event-stream-event-" src/`,逐处改到 `event-card-`。

- [ ] **Step 6: 全量测试 + 类型检查**

Run: `cd apps/admin-ui && npx vitest run && pnpm typecheck`
Expected: 全绿 + exit 0(EventStreamPanel / PlaygroundTab 两处渲染回归不变)。

- [ ] **Step 7: 提交**

```bash
git add apps/admin-ui/src/components/EventCard.tsx apps/admin-ui/src/components/CompactionCard.tsx apps/admin-ui/src/pages/run_detail/EventStreamPanel.tsx apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx apps/admin-ui/src/pages/**/__tests__/*.test.tsx
git commit -m "refactor(admin-ui): 抽出共享 EventCard + CompactionCard,去重两处副本"
```

---

## Task 6: `AgentStatePanels` + retry/per-step 渲染组件

一个导出、可单测的组件,渲染 Task 2 的 `AgentStateView` + Task 3 的 retry + Task 4 的 per-step usage。

**Files:**
- Create: `apps/admin-ui/src/pages/agent_detail/playground/AgentStatePanels.tsx`
- Create: `apps/admin-ui/src/pages/agent_detail/playground/__tests__/AgentStatePanels.test.tsx`
- Modify: `apps/admin-ui/src/i18n/locales/{en,zh-CN}.ts`

**Interfaces:**
- Consumes: `AgentStateView`(Task 2)、`RetryEntry`(Task 3)、`StepUsage`(Task 4)。
- Produces:`export interface AgentStatePanelsProps { state: AgentStateView; retries: RetryEntry[]; perStepUsage: StepUsage[] }` + `export function AgentStatePanels(props): JSX | null`(全空时返回 null)。

- [ ] **Step 1: 加 i18n 键(en 类型接口 + en 值 + zh 值)**

在 `playground` 块加(en 三处对齐类型;zh 值):

| key | zh | en |
|---|---|---|
| `state_memories` | 召回记忆 | Recalled memory |
| `state_failures` | 工具失败 | Tool failures |
| `state_reflections` | 反思 | Reflections |
| `state_subagents` | 子 agent 调用 | Subagent calls |
| `state_signals` | 运行信号 | Run signals |
| `state_retries` | 重试 | Retries |
| `state_per_step` | 分步 Token | Per-step tokens |
| `signal_no_progress` | 无进展连击 | No-progress streak |
| `signal_escalate` | 下轮升级 | Escalate next |
| `retry_attempt` | 第 {{n}} 次 · {{cls}} · 退避 {{s}}s | attempt {{n}} · {{cls}} · backoff {{s}}s |

- [ ] **Step 2: 写失败测试**

`AgentStatePanels.test.tsx`(渲染断言,testid 驱动):

```typescript
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import "../../../../i18n";

import { AgentStatePanels } from "../AgentStatePanels";
import type { AgentStateView } from "../../../../api/agent_state";

const empty: AgentStateView = {
  recalledMemories: [], toolFailures: [], reflections: [], subagentInvocations: [],
  signals: { noProgressStreak: null, escalateNext: null, stepCountRefundPending: null },
};

describe("AgentStatePanels", () => {
  it("returns null when everything is empty", () => {
    const { container } = render(<AgentStatePanels state={empty} retries={[]} perStepUsage={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders a tool-failure row with its error class", () => {
    render(
      <AgentStatePanels
        state={{ ...empty, toolFailures: [{ toolName: "exec_python", errorClass: "transient", summary: "boom", retryable: true, advice: "retry" }] }}
        retries={[]}
        perStepUsage={[]}
      />,
    );
    expect(screen.getByTestId("agent-state-failures")).toHaveTextContent("exec_python");
    expect(screen.getByTestId("agent-state-failures")).toHaveTextContent("transient");
  });

  it("renders retries and a subagent call", () => {
    render(
      <AgentStatePanels
        state={{ ...empty, subagentInvocations: [{ taskId: "t1", name: "researcher", agentRef: "researcher@1", status: "completed", iterationUsed: 3, llmCallCount: 5, wallClockMs: 1200, resultExcerpt: "done", error: null }] }}
        retries={[{ receivedAt: "t", attempt: 2, errorClass: "TimeoutError", backoffS: 1.5 }]}
        perStepUsage={[]}
      />,
    );
    expect(screen.getByTestId("agent-state-subagents")).toHaveTextContent("researcher");
    expect(screen.getByTestId("agent-state-retries")).toHaveTextContent("TimeoutError");
  });
});
```

- [ ] **Step 3: 跑测试确认 FAIL** —— Run: `cd apps/admin-ui && npx vitest run src/pages/agent_detail/playground/__tests__/AgentStatePanels.test.tsx` → 模块不存在。

- [ ] **Step 4: 写 `AgentStatePanels.tsx`**

组件按段渲染(每段有值才渲染;全空返回 null)。用 AntD `Tag` / `Typography.Text` / 轻列表,`data-testid` 分别 `agent-state-memories/failures/reflections/subagents/signals/retries/per-step`。样式令牌用 `var(--ew-*)`。文案走 i18n。tool_failures 失败行标红(`var(--ew-text-danger, #cf1322)`),subagent 显 `name · status · {iterationUsed}it/{llmCallCount}call/{wallClockMs}ms`,retry 用 `retry_attempt` 插值,per-step 每行显 `node #stepCount · total tokens`。signals 里 `noProgressStreak>0` 或 `escalateNext===true` 才高亮显示。

```tsx
import { Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";

import type { AgentStateView } from "../../../api/agent_state";
import type { RetryEntry } from "../../../api/tool_timeline";
import type { StepUsage } from "../../../api/turn_summary";

const { Text } = Typography;

export interface AgentStatePanelsProps {
  state: AgentStateView;
  retries: RetryEntry[];
  perStepUsage: StepUsage[];
}

export function AgentStatePanels({ state, retries, perStepUsage }: AgentStatePanelsProps) {
  const { t } = useTranslation();
  const { recalledMemories, toolFailures, reflections, subagentInvocations, signals } = state;
  const hasSignals =
    (signals.noProgressStreak ?? 0) > 0 || signals.escalateNext === true;
  const anything =
    recalledMemories.length || toolFailures.length || reflections.length ||
    subagentInvocations.length || retries.length || perStepUsage.length || hasSignals;
  if (!anything) return null;

  const section = (testId: string, label: string, body: React.ReactNode) => (
    <div data-testid={testId} style={{ marginTop: 6 }}>
      <Text type="secondary" style={{ fontSize: 11 }}>{label}</Text>
      <div style={{ marginTop: 2 }}>{body}</div>
    </div>
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {recalledMemories.length > 0 &&
        section("agent-state-memories", t("playground.state_memories"),
          recalledMemories.map((m) => (
            <div key={m.id} style={{ fontSize: 12 }}>
              <Tag bordered={false}>{m.kind}</Tag>{m.content}
            </div>
          )))}
      {toolFailures.length > 0 &&
        section("agent-state-failures", t("playground.state_failures"),
          toolFailures.map((f, i) => (
            <div key={i} style={{ fontSize: 12, color: "var(--ew-text-danger, #cf1322)" }}>
              <span className="mono">{f.toolName}</span> · {f.errorClass} — {f.advice}
            </div>
          )))}
      {reflections.length > 0 &&
        section("agent-state-reflections", t("playground.state_reflections"),
          reflections.map((r, i) => (
            <div key={i} style={{ fontSize: 12 }}>
              <Tag bordered={false} color={r.verdict === "revise" ? "orange" : "green"}>{r.verdict}</Tag>
              {r.critique}
            </div>
          )))}
      {subagentInvocations.length > 0 &&
        section("agent-state-subagents", t("playground.state_subagents"),
          subagentInvocations.map((s) => (
            <div key={s.taskId} style={{ fontSize: 12 }}>
              <span className="mono">{s.name}</span> · {s.status} ·{" "}
              {s.iterationUsed}it/{s.llmCallCount}call/{s.wallClockMs}ms
            </div>
          )))}
      {retries.length > 0 &&
        section("agent-state-retries", t("playground.state_retries"),
          retries.map((r, i) => (
            <Tag key={i} bordered={false} color="orange" style={{ margin: "0 4px 4px 0" }}>
              {t("playground.retry_attempt", { n: r.attempt, cls: r.errorClass, s: r.backoffS })}
            </Tag>
          )))}
      {perStepUsage.length > 0 &&
        section("agent-state-per-step", t("playground.state_per_step"),
          perStepUsage.map((u, i) => (
            <div key={i} style={{ fontSize: 12 }} className="mono">
              {u.node} #{u.stepCount ?? "?"} · {u.usage.totalTokens} tok
            </div>
          )))}
      {hasSignals &&
        section("agent-state-signals", t("playground.state_signals"),
          <span style={{ fontSize: 12 }}>
            {(signals.noProgressStreak ?? 0) > 0 && (
              <Tag color="volcano" bordered={false}>
                {t("playground.signal_no_progress")}: {signals.noProgressStreak}
              </Tag>
            )}
            {signals.escalateNext === true && (
              <Tag color="red" bordered={false}>{t("playground.signal_escalate")}</Tag>
            )}
          </span>)}
    </div>
  );
}
```

> 注:若 `React.ReactNode` 触发 UMD-global 报错(Batch 1 教训),改用从 `react` 具名 import `type ReactNode` 并用 `ReactNode`。以 `pnpm typecheck` 为准。

- [ ] **Step 5: 跑测试确认 PASS + 类型检查** —— Run: `cd apps/admin-ui && npx vitest run src/pages/agent_detail/playground/__tests__/AgentStatePanels.test.tsx && pnpm typecheck` → PASS + exit 0。

- [ ] **Step 6: 提交**

```bash
git add apps/admin-ui/src/pages/agent_detail/playground/AgentStatePanels.tsx apps/admin-ui/src/pages/agent_detail/playground/__tests__/AgentStatePanels.test.tsx apps/admin-ui/src/i18n/locales/en.ts apps/admin-ui/src/i18n/locales/zh-CN.ts
git commit -m "feat(playground): AgentStatePanels 渲染通道/retry/per-step + i18n"
```

---

## Task 7: 接入 `PlaygroundTab` TurnCard(panels + PlanPanel + compaction）

把 Task 2/3/4/6 的产物 + `PlanPanel` + 共享 `CompactionSummaryList` 接进 TurnCard,放在事件折叠段上方的可折叠"执行状态"区。

**Files:**
- Modify: `apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx`
- Modify: `apps/admin-ui/src/i18n/locales/{en,zh-CN}.ts`(折叠段标题)

**Interfaces:**
- Consumes: `parseAgentState`(T2)、`parseRetryEvents`(T3)、`summarizeTurn().perStepUsage`(T4)、`AgentStatePanels`(T6)、`parseCompactionEvents`+`CompactionSummaryList`(T5)、`PlanPanel`(现成)。

- [ ] **Step 1: 加 i18n 键**(en 类型+值 / zh 值,`playground` 块):`state_section` = zh"执行状态" / en"Run state";`plan_section` = zh"计划" / en"Plan"。

- [ ] **Step 2: TurnCard 内接线**

`PlaygroundTab.tsx` 顶部 import 补:

```typescript
import { parseAgentState } from "../../api/agent_state";
import { parseRetryEvents, parseCompactionEvents } from "../../api/tool_timeline"; // 合并进已有 tool_timeline import
import { AgentStatePanels } from "./playground/AgentStatePanels";
import { CompactionSummaryList } from "../../components/CompactionCard";
import { PlanPanel } from "../run_detail/PlanPanel";
```

在 `TurnCard` 组件体内(`const summary = summarizeTurn(turn.events)` 附近)加:

```typescript
  const agentState = parseAgentState(turn.events);
  const retries = parseRetryEvents(turn.events);
  const compactions = parseCompactionEvents(turn.events);
```

在 events `Collapse`(Batch 1 的 `defaultActiveKey={["events"]}`)的 `items` 数组里,在 `events` 项**之前**插入一个 `run-state` 折叠项(默认展开与否:不加进 `defaultActiveKey`,默认收起),children 组合:

```tsx
          ...(agentState || retries.length || compactions.length || summary.perStepUsage.length
            ? [{
                key: "run-state",
                label: t("playground.state_section"),
                children: (
                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    {threadId && (
                      <PlanPanel threadId={threadId} runStatus={turn.status === "running" ? "running" : "success"} />
                    )}
                    <AgentStatePanels state={agentState} retries={retries} perStepUsage={summary.perStepUsage} />
                    {compactions.length > 0 && <CompactionSummaryList items={compactions} />}
                  </div>
                ),
              }]
            : []),
```

> 注:`PlanPanel` 走 REST 自取,仅在 `threadId` 存在时挂;它自带空态处理。`run-state` 段整体在无任何内容时不渲染(上面的条件 + `AgentStatePanels` 返回 null 双保险)。

- [ ] **Step 3: 全量测试 + 类型检查**

Run: `cd apps/admin-ui && npx vitest run && pnpm typecheck`
Expected: 全绿 + exit 0。

- [ ] **Step 4: 手动冒烟**

跑一轮触发工具失败 / 子 agent / 记忆召回的 run(或用带这些通道的既有 thread),确认调试台"执行状态"段出现对应卡片;确认 plan 显示。

- [ ] **Step 5: 提交**

```bash
git add apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx apps/admin-ui/src/i18n/locales/en.ts apps/admin-ui/src/i18n/locales/zh-CN.ts
git commit -m "feat(playground): TurnCard 接入执行状态段(AgentState/plan/retry/compaction/per-step)"
```

---

## 验收(Batch 2 整体)

- [ ] 后端:`cd services/orchestrator && python -m pytest tests/test_sse.py -v` 全绿;`mypy src/orchestrator/sse.py` 无新错。
- [ ] 前端:`cd apps/admin-ui && npx vitest run` 全绿;`pnpm typecheck` exit 0。
- [ ] 手动冒烟:调试台某轮"执行状态"段显示 plan / 召回记忆 / 工具失败(标红)/ 反思 / 子 agent(it/call/ms)/ retry / per-step token / 无进展信号 —— 按 run 实际含有的通道。
- [ ] 回归:RunDetail 的 EventStreamPanel(改用共享 EventCard/CompactionCard 后)渲染不变。

## 依赖与顺序

`T1 → T2`(T2 fixture 按 T1 后 JSON 形状);T3 / T4 / T5 相互独立(可任意序,均不依赖 T1);T6 依赖 T2/T3/T4;T7 依赖 T5(CompactionSummaryList 抽出)+ T6 + 现成 PlanPanel。推荐序:**T1 → T2 → T3 → T4 → T5 → T6 → T7**。

## Self-Review(计划 vs spec Batch 2)

- **spec 项 6(AgentState 通道卡片)** → T2(解析)+ T6(渲染)+ T7(接入);plan 复用 PlanPanel(T7)。✅
- **spec 项 7(retry / compaction 在调试台)** → T3(retry 解析)+ T5(compaction 抽共享)+ T6/T7(渲染接入)。✅
- **spec 项 8(per-step token)** → T4(解析)+ T6/T7(渲染)。✅
- **序列化前提(spec 修订)** → T1。✅
- **EventCard 去重(Batch 1 遗留 + spec §8)** → T5。✅
- **类型一致性**:`AgentStateView`/`RetryEntry`/`StepUsage`/`AgentStatePanelsProps` 定义(T2/T3/T4/T6)与消费(T6/T7)命名一致。✅
- **无 placeholder**:每步含真实代码/命令/期望。✅
- **Batch 1 教训纳入**:全程用 `pnpm typecheck` 而非裸 `npx tsc`;`React.ReactNode` UMD 风险已标注 fallback。✅
