# 调试台 Batch 1(P0:表面化已有数据)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把已到前端、但只埋在"原始事件"JSON dump 里的调试字段(finish_reason、model_name、exec_python 的 stdout/exit_code、工具成败聚合、缓存写入)表面化,并让"事件"视图选择跨刷新持久 —— 全部纯前端,零后端依赖。

**Architecture:** 数据解析集中在两个纯函数模块(`api/turn_summary.ts` / `api/tool_timeline.ts`),渲染层薄。本批把新字段加进解析器(严格 TDD 单测),抽出可测的 `TurnMeta` 展示组件,并在 `ToolCallCard` 里把 exec 结果结构化。逻辑进纯函数、JSX 保持薄,以便单测覆盖。

**Tech Stack:** React + Vite + Ant Design 5 + react-i18next;测试 vitest + @testing-library/react。

## Global Constraints

- 语言:所有用户可见文案走 i18n,`en.ts` + `zh-CN.ts` **双语同增**,键不得只加一侧。
- 不可变:解析器构造期对本地聚合对象的就地填充沿用现有文件风格(如 `parseToolCalls` 现有 `awaitingApproval` 就地改 `entry.status`);对外返回的数据视为只读。
- 文件行数:新建组件文件 200–400 行为宜,不碰 800 上限。本批**不**动 `PlaygroundTab` 的大拆分(留 Batch 2),只抽 `TurnMeta`。
- 样式令牌:颜色/字体用现有 CSS 变量(`var(--ew-border-subtle)` / `var(--ew-font-mono)` / `var(--ew-surface-raised)` / `var(--ew-text-secondary)`),不硬编码色值。
- 类型:导出函数带显式入参/返回类型;外部/不可信数据用 `unknown` 再收窄,禁 `any`(见 rules/typescript)。
- 提交:每个 Task 末尾 commit,conventional commits(feat/refactor/test);属性签名全局已禁用。
- 运行测试根目录:`apps/admin-ui`(`cd apps/admin-ui && npx vitest run <path>`)。

**已确认的后端事实(解析依据,勿改):**
- exec_python / bash 的 `ToolMessage.content` 是 `format_sandbox_outcome`(`services/orchestrator/src/orchestrator/tools/sandbox.py:604`)渲染的**固定字符串**:各段以 `\n\n` 连接,形如 `stdout:\n<out>\n\nstderr:\n<err>\n\nexit_code: <n>`;stdout/stderr 段可选(都无则为 `(no output)`),超时时在 `exit_code` 前插一行 `[execution timed out …]`;`exit_code: <n>` **恒为最后一段**。前端已 `stripFence` + `trim`。
- `finish_reason` / `model_name` 在每条 AI message 的 `response_metadata`(`services/.../llm/providers/openai.py:563`)。
- 缓存写入 token 在 `usage_metadata.input_token_details.cache_creation`(兼容 vendor 常为 0 —— 为 0 不渲染)。

**不在 Batch 1 范围(留后续批次):** `PlaygroundTab` 2066 行大拆分、`EventCard` 去重、AgentState 通道卡片、retry/compaction、per-step token 粒度、per-step finish_reason、事件过滤/搜索、时间线布局、后端与 Langfuse。

---

## Task 1: `turn_summary` 解析 finish_reason + model_name + 缓存写入 token

**Files:**
- Modify: `apps/admin-ui/src/api/turn_summary.ts`
- Test: `apps/admin-ui/src/api/__tests__/turn_summary.test.ts`

**Interfaces:**
- Produces:
  - `TurnUsage` 新增 `cacheCreationTokens: number`
  - `TurnSummary` 新增 `finishReason: string | null`(最后一条带 `response_metadata.finish_reason` 的 AI message 的值)、`modelName: string | null`(同上,取 `model_name`)

- [ ] **Step 1: 加失败测试**

在 `apps/admin-ui/src/api/__tests__/turn_summary.test.ts` 的 `describe("summarizeTurn", …)` 内追加:

```typescript
it("takes finish_reason and model_name from the last AI message that reports them", () => {
  const events = [
    updates([
      {
        type: "ai",
        content: "",
        response_metadata: { finish_reason: "tool_calls", model_name: "glm-5.2" },
      },
    ]),
    updates([
      {
        type: "ai",
        content: "done",
        response_metadata: { finish_reason: "stop", model_name: "glm-5.2" },
      },
    ]),
  ];
  const summary = summarizeTurn(events);
  expect(summary.finishReason).toBe("stop");
  expect(summary.modelName).toBe("glm-5.2");
});

it("leaves finishReason/modelName null when response_metadata is absent", () => {
  const summary = summarizeTurn([updates([{ type: "ai", content: "hi" }])]);
  expect(summary.finishReason).toBeNull();
  expect(summary.modelName).toBeNull();
});

it("sums cache_creation into cacheCreationTokens", () => {
  const events = [
    updates([
      {
        type: "ai",
        content: "x",
        usage_metadata: {
          input_tokens: 10,
          output_tokens: 2,
          total_tokens: 12,
          input_token_details: { cache_read: 4, cache_creation: 6 },
        },
      },
    ]),
  ];
  const summary = summarizeTurn(events);
  expect(summary.usage?.cacheCreationTokens).toBe(6);
  expect(summary.usage?.cacheReadTokens).toBe(4);
});
```

