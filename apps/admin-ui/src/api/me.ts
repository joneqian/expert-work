/**
 * Identity SDK — backed by ``GET /v1/me`` (Stream H.1b PR 2a).
 *
 * The server is the source of truth for ``is_system_admin`` (Stream N
 * augments the principal after the JWT verifies) and for the resolved
 * identity behind opaque API keys. Calling this once after login gives
 * the UI a stable identity object without decoding JWTs in-browser.
 */
import { getJson } from "./client";

/** Sentinel for cross-tenant access. Matches the server wire format. */
export const ALL_TENANTS = "*" as const;

export interface MeResponse {
  subject_id: string;
  subject_type: "user" | "service_account" | "service";
  /** Caller's *home* tenant. Stream N system_admins still carry one;
   *  cross-tenant capability is carried by ``allowed_tenants==="*"``. */
  tenant_id: string;
  /** OIDC email for the user menu (JWT only; null for API key / mTLS). */
  email: string | null;
  auth_method: "jwt" | "api_key" | "mtls";
  roles: string[];
  scopes: string[];
  is_system_admin: boolean;
  /** ``true`` when the home tenant is the synthetic platform tenant (the
   *  storage home for /setup-provisioned system_admins, not a real customer
   *  tenant). The TenantSwitcher hides this tenant — the platform level is
   *  the ``"*"`` scope, not a peer row. (Stream ACCT.) */
  home_is_platform: boolean;
  allowed_tenants: string[] | typeof ALL_TENANTS;
}

export async function getMe(): Promise<MeResponse> {
  return getJson<MeResponse>("/v1/me");
}
