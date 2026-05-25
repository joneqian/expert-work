/**
 * Agents SDK — backed by control-plane ``/v1/agents``.
 *
 * Stream N: ``listAgents`` accepts a ``TenantScope`` so system_admin
 * callers can pass ``"*"`` for the cross-tenant aggregate; the
 * ``cross_tenant`` flag on the response tells the UI which mode it got.
 */
import { getJson, withTenantScope, type TenantScope } from "./client";

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
