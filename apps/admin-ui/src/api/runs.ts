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

/** Per-run token usage summary — aggregated from helix's own
 *  ``token_usage`` (G.9), joined to the run by ``trace_id``. ``null``
 *  when the run has no trace_id / no recorded usage (legacy or
 *  auto-triggered runs). Deep per-span traces stay in Langfuse. */
export interface RunTokens {
  input_tokens: number;
  output_tokens: number;
  cache_creation_tokens: number;
  cache_read_tokens: number;
  total_tokens: number;
  llm_calls: number;
  models: string[];
}

export interface RunDetail {
  run_id: string;
  thread_id: string;
  status: RunStatus;
  pending_approval: PendingApproval | null;
  /** Mini-ADR H-9.5 — OTel trace_id captured at run start, persisted
   *  on the ``agent_run`` row. ``null`` for legacy runs created before
   *  the migration or for auto-triggered runs (scheduler / curation
   *  worker that passes ``None``). */
  trace_id?: string | null;
  /** Runs-enrichment — per-run token summary (``null`` = no usage). */
  tokens?: RunTokens | null;
  /** Runs-enrichment — durable-row timestamps (``null`` when the run is
   *  only in the in-memory RunManager); the summary derives duration. */
  created_at?: string | null;
  finished_at?: string | null;
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

/** POST body — matches backend ``ResumeRequest`` shape:
 *  ``decision ∈ {"approve","reject","modify"}``;
 *  ``modified_args`` MUST be present iff ``decision === "modify"``.
 *  H.3 PR 5 — Mini-ADR H-9: ``modify`` lands when the reviewer edits
 *  the agent's proposed_args in the Monaco JSON inline. */
export interface ResumeRunRequest {
  decision: "approve" | "reject" | "modify";
  reason?: string;
  modified_args?: Record<string, unknown>;
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
  /** Mini-ADR H-9.5 — see :ref:`RunDetail.trace_id`. */
  trace_id: string | null;
  /** Runs-enrichment — per-run token summary (``null`` = no usage). */
  tokens?: RunTokens | null;
}

export interface RunList {
  items: RunListItem[];
  total: number;
  cross_tenant: boolean;
  /** Stream H.6 (Mini-ADR H-10) — true when the agent filter's thread
   *  window hit the server cap (older threads' runs not included).
   *  Optional so pre-H.6 mocks stay valid. */
  thread_window_capped?: boolean;
}

export interface ListRunsParams {
  tenantScope?: TenantScope;
  status?: RunStatus;
  /** Stream H.6 — narrow to one agent's runs (AgentDetail Runs tab).
   *  ``agentVersion`` requires ``agentName`` (backend 422s otherwise). */
  agentName?: string;
  agentVersion?: string;
  /** Free-text filter — substring match on run_id / thread_id. */
  q?: string;
  limit?: number;
  offset?: number;
}

/** GET /v1/runs — cross-thread index. ``tenantScope`` mirrors the
 *  agents-list shape (``"*"`` for cross-tenant, UUID for explicit, or
 *  ``undefined`` for the caller's home tenant). */
export async function listRuns(params: ListRunsParams = {}): Promise<RunList> {
  const { tenantScope, status, agentName, agentVersion, q, limit, offset } = params;
  const query = withTenantScope(
    { status, agent_name: agentName, agent_version: agentVersion, q, limit, offset },
    tenantScope,
  );
  return getJson<RunList>("/v1/runs", { params: query });
}

/** GET /v1/sessions/{thread}/runs/{run}/events — SSE stream.
 *
 *  Active runs (running/paused/pending) get a live attach via the
 *  StreamBridge; terminal runs get a one-shot replay from the
 *  ``run_event`` table. The wire format is identical (Mini-ADR H-7
 *  decision A — SSE id ``"{ms}-{seq}"``); this SDK doesn't have to
 *  know which mode it got.
 *
 *  ``sinceSeq`` is Last-Event-ID semantics: ``undefined`` returns the
 *  stream from the beginning; ``N`` returns events with ``seq > N``. */
export async function* streamRunEvents(
  threadId: string,
  runId: string,
  options: {
    sinceSeq?: number;
    signal?: AbortSignal;
    baseUrl?: string;
  } = {},
): AsyncGenerator<import("./sessions").SseEvent, void, void> {
  const { sinceSeq, signal, baseUrl = "" } = options;
  const params = new URLSearchParams();
  if (sinceSeq !== undefined) params.set("since_seq", String(sinceSeq));
  const qs = params.toString();
  const url =
    `${baseUrl}/v1/sessions/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}/events` +
    (qs ? `?${qs}` : "");
  const { getStoredToken } = await import("./client");
  const token = getStoredToken();
  const headers: Record<string, string> = { Accept: "text/event-stream" };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const response = await fetch(url, { method: "GET", headers, signal });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status} on /events`);
  }
  if (!response.body) {
    throw new Error("response has no body — SSE not available");
  }
  const { parseSseStream } = await import("./sessions");
  yield* parseSseStream(response.body, signal);
}
