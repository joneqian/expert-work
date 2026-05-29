/**
 * Platform Credentials SDK — backed by ``/v1/platform/credentials`` (Stream P,
 * Mini-ADR P-11). **Platform-level, system_admin-only** (not tenant-scoped).
 *
 * The backend returns the full provider/tool catalog with each row's source
 * (env seed / DB overlay / unset), the effective secret_ref (DB wins), the
 * enabled flag, and a cross-tenant used-by-agents count. Values are never
 * returned — only refs (kms:// / secret:// URIs) and flags. Writes carry a
 * ref (validated server-side to reject plaintext).
 */
import { apiClient, getJson, putJson } from "./client";

export type PlatformSecretSource = "env" | "db" | "unset";

export interface PlatformProviderRow {
  provider: string;
  source: PlatformSecretSource;
  secret_ref: string | null;
  enabled: boolean;
  used_by_agents: number;
}

export interface PlatformToolRow {
  tool: string;
  source: PlatformSecretSource;
  secret_ref: string | null;
  enabled: boolean;
  used_by_agents: number;
}

export interface PlatformCredentialsView {
  providers: PlatformProviderRow[];
  tools: PlatformToolRow[];
}

/**
 * Write body — provide exactly one of:
 *  - ``secret_ref``: a ``secret://`` / ``kms://`` reference (operator-managed); or
 *  - ``value``: a raw key pasted in the UI. The backend encrypts it and stores
 *    only the generated ref (Stream Q). Never logged; sent over the wire once.
 */
export interface PlatformSecretUpsertBody {
  secret_ref?: string;
  value?: string;
  enabled: boolean;
}

export async function getPlatformCredentials(): Promise<PlatformCredentialsView> {
  return getJson<PlatformCredentialsView>("/v1/platform/credentials");
}

export async function upsertPlatformProvider(
  provider: string,
  body: PlatformSecretUpsertBody,
): Promise<PlatformProviderRow> {
  return putJson<PlatformProviderRow>(
    `/v1/platform/credentials/providers/${encodeURIComponent(provider)}`,
    body,
  );
}

export async function upsertPlatformTool(
  tool: string,
  body: PlatformSecretUpsertBody,
): Promise<PlatformToolRow> {
  return putJson<PlatformToolRow>(
    `/v1/platform/credentials/tools/${encodeURIComponent(tool)}`,
    body,
  );
}

export async function deletePlatformProvider(provider: string): Promise<void> {
  await apiClient.delete(`/v1/platform/credentials/providers/${encodeURIComponent(provider)}`);
}

export async function deletePlatformTool(tool: string): Promise<void> {
  await apiClient.delete(`/v1/platform/credentials/tools/${encodeURIComponent(tool)}`);
}
