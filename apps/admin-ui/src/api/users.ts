/**
 * Agent users SDK — the per-agent users rollup
 * (``docs/design/conversation-centric-ia.md`` §5 M2).
 *
 * ``GET /v1/agents/{name}/{version}/users`` lists every end-user with
 * ≥1 conversation on the agent, with their conversation / run rollup
 * and token totals — the top of the user → conversation → run
 * drill-down. Standard ``{success,data}`` envelope.
 */
import { getJson, withTenantScope, type TenantScope } from "./client";
import type { RunTokens } from "./runs";

/** One row from the users rollup. */
export interface AgentUserItem {
  user_id: string;
  /** From the ``tenant_user`` registry — ``null`` when never registered. */
  display_name: string | null;
  conversation_count: number;
  run_count: number;
  error_count: number;
  pending_count: number;
  /** Newest run across the user's conversations (``null`` if none). */
  last_run_at: string | null;
  /** Token totals for this user on this agent (``null`` = no usage). */
  tokens: RunTokens | null;
}

export interface AgentUserList {
  items: AgentUserItem[];
  total: number;
  cross_tenant: boolean;
}

export interface ListAgentUsersParams {
  tenantScope?: TenantScope;
  limit?: number;
  offset?: number;
}

/** GET /v1/agents/{name}/{version}/users — the users rollup. */
export async function listAgentUsers(
  agentName: string,
  agentVersion: string,
  params: ListAgentUsersParams = {},
): Promise<AgentUserList> {
  const { tenantScope, limit, offset } = params;
  const query = withTenantScope({ limit, offset }, tenantScope);
  return getJson<AgentUserList>(
    `/v1/agents/${encodeURIComponent(agentName)}/${encodeURIComponent(agentVersion)}/users`,
    { params: query },
  );
}

/** One ``tenant_user`` registry row — the user-detail header. */
export interface TenantUser {
  user_id: string;
  display_name: string | null;
  subject_type: string;
  created_at: string | null;
  last_active_at: string | null;
}

/** GET /v1/users/{user_id} — one registry row (self-or-admin gated). */
export async function getTenantUser(
  userId: string,
  tenantScope?: TenantScope,
): Promise<TenantUser> {
  const query = withTenantScope({}, tenantScope);
  return getJson<TenantUser>(`/v1/users/${encodeURIComponent(userId)}`, { params: query });
}
