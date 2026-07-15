/**
 * A' purpose labelling — Batch 4b spec §3.2.
 *
 * The SSE stream only surfaces the agent's own AI-message turns — hidden
 * sub-calls (memory extraction, planner, reflect, …) never appear as
 * `kind: "agent"` frames in the parsed timeline. Those sub-calls are now
 * tagged by the facade with a non-empty `purpose`, so they're excluded up
 * front: only the main-conversation llm spans (purpose "" or "main") are
 * counted and labelled. "Primary reasoning" is stamped when that main-span
 * count lines up 1:1 with the agent step count parsed from the same turn's
 * SSE timeline; otherwise nothing is labelled (no heuristics, no guessing).
 * The detail panel's raw prompt/response still lets the user self-verify
 * each span's purpose.
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

  // Only main-conversation llm spans are candidates for "primary reasoning".
  // Auxiliary sub-calls carry a non-empty, non-"main" `purpose` (their own
  // labelled kind — memory/planner/reflect/…) and must never *also* read as
  // primary. Excluding them here also stops a cache-hit turn (an agent frame
  // with no llm span) from re-balancing the count and mislabelling an aux span.
  const primaryLlmIds = trace.spans
    .filter((span) => span.kind === "llm" && (span.purpose === "" || span.purpose === "main"))
    .map((span) => span.id);
  if (primaryLlmIds.length !== agentStepCount) return trace;

  const primaryIds = new Set(primaryLlmIds);
  return {
    ...trace,
    spans: trace.spans.map((span) =>
      primaryIds.has(span.id) ? { ...span, detail: primaryReasoningLabel } : span,
    ),
  };
}
