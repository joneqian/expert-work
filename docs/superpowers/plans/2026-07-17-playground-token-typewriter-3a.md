# Playground content 打字机 3a Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** playground 流式 turn 里,活跃 step 卡逐字显示 live `content` token(打字机 + streaming/中断徽标 + TTFT),该 step 的权威 `updates` 帧到达后被权威卡取代;纯前端,仅消费 #1000 已发的 `token` 帧。

**Architecture:** 新 `useTokenStream` hook 在 PlaygroundTab 级持有 live 累加(mutable ref + rAF 合批),token 帧**分流不进 `turn.events`**(故 `parseTimeline`/`summarizeTurn` 的 O(n) memo 在 token 流期间稳定命中,只 live 文本重渲染)。StepTimeline 拿 `liveByStep`,为**尚无权威卡**的流式 step 追加合成 `StreamingStepCard`;reconcile 是**渲染期过滤**(权威 `AgentStep.stepCount` 集合抑制对应合成卡)。

**Tech Stack:** React 18 + TypeScript,Antd,react-i18next,vitest + @testing-library/react(jsdom)。

## Global Constraints

- **纯前端**,零后端改动。仅消费现有 `token` SSE 帧:`{step:number, channel:"content", text:string}`(text 已服务端脱敏)。reasoning/tool_args 频道归子项目 3b,本计划不碰。
- **token 帧绝不进 `turn.events`**,也不进 handleRun 的本地 `frames[]`(保 `parseTimeline`/`summarizeTurn` 在 token 流期间不重跑)。
- 新文件放 `apps/admin-ui/src/pages/agent_detail/playground/`;测试放 `playground/__tests__/*.test.ts(x)`。
- **不膨胀 `PlaygroundTab.tsx`(现 2465 行)**:逻辑进新 hook/组件,PlaygroundTab 仅接线。
- reconcile = 渲染期过滤:StepTimeline 用权威 `AgentStep.stepCount` 集合抑制同 step 的合成卡(token.step 与 AgentStep.stepCount 同源 = builder.py 的 `step_count`)。
- 历史/readOnly turn:live props 只传给流式 turn,历史路径零改动。
- i18n **嵌套** `playground: {}`,新键加**三处**:`en.ts` 的 `TranslationKeys` interface(约 844 行 `tl_step: string;` 附近)+ `en.ts` en 值(约 3407 行 `tl_step: "Step {{n}}",` 附近)+ `zh-CN.ts` 值(约 910 行 `tl_step: "步骤 {{n}}",` 附近)。插值 `{{name}}`。
- 测试命令(admin-ui 包 = `@expert-work/admin-ui`):`cd apps/admin-ui && pnpm exec vitest run <file>`(全量 `pnpm test`)。类型:`cd apps/admin-ui && pnpm typecheck`(= `tsc -b --noEmit`;**信真 tsc,不信编辑器 stale 诊断**)。lint 若有:`pnpm lint`。
- rAF 测试:jsdom 提供 `requestAnimationFrame`;测里用 `vi.stubGlobal` 捕获 callback 手动触发,断言合批(多 push → 一次 flush)。

---

## File Structure

| 文件 | 职责 | 改动 |
|---|---|---|
| `playground/useTokenStream.ts`(新) | live token 累加 + rAF 合批 + TTFT + finalize | Task 1 |
| `playground/__tests__/useTokenStream.test.ts`(新) | hook 单测 | Task 1 |
| `playground/StreamingStepCard.tsx`(新) | 合成流式 step 卡(纯文本 + 徽标 + TTFT) | Task 2 |
| `playground/__tests__/StreamingStepCard.test.tsx`(新) | 组件测 | Task 2 |
| `playground/StepTimeline.tsx`(改) | 加 live props + 追加合成卡 + reconcile 过滤 | Task 3 |
| `playground/__tests__/StepTimeline.test.tsx`(改,追加) | 集成测 | Task 3 |
| `PlaygroundTab.tsx`(改,接线) | 实例化 hook、token 分流、传 props、finalize | Task 4 |
| `i18n/locales/en.ts` + `zh-CN.ts`(改) | 3 键 | Task 4 |

依赖:Task 3 依赖 Task 2(渲染 StreamingStepCard);Task 4 依赖 Task 1(hook)+ Task 3(StepTimeline props)。Task 1、2 相互独立。

---

## Task 1: `useTokenStream` hook

**Files:**
- Create: `apps/admin-ui/src/pages/agent_detail/playground/useTokenStream.ts`
- Test: `apps/admin-ui/src/pages/agent_detail/playground/__tests__/useTokenStream.test.ts`

