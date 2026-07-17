# Playground reasoning + tool_args 流式 3b Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Playground 把 LLM 的 reasoning token 逐字 live 渲进活跃 step 卡(流时展开→定档收起为"思考 Xs"),并在工具步显工具名 chip;后端 `TokenSink` 补发 reasoning/tool_args 两频道。

**Architecture:** 后端唯一改动点是 `TokenSink` 一个类——`router` 每 delta 已 `await sink(delta)`(含 reasoning/tool delta,今天被丢弃),3b 让 sink 读 `delta.reasoning`(独立第二 `StreamingRedactor`)与 `delta.tool_calls`(工具名首现一帧)。前端把 3a 已验证的分流管道从单 `string` 加宽成 `LiveStep{content,reasoning,toolNames,reasoningMs}`:同 hook、同合成卡(加三区)、同渲染期 reconcile。token 帧仍分流不进 `turn.events`(3a 的 O(n) memo 前提)。

**Tech Stack:** Python(orchestrator,pytest/asyncio)、TypeScript/React(admin-ui,vitest/@testing-library/react)、SSE。

## Global Constraints

- **token 帧分流不进 `turn.events`**:命脉不变;`PlaygroundTab` 循环 `if(frame.event==="token"){tokenStream.push(frame);continue;}` 先于 `frames.push`/`setTurns`。
- **provisional 契约**:token 帧 live-only 不持久化、reconnect 不回放;权威 `updates` 帧是最终真相。
- **门控**:judge-off ∧ publish 存在(`make_token_sink` 不变);judge-on 回退 step 级帧不流。
- **脱敏**:reasoning 走 buffered-release(`StreamingRedactor`,`HOLD_CHARS=64`);tool name 不脱敏(声明的静态标识);**tool args 不流**(名字-only)→ 零 args 脱敏路径。content 与 reasoning 是**独立 `StreamingRedactor` 实例**(两条流,状态不共享)。
- **SSE 帧形状**:`content`/`reasoning` 帧 `{step:int, channel:str, text:str}`;`tool_args` 帧 `{step:int, channel:"tool_args", tool_index:int, name:str}`(无 text/args)。
- **测试命令**:后端 `cd services/orchestrator && uv run python -m pytest`(裸 `python` 挑不动编译扩展);前端 `cd apps/admin-ui && pnpm exec vitest run <file>` + `pnpm typecheck`(真 `tsc -b`,不信编辑器 stale 诊断)。
- **i18n**:新键三处(`en.ts` 接口 + en 值 + `zh-CN.ts` 值),键名 `playground.reasoning_label` / `playground.reasoning_summary`。
- **surgical**:`PlaygroundTab.tsx`(2465 行)只接线/类型加宽,不搬逻辑、不膨胀。

---

## 文件结构

**后端**
- `services/orchestrator/src/orchestrator/graph_builder/streaming_redact.py` — 改 `TokenSink`(三频道)。`StreamingRedactor`/`make_token_sink` 不动。
- `docs/api/streaming-events.md` — channel 枚举 + reasoning/tool_args 帧文档。
- `services/orchestrator/tests/test_streaming_redact.py` — 扩 TokenSink 测(reasoning/隔离/工具名/回归)。
- `services/orchestrator/tests/test_llm_router_streaming.py` — 加 cancel 传播核查测。

**前端**
- `apps/admin-ui/src/pages/agent_detail/playground/useTokenStream.ts` — 改:值类型 `string→LiveStep` + `parseToken` 三频道 + reasoningMs。导出 `LiveStep`。
- `apps/admin-ui/src/pages/agent_detail/playground/StreamingStepCard.tsx` — 改:props `text→live:LiveStep`;加 reasoning 折叠区 + tool chips。
- `apps/admin-ui/src/pages/agent_detail/playground/StepTimeline.tsx` — 改:`liveByStep` 类型加宽 + 传 `live` 给卡。
- `apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx` — 改:`TurnCardProps.liveByStep` 类型加宽(导入 `LiveStep`)。仅类型,无逻辑改。
- `apps/admin-ui/src/i18n/locales/en.ts` + `zh-CN.ts` — 加 `reasoning_label`/`reasoning_summary`。
- 各 `__tests__/*.test.ts(x)` — 更新既有断言(值类型变) + 加新频道测。

**任务顺序**:1 后端频道 → 2 cancel 核查 → 3 前端 hook(基建)→ 4 前端合成卡 → 5 前端接线。3→4→5 有类型依赖(`LiveStep` 由 3 产)。

---

### Task 1: 后端 `TokenSink` 三频道 + 契约文档

**Files:**
- Modify: `services/orchestrator/src/orchestrator/graph_builder/streaming_redact.py:155-177`(`TokenSink` 类)
- Modify: `docs/api/streaming-events.md:22-51`(token 事件段)
- Test: `services/orchestrator/tests/test_streaming_redact.py`(在既有 TokenSink 测后追加)

**Interfaces:**
- Consumes: `LLMDelta{content:str, reasoning:str, tool_calls:tuple[ToolCallChunk,...]}`、`ToolCallChunk{index:int, id:str|None, name:str|None, args_fragment:str}`(`orchestrator.llm.providers._streaming`);`StreamingRedactor(dlp:bool, screen:bool)`(同文件)。
- Produces: `TokenSink(step, publish, dlp, screen)` 现发三种帧:content `{step,channel:"content",text}`、reasoning `{step,channel:"reasoning",text}`、tool_args `{step,channel:"tool_args",tool_index,name}`。`make_token_sink(...)` 签名与门控不变。

- [ ] **Step 1: 写失败测试**(追加到 `test_streaming_redact.py` 末尾)