同时更新既有的 `"sums usage across AI messages…"` 用例的 `toEqual` 断言,加 `cacheCreationTokens: 0`:

```typescript
    expect(summary.usage).toEqual({
      inputTokens: 150,
      outputTokens: 30,
      totalTokens: 180,
      cacheReadTokens: 64,
      cacheCreationTokens: 0,
      reasoningTokens: 8,
    });
```

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `cd apps/admin-ui && npx vitest run src/api/__tests__/turn_summary.test.ts`
Expected: FAIL —— `finishReason`/`modelName` 为 `undefined`;`cacheCreationTokens` 属性不存在。

- [ ] **Step 3: 改实现**

`turn_summary.ts` —— 给 `TurnUsage` 加字段:

```typescript
export interface TurnUsage {
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  cacheReadTokens: number;
  cacheCreationTokens: number;
  reasoningTokens: number;
}
```

给 `TurnSummary` 加字段(接在 `latencyMs` 后):

```typescript
  /** Wall-clock from the turn's first frame to its last, in ms (null if <2 frames). */
  latencyMs: number | null;
  /** ``response_metadata.finish_reason`` of the last AI message that reports one (null if none). */
  finishReason: string | null;
  /** ``response_metadata.model_name`` of the last AI message that reports one (null if none). */
  modelName: string | null;
}
```

`summarizeTurn` 里,`usage` 初值加一行、并新增两个游标(放在 `stepCount` 声明附近):

```typescript
  let stepCount: number | null = null;
  let finishReason: string | null = null;
  let modelName: string | null = null;
  const usage: TurnUsage = {
    inputTokens: 0,
    outputTokens: 0,
    totalTokens: 0,
    cacheReadTokens: 0,
    cacheCreationTokens: 0,
    reasoningTokens: 0,
  };
```

在 AI message 分支内(`if (m.type !== "ai") continue;` 之后,`const ak = …` 之前)插入 `response_metadata` 读取:

```typescript
      const rm = m.response_metadata;
      if (rm !== null && typeof rm === "object") {
        const r = rm as Record<string, unknown>;
        if (typeof r.finish_reason === "string") finishReason = r.finish_reason;
        if (typeof r.model_name === "string") modelName = r.model_name;
      }
```

在 `usage_metadata` 的 `input_token_details` 读取块里,`cache_read` 旁补 `cache_creation`:

```typescript
        const itd = u.input_token_details;
        if (itd !== null && typeof itd === "object") {
          const d = itd as Record<string, unknown>;
          usage.cacheReadTokens += asInt(d.cache_read);
          usage.cacheCreationTokens += asInt(d.cache_creation);
        }
```

return 语句补两字段:

```typescript
  return {
    finalText,
    reasoning,
    usage: reported ? usage : null,
    stepCount,
    latencyMs,
    finishReason,
    modelName,
  };
```

- [ ] **Step 4: 跑测试确认 PASS**

Run: `cd apps/admin-ui && npx vitest run src/api/__tests__/turn_summary.test.ts`
Expected: PASS(全部用例含新增 3 条)。

- [ ] **Step 5: 提交**

```bash
git add apps/admin-ui/src/api/turn_summary.ts apps/admin-ui/src/api/__tests__/turn_summary.test.ts
git commit -m "feat(playground): turn_summary 解析 finish_reason/model_name/缓存写入"
```

---

## Task 2: `tool_timeline` 结构化 exec_python / bash 结果

**Files:**
- Modify: `apps/admin-ui/src/api/tool_timeline.ts`
- Test: `apps/admin-ui/src/api/__tests__/tool_timeline.test.ts`

**Interfaces:**
- Consumes: `ToolCallEntry`(Task 无前置,同文件已有类型)
- Produces:
  - `export interface ExecResult { stdout: string; stderr: string; exitCode: number | null }`
  - `export function parseExecResult(preview: string): ExecResult`
  - `ToolCallEntry` 新增可选 `execResult?: ExecResult`(仅内置 `exec_python` / `bash` 且有结果时挂上)

- [ ] **Step 1: 加失败测试**

在 `apps/admin-ui/src/api/__tests__/tool_timeline.test.ts` 顶部 import 补 `parseExecResult`:

```typescript
import {
  artifactsFromTools,
  parseCompactionEvents,
  parseExecResult,
  parseToolCalls,
} from "../tool_timeline";
```

