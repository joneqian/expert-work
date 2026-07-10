# 调试台 Batch 3(P2:分类型执行轨迹时间线)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把调试台 `events` 段的「时间线」视图从扁平工具卡改为**分类型的执行轨迹**:一条纵轴按真实序列穿起全部节点执行(agent/memory_recall/planner/reflect/…)+ 关键事件(compaction/retry/approval/error/end),配类型过滤 + 文本搜索。纯前端。

**Architecture:** 一个新装配 parser(`timeline.ts` `parseTimeline`)把已解析的字段(Batch 1 `parseToolCalls`、每条 AI message 的 reasoning/usage/finish/model、Batch 2 各通道、retry/compaction 事件)按 `receivedAt` 序列合并成有序 typed items;一个纯过滤函数;渲染组件(StepTimeline + 三类子组件)转写已批线框;接进 TurnCard 替换 `ToolTimeline`(timeline 分支),raw 分支不变。

**Tech Stack:** React + Vite + AntD 5 + react-i18next;测试 vitest + @testing-library/react。

**设计来源(权威):** `docs/superpowers/specs/2026-07-10-playground-debug-console-redesign-design.md` 的 Batch 3 段 + **已批可交互线框 `docs/superpowers/specs/2026-07-10-batch3-wireframe.html`**(4 轮评审定稿)。组件 JSX 的视觉结构/testid/文案以线框为准转写成 React。

## Global Constraints

- 纯前端,不碰后端。数据全部已在帧里(Batch 1/2 已解析)。
- i18n:用户可见文案走 i18n,`en.ts`(**类型接口 + 值**)+ `zh-CN.ts`(值)三处同增。
- 类型:导出函数带显式入参/返回类型;`unknown` 收窄;禁 `any`;不给组件注解 `JSX.Element` 返回类型,需 node 类型用具名 `import type { ReactNode } from "react"`(Batch 1/2 教训:`tsc -b` 下全局 JSX/React 命名空间不可用)。
- 验证一律用 **`pnpm typecheck`(= `tsc -b --noEmit`)不是裸 `npx tsc`**;`cd apps/admin-ui && npx vitest run <path>`。编辑器"React UMD"/"module not found"诊断多为编辑中途 stale,以亲跑为准。
- 样式令牌用 CSS 变量(`var(--ew-*)`);语义色 good/warn/bad 与线框一致;不硬编码 hex(线框里的 `#cf1322` 等作 `var(--ew-text-danger, …)` 回退,与 Batch 1/2 一致)。
- 「过滤 = 隐藏非命中项」;「不显 per-step 耗时」(SSE 无,留 Batch 4);「与 Batch 2 执行状态聚合区并存」(不动 run-state 段)。
- 提交:每 Task 末尾 commit,conventional commits。

**数据形状(装配依据,勘查所得):**
- 帧封套 `{node: {channel: value, messages?: [...], step_count?: N}}`。node 名:`agent`/`tools`/`memory_recall`/`planner`/`reflect`/`workspace_ingest`/`memory_writeback`。
- AI message(`type:"ai"`):`content`、`tool_calls[]`(每个 `{id,name,args}`)、`additional_kwargs.reasoning_content`、`usage_metadata`、`response_metadata.{finish_reason,model_name}`。
- 通道:`recalled_memories`(list MemoryItem:id/kind/content/importance/confidence)、`plan`({goal, steps:[{id,description,status}]})、`reflections`(list {verdict,critique})、`subagent_invocations`、`tool_failures`。
- 事件:`compaction`({passes,tokens_before,tokens_after,summary_chars})、`retry`({attempt,error_class,backoff_s})、`approval`(ApprovalRequest)、`error`({message,name})、`end`。
- 工具:`parseToolCalls(events)` 已给出每个 `ToolCallEntry{id,rawName,isMcp,server,toolName,args,status,resultPreview,execResult?}`,按 `id` 与 AI message 的 `tool_calls[].id` 对应。

