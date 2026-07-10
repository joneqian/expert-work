import { describe, expect, it } from "vitest";

import type { SseEvent } from "../sessions";
import { parseAgentState } from "../agent_state";

function updates(node: string, channels: Record<string, unknown>): SseEvent {
  return { id: null, event: "updates", data: { [node]: channels }, rawData: "", receivedAt: "" };
}

describe("parseAgentState", () => {
  it("takes the last non-empty recalled_memories", () => {
    const events = [
      updates("memory_recall", {
        recalled_memories: [
          { id: "m1", kind: "fact", content: "user likes tea", importance: 0.6, confidence: 0.9 },
        ],
      }),
    ];
    const { recalledMemories } = parseAgentState(events);
    expect(recalledMemories).toEqual([
      { id: "m1", kind: "fact", content: "user likes tea", importance: 0.6, confidence: 0.9 },
    ]);
  });

  it("accumulates non-empty tool_failures across steps (reset in between)", () => {
    const events = [
      updates("tools", {
        tool_failures: [
          { tool_name: "exec_python", error_class: "transient", summary: "boom", retryable: true, advice: "retry" },
        ],
      }),
      updates("agent", { tool_failures: [] }), // reset — must not wipe the log
      updates("tools", {
        tool_failures: [
          { tool_name: "web_search", error_class: "invalid_arguments", summary: "bad q", retryable: false, advice: "fix args" },
        ],
      }),
    ];
    const { toolFailures } = parseAgentState(events);
    expect(toolFailures.map((f) => f.toolName)).toEqual(["exec_python", "web_search"]);
    expect(toolFailures[0].errorClass).toBe("transient");
  });

  it("accumulates reflections and dedupes subagent_invocations by taskId (last wins)", () => {
    const events = [
      updates("reflect", { reflections: [{ verdict: "revise", critique: "missed a case" }] }),
      updates("tools", {
        subagent_invocations: [
          { task_id: "t1", name: "researcher", agent_ref: "researcher@1", status: "running",
            result_excerpt: "", error: null, iteration_used: 0, llm_call_count: 0, wall_clock_ms: 0 },
        ],
      }),
      updates("tools", {
        subagent_invocations: [
          { task_id: "t1", name: "researcher", agent_ref: "researcher@1", status: "completed",
            result_excerpt: "done", error: null, iteration_used: 3, llm_call_count: 5, wall_clock_ms: 1200 },
        ],
      }),
    ];
    const { reflections, subagentInvocations } = parseAgentState(events);
    expect(reflections).toEqual([{ verdict: "revise", critique: "missed a case" }]);
    expect(subagentInvocations).toHaveLength(1);
    expect(subagentInvocations[0].status).toBe("completed");
    expect(subagentInvocations[0].wallClockMs).toBe(1200);
  });

  it("takes the latest scalar signals", () => {
    const events = [
      updates("agent", { no_progress_streak: 1, escalate_next: false, step_count_refund_pending: 0 }),
      updates("agent", { no_progress_streak: 2, escalate_next: true, step_count_refund_pending: 1 }),
    ];
    const { signals } = parseAgentState(events);
    expect(signals).toEqual({ noProgressStreak: 2, escalateNext: true, stepCountRefundPending: 1 });
  });

  it("returns empty view for no updates frames", () => {
    const view = parseAgentState([]);
    expect(view.recalledMemories).toEqual([]);
    expect(view.signals).toEqual({ noProgressStreak: null, escalateNext: null, stepCountRefundPending: null });
  });
});
