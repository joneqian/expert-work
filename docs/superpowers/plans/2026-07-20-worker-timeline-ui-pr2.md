# B2 PR2 前端 worker 子时间线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 调试台时间线把 `worker` SSE 帧渲染为 spawn_worker/subagent 工具卡下的可展开嵌套子时间线(live 实时增长 + 历史回放同渲染),end 帧作卡片脚注汇总。

**Architecture:** 新叶子模块 `api/worker_timeline.ts` 把 `worker` 帧折叠成 `WorkerTimeline` 树(start/update/end → status/steps/summary,孙 worker 经 parent_worker_id 嵌套);`parseTimeline` 预扫一次并把树挂到对应 `ToolCallEntry.workers`;`StepTimeline.tsx` 工具卡内新 `WorkerSubTimeline` 组件(复用 caret-expand 惯例)。PlaygroundTab 进料零改动(worker 帧已无差别进 `turn.events`,live/replay 双路径)。

**Tech Stack:** React + TypeScript / react-i18next / vitest + testing-library。

**Spec:** `docs/superpowers/specs/2026-07-19-worker-observability-design.md` 前端节。**一处已确认的 spec 放宽**:孙 worker 不挂"父 worker 对应步"(update 消息摘要的 tool_calls 无 id,无从对应),改为按帧到达序内联进父 worker 的条目列表——时序视觉等效。

## Global Constraints

