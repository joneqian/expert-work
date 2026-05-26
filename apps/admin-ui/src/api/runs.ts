/**
 * Runs SDK — backed by ``/v1/sessions/{thread_id}/runs/*`` (per-thread)
 * and ``/v1/runs`` (Stream H.3 PR 1 — cross-thread index per Mini-ADR
 * H-6, resolves the Mini-ADR J-41 deferred ``GET .../runs`` item).
 *
 * ``GET /v1/sessions/{thread_id}/runs/{run_id}`` is one of the few
 * endpoints that returns the raw payload (no envelope) — see :func:`getRun`.
 * ``GET /v1/runs`` follows the standard envelope (matches agents /
 * triggers list endpoints).
 */
import { apiClient, getJson, withTenantScope, type TenantScope } from "./client";

export type RunStatus =
  // Server-side ``RunStatus`` enum (helix_agent.runtime.runs.RunStatus).
  // ``pending`` / ``running`` / ``success`` / ``error`` / ``timeout`` /
  // ``interrupted`` / ``paused`` are the canonical values.
  | "pending"
  | "running"
  | "success"
  | "error"
  | "timeout"
  | "interrupted"
  | "paused"
  // Pre-H.3 PR 1 names kept here for the per-thread endpoint that may
  // still be in flight in older clients.
  | "queued"
  | "awaiting_approval"
  | "completed"
  | "failed"
  | "cancelled"
  | "unknown";

export interface PendingApproval {
  request_id: string;
  node: string;
  reason_kind: string;
  action_summary: string;
  proposed_args: Record<string, unknown>;
  requested_at: string;
  timeout_at: string;
}

export interface RunDetail {
  run_id: string;
  thread_id: string;
  status: RunStatus;
  pending_approval: PendingApproval | null;
}

/** Raw (no envelope) fetch — runs.py historically returns the run
 *  status directly. Keeping this endpoint un-enveloped to match
 *  Mini-ADR J-41; ADR refresh PR would normalise. */
export async function getRun(
  threadId: string,
  runId: string,
): Promise<RunDetail> {
  const response = await apiClient.get<RunDetail>(
    `/v1/sessions/${threadId}/runs/${runId}`,
  );
  return response.data;
}

export interface ResumeRunRequest {
  approved: boolean;
  reason?: string;
  override_args?: Record<string, unknown>;
}

export async function resumeRun(
  threadId: string,
  runId: string,
  body: ResumeRunRequest,
): Promise<RunDetail> {
  const response = await apiClient.post<RunDetail>(
    `/v1/sessions/${threadId}/runs/${runId}/resume`,
    body,
  );
  return response.data;
}

/** One row returned by ``GET /v1/runs``. The ``agent_name`` /
 *  ``agent_version`` come from a server-side JOIN against ``thread_meta``
 *  (Mini-ADR H-6 § 6.5.5 (b)); ``null`` when the thread was deleted. */
export interface RunListItem {
  run_id: string;
  tenant_id: string;
  thread_id: string;
  user_id: string | null;
  status: RunStatus;
  is_resume: boolean;
  error: string | null;
  agent_name: string | null;
  agent_version: string | null;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
}

export interface RunList {
  items: RunListItem[];
  total: number;
  cross_tenant: boolean;
}

export interface ListRunsParams {
  tenantScope?: TenantScope;
  status?: RunStatus;
  limit?: number;
  offset?: number;
}

/** GET /v1/runs — cross-thread index. ``tenantScope`` mirrors the
 *  agents-list shape (``"*"`` for cross-tenant, UUID for explicit, or
 *  ``undefined`` for the caller's home tenant). */
export async function listRuns(params: ListRunsParams = {}): Promise<RunList> {
  const { tenantScope, status, limit, offset } = params;
  const query = withTenantScope({ status, limit, offset }, tenantScope);
  return getJson<RunList>("/v1/runs", { params: query });
}
