/**
 * Sessions SDK — Stream H.2 PR 3.
 *
 * Two flows:
 *
 *   1. ``createSession`` — POST /v1/sessions; binds a fresh thread to
 *      ``(agent_name, agent_version)``. Returns the persisted thread
 *      metadata (status, owner, timestamps).
 *   2. ``streamRun`` — POST /v1/sessions/{thread_id}/runs with input
 *      payload; the response is an ``text/event-stream``. We parse the
 *      SSE frames on the fly and yield :class:`SseEvent` per ``id /
 *      event / data`` triple.
 *
 * Implementation note — browser ``EventSource`` doesn't support custom
 * headers (which kills the Bearer token), so we drive the stream with
 * ``fetch`` + ``ReadableStream``. ``abortSignal`` plumbs the consumer's
 * cancel back to the network layer.
 */
import { apiClient, getStoredToken, type ApiEnvelope } from "./client";
import { unwrap } from "./client";

export interface ThreadMeta {
  thread_id: string;
  tenant_id: string;
  agent_name: string | null;
  agent_version: string | null;
  user_id: string | null;
  status: string;
  /** Human label for the session-history list — auto-set from the first user
   *  message, manually overridable. Null for threads that never ran. */
  title: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface CreateSessionRequest {
  agent_name: string;
  agent_version: string;
  /** Playground impersonation — run the session as this user_id (a real
   *  tenant user's id or an arbitrary sandbox UUID) instead of the caller.
   *  Admin-gated + audited backend-side. Omitted → run as self. */
  run_as_user_id?: string;
}

export async function createSession(
  payload: CreateSessionRequest,
): Promise<ThreadMeta> {
  const response = await apiClient.post<ApiEnvelope<ThreadMeta>>(
    "/v1/sessions",
    payload,
  );
  return unwrap(response.data);
}

/** Playground-Uplift D4 — the thread user's persistent workspace + artifacts.
 *  ``workspace`` is null when no VM has ever started for that user (read-only;
 *  the inspector never provisions one). */
export interface WorkspaceMeta {
  id: string;
  tenant_id: string;
  user_id: string;
  volume_name: string;
  size_bytes: number;
  size_limit_bytes: number;
  created_at: string | null;
  last_accessed_at: string | null;
  deleted_at: string | null;
  archived_object_key: string | null;
}

export interface WorkspaceArtifact {
  name: string;
  kind: string;
  latest_version: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface SessionWorkspace {
  workspace: WorkspaceMeta | null;
  artifacts: WorkspaceArtifact[];
}

export async function getSessionWorkspace(
  threadId: string,
): Promise<SessionWorkspace> {
  const response = await apiClient.get<ApiEnvelope<SessionWorkspace>>(
    `/v1/sessions/${threadId}/workspace`,
  );
  return unwrap(response.data);
}

/** One file in the thread user's persistent workspace volume (browse). */
export interface WorkspaceFile {
  path: string;
  size: number;
}

/** List the files in the thread user's persistent workspace (newest run's
 *  output included). Empty when no volume / no supervisor. */
export async function getSessionWorkspaceFiles(
  threadId: string,
): Promise<WorkspaceFile[]> {
  const response = await apiClient.get<ApiEnvelope<{ files: WorkspaceFile[] }>>(
    `/v1/sessions/${threadId}/workspace/files`,
  );
  return unwrap(response.data).files;
}

/** Download one workspace file. Uses ``fetch`` (not the axios client) so the
 *  binary body streams to a Blob; the Bearer token is attached manually since
 *  a plain anchor href can't carry it. Triggers a browser save. */
export async function downloadSessionWorkspaceFile(
  threadId: string,
  path: string,
): Promise<void> {
  const token = getStoredToken();
  const url = `/v1/sessions/${encodeURIComponent(threadId)}/workspace/file?path=${encodeURIComponent(path)}`;
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const response = await fetch(url, { headers });
  if (!response.ok) {
    throw new Error(`workspace file download failed: HTTP ${response.status}`);
  }
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = path.split("/").pop() || "download";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

/** Download a registered artifact (latest version) by logical name. Thread-
 *  scoped — resolves the impersonated user server-side. Mirrors the workspace
 *  file download (manual Bearer + Blob save). */
export async function downloadSessionArtifact(
  threadId: string,
  name: string,
): Promise<void> {
  const token = getStoredToken();
  const url = `/v1/sessions/${encodeURIComponent(threadId)}/workspace/artifacts/${encodeURIComponent(name)}/download`;
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const response = await fetch(url, { headers });
  if (!response.ok) {
    throw new Error(`artifact download failed: HTTP ${response.status}`);
  }
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = name.split("/").pop() || "download";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

/** Delete one file from the thread user's workspace volume (hard delete). */
export async function deleteSessionWorkspaceFile(
  threadId: string,
  path: string,
): Promise<void> {
  await apiClient.delete(
    `/v1/sessions/${encodeURIComponent(threadId)}/workspace/file`,
    {
      params: { path },
    },
  );
}

/** Soft-delete one registered artifact (metadata only; the file remains). */
export async function deleteSessionArtifact(
  threadId: string,
  name: string,
): Promise<void> {
  await apiClient.delete(
    `/v1/sessions/${encodeURIComponent(threadId)}/workspace/artifacts/${encodeURIComponent(name)}`,
  );
}

/** Playground-Uplift #6 — a resumed thread's prior conversation (from the
 *  durable checkpoint), so resume shows what was said before. User/assistant
 *  text turns only. */
export interface HistoryMessage {
  role: "user" | "assistant";
  content: string;
}

export async function getSessionMessages(
  threadId: string,
): Promise<HistoryMessage[]> {
  const response = await apiClient.get<
    ApiEnvelope<{ messages: HistoryMessage[] }>
  >(`/v1/sessions/${threadId}/messages`);
  return unwrap(response.data).messages;
}

/** Playground-Uplift #6 — list the caller's threads (user-scoped server-side),
 *  newest first; the playground filters to the current agent for resume.
 *  ``q`` searches the title; ``offset`` paginates; ``includeArchived`` also
 *  returns soft-deleted (archived) threads. */
export async function listSessions(
  params: {
    limit?: number;
    offset?: number;
    q?: string;
    agentName?: string;
    status?: string;
    includeArchived?: boolean;
  } = {},
): Promise<ThreadMeta[]> {
  const query: Record<string, string | number | boolean> = {
    limit: params.limit ?? 100,
  };
  if (params.offset) query.offset = params.offset;
  if (params.q) query.q = params.q;
  if (params.agentName) query.agent_name = params.agentName;
  if (params.status) query.status = params.status;
  if (params.includeArchived) query.include_archived = true;
  const response = await apiClient.get<ApiEnvelope<{ items: ThreadMeta[] }>>(
    "/v1/sessions",
    { params: query },
  );
  return unwrap(response.data).items;
}

/** Rename a session — sets its title (overrides the auto-title). */
export async function renameSession(
  threadId: string,
  title: string,
): Promise<ThreadMeta> {
  const response = await apiClient.patch<ApiEnvelope<ThreadMeta>>(
    `/v1/sessions/${encodeURIComponent(threadId)}`,
    { title },
  );
  return unwrap(response.data);
}

/** Soft-delete a session — archive it (hidden from the default list,
 *  reversible; checkpoint/runs/workspace untouched). */
export async function archiveSession(threadId: string): Promise<void> {
  await apiClient.delete(`/v1/sessions/${encodeURIComponent(threadId)}`);
}

/** Hard-delete a session — irreversibly purge the whole conversation
 *  (checkpoint messages + run rows + the thread). The user's shared
 *  workspace/artifacts are intentionally left intact. */
export async function purgeSession(threadId: string): Promise<void> {
  await apiClient.post(`/v1/sessions/${encodeURIComponent(threadId)}:purge`);
}

export interface RunRequest {
  input?: string | null;
  image_refs?: string[];
  /** Stream PI-1c — structured untrusted input. Data to act on (a ticket,
   *  email, or document) passed here instead of concatenated into
   *  ``input`` is fenced with spotlighting before the model sees it, so an
   *  instruction embedded in it is treated as data — the root fix for
   *  inline prompt injection. Omitted → today's behaviour. */
  untrusted_content?: string[];
  /** Dynamic-Prompt — run-time Jinja variables substituted into the agent's
   *  ``system_prompt`` template (when it opts into jinja mode), validated
   *  against the agent's declared ``variables``. Omitted → no substitution. */
  inputs?: Record<string, string>;
}

/** A single SSE frame as parsed from the network stream. ``data`` is
 *  the JSON-decoded body when the frame's ``data:`` block is valid
 *  JSON; otherwise the raw string. */
export interface SseEvent {
  id: string | null;
  event: string;
  data: unknown;
  rawData: string;
  /** UTC timestamp the client received the frame. */
  receivedAt: string;
}

/** Yield SSE frames from a control-plane run stream. The caller awaits
 *  the iterator; cancellation flows through ``options.signal``. */
export async function* streamRun(
  threadId: string,
  payload: RunRequest,
  options: { signal?: AbortSignal; baseUrl?: string } = {},
): AsyncGenerator<SseEvent, void, void> {
  const baseUrl = options.baseUrl ?? "";
  const url = `${baseUrl}/v1/sessions/${encodeURIComponent(threadId)}/runs`;
  const token = getStoredToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const response = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
    signal: options.signal,
  });
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = (await response.json()) as {
        detail?: { code?: string; message?: string };
      };
      const code = body.detail?.code ?? `HTTP_${response.status}`;
      const message = body.detail?.message ?? detail;
      detail = `${code}: ${message}`;
    } catch {
      // Body wasn't JSON — keep the HTTP-N fallback.
    }
    throw new Error(detail);
  }
  if (!response.body) {
    throw new Error("response has no body — SSE not available");
  }
  yield* parseSseStream(response.body, options.signal);
}