**Interfaces:**
- Consumes:`SseEvent`(`from "../../../api/sessions"`,字段 `{ event:string; data:unknown; receivedAt:string; ... }`)。
- Produces:
  - `interface TokenStreamState { liveByStep: ReadonlyMap<number,string>; ttftMs: number|null; finalized: boolean }`
  - `interface TokenStreamController extends TokenStreamState { push(frame: SseEvent): void; reset(): void; finalize(): void }`
  - `function useTokenStream(): TokenStreamController`

- [ ] **Step 1: 写失败测**

创建 `apps/admin-ui/src/pages/agent_detail/playground/__tests__/useTokenStream.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";

import { useTokenStream } from "../useTokenStream";
import type { SseEvent } from "../../../../api/sessions";

function tokenFrame(step: number, text: string): SseEvent {
  return { id: null, event: "token", data: { step, channel: "content", text }, rawData: "", receivedAt: "t" };
}

// Deterministic rAF: capture the scheduled callback; tests flush it manually.
let rafCbs: FrameRequestCallback[] = [];
beforeEach(() => {
  rafCbs = [];
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    rafCbs.push(cb);
    return rafCbs.length;
  });
  vi.stubGlobal("cancelAnimationFrame", () => {});
});
afterEach(() => vi.unstubAllGlobals());
function flushRaf(): void {
  const cbs = rafCbs;
  rafCbs = [];
  cbs.forEach((cb) => cb(0));
}

describe("useTokenStream", () => {
  it("accumulates content tokens per step after a rAF flush", () => {
    const { result } = renderHook(() => useTokenStream());
    act(() => result.current.reset());
    act(() => {
      result.current.push(tokenFrame(0, "Hel"));
      result.current.push(tokenFrame(0, "lo"));
    });
    // Before flush the snapshot is still empty (batched).
    expect(result.current.liveByStep.size).toBe(0);
    act(() => flushRaf());
    expect(result.current.liveByStep.get(0)).toBe("Hello");
  });

  it("coalesces many pushes into a single flush (one rAF scheduled)", () => {
    const { result } = renderHook(() => useTokenStream());
    act(() => result.current.reset());
    act(() => {
      result.current.push(tokenFrame(0, "a"));
      result.current.push(tokenFrame(0, "b"));
      result.current.push(tokenFrame(0, "c"));
    });
    expect(rafCbs.length).toBe(1); // batched, not 3
    act(() => flushRaf());
    expect(result.current.liveByStep.get(0)).toBe("abc");
  });

  it("ignores non-token frames and non-content channels", () => {
    const { result } = renderHook(() => useTokenStream());
    act(() => result.current.reset());
    act(() => {
      result.current.push({ id: null, event: "updates", data: {}, rawData: "", receivedAt: "t" });
      result.current.push({ id: null, event: "token", data: { step: 0, channel: "reasoning", text: "x" }, rawData: "", receivedAt: "t" });
    });
    act(() => flushRaf());
    expect(result.current.liveByStep.size).toBe(0);
  });

  it("captures TTFT on the first token", () => {
    vi.spyOn(Date, "now").mockReturnValueOnce(1000).mockReturnValueOnce(1250);
    const { result } = renderHook(() => useTokenStream());
    act(() => result.current.reset()); // Date.now() → 1000 (start)
    act(() => result.current.push(tokenFrame(0, "hi"))); // Date.now() → 1250
    act(() => flushRaf());
    expect(result.current.ttftMs).toBe(250);
  });

  it("finalize marks finalized and keeps the buffered text", () => {
    const { result } = renderHook(() => useTokenStream());
    act(() => result.current.reset());
    act(() => result.current.push(tokenFrame(1, "partial")));
    act(() => result.current.finalize());
    expect(result.current.finalized).toBe(true);
    expect(result.current.liveByStep.get(1)).toBe("partial");
  });

  it("reset clears buffers and finalized flag", () => {
    const { result } = renderHook(() => useTokenStream());
    act(() => result.current.reset());
    act(() => result.current.push(tokenFrame(0, "x")));
    act(() => result.current.finalize());
    act(() => result.current.reset());
    expect(result.current.finalized).toBe(false);
    expect(result.current.liveByStep.size).toBe(0);
    expect(result.current.ttftMs).toBe(null);
  });
});
```

- [ ] **Step 2: 跑测,确认失败**

Run: `cd apps/admin-ui && pnpm exec vitest run src/pages/agent_detail/playground/__tests__/useTokenStream.test.ts`
Expected: FAIL —— `Failed to resolve import "../useTokenStream"`(文件未建)。

- [ ] **Step 3: 实现 hook**

