/**
 * Pure status derivation for the timeline view's RunStatusBanner — Task 11.
 * Mirrors trace_banner.ts (Task 10)'s shape but derives from the SSE-parsed
 * `TimelineItem[]` (this turn's own error signal — an agent step whose tool
 * call failed, or an `error` marker frame) rather than Langfuse span levels.
 * Mirrors the existing fail predicate at PlaygroundTab.tsx's
 * `timelineFailCount` (`(it.kind === "agent" && it.hasError) || it.kind ===
 * "error"`).
 */
import type { TimelineItem } from "../../../api/timeline";

export interface TimelineBannerModel {
  status: "ok" | "error";
  /** The failing agent step's `stepCount` (null when the error came from a
   *  standalone `error` marker instead of a tool-call failure). */
  errorStepCount: number | null;
  /** The `error` marker's text (null when the error came from an agent
   *  step's failed tool call instead). */
  errorText: string | null;
}

/** Returns null when there's nothing to show (no timeline items yet). */
export function timelineBannerModel(items: readonly TimelineItem[]): TimelineBannerModel | null {
  if (items.length === 0) return null;
  const errStep = items.find((it) => it.kind === "agent" && it.hasError);
  const errMarker = items.find((it) => it.kind === "error");
  if (!errStep && !errMarker) return { status: "ok", errorStepCount: null, errorText: null };
  return {
    status: "error",
    errorStepCount: errStep && errStep.kind === "agent" ? errStep.stepCount : null,
    errorText: errMarker && errMarker.kind === "error" ? errMarker.text : null,
  };
}