- 后端帧契约(#1026 已合,权威):event 名 `"worker"`;信封 `worker_id / parent_worker_id(depth-1 为 null)/ parent_tool_call_id / label / agent_ref / depth / kind("start"|"update"|"end") / wseq / data`;start data=`{task_excerpt, role, max_steps}`;update data=`{node, step_count?, _duration_ms, messages:[{type, content_excerpt?, tool_calls?:[{name,args_excerpt}], name?, tool_result_excerpt?}]}`;end data=`{outcome:"success"|"max_steps"|"cancelled", iteration_used, llm_call_count, wall_clock_ms}`。
- worker 帧先于 tools_node 结果 chunk 到达;pending 工具卡已存在(parseToolCalls 预扫全量事件),挂靠无时序问题。
- 解析必须防御:end 无 start / 未知 parent_worker_id / parent_tool_call_id 为 null / data 形状异常——全部不抛,能挂则挂,不能挂则丢。
- React key:worker 用 `workerId`,step 用 `wseq`;`TimelineItem` 联合体**不加**新 kind(worker 骑在 ToolCallEntry 上);`timeline_filter.ts` 不动。
- i18n 新键三处:`en.ts` interface(playground 块 :1012 区)+ `en.ts` 英文值块(:3868 区 tl_* 附近)+ `zh-CN.ts` 中文值块(:1085 区);先 grep 撞键;i18n parity 测试会抓漏。
- 样式循既有令牌:`var(--ew-*)` 语义色 consts(StepTimeline.tsx:23-27)、`fmtDuration`(duration_format.ts)、caret `▾`/`▸` + useState 展开惯例;不引新依赖。
- 测试命令:`cd apps/admin-ui && pnpm exec vitest run <paths>`;终门 `pnpm typecheck` + `pnpm exec vitest run src`(全量);IDE 诊断 stale,只认真 tsc/vitest。
- 提交格式:`feat:`/`test:`,无 attribution。

---

## File Structure

| 文件 | 职责 |
|---|---|
| Create `apps/admin-ui/src/api/worker_timeline.ts` | `WorkerTimeline`/`WorkerStepSummary` 类型 + `parseWorkerFrames(events)`(纯函数叶子) |
| Modify `apps/admin-ui/src/api/tool_timeline.ts` | `ToolCallEntry` 加可选 `workers?: WorkerTimeline[]` |
| Modify `apps/admin-ui/src/api/timeline.ts` | `parseTimeline` 预扫 worker 帧并挂到 ToolCallEntry;`"worker"` 事件跳过主循环 |
| Modify `apps/admin-ui/src/pages/agent_detail/playground/StepTimeline.tsx` | `WorkerSubTimeline` 组件 + 工具卡内注入 |
| Modify `apps/admin-ui/src/i18n/locales/en.ts` + `zh-CN.ts` | `tl_worker_*` 键三处 |
| Test Create `apps/admin-ui/src/api/__tests__/worker_timeline.test.ts` | 解析单测 |
| Test Modify `apps/admin-ui/src/api/__tests__/timeline.test.ts` | 挂靠集成 |
| Test Modify `apps/admin-ui/src/pages/agent_detail/playground/__tests__/StepTimeline.test.tsx`(若无则建) | 组件渲染 |

---

### Task 1: `worker_timeline.ts` 解析纯函数

**Files:**
- Create: `apps/admin-ui/src/api/worker_timeline.ts`
- Test: `apps/admin-ui/src/api/__tests__/worker_timeline.test.ts`

**Interfaces:**
- Consumes: `SseEvent`(`apps/admin-ui/src/api/sessions.ts:206-213`,`{id, event, data, rawData, receivedAt}`)。
- Produces(后续 Task 依赖,精确):

```ts
export interface WorkerMessageSummary {
  type: string;
  contentExcerpt?: string;
  toolCalls?: { name: string; argsExcerpt: string }[];
  name?: string;
  toolResultExcerpt?: string;
}
export interface WorkerStepSummary {
  wseq: number;
  node: string;
  stepCount: number | null;
  durationMs: number;
  messages: WorkerMessageSummary[];
}
export type WorkerStatus = "running" | "success" | "max_steps" | "cancelled";
export interface WorkerTimeline {
  workerId: string;
  parentWorkerId: string | null;
  parentToolCallId: string | null;
  label: string;
  agentRef: string;
  depth: number;
  role: string | null;
  taskExcerpt: string;
  maxSteps: number | null;
  status: WorkerStatus;
  steps: WorkerStepSummary[];
  children: WorkerTimeline[];
  summary: { iterationUsed: number; llmCallCount: number; wallClockMs: number } | null;
}
export function parseWorkerFrames(events: readonly SseEvent[]): Map<string, WorkerTimeline[]>
// key = parent_tool_call_id;值 = 该工具调用直属(depth-1 相对该卡)worker 列表,到达序
```

- [ ] **Step 1: 写失败测试**

```ts
import { describe, expect, it } from "vitest";

import type { SseEvent } from "../sessions";
import { parseWorkerFrames } from "../worker_timeline";

let n = 0;
function wf(kind: string, over: Record<string, unknown> = {}, data: Record<string, unknown> = {}): SseEvent {
  n += 1;
  return {
    id: String(n),
    event: "worker",
    data: {
      worker_id: "w-1",
      parent_worker_id: null,
      parent_tool_call_id: "call-1",
      label: "spawn_worker",
      agent_ref: "dynamic:research",
      depth: 1,
      kind,
      wseq: n,
      data,
      ...over,
    },
    rawData: "",
    receivedAt: new Date().toISOString(),
  };
}

describe("parseWorkerFrames", () => {
  it("folds start/update/end into one WorkerTimeline", () => {
    const events = [
      wf("start", { wseq: 0 }, { task_excerpt: "research X", role: "research", max_steps: 32 }),
      wf("update", { wseq: 1 }, {
        node: "agent", step_count: 1, _duration_ms: 120,
        messages: [{ type: "ai", content_excerpt: "thinking", tool_calls: [{ name: "http_request", args_excerpt: "{}" }] }],
      }),
      wf("update", { wseq: 2 }, {
        node: "tools", _duration_ms: 300,
        messages: [{ type: "tool", name: "http_request", tool_result_excerpt: "<html>" }],
      }),
      wf("end", { wseq: 3 }, { outcome: "success", iteration_used: 2, llm_call_count: 1, wall_clock_ms: 900 }),
    ];
    const map = parseWorkerFrames(events);
    const [w] = map.get("call-1") ?? [];
    expect(w.workerId).toBe("w-1");
    expect(w.role).toBe("research");
    expect(w.taskExcerpt).toBe("research X");
    expect(w.maxSteps).toBe(32);
    expect(w.status).toBe("success");
    expect(w.steps).toHaveLength(2);
    expect(w.steps[0].node).toBe("agent");
    expect(w.steps[0].stepCount).toBe(1);
    expect(w.steps[0].durationMs).toBe(120);
    expect(w.steps[0].messages[0].toolCalls?.[0].name).toBe("http_request");
    expect(w.steps[1].messages[0].toolResultExcerpt).toBe("<html>");
    expect(w.summary).toEqual({ iterationUsed: 2, llmCallCount: 1, wallClockMs: 900 });
  });

  it("no end frame → status running, summary null", () => {
    const map = parseWorkerFrames([
      wf("start", { wseq: 0 }, { task_excerpt: "t", role: null, max_steps: 8 }),
      wf("update", { wseq: 1 }, { node: "agent", _duration_ms: 5, messages: [] }),
    ]);
    const [w] = map.get("call-1") ?? [];
    expect(w.status).toBe("running");
    expect(w.summary).toBeNull();
  });

  it("nests grandchild under parent via parent_worker_id", () => {
    const map = parseWorkerFrames([
      wf("start", { wseq: 0 }, { task_excerpt: "p", role: null, max_steps: 8 }),
      wf("start", { worker_id: "w-2", parent_worker_id: "w-1", parent_tool_call_id: "inner-call", depth: 2, wseq: 0 },
        { task_excerpt: "c", role: null, max_steps: 8 }),
      wf("end", { worker_id: "w-2", parent_worker_id: "w-1", parent_tool_call_id: "inner-call", depth: 2, wseq: 1 },
        { outcome: "success", iteration_used: 1, llm_call_count: 1, wall_clock_ms: 10 }),
      wf("end", { wseq: 1 }, { outcome: "success", iteration_used: 1, llm_call_count: 1, wall_clock_ms: 99 }),
    ]);
    const roots = map.get("call-1") ?? [];
    expect(roots).toHaveLength(1);
    expect(roots[0].children).toHaveLength(1);
    expect(roots[0].children[0].workerId).toBe("w-2");
    // 孙 worker 不出现在顶层 map(inner-call 不是父 run 的工具卡)
    expect(map.has("inner-call")).toBe(false);
  });

  it("end without start still yields an entry (degraded)", () => {
    const map = parseWorkerFrames([
      wf("end", { wseq: 0 }, { outcome: "cancelled", iteration_used: 0, llm_call_count: 0, wall_clock_ms: 1 }),
    ]);
    const [w] = map.get("call-1") ?? [];
    expect(w.status).toBe("cancelled");
    expect(w.taskExcerpt).toBe("");
  });

  it("drops frames without parent_tool_call_id at depth-1 and malformed data without throwing", () => {
    const map = parseWorkerFrames([
      wf("start", { parent_tool_call_id: null, wseq: 0 }, { task_excerpt: "orphan", role: null, max_steps: 1 }),
      { id: "x", event: "worker", data: "not-an-object", rawData: "", receivedAt: "" } as SseEvent,
      { id: "y", event: "updates", data: {}, rawData: "", receivedAt: "" } as SseEvent,
    ]);
    expect(map.size).toBe(0);
  });

  it("multiple workers on one tool call keep arrival order", () => {
    const map = parseWorkerFrames([
      wf("start", { worker_id: "a", wseq: 0 }, { task_excerpt: "1", role: null, max_steps: 1 }),
      wf("start", { worker_id: "b", wseq: 0 }, { task_excerpt: "2", role: null, max_steps: 1 }),
    ]);
    expect((map.get("call-1") ?? []).map((w) => w.workerId)).toEqual(["a", "b"]);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/mac/src/github/jone_qian/expert-work/apps/admin-ui && pnpm exec vitest run src/api/__tests__/worker_timeline.test.ts`
Expected: FAIL — 模块不存在

- [ ] **Step 3: 实现**

```ts
// apps/admin-ui/src/api/worker_timeline.ts
// B2 PR2 — 把持久化/实时同源的 "worker" SSE 帧折叠成工具卡可挂载的
// WorkerTimeline 树。纯函数,防御式:异常帧丢弃不抛。
// 帧契约见 docs/superpowers/specs/2026-07-19-worker-observability-design.md。

import type { SseEvent } from "./sessions";

export interface WorkerMessageSummary {
  type: string;
  contentExcerpt?: string;
  toolCalls?: { name: string; argsExcerpt: string }[];
  name?: string;
  toolResultExcerpt?: string;
}

export interface WorkerStepSummary {
  wseq: number;
  node: string;
  stepCount: number | null;
  durationMs: number;
  messages: WorkerMessageSummary[];
}

export type WorkerStatus = "running" | "success" | "max_steps" | "cancelled";

export interface WorkerTimeline {
  workerId: string;
  parentWorkerId: string | null;
  parentToolCallId: string | null;
  label: string;
  agentRef: string;
  depth: number;
  role: string | null;
  taskExcerpt: string;
  maxSteps: number | null;
  status: WorkerStatus;
  steps: WorkerStepSummary[];
  children: WorkerTimeline[];
  summary: { iterationUsed: number; llmCallCount: number; wallClockMs: number } | null;
}

interface RawFrame {
  worker_id: string;
  parent_worker_id: string | null;
  parent_tool_call_id: string | null;
  label: string;
  agent_ref: string;
  depth: number;
  kind: "start" | "update" | "end";
  wseq: number;
  data: Record<string, unknown>;
}

function asFrame(data: unknown): RawFrame | null {
  if (typeof data !== "object" || data === null) return null;
  const d = data as Record<string, unknown>;
  if (typeof d.worker_id !== "string" || typeof d.kind !== "string") return null;
  if (d.kind !== "start" && d.kind !== "update" && d.kind !== "end") return null;
  return {
    worker_id: d.worker_id,
    parent_worker_id: typeof d.parent_worker_id === "string" ? d.parent_worker_id : null,
    parent_tool_call_id: typeof d.parent_tool_call_id === "string" ? d.parent_tool_call_id : null,
    label: typeof d.label === "string" ? d.label : "",
    agent_ref: typeof d.agent_ref === "string" ? d.agent_ref : "",
    depth: typeof d.depth === "number" ? d.depth : 1,
    kind: d.kind,
    wseq: typeof d.wseq === "number" ? d.wseq : 0,
    data: typeof d.data === "object" && d.data !== null ? (d.data as Record<string, unknown>) : {},
  };
}

function ensureWorker(byId: Map<string, WorkerTimeline>, f: RawFrame): WorkerTimeline {
  let w = byId.get(f.worker_id);
  if (!w) {
    w = {
      workerId: f.worker_id,
      parentWorkerId: f.parent_worker_id,
      parentToolCallId: f.parent_tool_call_id,
      label: f.label,
      agentRef: f.agent_ref,
      depth: f.depth,
      role: null,
      taskExcerpt: "",
      maxSteps: null,
      status: "running",
      steps: [],
      children: [],
      summary: null,
    };
    byId.set(f.worker_id, w);
  }
  return w;
}

function summarizeMessages(raw: unknown): WorkerMessageSummary[] {
  if (!Array.isArray(raw)) return [];
  const out: WorkerMessageSummary[] = [];
  for (const m of raw) {
    if (typeof m !== "object" || m === null) continue;
    const r = m as Record<string, unknown>;
    const msg: WorkerMessageSummary = { type: typeof r.type === "string" ? r.type : "?" };
    if (typeof r.content_excerpt === "string") msg.contentExcerpt = r.content_excerpt;
    if (typeof r.name === "string") msg.name = r.name;
    if (typeof r.tool_result_excerpt === "string") msg.toolResultExcerpt = r.tool_result_excerpt;
    if (Array.isArray(r.tool_calls)) {
      msg.toolCalls = r.tool_calls.flatMap((c) => {
        if (typeof c !== "object" || c === null) return [];
        const cc = c as Record<string, unknown>;
        return [{
          name: typeof cc.name === "string" ? cc.name : "?",
          argsExcerpt: typeof cc.args_excerpt === "string" ? cc.args_excerpt : "",
        }];
      });
    }
    out.push(msg);
  }
  return out;
}

export function parseWorkerFrames(events: readonly SseEvent[]): Map<string, WorkerTimeline[]> {
  const byId = new Map<string, WorkerTimeline>();
  for (const evt of events) {
    if (evt.event !== "worker") continue;
    const f = asFrame(evt.data);
    if (f === null) continue;
    const w = ensureWorker(byId, f);
    if (f.kind === "start") {
      w.taskExcerpt = typeof f.data.task_excerpt === "string" ? f.data.task_excerpt : "";
      w.role = typeof f.data.role === "string" ? f.data.role : null;
      w.maxSteps = typeof f.data.max_steps === "number" ? f.data.max_steps : null;
    } else if (f.kind === "update") {
      w.steps.push({
        wseq: f.wseq,
        node: typeof f.data.node === "string" ? f.data.node : "?",
        stepCount: typeof f.data.step_count === "number" ? f.data.step_count : null,
        durationMs: typeof f.data._duration_ms === "number" ? f.data._duration_ms : 0,
        messages: summarizeMessages(f.data.messages),
      });
    } else {
      const outcome = f.data.outcome;
      w.status = outcome === "max_steps" || outcome === "cancelled" ? outcome : "success";
      w.summary = {
        iterationUsed: typeof f.data.iteration_used === "number" ? f.data.iteration_used : 0,
        llmCallCount: typeof f.data.llm_call_count === "number" ? f.data.llm_call_count : 0,
        wallClockMs: typeof f.data.wall_clock_ms === "number" ? f.data.wall_clock_ms : 0,
      };
    }
  }

  // 第二遍:孙 worker 挂父;根按 parent_tool_call_id 分组(到达序 = Map 插入序)。
  const roots = new Map<string, WorkerTimeline[]>();
  for (const w of byId.values()) {
    if (w.parentWorkerId !== null) {
      const parent = byId.get(w.parentWorkerId);
      if (parent) parent.children.push(w);
      continue; // 父缺失 → 丢(防御)
    }
    if (w.parentToolCallId === null) continue; // 无法挂卡 → 丢(防御)
    const bucket = roots.get(w.parentToolCallId);
    if (bucket) bucket.push(w);
    else roots.set(w.parentToolCallId, [w]);
  }
  return roots;
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pnpm exec vitest run src/api/__tests__/worker_timeline.test.ts`
Expected: PASS(6 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/admin-ui/src/api/worker_timeline.ts apps/admin-ui/src/api/__tests__/worker_timeline.test.ts
git commit -m "feat: B2 PR2 worker 帧解析纯函数(WorkerTimeline 树,防御式折叠)"
```

---

### Task 2: 挂靠 ToolCallEntry + parseTimeline 接线

**Files:**
- Modify: `apps/admin-ui/src/api/tool_timeline.ts:20-38`(ToolCallEntry)
- Modify: `apps/admin-ui/src/api/timeline.ts:66-122`(parseTimeline)
- Test Modify: `apps/admin-ui/src/api/__tests__/timeline.test.ts`

**Interfaces:**
- Consumes: Task 1 `parseWorkerFrames` / `WorkerTimeline`。
- Produces: `ToolCallEntry.workers?: WorkerTimeline[]`(仅有 worker 时才设);parseTimeline 输出中对应工具卡带 workers。

- [ ] **Step 1: 写失败测试(追加到 timeline.test.ts,沿用文件内 `ev`/`upd` fixture 惯例)**

```ts
it("attaches worker timelines to the matching tool call entry", () => {
  const events = [
    upd("agent", {
      messages: [
        {
          type: "ai",
          content: "",
          tool_calls: [{ id: "call-9", name: "spawn_worker", args: { task: "x" } }],
        },
      ],
      step_count: 1,
    }),
    ev("worker", {
      worker_id: "w-9", parent_worker_id: null, parent_tool_call_id: "call-9",
      label: "spawn_worker", agent_ref: "dynamic:general", depth: 1, kind: "start", wseq: 0,
      data: { task_excerpt: "x", role: null, max_steps: 8 },
    }),
    ev("worker", {
      worker_id: "w-9", parent_worker_id: null, parent_tool_call_id: "call-9",
      label: "spawn_worker", agent_ref: "dynamic:general", depth: 1, kind: "end", wseq: 1,
      data: { outcome: "success", iteration_used: 1, llm_call_count: 1, wall_clock_ms: 42 },
    }),
  ];
  const items = parseTimeline(events);
  const agent = items.find((i) => i.kind === "agent");
  expect(agent?.kind).toBe("agent");
  const tool = agent?.kind === "agent" ? agent.tools.find((t) => t.id === "call-9") : undefined;
  expect(tool?.workers).toHaveLength(1);
  expect(tool?.workers?.[0].status).toBe("success");
});

it("worker events do not become standalone timeline items", () => {
  const items = parseTimeline([
    ev("worker", {
      worker_id: "w", parent_worker_id: null, parent_tool_call_id: "c",
      label: "spawn_worker", agent_ref: "d", depth: 1, kind: "start", wseq: 0,
      data: { task_excerpt: "", role: null, max_steps: 1 },
    }),
  ]);
  expect(items).toHaveLength(0);
});
```

(`ev`/`upd` 形状以 `timeline.test.ts:6-11` 现有实现为准;AI 消息内 tool_calls 的具体字段形状照文件内既有工具用例。)

- [ ] **Step 2: 跑测试确认失败**

Run: `pnpm exec vitest run src/api/__tests__/timeline.test.ts`
Expected: 新增 2 FAIL(`workers` undefined / worker 事件落入未知处理)

- [ ] **Step 3: 实现**

`tool_timeline.ts` — `ToolCallEntry` 追加字段(import type 自 `./worker_timeline`):

```ts
  /** B2 PR2 — 本次调用派生的 worker 子时间线(spawn_worker / subagent);无则缺省。 */
  workers?: WorkerTimeline[];
```

`timeline.ts` — `parseTimeline` 开头预扫(`parseToolCalls` 调用旁,:68-69):

```ts
  const workersByCall = parseWorkerFrames(events);
```

主循环 marker 分支区(`:77-101`,`if (evt.event !== "updates") continue;` 之前)加:

```ts
    if (evt.event === "worker") continue; // 已由 parseWorkerFrames 预扫,挂在工具卡上
```

工具挂靠点(`:118-122`,`byId.get(id)` 取出 entry 后):

```ts
        const workers = workersByCall.get(id);
        if (workers) entry.workers = workers;
```

(具体变量名对齐现场代码;`entry` 即从 `byId` 取出的 `ToolCallEntry`。)

- [ ] **Step 4: 跑测试确认通过 + 邻居回归**

Run: `pnpm exec vitest run src/api/__tests__/timeline.test.ts src/api/__tests__/tool_timeline.test.ts src/api/__tests__/worker_timeline.test.ts`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add apps/admin-ui/src/api/tool_timeline.ts apps/admin-ui/src/api/timeline.ts apps/admin-ui/src/api/__tests__/timeline.test.ts
git commit -m "feat: B2 PR2 worker 时间线挂靠 ToolCallEntry(parseTimeline 预扫接线)"
```

---

### Task 3: `WorkerSubTimeline` 组件 + i18n

**Files:**
- Modify: `apps/admin-ui/src/pages/agent_detail/playground/StepTimeline.tsx`(工具卡块 :233-254 注入 + 新组件)
- Modify: `apps/admin-ui/src/i18n/locales/en.ts`(interface :1012 区 + 值块 :3957 区)、`apps/admin-ui/src/i18n/locales/zh-CN.ts`(:1171 区)
- Test: `apps/admin-ui/src/pages/agent_detail/playground/__tests__/StepTimeline.worker.test.tsx`(新)

**Interfaces:**
- Consumes: Task 2 的 `ToolCallEntry.workers`;`fmtDuration`(`./duration_format`);语义色 consts(StepTimeline.tsx:23-27)。
- Produces: 工具卡下嵌套渲染;e2e/测试选择器 `data-testid="worker-subtimeline"` / `worker-step` / `worker-summary`。

- [ ] **Step 1: i18n 键(三处,先 `grep -n "tl_worker" apps/admin-ui/src/i18n/locales/*.ts` 确认零撞)**

interface(en.ts playground 块):

```ts
    tl_worker_running: string;
    tl_worker_success: string;
    tl_worker_max_steps: string;
    tl_worker_cancelled: string;
    tl_worker_task: string;
    tl_worker_summary: string; // "{{steps}} steps · {{calls}} LLM calls · {{duration}}"
    tl_worker_children: string;
```

en 值:

```ts
    tl_worker_running: "running",
    tl_worker_success: "done",
    tl_worker_max_steps: "step limit",
    tl_worker_cancelled: "cancelled",
    tl_worker_task: "Task",
    tl_worker_summary: "{{steps}} steps · {{calls}} LLM calls · {{duration}}",
    tl_worker_children: "child workers",
```

zh-CN 值:

```ts
    tl_worker_running: "运行中",
    tl_worker_success: "完成",
    tl_worker_max_steps: "步数耗尽",
    tl_worker_cancelled: "已取消",
    tl_worker_task: "任务",
    tl_worker_summary: "{{steps}} 步 · {{calls}} 次 LLM · {{duration}}",
    tl_worker_children: "子 worker",
```

- [ ] **Step 2: 写失败组件测试(新文件;render 惯例照 playground/__tests__ 既有测试,i18n 测试环境默认 en)**

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { WorkerTimeline } from "../../../../api/worker_timeline";
import { WorkerSubTimeline } from "../StepTimeline";

function worker(over: Partial<WorkerTimeline> = {}): WorkerTimeline {
  return {
    workerId: "w-1",
    parentWorkerId: null,
    parentToolCallId: "call-1",
    label: "spawn_worker",
    agentRef: "dynamic:research",
    depth: 1,
    role: "research",
    taskExcerpt: "research X",
    maxSteps: 32,
    status: "success",
    steps: [
      { wseq: 1, node: "agent", stepCount: 1, durationMs: 120,
        messages: [{ type: "ai", contentExcerpt: "thinking", toolCalls: [{ name: "http_request", argsExcerpt: "{}" }] }] },
    ],
    children: [],
    summary: { iterationUsed: 2, llmCallCount: 1, wallClockMs: 900 },
    ...over,
  };
}

describe("WorkerSubTimeline", () => {
  it("renders header collapsed with role, status and summary", () => {
    render(<WorkerSubTimeline workers={[worker()]} />);
    const root = screen.getByTestId("worker-subtimeline");
    expect(root.textContent).toContain("research");
    expect(root.textContent).toContain("done");
    expect(screen.getByTestId("worker-summary").textContent).toContain("2 steps");
    expect(screen.queryByTestId("worker-step")).toBeNull(); // 默认收起
  });

  it("expands to steps on click", async () => {
    render(<WorkerSubTimeline workers={[worker()]} />);
    await userEvent.click(screen.getByTestId("worker-subtimeline-header"));
    expect(screen.getAllByTestId("worker-step")).toHaveLength(1);
    expect(screen.getByTestId("worker-step").textContent).toContain("http_request");
  });

  it("renders running status without summary and nests children recursively", async () => {
    const child = worker({ workerId: "w-2", depth: 2, status: "running", summary: null, steps: [], role: null });
    render(<WorkerSubTimeline workers={[worker({ children: [child] })]} />);
    await userEvent.click(screen.getByTestId("worker-subtimeline-header"));
    expect(screen.getAllByTestId("worker-subtimeline")).toHaveLength(2);
    expect(screen.getAllByTestId("worker-subtimeline")[1].textContent).toContain("running");
  });
});
```

- [ ] **Step 3: 跑测试确认失败**

Run: `pnpm exec vitest run src/pages/agent_detail/playground/__tests__/StepTimeline.worker.test.tsx`
Expected: FAIL — `WorkerSubTimeline` 未导出

- [ ] **Step 4: 实现组件 + 注入**

`StepTimeline.tsx` 新组件(export;放 `AuxNodeRow` 附近,风格照它:useState caret、语义色、mono 字体令牌):

```tsx
export function WorkerSubTimeline({ workers }: { workers: WorkerTimeline[] }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 4 }}>
      {workers.map((w) => (
        <WorkerNode key={w.workerId} worker={w} />
      ))}
    </div>
  );
}