创建 `apps/admin-ui/src/pages/agent_detail/playground/useTokenStream.ts`:

```ts
/**
 * useTokenStream — accumulates live `content` token SSE frames (流式 epic 子项目 3a)
 * into a per-step buffer for the playground's typewriter view.
 *
 * Token frames are high-frequency and deliberately kept OUT of `turn.events`
 * (so the O(n) `parseTimeline`/`summarizeTurn` memos stay stable during token
 * flow). This hook holds the buffer in a mutable ref and flushes to React state
 * once per animation frame — many tokens in one frame cause a single re-render.
 * The authoritative `updates` frame remains the source of truth; a step's live
 * buffer is superseded at render time once its authoritative card exists.
 */
import { useCallback, useRef, useState } from "react";

import type { SseEvent } from "../../../api/sessions";

export interface TokenStreamState {
  /** step index → accumulated (already server-redacted) content text. */
  liveByStep: ReadonlyMap<number, string>;
  /** ms from run start to the first token; null until the first token. */
  ttftMs: number | null;
  /** true once the run ended; live steps without an authoritative card are interrupted. */
  finalized: boolean;
}

export interface TokenStreamController extends TokenStreamState {
  /** Feed one SSE frame; only `token`/`channel:"content"` frames mutate state. */
  push: (frame: SseEvent) => void;
  /** Begin a new run: clear buffers + finalized flag, record the start time. */
  reset: () => void;
  /** End the run: final flush, mark finalized (keeps buffered partial text). */
  finalize: () => void;
}

interface TokenFrameData {
  step: number;
  text: string;
}

function parseContentToken(frame: SseEvent): TokenFrameData | null {
  if (frame.event !== "token") return null;
  const d = frame.data;
  if (d === null || typeof d !== "object") return null;
  const rec = d as Record<string, unknown>;
  if (typeof rec.step !== "number" || rec.channel !== "content" || typeof rec.text !== "string") {
    return null;
  }
  return { step: rec.step, text: rec.text };
}

const EMPTY: ReadonlyMap<number, string> = new Map();

export function useTokenStream(): TokenStreamController {
  const bufRef = useRef<Map<number, string>>(new Map());
  const startRef = useRef<number | null>(null);
  const ttftRef = useRef<number | null>(null);
  const rafRef = useRef<number | null>(null);
  const [snapshot, setSnapshot] = useState<TokenStreamState>({
    liveByStep: EMPTY,
    ttftMs: null,
    finalized: false,
  });

  const flush = useCallback(() => {
    rafRef.current = null;
    setSnapshot((prev) => ({
      liveByStep: new Map(bufRef.current),
      ttftMs: ttftRef.current,
      finalized: prev.finalized,
    }));
  }, []);

  const schedule = useCallback(() => {
    if (rafRef.current === null) rafRef.current = requestAnimationFrame(flush);
  }, [flush]);

  const cancel = useCallback(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
  }, []);

  const push = useCallback(
    (frame: SseEvent) => {
      const tok = parseContentToken(frame);
      if (tok === null) return;
      if (ttftRef.current === null && startRef.current !== null) {
        ttftRef.current = Date.now() - startRef.current;
      }
      bufRef.current.set(tok.step, (bufRef.current.get(tok.step) ?? "") + tok.text);
      schedule();
    },
    [schedule],
  );

  const reset = useCallback(() => {
    cancel();
    bufRef.current = new Map();
    startRef.current = Date.now();
    ttftRef.current = null;
    setSnapshot({ liveByStep: EMPTY, ttftMs: null, finalized: false });
  }, [cancel]);

  const finalize = useCallback(() => {
    cancel();
    setSnapshot({ liveByStep: new Map(bufRef.current), ttftMs: ttftRef.current, finalized: true });
  }, [cancel]);

  return { ...snapshot, push, reset, finalize };
}
```

- [ ] **Step 4: 跑测,确认通过**

Run: `cd apps/admin-ui && pnpm exec vitest run src/pages/agent_detail/playground/__tests__/useTokenStream.test.ts`
Expected: PASS(6 测全绿)。

- [ ] **Step 5: 类型检查**

Run: `cd apps/admin-ui && pnpm typecheck`
Expected: 退出 0(无 error)。

- [ ] **Step 6: Commit**

```bash
git add apps/admin-ui/src/pages/agent_detail/playground/useTokenStream.ts apps/admin-ui/src/pages/agent_detail/playground/__tests__/useTokenStream.test.ts
git commit -m "feat(playground): useTokenStream hook —— live token 累加 + rAF 合批(3a)"
```

---

## Task 2: `StreamingStepCard` 组件