```python
@pytest.mark.asyncio
async def test_token_sink_publishes_reasoning_frames() -> None:
    frames: list[dict] = []

    async def pub(f: dict) -> None:
        frames.append(f)

    sink = TokenSink(step=2, publish=pub, dlp=False, screen=False)
    await sink(LLMDelta(reasoning="R" * 100))
    await sink.flush()
    rf = [f for f in frames if f["channel"] == "reasoning"]
    assert rf and all(f["step"] == 2 for f in rf)
    assert "".join(f["text"] for f in rf) == "R" * 100


@pytest.mark.asyncio
async def test_token_sink_redacts_reasoning_pii() -> None:
    frames: list[dict] = []

    async def pub(f: dict) -> None:
        frames.append(f)

    sink = TokenSink(step=0, publish=pub, dlp=True, screen=False)
    await sink(LLMDelta(reasoning="card 4111 1111 1111 1111 hmm " + "y" * 60))
    await sink.flush()
    joined = "".join(f["text"] for f in frames if f["channel"] == "reasoning")
    assert "4111" not in joined and "[redacted]" in joined


@pytest.mark.asyncio
async def test_content_and_reasoning_streams_isolated() -> None:
    # Each text channel has its OWN StreamingRedactor — a card's digits in the
    # content stream and a different card in the reasoning stream each redact
    # independently; if they shared one redactor the interleaved feeds would
    # corrupt each other's buffered-release state.
    frames: list[dict] = []

    async def pub(f: dict) -> None:
        frames.append(f)

    sink = TokenSink(step=0, publish=pub, dlp=True, screen=False)
    await sink(LLMDelta(content="4111 1111 ", reasoning="9999 8888 "))
    await sink(LLMDelta(content="1111 1111", reasoning="7777 6666"))
    await sink.flush()
    content = "".join(f["text"] for f in frames if f["channel"] == "content")
    reasoning = "".join(f["text"] for f in frames if f["channel"] == "reasoning")
    assert content == "[redacted]" and reasoning == "[redacted]"
    assert "4111" not in content and "9999" not in reasoning


@pytest.mark.asyncio
async def test_token_sink_emits_tool_name_once_per_index() -> None:
    from orchestrator.llm.providers._streaming import ToolCallChunk

    frames: list[dict] = []

    async def pub(f: dict) -> None:
        frames.append(f)

    sink = TokenSink(step=1, publish=pub, dlp=False, screen=False)
    # name arrives on the first fragment for index 0; later fragments carry args only
    await sink(LLMDelta(tool_calls=(ToolCallChunk(index=0, id="c0", name="search_web", args_fragment='{"q":'),)))
    await sink(LLMDelta(tool_calls=(ToolCallChunk(index=0, args_fragment='"hi"}'),)))
    # a second parallel tool at index 1
    await sink(LLMDelta(tool_calls=(ToolCallChunk(index=1, id="c1", name="read_file", args_fragment="{}"),)))
    await sink.flush()
    tool_frames = [f for f in frames if f["channel"] == "tool_args"]
    assert tool_frames == [
        {"step": 1, "channel": "tool_args", "tool_index": 0, "name": "search_web"},
        {"step": 1, "channel": "tool_args", "tool_index": 1, "name": "read_file"},
    ]
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd services/orchestrator && uv run python -m pytest tests/test_streaming_redact.py -k "reasoning or isolated or tool_name" -v`
Expected: FAIL(reasoning 帧不发 → `test_token_sink_publishes_reasoning_frames` 断言空;tool_args 不发 → `test_token_sink_emits_tool_name_once_per_index` 得空列表)。

- [ ] **Step 3: 改 `TokenSink`**(替换 `streaming_redact.py` 的 `class TokenSink` 整体,`make_token_sink` 保持不变)

```python
class TokenSink:
    """Per-run multi-channel token emitter (子项目 2 content + 3b reasoning/tool_args).

    One :class:`StreamingRedactor` per *text* channel (content, reasoning —
    independent buffered-release streams); tool-call *names* are emitted once
    per ``index`` when first seen. Each streamed ``LLMDelta`` publishes the
    newly-stable redacted text of each text channel; ``flush`` releases the
    buffered-release tails after the router returns. Tool *arguments* are NOT
    streamed — they reach the client via the authoritative ``updates`` frame
    (name-only, 子项目 3b decision), so there is no argument-redaction path.
    """

    def __init__(self, *, step: int, publish: TokenPublish, dlp: bool, screen: bool) -> None:
        self._step = step
        self._publish = publish
        self._content = StreamingRedactor(dlp=dlp, screen=screen)
        self._reasoning = StreamingRedactor(dlp=dlp, screen=screen)
        self._tool_names: dict[int, str] = {}

    async def __call__(self, delta: LLMDelta) -> None:
        safe = self._content.feed(delta.content)
        if safe:
            await self._publish({"step": self._step, "channel": "content", "text": safe})
        rsafe = self._reasoning.feed(delta.reasoning)
        if rsafe:
            await self._publish({"step": self._step, "channel": "reasoning", "text": rsafe})
        for tc in delta.tool_calls:
            if tc.name and tc.index not in self._tool_names:
                self._tool_names[tc.index] = tc.name
                await self._publish(
                    {
                        "step": self._step,
                        "channel": "tool_args",
                        "tool_index": tc.index,
                        "name": tc.name,
                    }
                )

    async def flush(self) -> None:
        tail = self._content.flush()
        if tail:
            await self._publish({"step": self._step, "channel": "content", "text": tail})
        rtail = self._reasoning.flush()
        if rtail:
            await self._publish({"step": self._step, "channel": "reasoning", "text": rtail})
```

- [ ] **Step 4: 跑测试验证通过(含既有 content 回归)**

Run: `cd services/orchestrator && uv run python -m pytest tests/test_streaming_redact.py -v`
Expected: PASS 全部——新 5 测 + 既有 `test_token_sink_publishes_content_frames`/`test_token_sink_redacts_pii`/`test_make_token_sink_*`(content 路径零回归)。

- [ ] **Step 5: 改契约文档 `docs/api/streaming-events.md`**

把 `## The \`token\` event (provisional preview)` 段(约 22-51 行)的示例与 channel 说明改为三频道。将现有单一 content 示例块替换为:

````markdown
```
event: token
data: {"step": 0, "channel": "content", "text": "partial answer fragment"}
event: token
data: {"step": 0, "channel": "reasoning", "text": "let me think about..."}
event: token
data: {"step": 0, "channel": "tool_args", "tool_index": 0, "name": "search_web"}
```

- `step` — the agent step index the fragment belongs to.
- `channel` — one of `"content"` (answer text), `"reasoning"` (the model's
  thinking, for reasoning-capable models), or `"tool_args"` (a tool call is
  being made).
- `content` / `reasoning` frames carry `text` — an already-redacted fragment.
- `tool_args` frames carry `tool_index` (which parallel tool call) and `name`
  (the tool being called), emitted once when the name first appears. The tool
  **arguments are not streamed**; they arrive complete on the authoritative
  `updates` frame.
````

其余"provisional / 不持久化 / 哪些 run 发 token"文字不变。

- [ ] **Step 6: Commit**

```bash
git add services/orchestrator/src/orchestrator/graph_builder/streaming_redact.py \
        services/orchestrator/tests/test_streaming_redact.py \
        docs/api/streaming-events.md
git commit -m "feat(orchestrator): TokenSink 补 reasoning + tool_args 频道(流式 3b 后端)"
```

---

### Task 2: cancel 中途停读传播核查测

**Files:**
- Test: `services/orchestrator/tests/test_llm_router_streaming.py`(追加一测)

**Interfaces:**
- Consumes: `LLMRouter(providers, first_token_timeout_s, idle_timeout_s)`、`ProviderHandle(provider, key)`、`LLMDelta`(该文件已导入);流式 provider double = 有 `async def stream(self,*,messages,tools,output_schema=None)` 生成器 + `new_stream_assembler()` 的类(见文件内 `_StreamProvider`)。
- Produces: 无生产码;核查既有行为。