**不在 Batch 3 范围:** per-step 精确耗时 / SSE duration / trace-facade / Langfuse(Batch 4);不改 Batch 2 的 run-state 聚合段;不改「原始事件」视图的 EventCard。

---

## Task 1: `timeline.ts` —— 分类型执行轨迹装配 parser

**Files:**
- Create: `apps/admin-ui/src/api/timeline.ts`
- Test: `apps/admin-ui/src/api/__tests__/timeline.test.ts`

**Interfaces:**
- Consumes: `SseEvent`(`./sessions`)、`parseToolCalls` + `ToolCallEntry`(`./tool_timeline`)。
- Produces:
  ```ts
  export interface AgentStep {
    kind: "agent"; seq: number; receivedAt: string;
    stepCount: number | null; node: string;
    model: string | null; finishReason: string | null;
    reasoning: string | null; content: string | null;
    inputTokens: number; outputTokens: number; totalTokens: number;
    tools: ToolCallEntry[]; hasError: boolean;
  }
  export interface AuxNodeItem {
    kind: "memory_recall" | "planner" | "reflect" | "memory_writeback" | "workspace_ingest";
    seq: number; receivedAt: string; node: string;
    summary: string;                 // 一行摘要(计数/verdict)
    detail: Record<string, unknown>; // 展开体渲染用(memories[]/plan/critique…)
    tone: "normal" | "warn";         // reflect=revise → warn
  }
  export interface MarkerItem {
    kind: "compaction" | "retry" | "error" | "approval" | "end";
    seq: number; receivedAt: string; text: string; tone: "warn" | "bad" | "good" | "pause";
  }
  export type TimelineItem = AgentStep | AuxNodeItem | MarkerItem;
  export function parseTimeline(events: readonly SseEvent[]): TimelineItem[]
  ```