**Files:**
- Create: `apps/admin-ui/src/pages/agent_detail/playground/StreamingStepCard.tsx`
- Test: `apps/admin-ui/src/pages/agent_detail/playground/__tests__/StreamingStepCard.test.tsx`

**Interfaces:**
- Consumes:`fmtDuration`(`from "./duration_format"`,`(ms:number)=>string`);i18n(`react-i18next` `useTranslation`);新 i18n 键(Task 4 加)—— 测试用 `import "../../../../i18n"`,键缺失时 `t()` 回落显示键名,故本任务断言用 `data-testid` 不依赖文案。
- Produces:
  - `interface StreamingStepCardProps { step: number; text: string; interrupted: boolean; ttftMs: number|null }`
  - `function StreamingStepCard(props): JSX.Element`

- [ ] **Step 1: 写失败测**

创建 `apps/admin-ui/src/pages/agent_detail/playground/__tests__/StreamingStepCard.test.tsx`:

```tsx
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import "../../../../i18n";

import { StreamingStepCard } from "../StreamingStepCard";

describe("StreamingStepCard", () => {
  it("renders the accumulated text as plain text (not markdown)", () => {
    render(<StreamingStepCard step={0} text={"# not a heading\n**still literal**"} interrupted={false} ttftMs={null} />);
    const card = screen.getByTestId("streaming-step-card");
    // Plain-text render: the raw markdown chars are present verbatim; no <h1>/<strong>.
    expect(card).toHaveTextContent("# not a heading");
    expect(card).toHaveTextContent("**still literal**");
    expect(card.querySelector("h1")).toBeNull();
    expect(card.querySelector("strong")).toBeNull();
  });

  it("shows the streaming badge while not interrupted", () => {
    render(<StreamingStepCard step={1} text="hi" interrupted={false} ttftMs={null} />);
    expect(screen.getByTestId("streaming-badge")).toBeInTheDocument();
    expect(screen.queryByTestId("interrupted-badge")).toBeNull();
  });

  it("shows the interrupted badge when interrupted", () => {
    render(<StreamingStepCard step={1} text="partial" interrupted={true} ttftMs={null} />);
    expect(screen.getByTestId("interrupted-badge")).toBeInTheDocument();
    expect(screen.queryByTestId("streaming-badge")).toBeNull();
  });

  it("shows a TTFT badge when ttftMs is set, hides it when null", () => {
    const { rerender } = render(<StreamingStepCard step={0} text="x" interrupted={false} ttftMs={1234} />);
    expect(screen.getByTestId("ttft-badge")).toBeInTheDocument();
    rerender(<StreamingStepCard step={0} text="x" interrupted={false} ttftMs={null} />);
    expect(screen.queryByTestId("ttft-badge")).toBeNull();
  });

  it("labels the step by index", () => {
    render(<StreamingStepCard step={3} text="x" interrupted={false} ttftMs={null} />);
    expect(screen.getByTestId("streaming-step-card")).toHaveAttribute("data-step", "3");
  });
});
```

- [ ] **Step 2: 跑测,确认失败**

Run: `cd apps/admin-ui && pnpm exec vitest run src/pages/agent_detail/playground/__tests__/StreamingStepCard.test.tsx`
Expected: FAIL —— `Failed to resolve import "../StreamingStepCard"`。

- [ ] **Step 3: 实现组件**

创建 `apps/admin-ui/src/pages/agent_detail/playground/StreamingStepCard.tsx`:

```tsx
/**
 * StreamingStepCard — a synthetic, live step card for the step currently being
 * streamed token-by-token (流式 epic 子项目 3a). Rendered by StepTimeline for a
 * step that has live tokens but no authoritative `AgentStep` card yet. Text is
 * plain (`pre-wrap`), never markdown — markdown reflow on every token is janky
 * and partial fences render oddly; the authoritative card renders markdown once
 * the `updates` frame settles the step.
 */
import { Typography } from "antd";
import { useTranslation } from "react-i18next";

import { fmtDuration } from "./duration_format";

const { Text } = Typography;

const STREAMING = "var(--ew-accent-violet, #a855f7)";
const DANGER = "var(--ew-text-danger, #cf1322)";

export interface StreamingStepCardProps {
  step: number;
  text: string;
  interrupted: boolean;
  ttftMs: number | null;
}

export function StreamingStepCard({ step, text, interrupted, ttftMs }: StreamingStepCardProps) {
  const { t } = useTranslation();
  const accent = interrupted ? DANGER : STREAMING;
  return (
    <div
      data-testid="streaming-step-card"
      data-step={step}
      style={{
        border: `1px solid ${accent}`,
        borderRadius: 8,
        padding: "8px 12px",
        marginBottom: 8,
        background: "var(--ew-bg-elevated, transparent)",
      }}
    >
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6 }}>
        <span style={{ fontWeight: 600 }}>{t("playground.tl_step", { n: step })}</span>
        {interrupted ? (
          <span data-testid="interrupted-badge" style={{ color: DANGER, fontSize: 12 }}>
            {t("playground.interrupted_badge")}
          </span>
        ) : (
          <span data-testid="streaming-badge" style={{ color: STREAMING, fontSize: 12 }}>
            {t("playground.streaming_badge")}
          </span>
        )}
        {ttftMs !== null && (
          <span data-testid="ttft-badge" style={{ color: "var(--ew-text-secondary, #888)", fontSize: 12 }}>
            {t("playground.ttft", { d: fmtDuration(ttftMs) })}
          </span>
        )}
      </div>
      <Text style={{ whiteSpace: "pre-wrap", fontSize: 13 }}>{text}</Text>
    </div>
  );
}
```

