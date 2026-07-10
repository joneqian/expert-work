import { describe, expect, it } from "vitest";
import { filterTimeline } from "../timeline_filter";
import type { TimelineItem } from "../timeline";

const agent = (over: Partial<Extract<TimelineItem, {kind:"agent"}>> = {}): TimelineItem => ({
  kind: "agent", seq: 0, receivedAt: "", stepCount: 1, node: "agent",
  model: "glm-5.2", finishReason: "stop", reasoning: null, content: "hi",
  inputTokens: 0, outputTokens: 0, totalTokens: 0, tools: [], hasError: false,
  durationMs: null, ...over,
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
