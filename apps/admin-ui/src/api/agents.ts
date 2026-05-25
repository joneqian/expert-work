/**
 * Agents SDK — backed by control-plane ``/v1/agents``.
 *
 * Stream N: ``listAgents`` accepts a ``TenantScope`` so system_admin
 * callers can pass ``"*"`` for the cross-tenant aggregate; the
 * ``cross_tenant`` flag on the response tells the UI which mode it got.
 */
import { getJson, postJson, putJson, withTenantScope, type TenantScope } from "./client";

export interface AgentRecord {
  id: string;
  tenant_id: string;
  name: string;
  version: string;
  status: string;
  spec_sha256: string;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface AgentList {
  items: AgentRecord[];
  total: number;
  cross_tenant: boolean;
}

export interface ListAgentsParams {
  tenantScope?: TenantScope;
  status?: string;
  name?: string;
  limit?: number;
  offset?: number;
}

export async function listAgents(params: ListAgentsParams = {}): Promise<AgentList> {
  const { tenantScope, status, name, limit, offset } = params;
  const query = withTenantScope(
    { status, name, limit, offset },
    tenantScope,
  );
  return getJson<AgentList>("/v1/agents", { params: query });
}

export interface AgentDetailResponse {
  record: AgentRecord & {
    /** Full spec — same shape as POST /v1/agents accepts. Used by
     *  the Manifest preview / edit tab in :ref:`AgentDetail`. */
    spec: Record<string, unknown>;
  };
}

export async function getAgent(
  name: string,
  version: string,
): Promise<AgentDetailResponse> {
  return getJson<AgentDetailResponse>(
    `/v1/agents/${encodeURIComponent(name)}/${encodeURIComponent(version)}`,
  );
}

/** Server-side ``ManifestPayload`` accepts raw YAML + optional template
 *  vars; the backend re-loads it through :class:`ManifestLoader` so the
 *  spec is validated end-to-end (Pydantic + ManifestError) on save. */
export interface ManifestPayload {
  manifest_yaml: string;
  template_vars?: Record<string, unknown> | null;
}

/** PUT /v1/agents/{name}/{version} — in-place spec update. The
 *  ``manifest_yaml`` metadata block MUST match the path's ``name`` and
 *  ``version`` or the server rejects with ``MANIFEST_PATH_MISMATCH``
 *  (422). */
export async function updateAgent(
  name: string,
  version: string,
  payload: ManifestPayload,
): Promise<AgentDetailResponse> {
  return putJson<AgentDetailResponse>(
    `/v1/agents/${encodeURIComponent(name)}/${encodeURIComponent(version)}`,
    payload,
  );
}

/** POST /v1/agents — create a new agent from raw YAML. The backend
 *  derives ``name + version`` from the manifest's ``metadata`` block.
 *  409 ``MANIFEST_DUPLICATE`` on collision; 422 with envelope code on
 *  Pydantic / template validation errors. */
export async function createAgent(
  payload: ManifestPayload,
): Promise<AgentDetailResponse> {
  return postJson<AgentDetailResponse>("/v1/agents", payload);
}