**背景(实现者必读)**:`router._drive_stream` 循环 `await _next_delta(it, timeout)`,`_next_delta` 用 `asyncio.wait_for(it.__anext__(), timeout)`。取消消费 `router()` 的 task 时,`wait_for` 主动 cancel 并 await 内层 `__anext__` task → CancelledError 抛进 provider 生成器的挂起点 → 其 `finally`/httpx `async with __aexit__` 作为取消的一部分**同步**运行,`await task` 抛 CancelledError 返回时上游已关。**预期此测通过**(确定性关上游,非靠 GC)。若失败 = 暴露泄漏 → 上报用户(非目标里的"主动 cancel 修"),不静默改 `_drive_stream`。

- [ ] **Step 1: 写测试**(追加到 `test_llm_router_streaming.py` 末尾)

```python
@pytest.mark.asyncio
async def test_cancel_mid_stream_runs_provider_cleanup() -> None:
    # Cancelling the run while the router awaits the next token must throw into
    # the provider stream's suspended __anext__ and run its cleanup (finally /
    # httpx `async with` __aexit__) — no leaked upstream generation. This is
    # deterministic: _next_delta awaits it.__anext__() under asyncio.wait_for,
    # which cancels-and-awaits the inner task on cancellation.
    reached = asyncio.Event()
    closed = asyncio.Event()

    class _Stalling:
        async def stream(self, *, messages, tools, output_schema=None):
            try:
                yield LLMDelta(content="a")  # first token → enters Phase 2
                reached.set()  # signal we are about to stall on the next token
                await asyncio.sleep(30)  # router awaits __anext__ here
                yield LLMDelta(content="b")  # never reached
            finally:
                closed.set()  # cancellation cleanup ran

        def new_stream_assembler(self):
            from orchestrator.llm.providers._streaming import OpenAIStreamAssembler

            return OpenAIStreamAssembler()

    router = LLMRouter(
        providers=[ProviderHandle(provider=_Stalling(), key="x")],
        first_token_timeout_s=5,
        idle_timeout_s=5,
    )
    task = asyncio.create_task(router(messages=[], tools=[]))
    await asyncio.wait_for(reached.wait(), timeout=1)  # deterministic: now stalling
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # wait_for cancels-and-awaits the inner __anext__, so the finally has run by now.
    assert closed.is_set()
```

- [ ] **Step 2: 跑测试**

Run: `cd services/orchestrator && uv run python -m pytest tests/test_llm_router_streaming.py::test_cancel_mid_stream_runs_provider_cleanup -v`
Expected: PASS(确定性关上游)。**若 FAIL(`closed` 未 set)→ 停,报告 controller:核查暴露上游未在 cancel 时确定性关闭,属"主动 cancel 修"需用户裁决,不在本任务内改 `_drive_stream`。**

- [ ] **Step 3: Commit**

```bash
git add services/orchestrator/tests/test_llm_router_streaming.py
git commit -m "test(orchestrator): 核查 cancel 中途停读关上游流(流式 3b)"
```

---

### Task 3: 前端 `useTokenStream` 多频道(`LiveStep`)

**Files:**
- Modify: `apps/admin-ui/src/pages/agent_detail/playground/useTokenStream.ts`(整文件重写)
- Test: `apps/admin-ui/src/pages/agent_detail/playground/__tests__/useTokenStream.test.ts`(更新既有 + 加新)

**Interfaces:**
- Consumes: `SseEvent{event:string, data:unknown, ...}`(`../../../api/sessions`)。
- Produces:导出 `interface LiveStep{content:string; reasoning:string; toolNames:ReadonlyMap<number,string>; reasoningMs:number|null}`;`useTokenStream():TokenStreamController` 其中 `liveByStep:ReadonlyMap<number,LiveStep>`,`push/reset/finalize` 签名不变,`ttftMs`/`finalized` 不变。Task 4/5 依赖 `LiveStep` 与 `liveByStep` 值类型。

- [ ] **Step 1: 更新既有测试到新值类型 + 加新频道测**(整文件替换 `__tests__/useTokenStream.test.ts`)

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";

import { useTokenStream } from "../useTokenStream";
import type { SseEvent } from "../../../../api/sessions";

