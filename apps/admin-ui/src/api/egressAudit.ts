/**
 * Sandbox egress audit SDK — sandbox-egress §3.1 Phase 3.
 *
 * Backed by ``GET /v1/sandbox-egress-audit`` (Phase 3a). One row per
 * sandbox→internet connection through the transparent egress proxy: host /
 * port / byte volumes / verdict — never payload (HTTPS is tunnelled).
 *
 * Backend returns a raw payload (not the ``{success, data, error}`` envelope),
 * so this goes through ``apiClient`` directly (mirrors ``audit.ts``). Cursor
 * pagination is opaque base64 — pass ``next_cursor`` back verbatim.
 */
import { apiClient, withTenantScope, type TenantScope } from "./client";

export type EgressVerdict =
  | "allowed"
  | "blocked_ssrf"
  | "blocked_allowlist"
  | "blocked_auth"
  | "upstream_error";

export const EGRESS_VERDICTS: EgressVerdict[] = [
  "allowed",
  "blocked_ssrf",
  "blocked_allowlist",
  "blocked_auth",
  "upstream_error",
];

export interface EgressAuditEntry {
  id: number;
  tenant_id: string;
  agent_name: string | null;
  agent_version: string | null;
  sandbox_id: string | null;
  target_host: string;
  target_port: number;
  verdict: EgressVerdict;
  bytes_up: number;
  bytes_down: number;
  duration_ms: number | null;
  error_msg: string | null;
  occurred_at: string;
}

export interface EgressAuditList {
  items: EgressAuditEntry[];
  next_cursor: string | null;
  has_more: boolean;
  /** ``"cross_tenant"`` for the system_admin wildcard view; UUID otherwise. */
  applied_scope: string;
}

export interface ListEgressAuditParams {
  tenantScope?: TenantScope;
  agentName?: string;
  verdict?: EgressVerdict;
  targetHost?: string;
  cursor?: string | null;
  limit?: number;
}

export async function listEgressAudit(
  params: ListEgressAuditParams = {},
): Promise<EgressAuditList> {
  const { tenantScope, agentName, verdict, targetHost, cursor, limit } = params;
  const query = withTenantScope(
    {
      agent_name: agentName,
      verdict,
      target_host: targetHost,
      cursor: cursor ?? undefined,
      limit,
    },
    tenantScope,
  );
  const response = await apiClient.get<EgressAuditList>("/v1/sandbox-egress-audit", {
    params: query,
  });
  return response.data;
}
