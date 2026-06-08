/**
 * Skill-evolution governance SDK — backed by ``/v1/skill-evolution/*`` (SE-8).
 *
 * The backend returns **raw** ``JSONResponse`` payloads (no ``{success, data,
 * error}`` envelope — matching ``/v1/skills`` / ``/v1/curation``), so we go
 * through ``apiClient`` directly and read ``response.data`` verbatim (NOT
 * ``getJson``, which would try to unwrap an envelope). See
 * ``api/curation.ts`` for the same contract note.
 */
import { apiClient, withTenantScope, type TenantScope } from "./client";

export type PromoteRequestStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "superseded";

export interface PromoteRequest {
  id: string;
  tenant_id: string;
  skill_id: string;
  skill_version: number;
  status: PromoteRequestStatus;
  requested_by_user_id: string | null;
  requested_by_agent_name: string | null;
  reason: string;
  decided_by_user_id: string | null;
  decided_at: string | null;
  decision_reason: string;
  created_at: string;
}

export interface PromoteRequestList {
  items: PromoteRequest[];
  next_cursor: string | null;
  cross_tenant: boolean;
}

export interface ListPromoteRequestsParams {
  tenantScope?: TenantScope;
  status?: PromoteRequestStatus;
  cursor?: string | null;
  limit?: number;
}

export async function listPromoteRequests(
  params: ListPromoteRequestsParams = {},
): Promise<PromoteRequestList> {
  const { tenantScope, status, cursor, limit } = params;
  const query = withTenantScope(
    { status, cursor: cursor ?? undefined, limit },
    tenantScope,
  );
  const response = await apiClient.get<PromoteRequestList>(
    "/v1/skill-evolution/promote-requests",
    { params: query },
  );
  return response.data;
}

export async function requestPromote(
  skillId: string,
  body: { skill_version: number; reason?: string },
): Promise<PromoteRequest> {
  const response = await apiClient.post<PromoteRequest>(
    `/v1/skill-evolution/skills/${encodeURIComponent(skillId)}/promote-requests`,
    body,
  );
  return response.data;
}

export async function approvePromote(
  requestId: string,
  body: { decision_reason?: string } = {},
): Promise<PromoteRequest> {
  const response = await apiClient.post<PromoteRequest>(
    `/v1/skill-evolution/promote-requests/${encodeURIComponent(requestId)}/approve`,
    body,
  );
  return response.data;
}

export async function rejectPromote(
  requestId: string,
  body: { decision_reason?: string } = {},
): Promise<PromoteRequest> {
  const response = await apiClient.post<PromoteRequest>(
    `/v1/skill-evolution/promote-requests/${encodeURIComponent(requestId)}/reject`,
    body,
  );
  return response.data;
}
