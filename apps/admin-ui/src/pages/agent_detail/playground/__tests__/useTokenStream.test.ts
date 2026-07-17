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