- [ ] **Step 4: 跑测,确认通过**

Run: `cd apps/admin-ui && pnpm exec vitest run src/pages/agent_detail/playground/__tests__/StreamingStepCard.test.tsx`
Expected: PASS(5 测全绿)。

> 说明:i18n 键此刻尚未加(Task 4 加),`t()` 回落显示键名字符串,`data-testid` 断言不受影响,测仍绿。

- [ ] **Step 5: 类型检查**

Run: `cd apps/admin-ui && pnpm typecheck`
Expected: 退出 0。

- [ ] **Step 6: Commit**

```bash
git add apps/admin-ui/src/pages/agent_detail/playground/StreamingStepCard.tsx apps/admin-ui/src/pages/agent_detail/playground/__tests__/StreamingStepCard.test.tsx
git commit -m "feat(playground): StreamingStepCard —— 合成流式 step 卡(纯文本+徽标+TTFT,3a)"
```

---

## Task 3: StepTimeline 集成(live props + 合成卡 + reconcile 过滤)

**Files:**
- Modify: `apps/admin-ui/src/pages/agent_detail/playground/StepTimeline.tsx`(`StepTimelineProps` 加 3 可选 props;`StepTimeline` 函数体末尾追加合成卡渲染)
- Test: `apps/admin-ui/src/pages/agent_detail/playground/__tests__/StepTimeline.test.tsx`(追加集成测)

**Interfaces:**
- Consumes:`StreamingStepCard`(Task 2,`{ step, text, interrupted, ttftMs }`);`TimelineItem`/`AgentStep`(`../../../api/timeline`,`AgentStep.stepCount: number|null`)。
- Produces:`StepTimelineProps` 扩展为 `{ items; liveByStep?: ReadonlyMap<number,string>; ttftMs?: number|null; finalized?: boolean }`。

- [ ] **Step 1: 写失败测(追加到现有测文件末尾)**

在 `apps/admin-ui/src/pages/agent_detail/playground/__tests__/StepTimeline.test.tsx` **末尾追加**(保留文件现有全部内容 + 现有 import;复用文件里已有的 `agentStep` fixture — 其 `stepCount: 1`):

```ts
describe("StepTimeline live streaming (3a)", () => {
  it("renders a synthetic streaming card for a live step with no authoritative card", () => {
    render(<StepTimeline items={[]} liveByStep={new Map([[2, "typing…"]])} ttftMs={300} finalized={false} />);
    const card = screen.getByTestId("streaming-step-card");
    expect(card).toHaveAttribute("data-step", "2");
    expect(card).toHaveTextContent("typing…");
    expect(screen.getByTestId("streaming-badge")).toBeInTheDocument();
  });

  it("suppresses the streaming card once the authoritative step card exists (reconcile)", () => {
    // agentStep has stepCount: 1 → a live buffer for step 1 must NOT render a synthetic card.
    render(
      <StepTimeline
        items={[agentStep]}
        liveByStep={new Map([[1, "stale live text"]])}
        ttftMs={null}
        finalized={false}
      />,
    );
    expect(screen.queryByTestId("streaming-step-card")).toBeNull();
    expect(screen.queryByText("stale live text")).toBeNull();
  });

  it("marks an orphan live step interrupted when finalized", () => {
    render(<StepTimeline items={[]} liveByStep={new Map([[0, "half"]])} ttftMs={null} finalized={true} />);
    expect(screen.getByTestId("interrupted-badge")).toBeInTheDocument();
    expect(screen.queryByTestId("streaming-badge")).toBeNull();
  });

  it("renders nothing extra when there are no live steps (backward compatible)", () => {
    render(<StepTimeline items={[agentStep]} />);
    expect(screen.queryByTestId("streaming-step-card")).toBeNull();
    expect(screen.getByTestId("step-timeline")).toBeInTheDocument();
  });
});
```

