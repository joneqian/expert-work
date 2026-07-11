/**
 * A' purpose labelling — Batch 4b spec §3.2.
 *
 * The SSE stream only surfaces the agent's own AI-message turns — hidden
 * sub-calls (e.g. memory extraction) never appear as `kind: "agent"` frames
 * in the parsed timeline. That means an llm span in the Langfuse trace can
 * only be *safely* labelled "primary reasoning" when the trace's llm-span
 * count lines up 1:1 with the agent step count parsed from the same turn's
 * SSE timeline. When the counts don't match, some llm span is a hidden
 * sub-call and there's no reliable way to tell which — so nothing is
 * labelled (no heuristics, no guessing). The detail panel's raw
 * prompt/response still lets the user self-verify each span's purpose.
 */
import type { RunTrace } from "../../../api/trace_facade";

/** Returns a new `RunTrace` with every `kind: "llm"` span's `detail` set to
 *  `primaryReasoningLabel`, but only when the trace has exactly
 *  `agentStepCount` llm spans. Otherwise returns `trace` unchanged (same
 *  reference — nothing to relabel). Never mutates the input. */
export function labelPurpose(
  trace: RunTrace,
  agentStepCount: number,
  primaryReasoningLabel: string,
): RunTrace {
  if (trace.status !== "ok" || !trace.spans) return trace;

  const llmSpanIds = trace.spans
    .filter((span) => span.kind === "llm")
    .map((span) => span.id);
  if (llmSpanIds.length !== agentStepCount) return trace;

  const primaryIds = new Set(llmSpanIds);
  return {
    ...trace,
    spans: trace.spans.map((span) =>
      primaryIds.has(span.id) ? { ...span, detail: primaryReasoningLabel } : span,
    ),
  };
}
