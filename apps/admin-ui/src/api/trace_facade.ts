/**
 * Trace facade SDK — Batch 4b.
 *
 * Consumes ``GET /v1/sessions/{thread_id}/runs/{run_id}/trace`` (raw payload,
 * no envelope — matching the pattern in :func:`getRun`).
 */
import { apiClient } from "./client";

export type TraceStatus = "ok" | "not_ready" | "unavailable" | "no_trace";

export interface TraceSpan {
  id: string;
  parentId: string | null;
  kind: "session" | "llm" | "tool" | "span";
  label: string;
  detail: string | null;
  startMs: number;
  latencyMs: number;
  model: string | null;
  inputTokens: number | null;
  outputTokens: number | null;
  costUsd: number | null;
  input: string | null;
  output: string | null;
}

export interface RunTrace {
  status: TraceStatus;
  trace?: {
    name: string;
    latencyMs: number;
    totalCostUsd: number | null;
    spanCount: number;
  };
  spans?: TraceSpan[];
}

/** Fetch trace data for a run.
 *
 * The endpoint returns a raw payload (no envelope) with status indicating
 * availability. When status=ok, trace and spans are populated; otherwise
 * they are undefined.
 */
export async function getRunTrace(
  threadId: string,
  runId: string,
): Promise<RunTrace> {
  const response = await apiClient.get<RunTrace>(
    `/v1/sessions/${threadId}/runs/${runId}/trace`,
  );
  return response.data;
}
