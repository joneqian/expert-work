/**
 * Storybook stories for SettingsMcpServers — Stream V-F, unified list refresh.
 *
 * Two stories:
 *   - ``Empty``: an authenticated tenant admin with no MCP servers registered.
 *   - ``WithServers``: 3 custom servers (mixed transport / auth / enabled
 *     state) + 2 platform (allowlist) rows — one none-auth, one oauth2 — to
 *     show both platform action variants (Test+Remove vs Authorize+Remove).
 *
 * The page now fetches two endpoints (``listMcpServers`` + ``listAvailable
 * McpServers``), so the mock adapter branches on the request URL — mirrors
 * ``SettingsMcpOAuth.stories.tsx``'s ``withFixture`` — instead of the old
 * single-envelope-for-every-request stub.
 */
import type { ComponentType } from "react";
import type { Meta, StoryObj } from "@storybook/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "antd";

import { SettingsMcpServers } from "./SettingsMcpServers";
import { AuthProvider } from "../auth/AuthContext";
import { TenantScopeProvider } from "../tenant/TenantScopeContext";
import { apiClient, setStoredToken } from "../api/client";
import type { AvailableMcpServer, McpServer } from "../api/mcp-servers";
import "../i18n";

// ── Fixture helpers ────────────────────────────────────────────────────────

const TENANT = "00000000-0000-0000-0000-00000000acme";

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.`;
}

function withFixture(servers: McpServer[], available: AvailableMcpServer[] = []) {
  return (Story: ComponentType) => {
    setStoredToken(makeJwt({ sub: "u1", tenant_id: TENANT, roles: ["admin"] }));
    apiClient.defaults.adapter = (config) => {
      const url = config.url ?? "";
      const data = url.includes("/available")
        ? { success: true, data: available, error: null }
        : { success: true, data: servers, error: null };
      return Promise.resolve({
        data,
        status: 200,
        statusText: "OK",
        headers: {},
        config,
        request: {},
      });
    };
    return (
      <MemoryRouter>
        <AuthProvider>
          <TenantScopeProvider>
            <App>
              <Story />
            </App>
          </TenantScopeProvider>
        </AuthProvider>
      </MemoryRouter>
    );
  };
}

// ── Sample data ────────────────────────────────────────────────────────────

const SAMPLE_SERVERS: McpServer[] = [
  {
    id: "aaaaaaaa-0000-0000-0000-000000000001",
    name: "github",
    transport: "sse",
    url: "https://mcp.github.com/sse",
    auth_type: "bearer",
    timeout_s: 30,
    enabled: true,
    created_at: "2026-05-01T10:00:00Z",
    updated_at: "2026-05-01T10:00:00Z",
  },
  {
    id: "aaaaaaaa-0000-0000-0000-000000000002",
    name: "linear",
    transport: "streamable_http",
    url: "https://mcp.linear.app/mcp",
    auth_type: "bearer",
    timeout_s: 60,
    enabled: true,
    created_at: "2026-05-10T08:00:00Z",
    updated_at: "2026-05-10T08:00:00Z",
  },
  {
    id: "aaaaaaaa-0000-0000-0000-000000000003",
    name: "filesystem",
    transport: "sse",
    url: "https://mcp.internal.example.com/sse",
    auth_type: "none",
    timeout_s: 15,
    enabled: false,
    created_at: "2026-04-20T12:00:00Z",
    updated_at: "2026-05-15T09:00:00Z",
  },
];

/** Two platform (allowlist) rows — one none-auth (Test + Remove), one
 *  oauth2 (Authorize link + Remove, no Test). */
const SAMPLE_AVAILABLE: AvailableMcpServer[] = [
  {
    name: "amap",
    source: "platform",
    display_name: "高德地图",
    auth_type: "none",
    catalog_id: "cat-amap",
  },
  {
    name: "slack",
    source: "platform",
    display_name: "Slack",
    auth_type: "oauth2",
    catalog_id: "cat-slack",
  },
];

// ── Meta ───────────────────────────────────────────────────────────────────

const meta: Meta<typeof SettingsMcpServers> = {
  title: "Pages/SettingsMcpServers",
  component: SettingsMcpServers,
};

export default meta;

type Story = StoryObj<typeof SettingsMcpServers>;

// ── Stories ────────────────────────────────────────────────────────────────

/** Tenant admin with no MCP servers registered. Shows the guided empty state. */
export const Empty: Story = {
  decorators: [withFixture([], [])],
};

/** Tenant admin with 3 custom servers (SSE+bearer enabled, HTTP+bearer
 *  enabled, SSE+none disabled) + 2 platform rows (none-auth, oauth2) —
 *  shows the full unified table. */
export const WithServers: Story = {
  decorators: [withFixture(SAMPLE_SERVERS, SAMPLE_AVAILABLE)],
};