function contentFrame(step: number, text: string): SseEvent {
  return { id: null, event: "token", data: { step, channel: "content", text }, rawData: "", receivedAt: "t" };
}
function reasoningFrame(step: number, text: string): SseEvent {
  return { id: null, event: "token", data: { step, channel: "reasoning", text }, rawData: "", receivedAt: "t" };
}
function toolFrame(step: number, toolIndex: number, name: string): SseEvent {
  return {
    id: null,
    event: "token",
    data: { step, channel: "tool_args", tool_index: toolIndex, name },
    rawData: "",
    receivedAt: "t",
  };
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
      result.current.push(contentFrame(0, "Hel"));
      result.current.push(contentFrame(0, "lo"));
    });
    expect(result.current.liveByStep.size).toBe(0); // batched
    act(() => flushRaf());
    expect(result.current.liveByStep.get(0)?.content).toBe("Hello");
  });

  it("accumulates reasoning tokens per step (separate channel)", () => {
    const { result } = renderHook(() => useTokenStream());
    act(() => result.current.reset());
    act(() => {
      result.current.push(reasoningFrame(0, "think"));
      result.current.push(reasoningFrame(0, "ing"));
    });
    act(() => flushRaf());
    expect(result.current.liveByStep.get(0)?.reasoning).toBe("thinking");
    expect(result.current.liveByStep.get(0)?.content).toBe("");
  });

  it("records tool names by index", () => {
    const { result } = renderHook(() => useTokenStream());
    act(() => result.current.reset());
    act(() => {
      result.current.push(toolFrame(0, 0, "search_web"));
      result.current.push(toolFrame(0, 1, "read_file"));
    });
    act(() => flushRaf());
    const names = result.current.liveByStep.get(0)?.toolNames;
    expect(names?.get(0)).toBe("search_web");
    expect(names?.get(1)).toBe("read_file");
  });

  it("coalesces many pushes into a single flush (one rAF scheduled)", () => {
    const { result } = renderHook(() => useTokenStream());
    act(() => result.current.reset());
    act(() => {
      result.current.push(contentFrame(0, "a"));
      result.current.push(reasoningFrame(0, "b"));
      result.current.push(toolFrame(0, 0, "t"));
    });
    expect(rafCbs.length).toBe(1); // batched, not 3
    act(() => flushRaf());
    expect(result.current.liveByStep.get(0)?.content).toBe("a");
  });

  it("ignores non-token frames and unknown channels", () => {
    const { result } = renderHook(() => useTokenStream());
    act(() => result.current.reset());
    act(() => {
      result.current.push({ id: null, event: "updates", data: {}, rawData: "", receivedAt: "t" });
      result.current.push({ id: null, event: "token", data: { step: 0, channel: "bogus", text: "x" }, rawData: "", receivedAt: "t" });
      result.current.push({ id: null, event: "token", data: { step: 0, channel: "content" }, rawData: "", receivedAt: "t" }); // no text
    });
    act(() => flushRaf());
    expect(result.current.liveByStep.size).toBe(0);
  });

  it("captures TTFT on the first token (any channel)", () => {
    vi.spyOn(Date, "now").mockReturnValueOnce(1000).mockReturnValueOnce(1250).mockReturnValue(1250);
    const { result } = renderHook(() => useTokenStream());
    act(() => result.current.reset()); // Date.now() → 1000 (start)
    act(() => result.current.push(reasoningFrame(0, "hmm"))); // Date.now() → 1250
    act(() => flushRaf());
    expect(result.current.ttftMs).toBe(250);
  });

  it("computes reasoningMs from reasoning-start to content-start", () => {
    // push(reasoning) calls Date.now() TWICE: once for ttft, once for
    // reasoningStart. push(content) calls it once (ttft already set) for
    // contentStart. Mock the exact call sequence.
    vi.spyOn(Date, "now")
      .mockReturnValueOnce(1000) // #1 reset → start
      .mockReturnValueOnce(1100) // #2 ttft base
      .mockReturnValueOnce(1100) // #3 reasoningStart
      .mockReturnValue(1900); // #4 contentStart (and any later)
    const { result } = renderHook(() => useTokenStream());
    act(() => result.current.reset());
    act(() => result.current.push(reasoningFrame(0, "r")));
    act(() => result.current.push(contentFrame(0, "c")));
    act(() => flushRaf());
    expect(result.current.liveByStep.get(0)?.reasoningMs).toBe(800); // 1900 - 1100
  });

  it("computes reasoningMs to finalize time for a reasoning-only step", () => {
    // push(reasoning) calls Date.now() twice (ttft, reasoningStart); finalize
    // calls it once (finalizeTime). Mock the exact sequence.
    vi.spyOn(Date, "now")
      .mockReturnValueOnce(1000) // #1 reset → start
      .mockReturnValueOnce(1100) // #2 ttft base
      .mockReturnValueOnce(1100) // #3 reasoningStart
      .mockReturnValue(1600); // #4 finalizeTime (and any later)
    const { result } = renderHook(() => useTokenStream());
    act(() => result.current.reset());
    act(() => result.current.push(reasoningFrame(0, "r")));
    act(() => result.current.finalize());
    expect(result.current.liveByStep.get(0)?.reasoningMs).toBe(500); // 1600 - 1100
    expect(result.current.finalized).toBe(true);
  });

  it("leaves reasoningMs null while still reasoning (no content yet)", () => {
    const { result } = renderHook(() => useTokenStream());
    act(() => result.current.reset());
    act(() => result.current.push(reasoningFrame(0, "r")));
    act(() => flushRaf());
    expect(result.current.liveByStep.get(0)?.reasoningMs).toBe(null);
  });

  it("finalize marks finalized and keeps the buffered text", () => {
    const { result } = renderHook(() => useTokenStream());
    act(() => result.current.reset());
    act(() => result.current.push(contentFrame(1, "partial")));
    act(() => result.current.finalize());
    expect(result.current.finalized).toBe(true);
    expect(result.current.liveByStep.get(1)?.content).toBe("partial");
  });

  it("reset clears buffers and finalized flag", () => {
    const { result } = renderHook(() => useTokenStream());
    act(() => result.current.reset());
    act(() => result.current.push(contentFrame(0, "x")));
    act(() => result.current.finalize());
    act(() => result.current.reset());
    expect(result.current.finalized).toBe(false);
    expect(result.current.liveByStep.size).toBe(0);
    expect(result.current.ttftMs).toBe(null);
  });

  it("reschedules a new rAF for a push after a flush (typewriter keeps flowing)", () => {
    const { result } = renderHook(() => useTokenStream());
    act(() => result.current.reset());
    act(() => result.current.push(contentFrame(0, "a")));
    expect(rafCbs.length).toBe(1);
    act(() => flushRaf());
    act(() => result.current.push(contentFrame(0, "b")));
    expect(rafCbs.length).toBe(1); // a NEW rAF was scheduled — handle reset, not stuck
    act(() => flushRaf());
    expect(result.current.liveByStep.get(0)?.content).toBe("ab");
  });

  it("a stale queued flush after finalize does not clobber the finalized snapshot", () => {
    const { result } = renderHook(() => useTokenStream());
    act(() => result.current.reset());
    act(() => result.current.push(contentFrame(0, "partial")));
    act(() => result.current.finalize());
    expect(result.current.finalized).toBe(true);
    act(() => flushRaf());
    expect(result.current.finalized).toBe(true);
    expect(result.current.liveByStep.get(0)?.content).toBe("partial");
  });
});
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd apps/admin-ui && pnpm exec vitest run src/pages/agent_detail/playground/__tests__/useTokenStream.test.ts`
Expected: FAIL(`liveByStep.get(0)?.content` — 现值是 string 无 `.content`;`LiveStep` 未导出;reasoning/tool 未收)。

- [ ] **Step 3: 重写 `useTokenStream.ts`**(整文件替换)

```ts
/**
 * useTokenStream — accumulates live token SSE frames across three channels
 * (content / reasoning / tool_args) into a per-step LiveStep for the
 * playground's streaming step card (流式 epic 子项目 3a content + 3b
 * reasoning/tool_args).
 *
 * Token frames are high-frequency and deliberately kept OUT of `turn.events`
 * (so the O(n) `parseTimeline`/`summarizeTurn` memos stay stable during token
 * flow). This hook holds the buffers in mutable refs and flushes to React
 * state once per animation frame — many tokens in one frame cause a single
 * re-render. The authoritative `updates` frame remains the source of truth; a
 * step's live buffer is superseded at render time once its authoritative card
 * exists.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import type { SseEvent } from "../../../api/sessions";

/** One step's live, cross-channel token buffer. */
export interface LiveStep {
  /** Accumulated (already server-redacted) answer text. */
  content: string;
  /** Accumulated (already server-redacted) reasoning text. */
  reasoning: string;
  /** tool-call index → tool name (name-only; args arrive via the authoritative card). */
  toolNames: ReadonlyMap<number, string>;
  /** Reasoning duration in ms once known (reasoning-start → content-start, or
   *  → finalize for a step that never produced content); null while still
   *  reasoning (not yet collapsible). */
  reasoningMs: number | null;
}

