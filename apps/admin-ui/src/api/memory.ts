/**
 * Memory SDK — backed by ``/v1/memory`` (Stream K.K6).
 *
 * Stream H.1b PR 3 added list-only. Stream H.4 PR 2 fills in PATCH
 * (update content + kind, requires server-side embedder) and DELETE
 * (soft-delete with 30-day retention).
 *
 * Per-user scoping is enforced server-side — the caller's ``user_id``
 * is derived from their principal, not a query parameter. System_admin
 * via ``tenant_id=*`` cross-tenant aggregates across every user (note:
 * cross-tenant view intentionally drops the per-user binding so
 * platform admin sees the whole picture).
 */
import {
  apiClient,
  getJson,
  patchJson,
  postJson,
  withTenantScope,
  type TenantScope,
} from "./client";

export type MemoryKind = "fact" | "episodic";

export interface MemoryItem {
  id: string;
  tenant_id: string;
  user_id: string;
  kind: MemoryKind;
  content: string;
  created_at: string;
  /** Stream Memory-Enhance (M-2) — 0–1 scores. ``importance`` feeds the
   *  write-filter; ``confidence`` is 1.0 after a user correction (M-4). */
  importance: number;
  confidence: number;
  // P5b provenance + bi-temporal (already on the wire via full model_dump).
  source_thread_id?: string | null;
  source_run_id?: string | null;
  valid_at?: string | null;
  expired_at?: string | null;
  invalid_at?: string | null;
  expected_valid_days?: number | null;
}

export interface MemoryList {
  items: MemoryItem[];
  total: number;
  cross_tenant: boolean;
}

export interface ListMemoriesParams {
  tenantScope?: TenantScope;
  kind?: MemoryKind;
  limit?: number;
  /** Tenant-admin governance view of one member's memories (M2 user
   *  detail). Non-admins asking for someone else get a 403. */
  userId?: string;
  /** ISO8601 — time-travel: memories as they were valid at this instant.
   *  Omit for the current view. */
  as_of?: string;
}

export async function listMemories(
  params: ListMemoriesParams = {},
): Promise<MemoryList> {
  const { tenantScope, kind, limit, userId, as_of } = params;
  const query = withTenantScope({ kind, limit, user_id: userId, as_of }, tenantScope);
  return getJson<MemoryList>("/v1/memory", { params: query });
}

export interface UpdateMemoryBody {
  content: string;
  /** Optional re-classification when the reviewer corrects the
   *  worker's auto-tag. Backend keeps the existing kind when omitted. */
  kind?: MemoryKind;
}

/** ``userId`` is the tenant-admin governance target (M2 user detail —
 *  editing another user's memory via ``?user_id=``); omit for the
 *  caller's own memory. */
export async function updateMemory(
  memoryId: string,
  body: UpdateMemoryBody,
  userId?: string,
): Promise<MemoryItem> {
  return patchJson<MemoryItem>(
    `/v1/memory/${encodeURIComponent(memoryId)}`,
    body,
    userId ? { params: { user_id: userId } } : undefined,
  );
}

/** DELETE returns 204 No Content — no body. ``userId`` is the tenant-admin
 *  governance target (forgetting another user's memory). */
export async function deleteMemory(memoryId: string, userId?: string): Promise<void> {
  await apiClient.delete(`/v1/memory/${encodeURIComponent(memoryId)}`, {
    params: { user_id: userId },
  });
}

/** Stream Memory-Enhance (M-4) — an end-user's authoritative self-correction:
 *  ``rewrite`` replaces the content (sets confidence to 1.0) or ``forget``
 *  marks it wrong (soft-delete). Audited as ``MEMORY_CORRECT``. */
export interface CorrectMemoryBody {
  action: "rewrite" | "forget";
  /** Required for ``rewrite``. */
  content?: string;
}

export async function correctMemory(
  memoryId: string,
  body: CorrectMemoryBody,
): Promise<MemoryItem | null> {
  return postJson<MemoryItem | null>(
    `/v1/memory/${encodeURIComponent(memoryId)}/correct`,
    body,
  );
}