/** Internal — parse a ``text/event-stream`` ReadableStream into frames.
 *  Exported for tests; not part of the public SDK. */
export async function* parseSseStream(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<SseEvent, void, void> {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  try {
    while (!signal?.aborted) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      while (true) {
        const idx = buffer.indexOf("\n\n");
        if (idx === -1) break;
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const frame = parseSseBlock(block);
        if (frame) yield frame;
      }
    }
  } finally {
    reader.releaseLock();
  }
}

function parseSseBlock(block: string): SseEvent | null {
  let id: string | null = null;
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line === "" || line.startsWith(":")) continue;
    const colonAt = line.indexOf(":");
    if (colonAt === -1) continue;
    const field = line.slice(0, colonAt);
    const value = line.slice(colonAt + 1).replace(/^\s/, "");
    if (field === "id") id = value;
    else if (field === "event") event = value;
    else if (field === "data") dataLines.push(value);
  }
  if (dataLines.length === 0 && id === null) return null;
  const rawData = dataLines.join("\n");
  let data: unknown = rawData;
  try {
    data = JSON.parse(rawData);
  } catch {
    // Keep as raw string — not every event carries JSON (eg. ``end``).
  }
  return {
    id,
    event,
    data,
    rawData,
    receivedAt: new Date().toISOString(),
  };
}
