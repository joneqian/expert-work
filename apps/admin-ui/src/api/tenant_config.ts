/**
 * Tenant Config SDK — backed by ``/v1/tenants/{tenant_id}/config``
 * (Stream C.7).
 *
 * **Tenant-scoped only** (same shape as tenant_quotas). Backend
 * returns the standard ``{success, data, error}`` envelope.
 *
 * ETag concurrency: backend has none in M0 — PUT is last-writer-wins.
 * M1 will add ``If-Match``; until then the UI surfaces a soft
 * "Reload to see latest" hint after every save, not a 412.
 */
import { getJson, postJson, putJson } from "./client";

export type TenantPlan = "free" | "pro" | "enterprise";

/** Stream O (Mini-ADR O-2) — all-or-nothing credentials source. */
export type CredentialsMode = "platform" | "tenant";

export interface TenantConfigRecord {
  tenant_id: string;
  display_name: string;
  plan: TenantPlan;
  /** Stream O — credentials source for every LLM/tool call. */
  credentials_mode: CredentialsMode;
  /** Stream O — provider → tenant secret_ref (kms:// URI). */
  model_credentials_ref: Record<string, string>;
  /** Stream O — tool → tenant secret_ref (kms:// URI). */
  tool_credentials: Record<string, string>;
  mcp_allowlist: string[];
  rate_limit_override: Record<string, unknown>;
  pii_fields: string[];
  http_tool_allowlist: string[];
  mcp_servers: Record<string, unknown>[];
  audit_retention_days: number;
  event_log_retention_days: number;
  /** Sprint #4 (Mini-ADR U-28) — Curator thresholds.
   *  ``skill_stale_days`` default 30, range [1, 365].
   *  ``skill_archive_days`` default 90, range [2, 730], and must be
   *  strictly greater than ``skill_stale_days`` (DB CHECK + Pydantic
   *  model_validator). */
  skill_stale_days: number;
  skill_archive_days: number;
  created_at: string;
  updated_at: string;
  updated_by: string;
}

export interface TenantConfigPatchBody {
  display_name?: string;
  plan?: TenantPlan;
  credentials_mode?: CredentialsMode;
  model_credentials_ref?: Record<string, string>;
  tool_credentials?: Record<string, string>;
  mcp_allowlist?: string[];
  rate_limit_override?: Record<string, unknown>;
  pii_fields?: string[];
  http_tool_allowlist?: string[];
  mcp_servers?: Record<string, unknown>[];
  audit_retention_days?: number;
  event_log_retention_days?: number;
  skill_stale_days?: number;
  skill_archive_days?: number;
}

/** Stream O Mini-ADR O-13 — one row of the Credentials panel's provider /
 *  tool tables (composite view from ``GET .../config/credentials``). */
export interface CredentialRow {
  /** ``provider`` rows carry this; ``tool`` rows leave it undefined. */
  provider?: string;
  /** ``tool`` rows carry this; ``provider`` rows leave it undefined. */
  tool?: string;
  platform_configured: boolean;
  tenant_secret_ref: string | null;
  used_by_agents: number;
}

export interface CredentialsView {
  mode: CredentialsMode;
  providers: CredentialRow[];
  tools: CredentialRow[];
}

export interface DryRunResult {
  ok: boolean;
  missing_providers: string[];
  missing_tools: string[];
}

export interface DryRunBody {
  model_credentials_ref?: Record<string, string>;
  tool_credentials?: Record<string, string>;
}

export async function getTenantConfig(tenantId: string): Promise<TenantConfigRecord> {
  return getJson<TenantConfigRecord>(
    `/v1/tenants/${encodeURIComponent(tenantId)}/config`,
  );
}

export async function upsertTenantConfig(
  tenantId: string,
  body: TenantConfigPatchBody,
): Promise<TenantConfigRecord> {
  return putJson<TenantConfigRecord>(
    `/v1/tenants/${encodeURIComponent(tenantId)}/config`,
    body,
  );
}

/** Stream O Mini-ADR O-13 — composite view driving the Credentials panel. */
export async function getCredentialsView(tenantId: string): Promise<CredentialsView> {
  return getJson<CredentialsView>(
    `/v1/tenants/${encodeURIComponent(tenantId)}/config/credentials`,
  );
}

/** Stream O Mini-ADR O-13 — preview a switch to ``tenant`` mode without
 *  persisting; returns the providers/tools still missing a credential. */
export async function dryRunCredentialsMode(
  tenantId: string,
  body: DryRunBody,
): Promise<DryRunResult> {
  return postJson<DryRunResult>(
    `/v1/tenants/${encodeURIComponent(tenantId)}/config/credentials-mode/dry-run`,
    body,
  );
}