> 注意:现有 `StepTimeline.test.tsx` 顶部已 `import { StepTimeline } from "../StepTimeline";` 且定义了 `agentStep`(`stepCount:1`)。若现有文件未导出/未定义 `agentStep` 于 describe 外层作用域,把上面用到的 `agentStep` 就近复用文件已有常量即可(不要重复定义)。

- [ ] **Step 2: 跑测,确认失败**

Run: `cd apps/admin-ui && pnpm exec vitest run src/pages/agent_detail/playground/__tests__/StepTimeline.test.tsx`
Expected: FAIL —— `StepTimeline` 不接受 `liveByStep` 等 props(TS 层)/ 或运行期无 `streaming-step-card`(断言失败)。

- [ ] **Step 3: 改 StepTimeline**

编辑 `apps/admin-ui/src/pages/agent_detail/playground/StepTimeline.tsx`。

(a) 顶部 import 区加(与现有 import 并列):

```ts
import { StreamingStepCard } from "./StreamingStepCard";
```

(b) 把 `StepTimelineProps` 接口改为:

```ts
export interface StepTimelineProps {
  items: readonly TimelineItem[];
  /** Live token buffers by step (子项目 3a); absent for history/non-streaming turns. */
  liveByStep?: ReadonlyMap<number, string>;
  /** TTFT to show on the synthetic streaming card. */
  ttftMs?: number | null;
  /** Run ended — orphan live steps (no authoritative card) render as interrupted. */
  finalized?: boolean;
}
```

(c) 把 `StepTimeline` 函数签名与函数体改为(保留原 `items.length === 0` 早退? —— 见下:live 存在时不能早退):

```tsx
export function StepTimeline({ items, liveByStep, ttftMs = null, finalized = false }: StepTimelineProps) {
  // Steps that already have an authoritative card — their live buffer is superseded.
  const settled = new Set<number>();
  for (const it of items) {
    if (it.kind === "agent" && it.stepCount !== null) settled.add(it.stepCount);
  }
  const liveCards = [...(liveByStep ?? new Map<number, string>())]
    .filter(([step]) => !settled.has(step))
    .sort(([a], [b]) => a - b);

  if (items.length === 0 && liveCards.length === 0) return null;

  return (
    <div>
      <div data-testid="step-timeline" style={{ position: "relative", paddingLeft: 26 }}>
        <span
          aria-hidden
          style={{
            position: "absolute",
            left: 9,
            top: 4,
            bottom: 4,
            width: 2,
            background: "var(--ew-border-default)",
            borderRadius: 2,
          }}
        />
        {items.map((item) => {
          switch (item.kind) {
            case "agent":
              return <AgentStepCard key={item.seq} item={item} />;
            case "compaction":
            case "retry":
            case "error":
            case "approval":
            case "end":
              return <MarkerRow key={item.seq} item={item} />;
            default:
              return <AuxNodeRow key={item.seq} item={item} />;
          }
        })}
        {liveCards.map(([step, text]) => (
          <StreamingStepCard
            key={`live-${step}`}
            step={step}
            text={text}
            interrupted={finalized}
            ttftMs={ttftMs}
          />
        ))}
      </div>
      <Legend />
    </div>
  );
}
```

> 关键:原函数体第一行 `if (items.length === 0) return null;` 被替换为 `if (items.length === 0 && liveCards.length === 0) return null;`(流式首个 token 到达但尚无权威 item 时,仍要渲染合成卡)。其余渲染主体(轴线 span、items.map、Legend)逐字保留,仅在 items.map 之后插入 liveCards.map。

- [ ] **Step 4: 跑测,确认通过**

Run: `cd apps/admin-ui && pnpm exec vitest run src/pages/agent_detail/playground/__tests__/StepTimeline.test.tsx`
Expected: PASS(现有测 + 4 新集成测全绿)。

- [ ] **Step 5: 类型检查**

Run: `cd apps/admin-ui && pnpm typecheck`
Expected: 退出 0。

- [ ] **Step 6: Commit**

```bash
git add apps/admin-ui/src/pages/agent_detail/playground/StepTimeline.tsx apps/admin-ui/src/pages/agent_detail/playground/__tests__/StepTimeline.test.tsx
git commit -m "feat(playground): StepTimeline 集成 live 流式卡 + 渲染期 reconcile 过滤(3a)"
```

---

## Task 4: PlaygroundTab 接线 + i18n 键

