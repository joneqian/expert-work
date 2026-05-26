/**
 * useStatusPolling tests — Stream H.3 PR 5.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";

import { useStatusPolling } from "../useStatusPolling";

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.runOnlyPendingTimers();
  vi.useRealTimers();
});

describe("useStatusPolling", () => {
  it("calls onTick on the configured interval when status is active", () => {
    const onTick = vi.fn();
    renderHook(() =>
      useStatusPolling({ status: "running", onTick, intervalMs: 1000 }),
    );
    expect(onTick).not.toHaveBeenCalled();
    vi.advanceTimersByTime(3000);
    expect(onTick).toHaveBeenCalledTimes(3);
  });

  it("does NOT start the timer for terminal statuses", () => {
    const onTick = vi.fn();
    renderHook(() =>
      useStatusPolling({ status: "success", onTick, intervalMs: 1000 }),
    );
    vi.advanceTimersByTime(5000);
    expect(onTick).not.toHaveBeenCalled();
  });

  it("status=null is a no-op", () => {
    const onTick = vi.fn();
    renderHook(() => useStatusPolling({ status: null, onTick, intervalMs: 1000 }));
    vi.advanceTimersByTime(5000);
    expect(onTick).not.toHaveBeenCalled();
  });

  it("paused runs keep polling — the reviewer may approve mid-stream", () => {
    const onTick = vi.fn();
    renderHook(() =>
      useStatusPolling({ status: "paused", onTick, intervalMs: 500 }),
    );
    vi.advanceTimersByTime(1500);
    expect(onTick).toHaveBeenCalledTimes(3);
  });

  it("hidden tab skips ticks; visible tab resumes", () => {
    const onTick = vi.fn();
    renderHook(() =>
      useStatusPolling({ status: "running", onTick, intervalMs: 1000 }),
    );
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "hidden",
    });
    vi.advanceTimersByTime(3000);
    expect(onTick).not.toHaveBeenCalled();

    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "visible",
    });
    vi.advanceTimersByTime(2000);
    expect(onTick).toHaveBeenCalledTimes(2);
  });

  it("cleans up the timer when the status switches to terminal", () => {
    const onTick = vi.fn();
    const { rerender } = renderHook(
      (props: { status: "running" | "success" }) =>
        useStatusPolling({ status: props.status, onTick, intervalMs: 1000 }),
      { initialProps: { status: "running" } as { status: "running" | "success" } },
    );
    vi.advanceTimersByTime(2000);
    expect(onTick).toHaveBeenCalledTimes(2);

    rerender({ status: "success" });
    vi.advanceTimersByTime(5000);
    // No additional ticks after the rerender.
    expect(onTick).toHaveBeenCalledTimes(2);
  });
});