export interface TokenStreamState {
  liveByStep: ReadonlyMap<number, LiveStep>;
  /** ms from run start to the first token (any channel); null until the first token. */
  ttftMs: number | null;
  /** true once the run ended; live steps without an authoritative card are interrupted. */
  finalized: boolean;
}

export interface TokenStreamController extends TokenStreamState {
  /** Feed one SSE frame; only `token` frames on a known channel mutate state. */
  push: (frame: SseEvent) => void;
  /** Begin a new run: clear buffers + finalized flag, record the start time. */
  reset: () => void;
  /** End the run: final flush, mark finalized (keeps buffered partial text). */
  finalize: () => void;
}

interface StepBuf {
  content: string;
  reasoning: string;
  toolNames: Map<number, string>;
}

type ParsedToken =
  | { kind: "text"; channel: "content" | "reasoning"; step: number; text: string }
  | { kind: "tool"; step: number; toolIndex: number; name: string };

function parseToken(frame: SseEvent): ParsedToken | null {
  if (frame.event !== "token") return null;
  const d = frame.data;
  if (d === null || typeof d !== "object") return null;
  const rec = d as Record<string, unknown>;
  if (typeof rec.step !== "number") return null;
  if (rec.channel === "content" || rec.channel === "reasoning") {
    if (typeof rec.text !== "string") return null;
    return { kind: "text", channel: rec.channel, step: rec.step, text: rec.text };
  }
  if (rec.channel === "tool_args") {
    if (typeof rec.tool_index !== "number" || typeof rec.name !== "string") return null;
    return { kind: "tool", step: rec.step, toolIndex: rec.tool_index, name: rec.name };
  }
  return null;
}

const EMPTY: ReadonlyMap<number, LiveStep> = new Map();

export function useTokenStream(): TokenStreamController {
  const bufRef = useRef<Map<number, StepBuf>>(new Map());
  const reasoningStartRef = useRef<Map<number, number>>(new Map());
  const contentStartRef = useRef<Map<number, number>>(new Map());
  const startRef = useRef<number | null>(null);
  const ttftRef = useRef<number | null>(null);
  const rafRef = useRef<number | null>(null);
  const [snapshot, setSnapshot] = useState<TokenStreamState>({
    liveByStep: EMPTY,
    ttftMs: null,
    finalized: false,
  });

  // Build the render snapshot from the mutable buffers. `finalizeTime` non-null
  // lets a reasoning-only step (no content start) report its thinking duration.
  const build = useCallback((finalizeTime: number | null): Map<number, LiveStep> => {
    const map = new Map<number, LiveStep>();
    for (const [step, b] of bufRef.current) {
      const rs = reasoningStartRef.current.get(step);
      const cs = contentStartRef.current.get(step);
      let reasoningMs: number | null = null;
      if (rs !== undefined) {
        if (cs !== undefined) reasoningMs = cs - rs;
        else if (finalizeTime !== null) reasoningMs = finalizeTime - rs;
      }
      map.set(step, {
        content: b.content,
        reasoning: b.reasoning,
        toolNames: new Map(b.toolNames),
        reasoningMs,
      });
    }
    return map;
  }, []);

  const flush = useCallback(() => {
    rafRef.current = null;
    setSnapshot((prev) => ({
      liveByStep: build(null),
      ttftMs: ttftRef.current,
      finalized: prev.finalized,
    }));
  }, [build]);

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
      const tok = parseToken(frame);
      if (tok === null) return;
      if (ttftRef.current === null && startRef.current !== null) {
        ttftRef.current = Date.now() - startRef.current;
      }
      let b = bufRef.current.get(tok.step);
      if (b === undefined) {
        b = { content: "", reasoning: "", toolNames: new Map() };
        bufRef.current.set(tok.step, b);
      }
      if (tok.kind === "tool") {
        b.toolNames.set(tok.toolIndex, tok.name);
      } else if (tok.channel === "content") {
        if (!contentStartRef.current.has(tok.step)) contentStartRef.current.set(tok.step, Date.now());
        b.content += tok.text;
      } else {
        if (!reasoningStartRef.current.has(tok.step)) reasoningStartRef.current.set(tok.step, Date.now());
        b.reasoning += tok.text;
      }
      schedule();
    },
    [schedule],
  );

  const reset = useCallback(() => {
    cancel();
    bufRef.current = new Map();
    reasoningStartRef.current = new Map();
    contentStartRef.current = new Map();
    startRef.current = Date.now();
    ttftRef.current = null;
    setSnapshot({ liveByStep: EMPTY, ttftMs: null, finalized: false });
  }, [cancel]);

  const finalize = useCallback(() => {
    cancel();
    setSnapshot({ liveByStep: build(Date.now()), ttftMs: ttftRef.current, finalized: true });
  }, [cancel, build]);

  useEffect(() => cancel, [cancel]);

  return { ...snapshot, push, reset, finalize };
}
```

- [ ] **Step 4: 跑测试 + typecheck**

Run: `cd apps/admin-ui && pnpm exec vitest run src/pages/agent_detail/playground/__tests__/useTokenStream.test.ts && pnpm typecheck`
Expected: vitest PASS 全部;`pnpm typecheck` exit 0(注:StreamingStepCard/StepTimeline/PlaygroundTab 此刻仍用旧 `text`/string 类型 → **typecheck 会在那三文件报错,属预期**,Task 4/5 修。**本步只验 useTokenStream.test 通过**;若要单验类型,跑 `pnpm exec tsc --noEmit src/pages/agent_detail/playground/useTokenStream.ts` 不可行——用 vitest 通过即准,整库 typecheck 留到 Task 5 收口)。

- [ ] **Step 5: Commit**

```bash
git add apps/admin-ui/src/pages/agent_detail/playground/useTokenStream.ts \
        apps/admin-ui/src/pages/agent_detail/playground/__tests__/useTokenStream.test.ts
git commit -m "feat(admin-ui): useTokenStream 多频道 LiveStep(reasoning/tool/reasoningMs,流式 3b)"
```

---

### Task 4: 前端 `StreamingStepCard` 三区(reasoning 折叠 + tool chips)+ i18n

**Files:**
- Modify: `apps/admin-ui/src/pages/agent_detail/playground/StreamingStepCard.tsx`(整文件重写)
- Modify: `apps/admin-ui/src/i18n/locales/en.ts:847`(接口)+ `:3413`(en 值)
- Modify: `apps/admin-ui/src/i18n/locales/zh-CN.ts:913`(zh 值)
- Test: `apps/admin-ui/src/pages/agent_detail/playground/__tests__/StreamingStepCard.test.tsx`(整文件替换)

**Interfaces:**
- Consumes: `LiveStep`(Task 3,`./useTokenStream`);`fmtDuration`(`./duration_format`);i18n 键 `playground.tl_step`/`streaming_badge`/`interrupted_badge`/`ttft`/`reasoning_label`/`reasoning_summary`。
- Produces: `StreamingStepCardProps{step:number; live:LiveStep; interrupted:boolean; ttftMs:number|null}`。testid:`streaming-step-card`/`streaming-badge`/`interrupted-badge`/`ttft-badge`/`reasoning-region`/`reasoning-summary`/`tool-chip`。Task 5 的 StepTimeline 用此 props。

- [ ] **Step 1: 加 i18n 键**

`en.ts` 接口(在 `ttft: string;` 行后,约 847)插:
```ts
    reasoning_label: string;
    reasoning_summary: string;