const WORKER_STATUS_TONE: Record<WorkerStatus, string> = {
  running: INFO,
  success: SUCCESS,
  max_steps: WARNING,
  cancelled: DANGER,
};

function WorkerNode({ worker }: { worker: WorkerTimeline }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const tone = WORKER_STATUS_TONE[worker.status];
  const statusText = t(`playground.tl_worker_${worker.status}` as never);
  return (
    <div
      data-testid="worker-subtimeline"
      style={{
        border: "1px solid var(--ew-border-subtle)",
        borderLeft: `3px solid ${tone}`,
        borderRadius: 6,
        background: "var(--ew-surface-base)",
        fontSize: 12,
      }}
    >
      <div
        data-testid="worker-subtimeline-header"
        onClick={() => setExpanded((v) => !v)}
        style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 8px", cursor: "pointer" }}
      >
        <span style={{ color: "var(--ew-text-secondary)" }}>{expanded ? "▾" : "▸"}</span>
        <span style={{ fontFamily: "var(--ew-font-mono)" }}>{worker.role ?? worker.agentRef}</span>
        <span style={{ color: tone }}>{statusText}</span>
        {worker.summary ? (
          <span data-testid="worker-summary" style={{ marginLeft: "auto", color: "var(--ew-text-secondary)" }}>
            {t("playground.tl_worker_summary", {
              steps: worker.summary.iterationUsed,
              calls: worker.summary.llmCallCount,
              duration: fmtDuration(worker.summary.wallClockMs),
            })}
          </span>
        ) : null}
      </div>
      {expanded ? (
        <div style={{ padding: "0 8px 6px 24px", display: "flex", flexDirection: "column", gap: 4 }}>
          {worker.taskExcerpt ? (
            <div style={{ color: "var(--ew-text-secondary)" }}>
              {t("playground.tl_worker_task")}: {worker.taskExcerpt}
            </div>
          ) : null}
          {worker.steps.map((s) => (
            <div key={s.wseq} data-testid="worker-step" style={{ fontFamily: "var(--ew-font-mono)" }}>
              <span style={{ color: "var(--ew-text-secondary)" }}>
                {s.node}{s.stepCount !== null ? ` #${s.stepCount}` : ""} · {fmtDuration(s.durationMs)}
              </span>
              {s.messages.map((m, i) => (
                <div key={i} style={{ paddingLeft: 12, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                  {m.contentExcerpt ?? ""}
                  {m.toolCalls?.map((c) => `⚙ ${c.name}(${c.argsExcerpt})`).join(" ") ?? ""}
                  {m.toolResultExcerpt ? `→ ${m.toolResultExcerpt}` : ""}
                </div>
              ))}
            </div>
          ))}
          {worker.children.length > 0 ? (
            <div style={{ paddingLeft: 8 }}>
              <div style={{ color: "var(--ew-text-secondary)" }}>{t("playground.tl_worker_children")}</div>
              <WorkerSubTimeline workers={worker.children} />
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
```

(import 追加:`WorkerStatus`/`WorkerTimeline` type 自 `../../../api/worker_timeline`;`useState`/`useTranslation`/`fmtDuration` 已在文件内。)

工具卡注入(:233-254 的 per-tool `<div key={tool.id}>` 内,`ToolCallCard`/`DurationBadge` 之后):

```tsx
            {tool.workers ? <WorkerSubTimeline workers={tool.workers} /> : null}
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pnpm exec vitest run src/pages/agent_detail/playground/__tests__/StepTimeline.worker.test.tsx src/i18n`
Expected: 全 PASS(含 i18n parity)

- [ ] **Step 6: Commit**

```bash
git add apps/admin-ui/src/pages/agent_detail/playground/StepTimeline.tsx apps/admin-ui/src/pages/agent_detail/playground/__tests__/StepTimeline.worker.test.tsx apps/admin-ui/src/i18n/locales/en.ts apps/admin-ui/src/i18n/locales/zh-CN.ts
git commit -m "feat: B2 PR2 工具卡内嵌 worker 子时间线组件(嵌套/实时/脚注汇总)+ i18n"
```

---

### Task 4: 整链验证

**Files:** 无新改动(验证;修复单独 commit)

- [ ] **Step 1: 全量前端验证**

```bash
cd /Users/mac/src/github/jone_qian/expert-work/apps/admin-ui
pnpm exec vitest run src
pnpm typecheck
```
Expected: 全 PASS / 0 错误(IDE stale 诊断不作数)

- [ ] **Step 2: lint**

Run: `pnpm lint`(若脚本存在;否则跳过并注明)
Expected: 零错误

- [ ] **Step 3: Commit(如有修复)**

```bash
git add -A && git commit -m "test: B2 PR2 整链验证修复"
```