- 语义:按 `events` 到达序生成 `seq` 递增的有序 items。`agent` 节点每条 AI message → 一个 `AgentStep`(其 `tools` = 该消息 `tool_calls[].id` 在 `parseToolCalls(events)` 结果里的对应项;`hasError` = 任一工具 status==="error"）。`memory_recall`/`planner`/`reflect`/`memory_writeback`/`workspace_ingest` 节点各通道 → `AuxNodeItem`(reflect 的 `verdict==="revise"` → tone warn)。`compaction`/`retry`/`error`/`approval`/`end` 事件 → `MarkerItem`。

- [ ] **Step 1: 写失败测试**

`apps/admin-ui/src/api/__tests__/timeline.test.ts`:

```typescript
import { describe, expect, it } from "vitest";

import type { SseEvent } from "../sessions";
import { parseTimeline } from "../timeline";

function ev(event: string, data: unknown, receivedAt: string): SseEvent {
  return { id: null, event, data, rawData: "", receivedAt };
}
function upd(node: string, channels: Record<string, unknown>, at: string): SseEvent {
  return ev("updates", { [node]: channels }, at);
}

describe("parseTimeline", () => {
  it("builds an agent step with reasoning, finish/model, and its tool", () => {
    const events = [
      upd("agent", {
        step_count: 1,
        messages: [{
          type: "ai", content: "",
          additional_kwargs: { reasoning_content: "先查天气" },
          response_metadata: { finish_reason: "tool_calls", model_name: "glm-5.2" },
          usage_metadata: { input_tokens: 100, output_tokens: 10, total_tokens: 110 },
          tool_calls: [{ id: "c1", name: "exec_python", args: { code: "print(1)" } }],
        }],
      }, "t1"),
      upd("tools", {
        messages: [{ type: "tool", tool_call_id: "c1", name: "exec_python", content: "stdout:\n1\n\nexit_code: 0", status: "success" }],
      }, "t2"),
    ];
    const items = parseTimeline(events);
    const step = items.find((i) => i.kind === "agent");
    expect(step).toBeDefined();
    if (step && step.kind === "agent") {
      expect(step.reasoning).toBe("先查天气");
      expect(step.finishReason).toBe("tool_calls");
      expect(step.model).toBe("glm-5.2");
      expect(step.totalTokens).toBe(110);
      expect(step.tools).toHaveLength(1);
      expect(step.tools[0].toolName).toBe("exec_python");
      expect(step.tools[0].status).toBe("success");
      expect(step.hasError).toBe(false);
    }
  });

  it("emits aux node items for memory_recall and a revise reflect (warn tone)", () => {
    const events = [
      upd("memory_recall", { recalled_memories: [{ id: "m1", kind: "fact", content: "住嘉兴", importance: 0.7, confidence: 0.9 }] }, "t1"),
      upd("reflect", { reflections: [{ verdict: "revise", critique: "漏了夜间" }] }, "t2"),
    ];
    const items = parseTimeline(events);
    const mem = items.find((i) => i.kind === "memory_recall");
    const ref = items.find((i) => i.kind === "reflect");
    expect(mem).toBeDefined();
    expect(ref && ref.kind === "reflect" && ref.tone).toBe("warn");
  });

  it("emits markers for compaction / retry / end in order", () => {
    const events = [
      ev("compaction", { passes: 2, tokens_before: 46000, tokens_after: 22000, summary_chars: 800 }, "t1"),
      ev("retry", { attempt: 1, error_class: "TimeoutError", backoff_s: 2 }, "t2"),
      ev("end", {}, "t3"),
    ];
    const kinds = parseTimeline(events).map((i) => i.kind);
    expect(kinds).toEqual(["compaction", "retry", "end"]);
  });

  it("assigns increasing seq in arrival order across types", () => {
    const events = [
      upd("memory_recall", { recalled_memories: [{ id: "m1", kind: "fact", content: "x", importance: 0.5, confidence: 0.5 }] }, "t1"),
      upd("agent", { step_count: 1, messages: [{ type: "ai", content: "hi" }] }, "t2"),
      ev("end", {}, "t3"),
    ];
    const seqs = parseTimeline(events).map((i) => i.seq);
    expect(seqs).toEqual([0, 1, 2]);
  });
});
```

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `cd apps/admin-ui && npx vitest run src/api/__tests__/timeline.test.ts`
Expected: FAIL —— `../timeline` 不存在。

- [ ] **Step 3: 写实现**

`apps/admin-ui/src/api/timeline.ts`:

```typescript
/**
 * Execution-trace timeline assembler — merges a turn's SSE frames into an
 * ordered, typed item list for the step-timeline view. Reuses the already-parsed
 * fields (tool calls, per-message reasoning/usage/finish, AgentState channels,
 * retry/compaction events); the new work here is *ordering + typing*, not new
 * field parsing. See docs/.../2026-07-10-batch3-wireframe.html for the render.
 */
import type { SseEvent } from "./sessions";
import { parseToolCalls, type ToolCallEntry } from "./tool_timeline";

export interface AgentStep {
  kind: "agent";
  seq: number;
  receivedAt: string;
  stepCount: number | null;
  node: string;
  model: string | null;
  finishReason: string | null;
  reasoning: string | null;
  content: string | null;
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  tools: ToolCallEntry[];
  hasError: boolean;
}
export interface AuxNodeItem {
  kind: "memory_recall" | "planner" | "reflect" | "memory_writeback" | "workspace_ingest";
  seq: number;
  receivedAt: string;
  node: string;
  summary: string;
  detail: Record<string, unknown>;
  tone: "normal" | "warn";
}
export interface MarkerItem {
  kind: "compaction" | "retry" | "error" | "approval" | "end";
  seq: number;
  receivedAt: string;
  text: string;
  tone: "warn" | "bad" | "good" | "pause";
}
export type TimelineItem = AgentStep | AuxNodeItem | MarkerItem;

const AI = "ai";
function obj(v: unknown): Record<string, unknown> {
  return v !== null && typeof v === "object" ? (v as Record<string, unknown>) : {};
}
function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}
function int(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? v : 0;
}
function textOf(v: unknown): string | null {
  if (typeof v === "string") return v.trim() === "" ? null : v;
  return null;
}

export function parseTimeline(events: readonly SseEvent[]): TimelineItem[] {
  const items: TimelineItem[] = [];
  const byId = new Map<string, ToolCallEntry>();
  for (const e of parseToolCalls(events)) byId.set(e.id, e);
  let seq = 0;
  const push = (it: Omit<TimelineItem, "seq"> & { seq?: number }): void => {
    items.push({ ...(it as TimelineItem), seq: seq++ });
  };

  for (const evt of events) {
    const at = evt.receivedAt;
    if (evt.event === "compaction") {
      const d = obj(evt.data);
      push({ kind: "compaction", receivedAt: at, tone: "warn",
        text: `压缩 ${int(d.passes)} 遍 · ${int(d.tokens_before)} → ${int(d.tokens_after)} tok` });
      continue;
    }
    if (evt.event === "retry") {
      const d = obj(evt.data);
      push({ kind: "retry", receivedAt: at, tone: "warn",
        text: `重试 #${int(d.attempt)} · ${str(d.error_class)} · 退避 ${int(d.backoff_s)}s` });
      continue;
    }
    if (evt.event === "error") {
      const d = obj(evt.data);
      push({ kind: "error", receivedAt: at, tone: "bad", text: str(d.message) || str(d.name) || "运行错误" });
      continue;
    }
    if (evt.event === "approval") {
      push({ kind: "approval", receivedAt: at, tone: "pause", text: "等待人工审批" });
      continue;
    }
    if (evt.event === "end") {
      push({ kind: "end", receivedAt: at, tone: "good", text: "运行完成" });
      continue;
    }
    if (evt.event !== "updates") continue;

    const data = obj(evt.data);
    for (const [node, raw] of Object.entries(data)) {
      const ch = obj(raw);

      // agent LLM step(s) — one per AI message
      const msgs = Array.isArray(ch.messages) ? ch.messages : [];
      for (const m of msgs) {
        const mm = obj(m);
        if (mm.type !== AI) continue;
        const rm = obj(mm.response_metadata);
        const um = obj(mm.usage_metadata);
        const ak = obj(mm.additional_kwargs);
        const calls = Array.isArray(mm.tool_calls) ? mm.tool_calls : [];
        const tools: ToolCallEntry[] = [];
        for (const tc of calls) {
          const id = str(obj(tc).id);
          const entry = id === "" ? undefined : byId.get(id);
          if (entry) tools.push(entry);
        }
        push({
          kind: "agent", receivedAt: at, node,
          stepCount: typeof ch.step_count === "number" ? ch.step_count : null,
          model: str(rm.model_name) || null,
          finishReason: str(rm.finish_reason) || null,
          reasoning: textOf(ak.reasoning_content),
          content: textOf(mm.content),
          inputTokens: int(um.input_tokens),
          outputTokens: int(um.output_tokens),
          totalTokens: int(um.total_tokens),
          tools,
          hasError: tools.some((t) => t.status === "error"),
        });
      }

      // aux node channels — positioned where they arrive
      if (Array.isArray(ch.recalled_memories) && ch.recalled_memories.length > 0) {
        push({ kind: "memory_recall", receivedAt: at, node, tone: "normal",
          summary: `记忆召回 · ${ch.recalled_memories.length} 条`,
          detail: { memories: ch.recalled_memories } });
      }
      if (ch.plan !== undefined && ch.plan !== null) {
        const p = obj(ch.plan);
        const steps = Array.isArray(p.steps) ? p.steps : [];
        push({ kind: "planner", receivedAt: at, node, tone: "normal",
          summary: `制定计划 · 目标 + ${steps.length} 步`, detail: { plan: p } });
      }
      if (Array.isArray(ch.reflections) && ch.reflections.length > 0) {
        for (const r of ch.reflections) {
          const rr = obj(r);
          const verdict = str(rr.verdict);
          push({ kind: "reflect", receivedAt: at, node,
            tone: verdict === "revise" ? "warn" : "normal",
            summary: `反思 · ${verdict === "revise" ? "修订" : "通过"}`,
            detail: { verdict, critique: str(rr.critique) } });
        }
      }
      if (Array.isArray(ch.written_memories) && ch.written_memories.length > 0) {
        push({ kind: "memory_writeback", receivedAt: at, node, tone: "normal",
          summary: `记忆写回 · ${ch.written_memories.length} 条`,
          detail: { memories: ch.written_memories } });
      }
    }
  }
  return items;
}
```

> 注:`memory_writeback` 通道名以运行期实际帧为准 —— 若不是 `written_memories`,按实际改(勘查未固定;该分支缺字段时不产 item,不报错)。`workspace_ingest` 同理留占位(有对应通道时再加分支),缺失不影响其余。

- [ ] **Step 4: 跑测试确认 PASS**

Run: `cd apps/admin-ui && npx vitest run src/api/__tests__/timeline.test.ts`
Expected: PASS(4 用例)。

- [ ] **Step 5: 类型检查** —— Run: `cd apps/admin-ui && pnpm typecheck` → exit 0。

- [ ] **Step 6: 提交**

```bash
git add apps/admin-ui/src/api/timeline.ts apps/admin-ui/src/api/__tests__/timeline.test.ts
git commit -m "feat(playground): 分类型执行轨迹装配 parser parseTimeline"
```

---

## Task 2: `timeline_filter.ts` —— 类型过滤 + 文本搜索纯函数

**Files:**
- Create: `apps/admin-ui/src/api/timeline_filter.ts`
- Test: `apps/admin-ui/src/api/__tests__/timeline_filter.test.ts`

**Interfaces:**
- Consumes: `TimelineItem`(Task 1)。
- Produces:
  ```ts
  export type TimelineFilter = "all" | "tool" | "error" | "retry";
  export function filterTimeline(items: readonly TimelineItem[], type: TimelineFilter, query: string): TimelineItem[]
  ```
- 语义:`type` 命中(all=全留;tool=agent 步且有 tool;error=有错项含 error marker / hasError 步;retry=retry marker)AND `query`(小写子串命中 item 的可搜文本:工具名 / finishReason / node / marker text / summary / reasoning)。过滤=返回命中子集(渲染层隐藏其余)。

- [ ] **Step 1: 写失败测试**

```typescript
import { describe, expect, it } from "vitest";
import { filterTimeline } from "../timeline_filter";
import type { TimelineItem } from "../timeline";