```
`en.ts` 值(在 `ttft: "TTFT {{d}}",` 行后,约 3413)插:
```ts
    reasoning_label: "Thinking…",
    reasoning_summary: "Thought for {{d}}",
```
`zh-CN.ts` 值(在 `ttft: "首字 {{d}}",` 行后,约 913)插:
```ts
    reasoning_label: "思考中…",
    reasoning_summary: "思考 {{d}}",
```

- [ ] **Step 2: 写失败测试**(整文件替换 `__tests__/StreamingStepCard.test.tsx`)

```tsx
import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import "../../../../i18n";

import { StreamingStepCard } from "../StreamingStepCard";
import type { LiveStep } from "../useTokenStream";

function mkLive(p: Partial<LiveStep> = {}): LiveStep {
  return { content: "", reasoning: "", toolNames: new Map(), reasoningMs: null, ...p };
}

describe("StreamingStepCard", () => {
  it("renders content as plain text (not markdown)", () => {
    render(
      <StreamingStepCard
        step={0}
        live={mkLive({ content: "# not a heading\n**still literal**" })}
        interrupted={false}
        ttftMs={null}
      />,
    );
    const card = screen.getByTestId("streaming-step-card");
    expect(card).toHaveTextContent("# not a heading");
    expect(card).toHaveTextContent("**still literal**");
    expect(card.querySelector("h1")).toBeNull();
    expect(card.querySelector("strong")).toBeNull();
  });

  it("shows the streaming badge while not interrupted, interrupted badge when interrupted", () => {
    const { rerender } = render(
      <StreamingStepCard step={1} live={mkLive({ content: "hi" })} interrupted={false} ttftMs={null} />,
    );
    expect(screen.getByTestId("streaming-badge")).toBeInTheDocument();
    expect(screen.queryByTestId("interrupted-badge")).toBeNull();
    rerender(<StreamingStepCard step={1} live={mkLive({ content: "hi" })} interrupted={true} ttftMs={null} />);
    expect(screen.getByTestId("interrupted-badge")).toBeInTheDocument();
    expect(screen.queryByTestId("streaming-badge")).toBeNull();
  });

  it("shows a TTFT badge when ttftMs is set, hides it when null", () => {
    const { rerender } = render(
      <StreamingStepCard step={0} live={mkLive({ content: "x" })} interrupted={false} ttftMs={1234} />,
    );
    expect(screen.getByTestId("ttft-badge")).toBeInTheDocument();
    rerender(<StreamingStepCard step={0} live={mkLive({ content: "x" })} interrupted={false} ttftMs={null} />);
    expect(screen.queryByTestId("ttft-badge")).toBeNull();
  });

  it("hides reasoning/tool regions when those channels are empty", () => {
    render(<StreamingStepCard step={0} live={mkLive({ content: "x" })} interrupted={false} ttftMs={null} />);
    expect(screen.queryByTestId("reasoning-region")).toBeNull();
    expect(screen.queryByTestId("tool-chip")).toBeNull();
  });

  it("auto-expands reasoning while streaming (reasoningMs null) and shows the thinking label", () => {
    render(
      <StreamingStepCard
        step={0}
        live={mkLive({ reasoning: "let me think" })}
        interrupted={false}
        ttftMs={null}
      />,
    );
    expect(screen.getByTestId("reasoning-region")).toBeInTheDocument();
    expect(screen.getByText("let me think")).toBeInTheDocument(); // expanded
    expect(screen.getByTestId("reasoning-summary")).toHaveTextContent("Thinking…");
  });

  it("auto-collapses reasoning once a duration is known, and re-expands on click", () => {
    render(
      <StreamingStepCard
        step={0}
        live={mkLive({ reasoning: "hidden thought", content: "answer", reasoningMs: 8000 })}
        interrupted={false}
        ttftMs={null}
      />,
    );
    // Collapsed: summary shows duration, body hidden.
    expect(screen.getByTestId("reasoning-summary")).toHaveTextContent("Thought for 8.0s");
    expect(screen.queryByText("hidden thought")).toBeNull();
    // Click re-expands.
    fireEvent.click(screen.getByTestId("reasoning-summary"));
    expect(screen.getByText("hidden thought")).toBeInTheDocument();
  });

  it("renders a tool chip per tool name, sorted by index", () => {
    render(
      <StreamingStepCard
        step={0}
        live={mkLive({ toolNames: new Map([[1, "read_file"], [0, "search_web"]]) })}
        interrupted={false}
        ttftMs={null}
      />,
    );
    const chips = screen.getAllByTestId("tool-chip");
    expect(chips).toHaveLength(2);
    expect(chips[0]).toHaveTextContent("search_web"); // index 0 first
    expect(chips[1]).toHaveTextContent("read_file");
  });

  it("labels the step by index", () => {
    render(<StreamingStepCard step={3} live={mkLive({ content: "x" })} interrupted={false} ttftMs={null} />);
    expect(screen.getByTestId("streaming-step-card")).toHaveAttribute("data-step", "3");
  });
});
```

- [ ] **Step 3: 跑测试验证失败**

Run: `cd apps/admin-ui && pnpm exec vitest run src/pages/agent_detail/playground/__tests__/StreamingStepCard.test.tsx`
Expected: FAIL(props 用 `live=` 但组件还是 `text=`;无 reasoning/tool 区)。

- [ ] **Step 4: 重写 `StreamingStepCard.tsx`**(整文件替换)

```tsx
/**
 * StreamingStepCard — a synthetic, live step card for the step currently being
 * streamed token-by-token (流式 epic 子项目 3a content + 3b reasoning/tool_args).
 * Rendered by StepTimeline for a step that has live tokens but no authoritative
 * `AgentStep` card yet. Text is plain (`pre-wrap`), never markdown — markdown
 * reflow on every token is janky; the authoritative card renders markdown (and
 * the tool call arguments) once the `updates` frame settles the step.
 */
import { useState, type KeyboardEvent } from "react";
import { Typography } from "antd";
import { useTranslation } from "react-i18next";

import { fmtDuration } from "./duration_format";
import type { LiveStep } from "./useTokenStream";