文件末尾追加两个 describe:

```typescript
describe("parseExecResult", () => {
  it("splits stdout / stderr / exit_code from the rendered sandbox string", () => {
    const preview = "stdout:\nhello\nworld\n\nstderr:\noops\n\nexit_code: 0";
    expect(parseExecResult(preview)).toEqual({
      stdout: "hello\nworld",
      stderr: "oops",
      exitCode: 0,
    });
  });

  it("handles stdout-only output and a non-zero exit code", () => {
    expect(parseExecResult("stdout:\n42\n\nexit_code: 1")).toEqual({
      stdout: "42",
      stderr: "",
      exitCode: 1,
    });
  });

  it("handles the (no output) case", () => {
    expect(parseExecResult("(no output)\n\nexit_code: 0")).toEqual({
      stdout: "",
      stderr: "",
      exitCode: 0,
    });
  });

  it("returns null exitCode when the marker is absent", () => {
    expect(parseExecResult("stdout:\nx").exitCode).toBeNull();
  });
});

describe("parseToolCalls exec attribution", () => {
  it("attaches execResult for a builtin exec_python call", () => {
    const events = [
      updates("agent", [aiCall("c1", "exec_python", { code: "print(1)" })]),
      updates("tools", [toolResult("c1", "stdout:\n1\n\nexit_code: 0")]),
    ];
    const [entry] = parseToolCalls(events);
    expect(entry.execResult).toEqual({ stdout: "1", stderr: "", exitCode: 0 });
  });

  it("does not attach execResult for a non-sandbox tool", () => {
    const events = [
      updates("agent", [aiCall("c1", "web_search", { q: "x" })]),
      updates("tools", [toolResult("c1", "some result")]),
    ];
    const [entry] = parseToolCalls(events);
    expect(entry.execResult).toBeUndefined();
  });
});
```

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `cd apps/admin-ui && npx vitest run src/api/__tests__/tool_timeline.test.ts`
Expected: FAIL —— `parseExecResult` 未导出;`entry.execResult` 为 `undefined`。

- [ ] **Step 3: 改实现**

`tool_timeline.ts` —— 给 `ToolCallEntry` 加可选字段(接在 `resultPreview` 后):

```typescript
  /** Result text with the spotlight ``«UNTRUSTED…»`` fence stripped (``null`` until the result arrives). */
  resultPreview: string | null;
  /** Structured sandbox result (exec_python / bash only) parsed from ``resultPreview``. */
  execResult?: ExecResult;
}
```

在 `parseName` 上方加类型 + 解析器 + exec 工具集:

```typescript
/** Structured stdout / stderr / exit code of a sandbox tool (exec_python, bash). */
export interface ExecResult {
  stdout: string;
  stderr: string;
  exitCode: number | null;
}

/** Builtin tools whose result follows ``format_sandbox_outcome``'s rendering. */
const SANDBOX_TOOLS = new Set(["exec_python", "bash"]);

/**
 * Parse the rendered sandbox result string into structured fields. Format
 * (``format_sandbox_outcome``): sections joined by ``\n\n`` —
 * ``stdout:\n<out>``, ``stderr:\n<err>`` (each optional; ``(no output)`` when
 * both empty), an optional ``[execution timed out …]`` line, then a trailing
 * ``exit_code: <n>``. ``exit_code`` is always last. Best-effort: a null
 * ``exitCode`` signals an unrecognised shape.
 */
export function parseExecResult(preview: string): ExecResult {
  const exitMatch = preview.match(/\nexit_code:\s*(-?\d+)\s*$/);
  const exitCode = exitMatch ? Number(exitMatch[1]) : null;
  const body = exitMatch ? preview.slice(0, exitMatch.index).trimEnd() : preview;
  const section = (label: string): string => {
    const marker = `${label}:\n`;
    const start = body.indexOf(marker);
    if (start === -1) return "";
    const rest = body.slice(start + marker.length);
    const next = rest.search(/\n\n(?:stdout:\n|stderr:\n|\[execution timed out)/);
    return (next === -1 ? rest : rest.slice(0, next)).trim();
  };
  return { stdout: section("stdout"), stderr: section("stderr"), exitCode };
}
```

在 `parseToolCalls` 的 `const entries = order.map(…)` 之后、`if (awaitingApproval)` 之前,插入 exec 挂载:

```typescript
  const entries = order.map((id) => byId.get(id) as ToolCallEntry);
  for (const entry of entries) {
    if (!entry.isMcp && SANDBOX_TOOLS.has(entry.toolName) && entry.resultPreview) {
      entry.execResult = parseExecResult(entry.resultPreview);
    }
  }
  if (awaitingApproval) {
```

- [ ] **Step 4: 跑测试确认 PASS**

Run: `cd apps/admin-ui && npx vitest run src/api/__tests__/tool_timeline.test.ts`
Expected: PASS(含新增用例;既有 `parseToolCalls` 用例不受影响)。

