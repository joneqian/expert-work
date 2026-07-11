/**
 * trace_purpose tests — Batch 4b Task 5, A' purpose labelling.
 *
 * See labelPurpose's doc comment for the 1:1-or-nothing rule this covers.
 */
import { describe, expect, it } from "vitest";

import { labelPurpose } from "../trace_purpose";
import type { RunTrace, TraceSpan } from "../../../../api/trace_facade";

function makeSpan(
  over: Partial<TraceSpan> & Pick<TraceSpan, "id" | "parentId" | "kind" | "label">,
): TraceSpan {
  return {
    detail: null,
    startMs: 0,
    latencyMs: 0,
    model: null,
    inputTokens: null,
    outputTokens: null,
    costUsd: null,
    input: null,
    output: null,
    ...over,
  };
}

const PRIMARY = "主推理";

describe("labelPurpose", () => {
  it("labels every llm span '主推理' when the llm span count matches agentStepCount 1:1", () => {
    const root = makeSpan({ id: "r0", parentId: null, kind: "session", label: "会话运行" });
    const llm1 = makeSpan({ id: "r1", parentId: "r0", kind: "llm", label: "LLM 调用", startMs: 100 });
    const tool = makeSpan({ id: "r2", parentId: "r0", kind: "tool", label: "工具调用", detail: "get_weather" });
    const llm2 = makeSpan({ id: "r3", parentId: "r0", kind: "llm", label: "LLM 调用", startMs: 300 });
    const trace: RunTrace = {
      status: "ok",
      trace: { name: "t", latencyMs: 500, totalCostUsd: null, spanCount: 4 },
      spans: [root, llm1, tool, llm2],
    };

    const result = labelPurpose(trace, 2, PRIMARY);

    expect(result).not.toBe(trace);
    expect(result.spans?.find((s) => s.id === "r1")?.detail).toBe(PRIMARY);
    expect(result.spans?.find((s) => s.id === "r3")?.detail).toBe(PRIMARY);
    // Non-llm spans are untouched.
    expect(result.spans?.find((s) => s.id === "r0")?.detail).toBeNull();
    expect(result.spans?.find((s) => s.id === "r2")?.detail).toBe("get_weather");
    // The original trace/spans are never mutated.
    expect(trace.spans?.find((s) => s.id === "r1")?.detail).toBeNull();
  });

  it("leaves spans untouched when the llm span count does not match agentStepCount (hidden sub-call, e.g. memory extraction)", () => {
    const root = makeSpan({ id: "r0", parentId: null, kind: "session", label: "会话运行" });
    const llm1 = makeSpan({ id: "r1", parentId: "r0", kind: "llm", label: "LLM 调用" });
    const llm2 = makeSpan({ id: "r2", parentId: "r0", kind: "llm", label: "LLM 调用" });
    const trace: RunTrace = {
      status: "ok",
      trace: { name: "t", latencyMs: 500, totalCostUsd: null, spanCount: 3 },
      spans: [root, llm1, llm2],
    };

    const result = labelPurpose(trace, 1, PRIMARY);

    expect(result).toBe(trace);
    expect(result.spans?.every((s) => s.detail === null)).toBe(true);
  });

  it("returns the trace unchanged when status is not 'ok'", () => {
    const trace: RunTrace = { status: "not_ready" };

    expect(labelPurpose(trace, 1, PRIMARY)).toBe(trace);
  });

  it("returns the trace unchanged when spans is missing (malformed ok payload)", () => {
    const trace: RunTrace = {
      status: "ok",
      trace: { name: "t", latencyMs: 0, totalCostUsd: null, spanCount: 0 },
    };

    expect(labelPurpose(trace, 0, PRIMARY)).toBe(trace);
  });
});