const { Text } = Typography;

const ACCENT = "var(--ew-accent-violet, #a855f7)";
const DANGER = "var(--ew-text-danger, #cf1322)";

export interface StreamingStepCardProps {
  step: number;
  live: LiveStep;
  interrupted: boolean;
  ttftMs: number | null;
}

export function StreamingStepCard({ step, live, interrupted, ttftMs }: StreamingStepCardProps) {
  const { t } = useTranslation();
  const accent = interrupted ? DANGER : ACCENT;
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
          <span data-testid="streaming-badge" style={{ color: ACCENT, fontSize: 12 }}>
            {t("playground.streaming_badge")}
          </span>
        )}
        {ttftMs !== null && (
          <span data-testid="ttft-badge" style={{ color: "var(--ew-text-secondary, #888)", fontSize: 12 }}>
            {t("playground.ttft", { d: fmtDuration(ttftMs) })}
          </span>
        )}
      </div>
      {live.reasoning !== "" && (
        <ReasoningRegion reasoning={live.reasoning} reasoningMs={live.reasoningMs} />
      )}
      {live.toolNames.size > 0 && <ToolChips toolNames={live.toolNames} />}
      {live.content !== "" && <Text style={{ whiteSpace: "pre-wrap", fontSize: 13 }}>{live.content}</Text>}
    </div>
  );
}

function ReasoningRegion({ reasoning, reasoningMs }: { reasoning: string; reasoningMs: number | null }) {
  const { t } = useTranslation();
  // Auto-expand while still reasoning (reasoningMs === null); auto-collapse to a
  // "Thought for Xs" summary once the duration is known (content started / step
  // settled). A manual click overrides the auto behaviour thereafter.
  const [override, setOverride] = useState<boolean | null>(null);
  const expanded = override ?? reasoningMs === null;
  const label =
    reasoningMs === null
      ? t("playground.reasoning_label")
      : t("playground.reasoning_summary", { d: fmtDuration(reasoningMs) });
  const toggle = (): void => setOverride(!expanded);
  const onKeyDown = (e: KeyboardEvent): void => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggle();
    }
  };
  return (
    <div
      data-testid="reasoning-region"
      style={{
        marginBottom: 6,
        borderLeft: `2px solid color-mix(in srgb, ${ACCENT} 55%, var(--ew-border-subtle))`,
        paddingLeft: 10,
      }}
    >
      <div
        data-testid="reasoning-summary"
        role="button"
        tabIndex={0}
        onClick={toggle}
        onKeyDown={onKeyDown}
        style={{ cursor: "pointer", fontSize: 11, color: ACCENT, display: "flex", gap: 6, alignItems: "center" }}
      >
        <span>💭 {label}</span>
        <span style={{ color: "var(--ew-text-tertiary)" }}>{expanded ? "▾" : "▸"}</span>
      </div>
      {expanded && (
        <p
          style={{
            margin: "3px 0 0",
            fontSize: 12,
            color: "var(--ew-text-secondary)",
            fontStyle: "italic",
            whiteSpace: "pre-wrap",
          }}
        >
          {reasoning}
        </p>
      )}
    </div>
  );
}

function ToolChips({ toolNames }: { toolNames: ReadonlyMap<number, string> }) {
  const chips = [...toolNames].sort(([a], [b]) => a - b);
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 6 }}>
      {chips.map(([index, name]) => (
        <span
          key={index}
          data-testid="tool-chip"
          style={{
            fontFamily: "var(--ew-font-mono)",
            fontSize: 11,
            padding: "1px 7px",
            borderRadius: 4,
            background: `color-mix(in srgb, ${ACCENT} 14%, transparent)`,
            color: ACCENT,
          }}
        >
          🔧 {name}
        </span>
      ))}
    </div>
  );
}
```

- [ ] **Step 5: 跑测试验证通过**

Run: `cd apps/admin-ui && pnpm exec vitest run src/pages/agent_detail/playground/__tests__/StreamingStepCard.test.tsx`
Expected: PASS 全部。(`fmtDuration(8000)` = `"8.0s"` — 与既有 StepTimeline 测 `durationMs:1200→"1.2s"` 同格式,已确证。)

- [ ] **Step 6: Commit**

```bash
git add apps/admin-ui/src/pages/agent_detail/playground/StreamingStepCard.tsx \
        apps/admin-ui/src/pages/agent_detail/playground/__tests__/StreamingStepCard.test.tsx \
        apps/admin-ui/src/i18n/locales/en.ts apps/admin-ui/src/i18n/locales/zh-CN.ts
