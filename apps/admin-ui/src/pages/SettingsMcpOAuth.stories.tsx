/**
 * Storybook stories for SettingsMcpOAuth — Stream MCP-OAUTH.
 *
 * The page hits two endpoints with DIFFERENT response shapes: the OAuth
 * connections endpoint returns raw ``{ items }`` (no envelope) while the tenant
 * catalog returns the standard ``{ success, data }`` envelope — so the mock
 * adapter branches on the request URL.
 */
import type { ComponentType } from "react";
import type { Meta, StoryObj } from "@storybook/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "antd";

import { SettingsMcpOAuth } from "./SettingsMcpOAuth";
import type { McpOAuthConnection } from "../api/mcp-oauth";
import type { TenantCatalogEntry } from "../api/mcp-catalog";
import { AuthProvider } from "../auth/AuthContext";
import { apiClient, setStoredToken } from "../api/client";
import "../i18n";

const CATALOG: TenantCatalogEntry[] = [
  {
    id: "cat-linear",
    name: "linear",
    display_name: "Linear",
    description: "Your Linear issues.",
    category: "dev-tools",
    icon: "",
    transport: "sse",
    url_template: "https://mcp.linear.app/sse",
    auth_type: "oauth2",
    required_tier: "pro",
    enabled: true,
    entitled: true,
    tenant_enabled: true,
    created_at: "2026-05-01T10:00:00Z",
    updated_at: "2026-05-01T10:00:00Z",
    updated_by: "u1",
  },
];

const CONNECTIONS: McpOAuthConnection[] = [
  {
    id: "conn-1",
    tenant_id: "t1",
    user_id: "kc-user",
    catalog_id: "cat-linear",
    name: "linear",
    status: "connected",
    resolved_url: "https://mcp.linear.app/sse",
    scopes: "read write",
    token_expires_at: "2026-12-01T00:00:00Z",
    last_refresh_at: "2026-06-01T00:00:00Z",
    last_error: null,
    created_at: "2026-05-20T08:00:00Z",
    updated_at: "2026-06-01T00:00:00Z",
  },
];

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.`;
}

function withFixture(connections: McpOAuthConnection[]) {
  return (Story: ComponentType) => {
    setStoredToken(
      makeJwt({ sub: "kc-user", tenant_id: "t1", roles: ["operator"] }),
    );
    apiClient.defaults.adapter = (config) => {
      const url = config.url ?? "";
      const data = url.includes("/mcp-oauth/connections")
        ? { items: connections } // raw shape
        : { success: true, data: CATALOG, error: null }; // envelope
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
          <App>
            <Story />
          </App>
        </AuthProvider>
      </MemoryRouter>
    );
  };
}

const meta: Meta<typeof SettingsMcpOAuth> = {
  title: "Pages/SettingsMcpOAuth",
  component: SettingsMcpOAuth,
};

export default meta;

type Story = StoryObj<typeof SettingsMcpOAuth>;

/** A member with one connected OAuth connector. */
export const Connected: Story = {
  decorators: [withFixture(CONNECTIONS)],
};

/** No connections yet — empty state. */
export const Empty: Story = {
  decorators: [withFixture([])],
};