- [ ] **Step 5: 提交**

```bash
git add apps/admin-ui/src/api/tool_timeline.ts apps/admin-ui/src/api/__tests__/tool_timeline.test.ts
git commit -m "feat(playground): 结构化 exec_python/bash 结果(stdout/stderr/exit_code)"
```

---

## Task 3: `tool_timeline` 工具成败聚合 helper

**Files:**
- Modify: `apps/admin-ui/src/api/tool_timeline.ts`
- Test: `apps/admin-ui/src/api/__tests__/tool_timeline.test.ts`

**Interfaces:**
- Consumes: `parseToolCalls`(同文件)
- Produces: `export function toolStatusSummary(events: readonly SseEvent[]): { total: number; failed: number }` —— 供 `TurnCard` 在标题栏聚合"N 工具 · M 失败"

- [ ] **Step 1: 加失败测试**

import 追加 `toolStatusSummary`,并加 describe:

```typescript
describe("toolStatusSummary", () => {
  it("counts total tool calls and failures", () => {
    const events = [
      updates("agent", [aiCall("c1", "exec_python", {}), aiCall2("c2", "web_search", {})],
      ),
      updates("tools", [
        toolResult("c1", "stdout:\nok\n\nexit_code: 0", "success"),
        toolResult("c2", "boom", "error"),
      ]),
    ];
    expect(toolStatusSummary(events)).toEqual({ total: 2, failed: 1 });
  });

  it("returns zeros when there are no tool calls", () => {
    expect(toolStatusSummary([])).toEqual({ total: 0, failed: 0 });
  });
});
```

在文件顶部的 helper 区(`aiCall` 定义下方)补一个多调用构造器,供上面用例的两工具同帧:

```typescript
function aiCall2(id: string, name: string, args: Record<string, unknown>): unknown {
  return { type: "ai", content: "", tool_calls: [{ id, name, args, type: "tool_call" }] };
}
```

> 说明:`aiCall2` 与既有 `aiCall` 同形,仅为在一条 `updates` 里放两个 AI 调用消息时可读命名;若偏好可直接复用 `aiCall`。

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `cd apps/admin-ui && npx vitest run src/api/__tests__/tool_timeline.test.ts`
Expected: FAIL —— `toolStatusSummary` 未导出。

- [ ] **Step 3: 改实现**

`tool_timeline.ts` 末尾(`artifactsFromTools` 后)追加:

```typescript
/** Aggregate a turn's tool activity for an at-a-glance header: how many calls,
 *  how many failed. ``pending`` / ``pending_approval`` are not failures. */
export function toolStatusSummary(
  events: readonly SseEvent[],
): { total: number; failed: number } {
  const entries = parseToolCalls(events);
  const failed = entries.filter((e) => e.status === "error").length;
  return { total: entries.length, failed };
}
```

- [ ] **Step 4: 跑测试确认 PASS**

Run: `cd apps/admin-ui && npx vitest run src/api/__tests__/tool_timeline.test.ts`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add apps/admin-ui/src/api/tool_timeline.ts apps/admin-ui/src/api/__tests__/tool_timeline.test.ts
git commit -m "feat(playground): 工具成败聚合 helper toolStatusSummary"
```

---

## Task 4: 抽出 `TurnMeta` 组件(usage/step/latency/cost + finish_reason/model/缓存写入 chip)

把 `TurnCard` 里的 usage chips(`PlaygroundTab.tsx:1820–1847`)与 step/latency/cost/run-link 行(`:1849–1900`)整体搬进一个**导出的** `TurnMeta` 组件,顺手加 finish_reason / model / cache-write chip。抽出后可用 testing-library 单测,同时推进 §8 重构。

**Files:**
- Create: `apps/admin-ui/src/pages/agent_detail/playground/TurnMeta.tsx`
- Create: `apps/admin-ui/src/pages/agent_detail/playground/__tests__/TurnMeta.test.tsx`
- Modify: `apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx`(删两块内联 JSX,改为 `<TurnMeta …/>`)
- Modify: `apps/admin-ui/src/i18n/locales/zh-CN.ts` + `apps/admin-ui/src/i18n/locales/en.ts`

**Interfaces:**
- Consumes: `TurnSummary`(Task 1 的 `finishReason`/`modelName`/`usage.cacheCreationTokens`)
- Produces:
  ```typescript
  export interface TurnMetaProps {
    summary: TurnSummary;
    costCny: number | null;
    runId: string | null;
    threadId: string | null;
  }
  export function TurnMeta(props: TurnMetaProps): JSX.Element | null;
  ```

- [ ] **Step 1: 加 i18n 键(en + zh 同增)**

`zh-CN.ts` 的 `playground` 块内(`usage_reasoning` / `meta_latency` 附近)追加:

```typescript
    usage_cache_write: "缓存写入",
    meta_finish: "结束原因",
    meta_model: "模型",