**Files:**
- Modify: `apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx`(实例化 hook;`handleRun`/`handleDecide` 分流 token + reset/finalize;`TurnCard` 加 3 props 传给 `StepTimeline`;PlaygroundTab 把 live props 传给流式 turn 的 TurnCard)
- Modify: `apps/admin-ui/src/i18n/locales/en.ts`(interface + en 值)、`apps/admin-ui/src/i18n/locales/zh-CN.ts`(zh 值)
- Test: `apps/admin-ui/src/pages/agent_detail/playground/__tests__/StepTimeline.test.tsx` 已覆盖渲染;本任务补一个轻量 hook×timeline reconcile 的行为已由 Task 3 覆盖。接线正确性靠 typecheck + 手动冒烟(见 Step 6)。

**Interfaces:**
- Consumes:`useTokenStream`(Task 1)、`StepTimeline` 新 props(Task 3)。

- [ ] **Step 1: 加 i18n 键(三处)**

`apps/admin-ui/src/i18n/locales/en.ts` —— 在 `TranslationKeys` interface 的 `playground` 段(约 844 行 `tl_step: string;` 附近)加:

```ts
    streaming_badge: string;
    interrupted_badge: string;
    ttft: string;
```

同文件 en 值段(约 3407 行 `tl_step: "Step {{n}}",` 附近)加:

```ts
    streaming_badge: "streaming…",
    interrupted_badge: "interrupted",
    ttft: "TTFT {{d}}",
```

`apps/admin-ui/src/i18n/locales/zh-CN.ts` —— zh 值段(约 910 行 `tl_step: "步骤 {{n}}",` 附近)加:

```ts
    streaming_badge: "流式中…",
    interrupted_badge: "已中断",
    ttft: "首字 {{d}}",
```

- [ ] **Step 2: 实例化 hook + 分流 token(PlaygroundTab.tsx)**

(a) 顶部 import 区加:

```ts
import { useTokenStream } from "./playground/useTokenStream";
```

(b) 在 PlaygroundTab 组件体内(与其他 `useState`/`useRef` 并列,如 `abortRef` 附近)加:

```ts
  const tokenStream = useTokenStream();
  const [streamTurnId, setStreamTurnId] = useState<string | null>(null);
```

(c) 在 `handleRun` 里,创建 turn(`setTurns([...])`)之后、`const ac = new AbortController()` 之前,加:

```ts
    tokenStream.reset();
    setStreamTurnId(turnId);
```

(d) 把 `handleRun` 的 SSE 循环体改为 token 分流(token 帧不进 `frames`/`turn.events`):

```ts
      for await (const frame of streamRun(threadId, body, {
        signal: ac.signal,
      })) {
        if (frame.event === "token") {
          tokenStream.push(frame);
          continue;
        }
        frames.push(frame);
        const approvalFromFrame =
          frame.event === "approval" ? approvalItemFromEvent(frame.data) : null;
        setTurns((prev) =>
          prev.map((tn) =>
            tn.id === turnId
              ? {
                  ...tn,
                  events: [...tn.events, frame],
                  approval: approvalFromFrame ?? tn.approval,
                }
              : tn,
          ),
        );
        if (frame.event === "end") break;
      }
```

(e) 在 `handleRun` 的 `finally` 块里(`setRunning(false)` 附近)加:

```ts
      tokenStream.finalize();
```

- [ ] **Step 3: handleDecide 同款接线**

`handleDecide`(审批续跑,也走 `streamRun`/`streamRunEvents` 类似循环)按 Step 2 (c)(d)(e) 同法接线:进入前 `tokenStream.reset(); setStreamTurnId(<该 turn id>);`,循环 token 分流 `continue`,finally `tokenStream.finalize();`。**若 `handleDecide` 的续跑帧对应的是已有历史 turn(readOnly 重建),则不接线**(readOnly turn 不流式)——按 handleDecide 实际对应的 turn 语义决定;若它复用当前 live turn,则接线。实现者读 `handleDecide` 上下文判定;二者取一,不两开。

- [ ] **Step 4: TurnCard 传 props → StepTimeline**

(a) `TurnCard` 的 props 类型(约 1810-1853 行)加:

```ts
  liveByStep?: ReadonlyMap<number, string>;
  ttftMs?: number | null;
  finalized?: boolean;
```

并在 `function TurnCard({ ..., readOnly = false, liveByStep, ttftMs = null, finalized = false }: {...})` 解构里加这三个。

(b) `TurnCard` 里渲染 `<StepTimeline items={visibleTimeline} />`(约 2390 行)改为:

