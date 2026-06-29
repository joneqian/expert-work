import { describe, expect, it } from "vitest";

import type { SseEvent } from "../sessions";
import { summarizeTurn } from "../turn_summary";

function updates(messages: unknown[]): SseEvent {
  return {
    id: "u",
    event: "updates",
    data: { agent: { messages } },
    rawData: "",
    receivedAt: "2026-06-29T00:00:00Z",
  };
}

describe("summarizeTurn", () => {
  it("sums usage across AI messages and splits cache/reasoning details", () => {
    const events = [
      updates([
        {
          type: "ai",
          content: "",
          usage_metadata: {
            input_tokens: 100,
            output_tokens: 20,
            total_tokens: 120,
            input_token_details: { cache_read: 64 },
            output_token_details: { reasoning: 8 },
          },
        },
      ]),
      updates([
        {
          type: "ai",
          content: "final",
          usage_metadata: { input_tokens: 50, output_tokens: 10, total_tokens: 60 },
        },
      ]),
    ];
    const summary = summarizeTurn(events);
    expect(summary.finalText).toBe("final");
    expect(summary.usage).toEqual({
      inputTokens: 150,
      outputTokens: 30,
      totalTokens: 180,
      cacheReadTokens: 64,
      reasoningTokens: 8,
    });
  });

  it("collects reasoning_content blocks in order", () => {
    const events = [
      updates([
        { type: "ai", content: "answer", additional_kwargs: { reasoning_content: "step 1" } },
      ]),
    ];
    const summary = summarizeTurn(events);
    expect(summary.reasoning).toEqual(["step 1"]);
    expect(summary.finalText).toBe("answer");
  });

  it("returns null usage when no AI message reports usage", () => {
    const events = [updates([{ type: "ai", content: "hi" }])];
    const summary = summarizeTurn(events);
    expect(summary.usage).toBeNull();
    expect(summary.finalText).toBe("hi");
  });

  it("takes the highest node step_count and the frame-span latency", () => {
    const events: SseEvent[] = [
      {
        id: "a",
        event: "updates",
        data: { agent: { messages: [{ type: "ai", content: "" }], step_count: 1 } },
        rawData: "",
        receivedAt: "2026-06-29T00:00:00.000Z",
      },
      {
        id: "b",
        event: "updates",
        data: { agent: { messages: [{ type: "ai", content: "done" }], step_count: 3 } },
        rawData: "",
        receivedAt: "2026-06-29T00:00:02.500Z",
      },
    ];
    const summary = summarizeTurn(events);
    expect(summary.stepCount).toBe(3);
    expect(summary.latencyMs).toBe(2500);
  });

  it("ignores non-updates frames and tool messages", () => {
    const events: SseEvent[] = [
      { id: "m", event: "metadata", data: { run_id: "r" }, rawData: "", receivedAt: "t" },
      updates([{ type: "tool", content: "tool out", tool_call_id: "c1", name: "x" }]),
    ];
    const summary = summarizeTurn(events);
    expect(summary.finalText).toBeNull();
    expect(summary.usage).toBeNull();
  });
});
