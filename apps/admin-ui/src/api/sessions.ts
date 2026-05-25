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
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface CreateSessionRequest {
  agent_name: string;
  agent_version: string;
}

export async function createSession(payload: CreateSessionRequest): Promise<ThreadMeta> {
  const response = await apiClient.post<ApiEnvelope<ThreadMeta>>("/v1/sessions", payload);
  return unwrap(response.data);
}

export interface RunRequest {
  input?: string | null;
  image_refs?: string[];
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
      const body = (await response.json()) as { detail?: { code?: string; message?: string } };
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
