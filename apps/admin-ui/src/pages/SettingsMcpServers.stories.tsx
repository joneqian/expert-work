/**
 * Storybook stories for SettingsMcpServers — Stream V-F.
 *
 * Two stories:
 *   - ``Empty``: an authenticated tenant admin with no MCP servers registered.
 *   - ``WithServers``: 3 servers with mixed transport / auth / enabled state.
 *
 * Mirrors the fixture decorator pattern used in ``SettingsOps.stories.tsx``
 * (JWT via ``setStoredToken`` + ``apiClient.defaults.adapter`` returning the
 * full ``{success,data,error}`` envelope).
 */
import type { ComponentType } from "react";
import type { Meta, StoryObj } from "@storybook/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "antd";

import { SettingsMcpServers } from "./SettingsMcpServers";
import { AuthProvider } from "../auth/AuthContext";
import { TenantScopeProvider } from "../tenant/TenantScopeContext";
import { apiClient, setStoredToken } from "../api/client";
import type { McpServer } from "../api/mcp-servers";
import "../i18n";

// ── Fixture helpers ────────────────────────────────────────────────────────

const TENANT = "00000000-0000-0000-0000-00000000acme";

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.`;
}

function withFixture(envelope: unknown, status = 200) {
  return (Story: ComponentType) => {
    setStoredToken(makeJwt({ sub: "u1", tenant_id: TENANT, roles: ["admin"] }));
    apiClient.defaults.adapter = (config) =>
      Promise.resolve({
        data: envelope,
        status,
        statusText: "OK",
        headers: {},
        config,
        request: {},
      });
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
  decorators: [withFixture({ success: true, data: [], error: null })],
};

/** Tenant admin with 3 servers (SSE+bearer enabled, HTTP+bearer enabled,
 *  SSE+none disabled) — shows the full table. */
export const WithServers: Story = {
  decorators: [
    withFixture({ success: true, data: SAMPLE_SERVERS, error: null }),
  ],
};