```

`en.ts` 的 `playground` 块内相同键:

```typescript
    usage_cache_write: "Cache write",
    meta_finish: "Finish",
    meta_model: "Model",
```

- [ ] **Step 2: 加失败测试**

`playground/__tests__/TurnMeta.test.tsx`:

```typescript
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "../../../../i18n";

import { TurnMeta } from "../TurnMeta";
import type { TurnSummary } from "../../../../api/turn_summary";

function summary(over: Partial<TurnSummary> = {}): TurnSummary {
  return {
    finalText: "hi",
    reasoning: [],
    usage: {
      inputTokens: 100,
      outputTokens: 20,
      totalTokens: 120,
      cacheReadTokens: 0,
      cacheCreationTokens: 0,
      reasoningTokens: 0,
    },
    stepCount: 2,
    latencyMs: 1500,
    finishReason: "stop",
    modelName: "glm-5.2",
    ...over,
  };
}

function renderMeta(s: TurnSummary) {
  render(
    <MemoryRouter>
      <TurnMeta summary={s} costCny={null} runId={null} threadId={null} />
    </MemoryRouter>,
  );
}

describe("TurnMeta", () => {
  it("shows the model name chip", () => {
    renderMeta(summary());
    expect(screen.getByText(/glm-5\.2/)).toBeInTheDocument();
  });

  it("hides finish_reason when it is the normal 'stop'", () => {
    renderMeta(summary({ finishReason: "stop" }));
    expect(screen.queryByText(/length/)).not.toBeInTheDocument();
  });

  it("surfaces a non-stop finish_reason (e.g. length)", () => {
    renderMeta(summary({ finishReason: "length" }));
    expect(screen.getByText(/length/)).toBeInTheDocument();
  });

  it("shows a cache-write chip only when cacheCreationTokens > 0", () => {
    renderMeta(
      summary({
        usage: {
          inputTokens: 10,
          outputTokens: 2,
          totalTokens: 12,
          cacheReadTokens: 0,
          cacheCreationTokens: 7,
          reasoningTokens: 0,
        },
      }),
    );
    expect(screen.getByText(/7/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: 跑测试确认 FAIL**

Run: `cd apps/admin-ui && npx vitest run src/pages/agent_detail/playground/__tests__/TurnMeta.test.tsx`
Expected: FAIL —— 模块 `../TurnMeta` 不存在。

- [ ] **Step 4: 写 `TurnMeta.tsx`**

```typescript
/**
 * TurnMeta — the per-turn metric row of a playground turn: token usage chips,
 * step / latency / cost, the model + finish_reason debug chips, and the
 * "view run" deep link. Extracted from TurnCard so the metric logic is unit
 * testable and TurnCard stays focused (§8 refactor).
 */
import { Tag, Typography } from "antd";
import { ExternalLink } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import type { TurnSummary } from "../../../api/turn_summary";

const { Text } = Typography;

export interface TurnMetaProps {
  summary: TurnSummary;
  /** ≈CNY for the turn (null when no usage or no rate). */
  costCny: number | null;
  runId: string | null;
  threadId: string | null;
}

export function TurnMeta({ summary, costCny, runId, threadId }: TurnMetaProps) {
  const { t } = useTranslation();
  const { usage, stepCount, latencyMs, finishReason, modelName } = summary;
  // "stop" is the normal terminal reason — only surface the interesting ones
  // (length / content_filter / a turn that ended on tool_calls).
  const showFinish = finishReason !== null && finishReason !== "stop";

  const hasUsageRow = usage !== null;
  const hasMetaRow =
    stepCount !== null ||
    latencyMs !== null ||
    costCny !== null ||
    modelName !== null ||
    showFinish ||
    Boolean(runId && threadId);

  if (!hasUsageRow && !hasMetaRow) return null;

  return (
    <>
      {usage && (
        <div
          style={{ marginTop: 8, display: "flex", gap: 6, flexWrap: "wrap" }}
          data-testid="playground-usage"
        >
          <Tag bordered={false} color="geekblue">
            {t("playground.usage_in")}: {usage.inputTokens}
          </Tag>
          <Tag bordered={false} color="geekblue">
            {t("playground.usage_out")}: {usage.outputTokens}
          </Tag>
          <Tag bordered={false}>
            {t("playground.usage_total")}: {usage.totalTokens}
          </Tag>
          {usage.cacheReadTokens > 0 && (
            <Tag bordered={false} color="green">
              {t("playground.usage_cache")}: {usage.cacheReadTokens}
            </Tag>
          )}
          {usage.cacheCreationTokens > 0 && (
            <Tag bordered={false} color="cyan">
              {t("playground.usage_cache_write")}: {usage.cacheCreationTokens}
            </Tag>
          )}
          {usage.reasoningTokens > 0 && (
            <Tag bordered={false} color="purple">
              {t("playground.usage_reasoning")}: {usage.reasoningTokens}
            </Tag>
          )}
        </div>
      )}

      {hasMetaRow && (
        <div
          style={{
            marginTop: 6,
            display: "flex",
            gap: 6,
            flexWrap: "wrap",
            alignItems: "center",
          }}
          data-testid="playground-turn-meta"
        >
          {stepCount !== null && (
            <Tag bordered={false}>
              {t("playground.meta_steps")}: {stepCount}
            </Tag>
          )}
          {latencyMs !== null && (
            <Tag bordered={false}>
              {t("playground.meta_latency")}: {(latencyMs / 1000).toFixed(1)}s
            </Tag>
          )}
          {modelName !== null && (
            <Tag bordered={false} color="blue" data-testid="playground-turn-model">
              {t("playground.meta_model")}: {modelName}
            </Tag>
          )}
          {showFinish && (
            <Tag bordered={false} color="orange" data-testid="playground-turn-finish">
              {t("playground.meta_finish")}: {finishReason}
            </Tag>
          )}
          {costCny !== null && (
            <Tag bordered={false} color="gold" data-testid="playground-turn-cost">
              ≈ ¥{costCny.toFixed(4)}
            </Tag>
          )}
          {runId && threadId && (
            <Link
              to={`/runs/${threadId}/${runId}`}
              style={{
                fontSize: 12,
                display: "inline-flex",
                alignItems: "center",
                gap: 3,
              }}
              data-testid="playground-turn-run-link"
            >
              {t("playground.view_run")}
              <ExternalLink size={11} strokeWidth={1.75} />
            </Link>
          )}
        </div>
      )}
    </>
  );
}
```

> 注:`Text` 未在最终 JSX 用到则删掉该 import —— 保持无未用导入(见 CLAUDE.md §3)。上面示例未用 `Text`,**删掉 `const { Text } = Typography;` 与 `Typography` import 里多余部分**;`Typography` 若无他用一并删。

- [ ] **Step 5: 跑测试确认 PASS**

Run: `cd apps/admin-ui && npx vitest run src/pages/agent_detail/playground/__tests__/TurnMeta.test.tsx`
Expected: PASS。

- [ ] **Step 6: 在 `PlaygroundTab` 里替换内联 JSX**

`PlaygroundTab.tsx` 顶部 import 区加:

```typescript
import { TurnMeta } from "./playground/TurnMeta";
```

删掉 `TurnCard` 内 `{/* Per-turn usage chips */}`(`:1820–1847`)与 `{/* #4 step / latency / cost + #8 run-detail link. */}`(`:1849–1900`)两整块 JSX,替换为单行:

```tsx
        <TurnMeta
          summary={summary}
          costCny={costCny}
          runId={runId}
          threadId={threadId}
        />
```

放在原 usage chips 的位置(即 `ApprovalGate` 块之后、`FeedbackBar` 块之前)。若删除后 `PlaygroundTab.tsx` 里 `ExternalLink` / `Link` 仅被这两块用到而变为未用导入,一并删除对应 import。

- [ ] **Step 7: 全量前端测试 + 类型检查确认无回归**

Run: `cd apps/admin-ui && npx vitest run && npx tsc --noEmit`
Expected: PASS + 无类型错误(尤其无"未用变量/导入")。

- [ ] **Step 8: 提交**

```bash
git add apps/admin-ui/src/pages/agent_detail/playground/ apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx apps/admin-ui/src/i18n/locales/zh-CN.ts apps/admin-ui/src/i18n/locales/en.ts
git commit -m "feat(playground): 抽出 TurnMeta 并加 model/finish_reason/缓存写入 chip"
```

---

## Task 5: `ToolCallCard` 结构化 exec 结果 + `TurnCard` 工具成败标题聚合

**Files:**
- Modify: `apps/admin-ui/src/components/ToolTimeline.tsx`(`ToolCallCard` 渲染 exec 结果)
- Modify: `apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx`(events Collapse label 挂工具聚合)
- Modify: `apps/admin-ui/src/i18n/locales/zh-CN.ts` + `en.ts`
- Test: `apps/admin-ui/src/components/__tests__/ToolTimeline.test.tsx`

**Interfaces:**
- Consumes: `ToolCallEntry.execResult`(Task 2)、`toolStatusSummary`(Task 3)

- [ ] **Step 1: 加 i18n 键(en + zh 同增)**

`zh-CN.ts` 的 `tool_timeline` 块内追加:

```typescript
    exit_code: "退出码",
    stdout_label: "stdout",
    stderr_label: "stderr",
```

`zh-CN.ts` 的 `playground` 块内追加(工具聚合):

```typescript
    tool_count: "{{count}} 个工具",
    tool_failed_count: "{{count}} 个失败",
```

`en.ts` 相同键:

```typescript
    // tool_timeline:
    exit_code: "Exit code",
    stdout_label: "stdout",
    stderr_label: "stderr",
    // playground:
    tool_count: "{{count}} tools",
    tool_failed_count: "{{count}} failed",
```

- [ ] **Step 2: 加失败测试(exec 结果渲染)**

`ToolTimeline.test.tsx` 追加:

```typescript
it("renders a structured exec_python result with an exit-code chip", () => {
  const events = [
    updates("agent", [
      {
        type: "ai",
        content: "",
        tool_calls: [
          { id: "c1", name: "exec_python", args: { code: "print(1)" }, type: "tool_call" },
        ],
      },
    ]),
    updates("tools", [
      { type: "tool", tool_call_id: "c1", name: null, content: "stdout:\n1\n\nexit_code: 0", status: "success" },
    ]),
  ];
  render(<ToolTimeline events={events} />);
  expect(screen.getByTestId("tool-exec-result")).toBeInTheDocument();
  expect(screen.getByTestId("tool-exit-code")).toHaveTextContent("0");
});
```

- [ ] **Step 3: 跑测试确认 FAIL**

Run: `cd apps/admin-ui && npx vitest run src/components/__tests__/ToolTimeline.test.tsx`
Expected: FAIL —— `tool-exec-result` testid 不存在。

- [ ] **Step 4: 改 `ToolCallCard` 渲染 exec 结果**

`ToolTimeline.tsx`:import 处补 i18n 已通过 `t`;在 `ToolCallCard` 内,把"result"折叠项的构造改为**优先结构化 exec 结果**。将现有 `if (entry.resultPreview) { items.push({key:"result", …}) }` 块替换为:

```tsx
  if (entry.execResult) {
    const { stdout, stderr, exitCode } = entry.execResult;
    items.push({
      key: "result",
      label: t("tool_timeline.result_label"),
      children: (
        <div data-testid="tool-exec-result" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div>
            <Tag
              color={exitCode === 0 ? "success" : "error"}
              bordered={false}
              data-testid="tool-exit-code"
            >
              {t("tool_timeline.exit_code")}: {exitCode ?? "?"}
            </Tag>
          </div>
          {stdout && <ExecStream label={t("tool_timeline.stdout_label")} text={stdout} />}
          {stderr && (
            <ExecStream label={t("tool_timeline.stderr_label")} text={stderr} tone="error" />
          )}
        </div>
      ),
    });
  } else if (entry.resultPreview) {
    items.push({
      key: "result",
      label: t("tool_timeline.result_label"),
      children: (
        <pre
          style={{
            margin: 0,
            fontSize: 11,
            fontFamily: "var(--ew-font-mono)",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            maxHeight: 240,
            overflow: "auto",
          }}
        >
          {entry.resultPreview}
        </pre>
      ),
    });
  }
```

在文件底部(`ToolCallCard` 之后)加小组件:

```tsx
function ExecStream({ label, text, tone }: { label: string; text: string; tone?: "error" }) {
  return (
    <div>
      <Text type="secondary" style={{ fontSize: 11 }}>
        {label}
      </Text>
      <pre
        style={{
          margin: "2px 0 0",
          fontSize: 11,
          fontFamily: "var(--ew-font-mono)",
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          maxHeight: 200,
          overflow: "auto",
          color: tone === "error" ? "var(--ew-text-danger, #cf1322)" : undefined,
        }}
      >
        {text}
      </pre>
    </div>
  );
}
```

- [ ] **Step 5: 跑测试确认 PASS**

Run: `cd apps/admin-ui && npx vitest run src/components/__tests__/ToolTimeline.test.tsx`
Expected: PASS。

- [ ] **Step 6: `TurnCard` 事件标题挂工具成败聚合**

`PlaygroundTab.tsx`:import 区补:

```typescript
import { toolStatusSummary } from "../../api/tool_timeline";
```

(若 `PlaygroundTab` 已从 `../../api/tool_timeline` 引入其他符号,合并进同一 import。)

在 `TurnCard` 组件体内、`const summary = summarizeTurn(turn.events);` 附近加:

```typescript
  const toolStats = toolStatusSummary(turn.events);
```

在 events Collapse 的 label(`:1951` 的 `<span>{t("playground.events_label")}</span>`)旁,把该 `<span>` 替换为带聚合的一组:

```tsx
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  {t("playground.events_label")}
                  {toolStats.total > 0 && (
                    <Tag bordered={false} style={{ margin: 0 }} data-testid="playground-tool-count">
                      {t("playground.tool_count", { count: toolStats.total })}
                    </Tag>
                  )}
                  {toolStats.failed > 0 && (
                    <Tag color="error" bordered={false} style={{ margin: 0 }} data-testid="playground-tool-failed">
                      {t("playground.tool_failed_count", { count: toolStats.failed })}
                    </Tag>
                  )}
                </span>
```

- [ ] **Step 7: 全量测试 + 类型检查**

Run: `cd apps/admin-ui && npx vitest run && npx tsc --noEmit`
Expected: PASS + 无类型错误。

- [ ] **Step 8: 提交**

```bash
git add apps/admin-ui/src/components/ToolTimeline.tsx apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx apps/admin-ui/src/i18n/locales/zh-CN.ts apps/admin-ui/src/i18n/locales/en.ts apps/admin-ui/src/components/__tests__/ToolTimeline.test.tsx
git commit -m "feat(playground): exec 结果 exit_code/stdout/stderr 分区 + 工具成败标题聚合"
```

---

## Task 6: 事件视图选择跨刷新持久(localStorage)

`eventView`(timeline/raw)现为 `PlaygroundTab` 内 `useState`(`:174`),刷新即回退 timeline。按 `EventStreamPanel` 现有模式持久化。

**Files:**
- Modify: `apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx`

**Interfaces:** 无对外新接口(组件内部状态)。

- [ ] **Step 1: 改 `eventView` 初值 + setter 持久化**

`PlaygroundTab.tsx`:把 `:174` 的:

```typescript
  const [eventView, setEventView] = useState<"timeline" | "raw">("timeline");
```

替换为(初值从 localStorage 读,写入经封装 setter):

```typescript
  const [eventView, setEventViewState] = useState<"timeline" | "raw">(() => {
    if (typeof window === "undefined") return "timeline";
    return window.localStorage.getItem(EVENT_VIEW_STORAGE_KEY) === "raw"
      ? "raw"
      : "timeline";
  });
  const setEventView = useCallback((next: "timeline" | "raw") => {
    setEventViewState(next);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(EVENT_VIEW_STORAGE_KEY, next);
    }
  }, []);
```

在文件模块级常量区(其他 `const … = …` 顶层声明附近,组件外)加:

```typescript
const EVENT_VIEW_STORAGE_KEY = "expert_work.playground.eventView";
```

确认 `useCallback` 已在 React import 中(`PlaygroundTab` 已用 `useCallback`,无需新增)。`setEventView` 的下游用法(传给 `TurnCard` 的 `onViewChange`)不变。

- [ ] **Step 2: 类型检查 + 全量测试**

Run: `cd apps/admin-ui && npx tsc --noEmit && npx vitest run`
Expected: PASS + 无类型错误。

- [ ] **Step 3: 手动冒烟(浏览器)**

启动 admin-ui(`cd apps/admin-ui && npm run dev`),进某 agent 调试台,跑一轮,把"事件"切到"原始事件",刷新页面 → 仍停在"原始事件"。切回"工具调用"再刷新 → 停在"工具调用"。

- [ ] **Step 4: 提交**

```bash
git add apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx
git commit -m "feat(playground): 事件视图选择 localStorage 持久"
```

---

## 验收(Batch 1 整体)

- [ ] `cd apps/admin-ui && npx vitest run` 全绿。
- [ ] `cd apps/admin-ui && npx tsc --noEmit` 无错。
- [ ] 手动冒烟一轮带 exec_python + 工具调用的 run:
  - TurnMeta 显示 `模型: glm-5.2`;当 run 撞长度上限时显示橙色 `结束原因: length`(正常 stop 不显示)。
  - exec_python 工具卡结果区显示 `退出码: 0`(绿)/ 非零(红)+ stdout/stderr 分区 monospace。
  - 事件标题栏显示 `N 个工具`(有失败时红色 `M 个失败`)。
  - "事件"视图选择刷新后保留。

---

## Self-Review(计划 vs spec Batch 1)

- **spec 项 1(finish_reason/model chip)** → Task 1(解析)+ Task 4(渲染)。✅
- **spec 项 2(exec_python 结构化)** → Task 2(解析)+ Task 5(渲染)。✅
- **spec 项 3(工具状态外露)** → Task 3(聚合)+ Task 5 Step 6(标题栏)。✅
- **spec 项 4(展开态持久)** → Task 6(eventView localStorage);"默认展开事件"已由现有 `defaultActiveKey={["events"]}` 满足,无需改。✅
- **spec 项 5(cache_creation)** → Task 1(解析)+ Task 4(cyan chip,>0 才显)。✅
- **重构**:本批抽 `TurnMeta`;`EventCard` 去重 + `PlaygroundTab` 大拆分明确留 Batch 2。✅(与 spec §8"贯穿各批"一致)
- **类型一致性**:`finishReason`/`modelName`/`cacheCreationTokens`/`execResult`/`ExecResult`/`toolStatusSummary`/`TurnMetaProps` 在定义(Task 1–4)与消费(Task 4–5)处命名一致。✅
- **无 placeholder**:每步含真实代码/命令/期望输出。✅