const agent = (over: Partial<Extract<TimelineItem, {kind:"agent"}>> = {}): TimelineItem => ({
  kind: "agent", seq: 0, receivedAt: "", stepCount: 1, node: "agent",
  model: "glm-5.2", finishReason: "stop", reasoning: null, content: "hi",
  inputTokens: 0, outputTokens: 0, totalTokens: 0, tools: [], hasError: false, ...over,
});
const retry: TimelineItem = { kind: "retry", seq: 1, receivedAt: "", text: "重试 #1 · TimeoutError", tone: "warn" };

describe("filterTimeline", () => {
  it("all + empty query returns everything", () => {
    const items = [agent(), retry];
    expect(filterTimeline(items, "all", "")).toHaveLength(2);
  });
  it("retry type keeps only retry markers", () => {
    const out = filterTimeline([agent(), retry], "retry", "");
    expect(out).toEqual([retry]);
  });
  it("error type keeps error steps and error markers", () => {
    const errStep = agent({ hasError: true });
    const out = filterTimeline([agent(), errStep, retry], "error", "");
    expect(out).toEqual([errStep]);
  });
  it("text query matches tool name / marker text (case-insensitive)", () => {
    expect(filterTimeline([agent(), retry], "all", "timeout")).toEqual([retry]);
  });
});
```

- [ ] **Step 2: FAIL** —— Run: `cd apps/admin-ui && npx vitest run src/api/__tests__/timeline_filter.test.ts` → 模块不存在。

- [ ] **Step 3: 写实现**

```typescript
import type { TimelineItem } from "./timeline";

