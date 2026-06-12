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
  tenant_override_count: number;
}

export interface PlatformToolRow {
  tool: string;
  source: PlatformSecretSource;
  secret_ref: string | null;
  enabled: boolean;
  used_by_agents: number;
  tenant_override_count: number;
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

/**
 * Per-tenant credential overrides — Stream HX-8. Platform-managed
 * (system_admin only; tenants never see these): an enabled override row
 * routes the tenant through its own platform-procured ref, a disabled row
 * suppresses the key for the tenant entirely (no fallback).
 */
export type TenantEffectiveSource = "tenant" | "suppressed" | "db" | "env" | "unset";

export interface TenantOverrideRow {
  tenant_id: string;
  secret_ref: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
  updated_by: string;
}

export interface TenantProviderEntry {
  provider: string;
  override: (TenantOverrideRow & { provider: string }) | null;
  effective_source: TenantEffectiveSource;
  effective_ref: string | null;
}

export interface TenantToolEntry {
  tool: string;
  override: (TenantOverrideRow & { tool: string }) | null;
  effective_source: TenantEffectiveSource;
  effective_ref: string | null;
}

export interface TenantCredentialsView {
  tenant_id: string;
  providers: TenantProviderEntry[];
  tools: TenantToolEntry[];
}

export async function getTenantCredentials(tenantId: string): Promise<TenantCredentialsView> {
  return getJson<TenantCredentialsView>(
    `/v1/platform/credentials/tenants/${encodeURIComponent(tenantId)}`,
  );
}

export async function upsertTenantProviderOverride(
  tenantId: string,
  provider: string,
  body: PlatformSecretUpsertBody,
): Promise<TenantOverrideRow> {
  return putJson<TenantOverrideRow>(
    `/v1/platform/credentials/tenants/${encodeURIComponent(tenantId)}/providers/${encodeURIComponent(provider)}`,
    body,
  );
}

export async function upsertTenantToolOverride(
  tenantId: string,
  tool: string,
  body: PlatformSecretUpsertBody,
): Promise<TenantOverrideRow> {
  return putJson<TenantOverrideRow>(
    `/v1/platform/credentials/tenants/${encodeURIComponent(tenantId)}/tools/${encodeURIComponent(tool)}`,
    body,
  );
}

export async function deleteTenantProviderOverride(
  tenantId: string,
  provider: string,
): Promise<void> {
  await apiClient.delete(
    `/v1/platform/credentials/tenants/${encodeURIComponent(tenantId)}/providers/${encodeURIComponent(provider)}`,
  );
}

export async function deleteTenantToolOverride(tenantId: string, tool: string): Promise<void> {
  await apiClient.delete(
    `/v1/platform/credentials/tenants/${encodeURIComponent(tenantId)}/tools/${encodeURIComponent(tool)}`,
  );
}
