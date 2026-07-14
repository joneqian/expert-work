/**
 * timeline_banner tests — Task 11. Pure status derivation feeding the
 * timeline view's RunStatusBanner (SSE-derived, not Langfuse level).
 */
import { describe, expect, it } from "vitest";

import { timelineBannerModel } from "../timeline_banner";
import type { AgentStep, MarkerItem, TimelineItem } from "../../../../api/timeline";

function agentStep(over: Partial<AgentStep> = {}): AgentStep {
  return {
    kind: "agent",
    seq: 0,
    receivedAt: "",
    stepCount: 1,
    node: "agent",
    model: null,
    finishReason: null,
    reasoning: null,
    content: null,
    inputTokens: 0,
    outputTokens: 0,
    totalTokens: 0,
    tools: [],
    hasError: false,
    durationMs: null,
    ...over,
  };
}

function errorMarker(over: Partial<MarkerItem> = {}): MarkerItem {
  return { kind: "error", seq: 0, receivedAt: "", text: "运行错误", tone: "bad", ...over };
}

describe("timelineBannerModel", () => {
  it("empty items → null", () => {
    expect(timelineBannerModel([])).toBeNull();
  });

  it("all-ok items → status ok, no error fields", () => {
    const items: TimelineItem[] = [agentStep({ seq: 0 }), agentStep({ seq: 1, stepCount: 2 })];
    expect(timelineBannerModel(items)).toEqual({ status: "ok", errorStepCount: null, errorText: null });
  });

  it("agent step with hasError → status error + errorStepCount, no errorText", () => {
    const items: TimelineItem[] = [
      agentStep({ seq: 0 }),
      agentStep({ seq: 1, stepCount: 3, hasError: true }),
    ];
    expect(timelineBannerModel(items)).toEqual({ status: "error", errorStepCount: 3, errorText: null });
  });

  it("error marker → status error + errorText, no errorStepCount", () => {
    const items: TimelineItem[] = [
      agentStep({ seq: 0 }),
      errorMarker({ seq: 1, text: "SandboxTimeout: 执行超过 30s" }),
    ];
    expect(timelineBannerModel(items)).toEqual({
      status: "error",
      errorStepCount: null,
      errorText: "SandboxTimeout: 执行超过 30s",
    });
  });

  it("mixed (both a failed agent step and an error marker) → status error, both fields set", () => {
    const items: TimelineItem[] = [
      agentStep({ seq: 0, stepCount: 2, hasError: true }),
      errorMarker({ seq: 1, text: "运行错误" }),
    ];
    expect(timelineBannerModel(items)).toEqual({
      status: "error",
      errorStepCount: 2,
      errorText: "运行错误",
    });
  });
});
