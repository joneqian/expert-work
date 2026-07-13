/**
 * Pure status derivation for the exact (trace) view's RunStatusBanner —
 * Task 10. Kept separate from PlaygroundTab (which is already huge) so it's
 * cheaply unit-testable.
 */
import type { RunTrace } from "../../../api/trace_facade";

export interface TraceBannerModel {
  status: "ok" | "error";
  errorSpanId: string | null;
  /** The failing span's label (+ detail, if present). */
  errorLabel: string | null;
  /** The failing span's statusMessage. */
  errorMessage: string | null;
  latencyMs: number | null;
  totalCostUsd: number | null;
}

/** Returns null when the trace isn't a fully-loaded ok trace (banner hidden). */
export function traceBannerModel(trace: RunTrace): TraceBannerModel | null {
  if (trace.status !== "ok" || !trace.spans || trace.spans.length === 0) return null;
  const errorSpan = trace.spans.find((s) => s.level === "error") ?? null;
  return {
    status: errorSpan ? "error" : "ok",
    errorSpanId: errorSpan?.id ?? null,
    errorLabel: errorSpan ? errorSpan.label + (errorSpan.detail ? ` · ${errorSpan.detail}` : "") : null,
    errorMessage: errorSpan?.statusMessage ?? null,
    latencyMs: trace.trace?.latencyMs ?? null,
    totalCostUsd: trace.trace?.totalCostUsd ?? null,
  };
}
