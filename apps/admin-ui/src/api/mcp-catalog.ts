/**
 * MCP Catalog SDK — backed by ``/v1/platform/mcp-catalog`` (platform,
 * system_admin only) and ``/v1/mcp-servers/catalog`` (tenant admin),
 * Stream W.
 *
 * A *catalog entry* (``McpCatalogEntry``) is a connector **type** curated by
 * a platform admin: a transport + URL template + an ``auth_schema`` that
 * declares which params / secrets a tenant must supply to instantiate it.
 * Tenants browse the catalog (each entry carries an ``entitled`` flag derived
 * from their plan tier) and instantiate an entry into a concrete
 * :class:`McpServer` by POSTing params + secrets.
 *
 * Backend returns the standard ``{success, data, error}`` envelope; the
 * unwrapped payload is typed below.  ``getJson`` / ``postJson`` / ``patchJson``
 * call ``unwrap()`` internally — callers receive the data directly.
 */
import { getJson, patchJson, postJson, apiClient } from "./client";
import type { McpAuthType, McpServer, McpTransport } from "./mcp-servers";

// ── Domain types ─────────────────────────────────────────────────────────

export type McpAuthFieldKind = "secret" | "param";
export type McpRequiredTier = "free" | "pro" | "enterprise";

export interface McpCatalogAuthField {
  key: string;
  label: string;
  kind: McpAuthFieldKind;
  required: boolean;
}

export interface McpCatalogAuthSchema {
  fields: McpCatalogAuthField[];
}

export interface McpCatalogEntry {
  id: string;
  name: string;
  display_name: string;
  description: string;
  category: string;
  icon: string;
  transport: McpTransport;
  url_template: string;
  auth_type: McpAuthType;
  auth_schema: McpCatalogAuthSchema;
  required_tier: McpRequiredTier;
  enabled: boolean;
  created_at: string;
  updated_at: string;
  updated_by: string;
}

/** A catalog entry as seen by a tenant admin — augmented with whether the
 *  tenant's plan tier meets ``required_tier``. */
export interface TenantCatalogEntry extends McpCatalogEntry {
  entitled: boolean;
}

/** Upsert (create) body for ``POST /v1/platform/mcp-catalog``. */
export interface CatalogUpsertBody {
  name: string;
  display_name: string;
  description?: string;
  category?: string;
  icon?: string;
  transport: McpTransport;
  url_template: string;
  auth_type?: McpAuthType;
  auth_schema?: McpCatalogAuthSchema;
  required_tier?: McpRequiredTier;
  enabled?: boolean;
}

/** Patch body — ``name`` / ``transport`` are immutable and excluded. */
export interface CatalogPatchBody {
  display_name?: string;
  description?: string;
  category?: string;
  icon?: string;
  url_template?: string;
  auth_schema?: McpCatalogAuthSchema;
  required_tier?: McpRequiredTier;
  enabled?: boolean;
}

/** Body for instantiating a catalog entry into a tenant MCP server. */
export interface InstantiateBody {
  /** Optional instance-name override; the backend derives one from the
   *  entry name when omitted. */
  name?: string;
  params: Record<string, string>;
  secrets: Record<string, string>;
  timeout_s?: number;
}

// ── Platform catalog (system_admin) ────────────────────────────────────────

/** ``GET /v1/platform/mcp-catalog`` — full connector catalog. */
export async function listPlatformCatalog(): Promise<McpCatalogEntry[]> {
  return getJson<McpCatalogEntry[]>("/v1/platform/mcp-catalog");
}

/** ``POST /v1/platform/mcp-catalog`` — create a connector type. */
export async function createPlatformCatalogEntry(
  body: CatalogUpsertBody,
): Promise<McpCatalogEntry> {
  return postJson<McpCatalogEntry>("/v1/platform/mcp-catalog", body);
}

/** ``GET /v1/platform/mcp-catalog/{id}`` — single connector type. */
export async function getPlatformCatalogEntry(id: string): Promise<McpCatalogEntry> {
  return getJson<McpCatalogEntry>(`/v1/platform/mcp-catalog/${encodeURIComponent(id)}`);
}

/** ``PATCH /v1/platform/mcp-catalog/{id}`` — update a mutable subset. */
export async function updatePlatformCatalogEntry(
  id: string,
  body: CatalogPatchBody,
): Promise<McpCatalogEntry> {
  return patchJson<McpCatalogEntry>(
    `/v1/platform/mcp-catalog/${encodeURIComponent(id)}`,
    body,
  );
}

/** ``DELETE /v1/platform/mcp-catalog/{id}`` — 204; 409 ``CATALOG_IN_USE``
 *  when at least one tenant has instantiated the entry. */
export async function deletePlatformCatalogEntry(id: string): Promise<void> {
  await apiClient.delete(`/v1/platform/mcp-catalog/${encodeURIComponent(id)}`);
}

// ── Tenant catalog (tenant admin) ──────────────────────────────────────────

/** ``GET /v1/mcp-servers/catalog`` — catalog entries the tenant may browse,
 *  each carrying an ``entitled`` flag. */
export async function listTenantCatalog(): Promise<TenantCatalogEntry[]> {
  return getJson<TenantCatalogEntry[]>("/v1/mcp-servers/catalog");
}

/** ``POST /v1/mcp-servers/catalog/{id}/instances`` — instantiate a catalog
 *  entry into a concrete tenant MCP server. */
export async function instantiateCatalogEntry(
  id: string,
  body: InstantiateBody,
): Promise<McpServer> {
  return postJson<McpServer>(
    `/v1/mcp-servers/catalog/${encodeURIComponent(id)}/instances`,
    body,
  );
}
