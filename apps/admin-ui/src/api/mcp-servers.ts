/**
 * MCP Servers SDK — backed by ``/v1/mcp-servers`` (Stream V-F).
 *
 * Tenant admins manage remote MCP servers that their agents can call tools
 * from.  Tokens are write-only: create accepts a raw token; edit uses
 * ``UpdateMcpServerBody.token`` to rotate (leave ``undefined`` to keep the
 * current value).  The backend strips the persisted secret ref from every
 * response (``_public``), so ``token`` never appears in a server record.
 *
 * Backend returns the standard ``{success, data, error}`` envelope; the
 * unwrapped payload is typed below.  ``getJson`` / ``postJson`` / ``patchJson``
 * all call ``unwrap()`` internally — the caller receives the data directly.
 */
import { getJson, patchJson, postJson, apiClient } from "./client";

export type McpTransport = "sse" | "streamable_http";
export type McpAuthType = "none" | "bearer";

export interface McpServer {
  id: string;
  name: string;
  transport: McpTransport;
  url: string;
  auth_type: McpAuthType;
  timeout_s: number;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface CreateMcpServerBody {
  name: string;
  transport: McpTransport;
  url: string;
  auth_type: McpAuthType;
  /** Raw token — stored encrypted.  Required when ``auth_type="bearer"``. */
  token?: string;
  timeout_s?: number;
}

export interface UpdateMcpServerBody {
  url?: string;
  /**
   * Rotate the bearer token.  Omit (``undefined``) to keep the current
   * token; supply a non-empty string to replace it.
   */
  token?: string;
  timeout_s?: number;
  enabled?: boolean;
}

export interface McpTool {
  name: string;
  description: string;
}

export interface AvailableMcpServer {
  name: string;
  source: "platform" | "tenant";
  enabled?: boolean;
  /** Catalog connector id this tenant server was instantiated from (Stream W).
   *  Absent for custom-registered / platform-allowlisted servers. */
  catalog_id?: string;
  /** Human-readable catalog connector name (Stream W). */
  catalog_name?: string;
}

export interface TestConnectionBody {
  transport: McpTransport;
  url: string;
  auth_type: McpAuthType;
  /** Required when ``auth_type="bearer"``. */
  token?: string;
  timeout_s?: number;
}

/** ``GET /v1/mcp-servers`` — list all MCP servers for the current tenant. */
export async function listMcpServers(): Promise<McpServer[]> {
  return getJson<McpServer[]>("/v1/mcp-servers");
}

/** ``POST /v1/mcp-servers`` — register a new MCP server. */
export async function createMcpServer(body: CreateMcpServerBody): Promise<McpServer> {
  return postJson<McpServer>("/v1/mcp-servers", body);
}

/** ``PATCH /v1/mcp-servers/{name}`` — update URL / token / timeout / enabled. */
export async function updateMcpServer(name: string, body: UpdateMcpServerBody): Promise<McpServer> {
  return patchJson<McpServer>(`/v1/mcp-servers/${encodeURIComponent(name)}`, body);
}

/** ``DELETE /v1/mcp-servers/{name}`` — remove an MCP server. */
export async function deleteMcpServer(name: string): Promise<void> {
  await apiClient.delete(`/v1/mcp-servers/${encodeURIComponent(name)}`);
}

/**
 * ``POST /v1/mcp-servers/test`` — probe-only connection test; nothing is
 * persisted.  Returns the number of tools advertised by the server.
 */
export async function testMcpConnection(body: TestConnectionBody): Promise<{ tool_count: number }> {
  return postJson<{ tool_count: number }>("/v1/mcp-servers/test", body);
}

/**
 * ``GET /v1/mcp-servers/{name}/tools`` — live probe of a registered server;
 * returns the tool list.  On probe failure the backend returns 502 — callers
 * should treat that as ``unreachable``.
 */
export async function listMcpServerTools(name: string): Promise<McpTool[]> {
  return getJson<McpTool[]>(`/v1/mcp-servers/${encodeURIComponent(name)}/tools`);
}

/**
 * ``GET /v1/mcp-servers/available`` — servers available to this tenant
 * (platform-allowlisted names + the tenant's own registered servers).
 */
export async function listAvailableMcpServers(): Promise<AvailableMcpServer[]> {
  return getJson<AvailableMcpServer[]>("/v1/mcp-servers/available");
}