export type TimelineFilter = "all" | "tool" | "error" | "retry";

function haystack(it: TimelineItem): string {
  if (it.kind === "agent") {
    return [it.node, it.model ?? "", it.finishReason ?? "", it.reasoning ?? "", it.content ?? "",
      ...it.tools.map((t) => `${t.toolName} ${t.status}`)].join(" ").toLowerCase();
  }
  if (it.kind === "compaction" || it.kind === "retry" || it.kind === "error" ||
      it.kind === "approval" || it.kind === "end") {
    return it.text.toLowerCase();
  }
  return `${it.kind} ${it.summary}`.toLowerCase();
}

function matchesType(it: TimelineItem, type: TimelineFilter): boolean {
  switch (type) {
    case "all": return true;
    case "tool": return it.kind === "agent" && it.tools.length > 0;
    case "error": return (it.kind === "agent" && it.hasError) || it.kind === "error";
    case "retry": return it.kind === "retry";
  }
}

export function filterTimeline(
  items: readonly TimelineItem[], type: TimelineFilter, query: string,
): TimelineItem[] {
  const q = query.trim().toLowerCase();
  return items.filter((it) => matchesType(it, type) && (q === "" || haystack(it).includes(q)));
}
```

- [ ] **Step 4: PASS + typecheck** —— `cd apps/admin-ui && npx vitest run src/api/__tests__/timeline_filter.test.ts && pnpm typecheck` → PASS + exit 0。

- [ ] **Step 5: 提交**

```bash
git add apps/admin-ui/src/api/timeline_filter.ts apps/admin-ui/src/api/__tests__/timeline_filter.test.ts
git commit -m "feat(playground): 时间线类型过滤 + 文本搜索 filterTimeline"
```

---

## Task 3: `StepTimeline` 渲染组件(+ 子组件)

把 `TimelineItem[]` 渲染成线框里的分类型轴。**JSX 结构/testid/文案/样式以 `docs/superpowers/specs/2026-07-10-batch3-wireframe.html` 为准转写**(纵轴 `.timeline`、agent 大卡 `.step`、轻量行 `.node-row`、轴标记 `.marker`、类型图例 `.legend`)。

**Files:**
- Create: `apps/admin-ui/src/pages/agent_detail/playground/StepTimeline.tsx`
- Create: `apps/admin-ui/src/pages/agent_detail/playground/__tests__/StepTimeline.test.tsx`
- Modify: `apps/admin-ui/src/i18n/locales/{en,zh-CN}.ts`

**Interfaces:**
- Consumes: `TimelineItem`/`AgentStep`/`AuxNodeItem`/`MarkerItem`(Task 1);复用 `ToolTimeline` 里已有的工具卡渲染 —— 抽出 `ToolCallCard` 为导出组件(`components/ToolTimeline.tsx` 现为私有),供 StepTimeline 的 agent 步复用工具卡(入参/出参/exec 结构化,Batch 1 已实现)。
- Produces:
  ```ts
  export interface StepTimelineProps { items: readonly TimelineItem[] }
  export function StepTimeline(props: StepTimelineProps): ... // null when items empty
  ```

- [ ] **Step 1: 加 i18n 键(en 类型+值 / zh 值)** —— 节点/标记类型标签与摘要用到的键(`tl_reasoning`="思考(本次 LLM 调用)"、`tl_args`="入参 · args"、`tl_result`="出参 · 结果"、`tl_legend_agent`… 等;文案照线框)。完整键清单在实现时对着线框补齐,en 三处对齐。

- [ ] **Step 2: 抽出 `ToolCallCard` 为导出(前置)** —— `components/ToolTimeline.tsx` 的 `ToolCallCard` 改 `export function ToolCallCard(...)`,`ToolTimeline` 内部改用导出的;不改渲染。跑 `npx vitest run src/components/__tests__/ToolTimeline.test.tsx` 确认回归不变。

- [ ] **Step 3: 写失败测试** —— `StepTimeline.test.tsx`:渲染一个含 agent 步(带工具)、一个 memory_recall aux、一个 retry marker 的 `items`,断言:`data-testid="step-timeline"` 存在;agent 步显 finish_reason/model + 嵌工具卡;aux 行显 `memory_recall` 标签 + 计数;retry marker 显文本;空 items → 返回 null(`container` empty)。(testid 命名照线框:`step-card`/`node-row`/`marker` 派生。)

- [ ] **Step 4: FAIL** —— `cd apps/admin-ui && npx vitest run src/pages/agent_detail/playground/__tests__/StepTimeline.test.tsx`。

- [ ] **Step 5: 写 `StepTimeline.tsx`** —— 转写线框:纵轴容器 + 判别 `item.kind` 渲染三类(agent `.step`:头 `步骤号·node·model·finish·token` + 展开体 reasoning(紫左条)+ `ToolCallCard` 列表 / content;aux `.node-row`:摘要行 + 可展开 detail;marker `.marker`:菱形/圆点 + 文本)。异常步(hasError / tone bad)默认展开 + 红条;正常步/aux 默认折叠(受控 `useState` 展开态)。`reflect` tone warn 高亮。不显 per-step 耗时(只 token)。用具名 `ReactNode`,不注解 `JSX.Element`。样式 `var(--ew-*)`。

- [ ] **Step 6: PASS + typecheck + 全量** —— `cd apps/admin-ui && npx vitest run && pnpm typecheck` → 全绿 + exit 0。

- [ ] **Step 7: 提交**

```bash
git add apps/admin-ui/src/pages/agent_detail/playground/StepTimeline.tsx apps/admin-ui/src/pages/agent_detail/playground/__tests__/StepTimeline.test.tsx apps/admin-ui/src/components/ToolTimeline.tsx apps/admin-ui/src/i18n/locales/{en,zh-CN}.ts
git commit -m "feat(playground): StepTimeline 分类型轨迹渲染 + 复用 ToolCallCard"
```

---

## Task 4: `TimelineFilterBar` + 过滤状态

**Files:**
- Create: `apps/admin-ui/src/pages/agent_detail/playground/TimelineFilterBar.tsx`
- Create: `apps/admin-ui/src/pages/agent_detail/playground/__tests__/TimelineFilterBar.test.tsx`
- Modify: `apps/admin-ui/src/i18n/locales/{en,zh-CN}.ts`

**Interfaces:**
```ts
export interface TimelineFilterBarProps {
  type: TimelineFilter; query: string;
  onType: (t: TimelineFilter) => void; onQuery: (q: string) => void;
  count: string; // "9 项 · 2 工具 · 1 失败"
}
export function TimelineFilterBar(props): ...
```
转写线框 `.filter-bar`:类型 chip(全部/工具/错误/retry,`aria-pressed`)+ 搜索 input + 右侧计数。

- [ ] **Step 1: i18n 键**(全部/工具/错误/retry chip 文案 + 搜索 placeholder,en 三处)。
- [ ] **Step 2: 写失败测试** —— 渲染,断言 chip 存在、点击调 `onType`、输入调 `onQuery`、计数显示。
- [ ] **Step 3: FAIL** → **Step 4: 写实现**(受控组件,照线框)→ **Step 5: PASS + typecheck**。
- [ ] **Step 6: 提交** `git commit -m "feat(playground): TimelineFilterBar 类型过滤 + 搜索条"`。

---

## Task 5: 接入 TurnCard —— 用 StepTimeline 替换 timeline 视图 + 过滤

**Files:**
- Modify: `apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx`

**Interfaces:** Consumes Task 1–4 全部产物。

- [ ] **Step 1: TurnCard 内接线** —— 在 `TurnCard` 体内:
  ```typescript
  const timeline = useMemo(() => parseTimeline(turn.events), [turn.events]);
  const [tlType, setTlType] = useState<TimelineFilter>("all");
  const [tlQuery, setTlQuery] = useState("");
  const visibleTimeline = useMemo(() => filterTimeline(timeline, tlType, tlQuery), [timeline, tlType, tlQuery]);
  ```
  events Collapse 的 timeline 分支(`:1993` 现 `<ToolTimeline .../>`)替换为 `<TimelineFilterBar …/>` + `<StepTimeline items={visibleTimeline} />`;raw 分支保留,但也接同一 filter(可选:raw 视图按同 `tlType`/`tlQuery` 过滤 events —— 若超范围则本批仅对 timeline 生效,raw 不变,并在计划注明)。
  count 字符串由可见项算(总项/工具数/失败数)。
- [ ] **Step 2: 移除死引用** —— 若 `ToolTimeline` 不再被 PlaygroundTab 直接使用(仅 StepTimeline 复用 ToolCallCard),清理其 import(注意 ToolTimeline 组件本身可能仍被 RunDetail 或别处用;仅删 PlaygroundTab 里变孤儿的 import)。
- [ ] **Step 3: 全量 + typecheck** —— `cd apps/admin-ui && npx vitest run && pnpm typecheck` → 全绿 + exit 0。**e2e 注意**:若 `apps/admin-ui/e2e/*.spec.ts` 依赖旧 timeline 视图的 testid(如 `tool-timeline`/`tool-call-card`),`grep -rn` 找出并更新到 StepTimeline 的新 testid —— **本批的 CI 风险点**(Batch 2 踩过 e2e 漏改挂 Playwright)。
- [ ] **Step 4: 手动冒烟** —— 跑含工具/失败/记忆召回/reflect 的 run,确认步骤时间线分类型渲染、过滤/搜索生效、异常步默认展开。
- [ ] **Step 5: 提交** `git commit -m "feat(playground): TurnCard 接入 StepTimeline 替换扁平工具视图 + 过滤搜索"`。

---

## 验收(Batch 3 整体)

- [ ] `cd apps/admin-ui && npx vitest run` 全绿;`pnpm typecheck` exit 0。
- [ ] `grep -rn "tool-timeline\|tool-call-card\|playground-event-" apps/admin-ui/e2e/` —— 无因本批失效的 testid(照 Batch 2 教训,e2e 与 src 一起改)。
- [ ] 手动冒烟:步骤时间线按序列显示 agent 大卡(思考+工具入参/出参+答复)/ memory_recall / planner / reflect(revise 高亮)/ compaction·retry·end 标记;类型过滤 + 搜索隐藏非命中;异常步默认展开;不显 per-step 耗时。
- [ ] 回归:RunDetail 的 ToolTimeline(若仍用)+ 「原始事件」视图不变。

## 依赖与顺序

`T1 → T2`(filter 吃 T1 类型);`T3` 依赖 T1(+ 抽 ToolCallCard);`T4` 依赖 T2 类型;`T5` 依赖 T1–T4。序:**T1 → T2 → T3 → T4 → T5**。

## Self-Review(计划 vs spec Batch 3)

- **分类型执行轨迹(§9)** → T1(装配)+ T3(渲染);agent 大卡 + aux 行 + 轴标记 + reflect revise 高亮 + 异常默认展开 全覆盖。✅
- **工具入参/出参** → T3 复用 Batch 1 `ToolCallCard`(抽为导出)。✅
- **思考按步** → T1 的 AgentStep.reasoning 直接读每条 AI message(无需改 summarizeTurn)。✅
- **过滤 + 搜索(§10,隐藏非命中)** → T2(纯函数)+ T4(条)+ T5(接线)。✅
- **不显 per-step 耗时 / 与 run-state 并存** → T3 只显 token;不动 run-state 段。✅
- **类型一致**:`TimelineItem`/`AgentStep`/`AuxNodeItem`/`MarkerItem`/`TimelineFilter` 定义(T1/T2)与消费(T3/T4/T5)一致。✅
- **Batch 1/2 教训**:全程 `pnpm typecheck`;ReactNode 具名;e2e testid 同步(T5 Step 3 显式)。✅
- **无 placeholder**:parser/filter 完整代码;组件转写已批线框(committed HTML,非 placeholder)。✅