git commit -m "feat(admin-ui): StreamingStepCard 三区 reasoning 折叠 + tool chips(流式 3b)"
```

---

### Task 5: 前端 StepTimeline + PlaygroundTab 接线(类型加宽)

**Files:**
- Modify: `apps/admin-ui/src/pages/agent_detail/playground/StepTimeline.tsx:14-17,28-36,58-101`
- Modify: `apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx`(import + `TurnCardProps.liveByStep` 类型,约 1895)
- Test: `apps/admin-ui/src/pages/agent_detail/playground/__tests__/StepTimeline.test.tsx`(替换 3a live 段)

**Interfaces:**
- Consumes: `LiveStep`(Task 3);`StreamingStepCard{step,live,interrupted,ttftMs}`(Task 4)。
- Produces: `StepTimelineProps.liveByStep?:ReadonlyMap<number,LiveStep>`;`TurnCardProps.liveByStep?:ReadonlyMap<number,LiveStep>`。reconcile 逻辑(按 `stepCount` 抑制)不变。

- [ ] **Step 1: 更新 StepTimeline live 测**(替换 `StepTimeline.test.tsx` 第 134 行起的 `describe("StepTimeline live streaming (3a)", ...)` 整块)

```tsx
describe("StepTimeline live streaming (3a content + 3b reasoning/tool)", () => {
  function mkLive(p: Partial<import("../useTokenStream").LiveStep> = {}) {
    return { content: "", reasoning: "", toolNames: new Map<number, string>(), reasoningMs: null, ...p };
  }

  it("renders a synthetic streaming card for a live content step with no authoritative card", () => {
    render(
      <StepTimeline
        items={[]}
        liveByStep={new Map([[2, mkLive({ content: "typing…" })]])}
        ttftMs={300}
        finalized={false}
      />,
    );
    const card = screen.getByTestId("streaming-step-card");
    expect(card).toHaveAttribute("data-step", "2");
    expect(card).toHaveTextContent("typing…");
    expect(screen.getByTestId("streaming-badge")).toBeInTheDocument();
  });

  it("renders a synthetic card for a reasoning-only live step (no content)", () => {
    render(
      <StepTimeline
        items={[]}
        liveByStep={new Map([[0, mkLive({ reasoning: "thinking hard" })]])}
        ttftMs={null}
        finalized={false}
      />,
    );
    expect(screen.getByTestId("reasoning-region")).toBeInTheDocument();
    expect(screen.getByText("thinking hard")).toBeInTheDocument();
  });

  it("renders a synthetic card for a tool-only live step (tool name chip)", () => {
    render(
      <StepTimeline
        items={[]}
        liveByStep={new Map([[0, mkLive({ toolNames: new Map([[0, "search_web"]]) })]])}
        ttftMs={null}
        finalized={false}
      />,
    );
    expect(screen.getByTestId("tool-chip")).toHaveTextContent("search_web");
  });

  it("suppresses the streaming card once the authoritative step card exists (reconcile)", () => {
    // agentStep has stepCount: 1 → a live buffer for step 1 must NOT render a synthetic card.
    render(
      <StepTimeline
        items={[agentStep]}
        liveByStep={new Map([[1, mkLive({ content: "stale live text" })]])}
        ttftMs={null}
        finalized={false}
      />,
    );
    expect(screen.queryByTestId("streaming-step-card")).toBeNull();
    expect(screen.queryByText("stale live text")).toBeNull();
  });

  it("marks an orphan live step interrupted when finalized", () => {
    render(
      <StepTimeline
        items={[]}
        liveByStep={new Map([[0, mkLive({ content: "half" })]])}
        ttftMs={null}
        finalized={true}
      />,
    );
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

- [ ] **Step 2: 跑测试验证失败**

Run: `cd apps/admin-ui && pnpm exec vitest run src/pages/agent_detail/playground/__tests__/StepTimeline.test.tsx`
Expected: FAIL(`liveByStep` 现要 `LiveStep` 值,组件仍按 string 传 `text`)。

- [ ] **Step 3: 改 `StepTimeline.tsx`**

3a) import 段(第 14-17 行区)在 `import { StreamingStepCard } from "./StreamingStepCard";` 后加:
```ts
import type { LiveStep } from "./useTokenStream";
```

3b) props 接口(第 30-31 行)把 `liveByStep` 类型改:
```ts
  /** Live token buffers by step (子项目 3a content + 3b reasoning/tool); absent for history/non-streaming turns. */
  liveByStep?: ReadonlyMap<number, LiveStep>;
```

3c) `liveCards` 计算(第 58-60 行)把空 map 默认类型改:
```ts
  const liveCards = [...(liveByStep ?? new Map<number, LiveStep>())]
    .filter(([step]) => !settled.has(step))
    .sort(([a], [b]) => a - b);
```

3d) 渲染(第 93-101 行)把 `text` 改 `live`:
```tsx
        {liveCards.map(([step, live]) => (
          <StreamingStepCard
            key={`live-${step}`}
            step={step}
            live={live}
            interrupted={finalized}
            ttftMs={ttftMs}
          />
        ))}
```

- [ ] **Step 4: 改 `PlaygroundTab.tsx`**(仅类型加宽,无逻辑改)

4a) 在 `import { StepTimeline } from "./playground/StepTimeline";`(第 99 行)后加:
```ts
import type { LiveStep } from "./playground/useTokenStream";
```

4b) `TurnCardProps` 里 `liveByStep`(第 1895 行)改类型:
```ts
  liveByStep?: ReadonlyMap<number, LiveStep>;
```

其余(第 245 `useTokenStream()`、616-659/714-746 分流/reset/finalize、1539-1544 传 props、2430-2434 传 StepTimeline)**全不动**——`push`/`reset`/`finalize`/`liveByStep` 直传,值类型自动流经。

- [ ] **Step 5: 跑测试 + 整库 typecheck(收口)**

Run: `cd apps/admin-ui && pnpm exec vitest run src/pages/agent_detail/playground/__tests__/StepTimeline.test.tsx && pnpm typecheck`
Expected: vitest PASS;`pnpm typecheck` exit 0(整库,三文件类型现已一致)。**编辑器 stale 诊断不算数,以 `pnpm typecheck` 真结果为准。**

- [ ] **Step 6: 跑整 playground 测防回归**

Run: `cd apps/admin-ui && pnpm exec vitest run src/pages/agent_detail`
Expected: PASS 全部(useTokenStream/StreamingStepCard/StepTimeline + PlaygroundTab 相关无回归)。

- [ ] **Step 7: Commit**

```bash
git add apps/admin-ui/src/pages/agent_detail/playground/StepTimeline.tsx \
        apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx \
        apps/admin-ui/src/pages/agent_detail/playground/__tests__/StepTimeline.test.tsx
git commit -m "feat(admin-ui): StepTimeline/PlaygroundTab 接 LiveStep 多频道(流式 3b 收口)"
```

---

## 最终验证(全任务后)

- `cd services/orchestrator && uv run python -m pytest tests/test_streaming_redact.py tests/test_llm_router_streaming.py -v` — 后端全绿。
- `cd apps/admin-ui && pnpm typecheck && pnpm exec vitest run src/pages/agent_detail` — 前端类型 + 测试全绿。
- 手动冒烟:真推理模型(glm-z1/deepseek-r1 出 `reasoning_content`)跑 agent:
  1. 思考期 reasoning 逐字进合成卡、自动展开;
  2. 答案开始 → reasoning 折叠为"思考 Xs",点可重展;
  3. 工具步显 `🔧 工具名` chip;`updates` 落地 → 合成卡换权威卡(权威卡显完整 args);
  4. 中途 Stop → 保留 partial 三区 + 中断徽标;
  5. 历史轮不受影响(无 token 帧,走 `updates` 回放)。

## 已知窄缝(非本计划修,PR 注明)

cancel 恰落在 `_drive_stream` 的 `await sink(delta)` 那一瞬(生成器已 yield、挂在两 yield 之间)时,上游关闭退化为 async-gen GC 终结(非同步)。窗口极小(sink await),且 CPython 引用计数下终结及时。属"主动 cancel 修"(用户选的非目标),不在本计划;Task 2 的核查针对主导路径(等下一 token 时取消)。

## 自审记录

- **Spec 覆盖**:c reasoning=Task 1(后端)+3/4(前端);d tool_args 名字-only=Task 1+3/4;h-后端 cancel=Task 2;契约=Task 1;安全(独立 redactor/名字不脱敏/args 不流)=Task 1 测。全覆盖。
- **类型一致**:`LiveStep{content,reasoning,toolNames,reasoningMs}` 在 Task 3 定义并导出,Task 4(`live:LiveStep`)、Task 5(`liveByStep:ReadonlyMap<number,LiveStep>`)一致引用;`StreamingStepCardProps` Task 4 定义、Task 5 StepTimeline 按 `{step,live,interrupted,ttftMs}` 调用,一致。
- **无占位**:每步含完整代码/命令/预期。