```tsx
                  <StepTimeline
                    items={visibleTimeline}
                    liveByStep={liveByStep}
                    ttftMs={ttftMs}
                    finalized={finalized}
                  />
```

(c) PlaygroundTab 渲染 TurnCard 的地方(live turns 的 `.map`,**非** readOnly 历史那处),给匹配 `streamTurnId` 的 TurnCard 传 live props:

```tsx
              <TurnCard
                ... 现有 props 不动 ...
                liveByStep={turn.id === streamTurnId ? tokenStream.liveByStep : undefined}
                ttftMs={turn.id === streamTurnId ? tokenStream.ttftMs : null}
                finalized={turn.id === streamTurnId ? tokenStream.finalized : false}
              />
```

> readOnly 历史 turn 的 TurnCard(约 1448 行 `readOnly` 那处)**不传** live props(保持 undefined/默认)——历史 turn 永不流式。

- [ ] **Step 5: 类型检查 + 全量前端测**

Run: `cd apps/admin-ui && pnpm typecheck`
Expected: 退出 0。

Run: `cd apps/admin-ui && pnpm exec vitest run src/pages/agent_detail/playground`
Expected: PASS(useTokenStream / StreamingStepCard / StepTimeline 全绿)。

Run: `cd apps/admin-ui && pnpm test`
Expected: 全套前端测 PASS(无回归)。

- [ ] **Step 6: 手动冒烟(记录结果,非自动)**

真跑一个流式 agent(非 judge-on、非 queue、非 cache),在 playground 观察:
1. 逐字 token 进活跃 step 卡(streaming 徽标 + TTFT)。
2. 该 step 的 `updates` 到达 → 合成卡消失、权威 `AgentStepCard` 出现(无重复/无闪)。
3. Stop → partial 保留 + 已中断徽标。
4. 切到历史会话 turn → 无流式、正常回放。
5. judge-on / queue agent → 无 token 帧 → 纯 step 渲染(与今日一致)。

- [ ] **Step 7: Commit**

```bash
git add apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx apps/admin-ui/src/i18n/locales/en.ts apps/admin-ui/src/i18n/locales/zh-CN.ts
git commit -m "feat(playground): 接线 token 打字机 —— 分流/reset/finalize + live props + i18n(3a)"
```

---

## Self-Review(计划对照 spec)

**1. Spec coverage:**
- a. Provisional+authoritative dual-track(step 卡级)→ Task 1 hook + Task 3 合成卡 + reconcile 过滤。✅
- b. Step attachment(token.step → 活跃 step 卡)→ Task 3(按 stepCount 匹配/抑制)。✅
- e. 中途卡/错 → partial + 中断徽标 → Task 1 finalize + Task 2 interrupted 外观 + Task 3 orphan→interrupted。✅
- f. 历史不变 → Task 4(live props 只传流式 turn;readOnly 不传)。✅
- g. TTFT → Task 1 捕获 + Task 2 徽标。✅
- h. 取消(前端)→ Task 4(复用 abortRef;abort→finalize→中断)。✅
- i. 渲染合批 → Task 1 rAF。✅
- token 不进 events(O(n) memo 稳定)→ Task 4 分流 `continue`。✅
- reconcile 渲染期过滤 → Task 3。✅
- 非目标(reasoning/tool_args 频道、后端 cancel、主答案区打字机)→ 计划未触及。✅

**2. Placeholder scan:** 无 TBD/TODO;每个 code step 给全码;测试给全断言。Task 4 Step 3 的"二者取一"是让实现者按 `handleDecide` 实际语义判定(读现有代码即定),非占位。✅

**3. Type consistency:** `useTokenStream(): TokenStreamController`(`push`/`reset`/`finalize`/`liveByStep: ReadonlyMap<number,string>`/`ttftMs: number|null`/`finalized: boolean`)在 Task 1 定义,Task 4 消费一致;`StreamingStepCardProps { step, text, interrupted, ttftMs }` Task 2 定义、Task 3 消费一致;`StepTimelineProps` 扩展 `{ liveByStep?, ttftMs?, finalized? }` Task 3 定义、Task 4 消费一致;`AgentStep.stepCount: number|null` 与 `token.step: number` 同源匹配。✅

## Follow-up(3a 后,非阻塞)
- PlaygroundTab 级 hook 使其每 rAF 重渲染(token 流期 events 稳定 → 内存 memo 命中 → 廉价);若冒烟见卡顿,`React.memo(TurnCard)` 或把 hook 下沉到流式 TurnCard(callback-ref 注入)。
- 子项目 3b:后端 `TokenSink` 补 `reasoning`/`tool_args` 频道 + 前端 live 视图,复用本 hook 的多频道扩展。
