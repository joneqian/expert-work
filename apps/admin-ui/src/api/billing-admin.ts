/**
 * Billing chargeback SDK — Stream Z3 (system_admin only).
 *
 * ``GET /v1/admin/billing/chargeback`` is the ONE place the full cost split
 * (base / markup / billed / margin) is exposed — a platform-admin view across
 * tenants. The tenant-facing ``/v1/usage`` surface never carries these fields.
 *
 * Standard ``{success, data, error}`` envelope; ``getJson`` unwraps it.
 */
import { getJson } from "./client";
import type { TokenCounts } from "./usage";

/** Per-tenant chargeback row — the full cost split. */
export interface ChargebackTenantRow extends TokenCounts {
  tenant_id: string;
  base_cost_micros: number;
  markup_cost_micros: number;
  billed_cost_micros: number;
  margin_micros: number;
  unpriced_buckets: number;
}

/**
 * Per-agent chargeback row — same full split, one agent within a tenant.
 * Stream 12.4: only present when the request was scoped to a single tenant.
 */
export interface ChargebackAgentRow extends TokenCounts {
  agent_name: string;
  base_cost_micros: number;
  markup_cost_micros: number;
  billed_cost_micros: number;
  margin_micros: number;
  unpriced_buckets: number;
}

export interface Chargeback {
  month: string;
  as_of: string;
  total_base_cost_micros: number;
  total_billed_cost_micros: number;
  total_margin_micros: number;
  tenants: ChargebackTenantRow[];
  /** Per-agent split — present only when ``tenantId`` was supplied. */
  agents?: ChargebackAgentRow[];
}

export interface GetChargebackParams {
  /** ``YYYY-MM``; defaults server-side to the current month when omitted. */
  month?: string;
  /** Restrict the report to a single tenant UUID. */
  tenantId?: string;
}

/** ``GET /v1/admin/billing/chargeback`` — cross-tenant cost split. */
export async function getChargeback(
  params: GetChargebackParams = {},
): Promise<Chargeback> {
  const query: Record<string, string> = {};
  if (params.month) query.month = params.month;
  if (params.tenantId) query.tenant_id = params.tenantId;
  return getJson<Chargeback>("/v1/admin/billing/chargeback", { params: query });
}
