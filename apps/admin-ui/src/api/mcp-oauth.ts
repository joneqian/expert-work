/**
 * MCP OAuth SDK — backed by the per-user OAuth endpoints (Stream MCP-OAUTH).
 *
 * Unlike the rest of the admin-ui SDK, these endpoints return **raw JSON**
 * (``JSONResponse``), NOT the ``{success, data, error}`` envelope — so this
 * module talks to ``apiClient`` directly instead of ``getJson`` / ``postJson``
 * (which call ``unwrap()``). The response interceptor still normalizes errors
 * into ``ApiError``.
 *
 * Flow for an ``oauth2`` catalog connector:
 *   1. ``initiateMcpOAuth(catalogId)`` → ``{ authorize_url }`` → redirect the
 *      browser there so the user authorizes with their own account.
 *   2. The provider redirects back to the admin-ui callback page, which calls
 *      ``forwardOAuthCallback(state, code)`` to finish the exchange.
 *   3. ``listOAuthConnections()`` / ``disconnectOAuth(id)`` manage connections.
 */
import { apiClient } from "./client";

export type McpOAuthStatus =
  | "pending"
  | "connected"
  | "expired"
  | "revoked"
  | "error";

/** One per-user OAuth connection (token refs + flow secrets never exposed). */
export interface McpOAuthConnection {
  id: string;
  tenant_id: string;
  user_id: string;
  catalog_id: string;
  name: string;
  status: McpOAuthStatus;
  resolved_url: string;
  scopes: string;
  token_expires_at: string | null;
  last_refresh_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface InitiateOAuthResult {
  connection_id: string;
  authorize_url: string;
  status: McpOAuthStatus;
}

export interface OAuthCallbackResult {
  connection_id: string;
  name: string;
  status: McpOAuthStatus;
}

/** admin-ui's own OAuth callback page — the redirect target it supplies to
 *  `initiate` (multi-client OAuth). Must be allowlisted on the backend. */
export const MCP_OAUTH_CALLBACK_PATH = "/settings/mcp-oauth/callback";

function adminUiRedirectUri(): string {
  return `${window.location.origin}${MCP_OAUTH_CALLBACK_PATH}`;
}

/**
 * ``POST /v1/mcp-servers/catalog/{id}/oauth/initiate`` — start the OAuth flow.
 * Returns the provider authorize URL the browser should navigate to.
 *
 * Passes admin-ui's own `redirect_uri` (multi-client OAuth) so the provider
 * returns here, regardless of the deployment's global default. Older backends
 * ignore the body and fall back to the global value.
 */
export async function initiateMcpOAuth(
  catalogId: string,
  redirectUri: string = adminUiRedirectUri(),
): Promise<InitiateOAuthResult> {
  const res = await apiClient.post<InitiateOAuthResult>(
    `/v1/mcp-servers/catalog/${encodeURIComponent(catalogId)}/oauth/initiate`,
    { redirect_uri: redirectUri },
  );
  return res.data;
}

/**
 * ``GET /v1/mcp-oauth/callback?state&code`` — finish the OAuth exchange. Called
 * by the admin-ui callback page (carries the logged-in user's bearer token).
 */
export async function forwardOAuthCallback(
  state: string,
  code: string,
): Promise<OAuthCallbackResult> {
  const res = await apiClient.get<OAuthCallbackResult>(
    "/v1/mcp-oauth/callback",
    {
      params: { state, code },
    },
  );
  return res.data;
}

/** ``GET /v1/mcp-oauth/connections`` — the caller's own OAuth connections. */
export async function listOAuthConnections(): Promise<McpOAuthConnection[]> {
  const res = await apiClient.get<{ items: McpOAuthConnection[] }>(
    "/v1/mcp-oauth/connections",
  );
  return res.data.items;
}

/** ``DELETE /v1/mcp-oauth/connections/{id}`` — disconnect (revoke + drop). */
export async function disconnectOAuth(connectionId: string): Promise<void> {
  await apiClient.delete(
    `/v1/mcp-oauth/connections/${encodeURIComponent(connectionId)}`,
  );
}
