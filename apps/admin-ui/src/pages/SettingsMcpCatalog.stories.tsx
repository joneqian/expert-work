/**
 * Storybook stories for SettingsMcpCatalog — Stream W.
 *
 * Mirrors ``SettingsPlatformConfig.stories.tsx``: JWT via ``setStoredToken`` +
 * ``apiClient.defaults.adapter`` returning the ``{success,data,error}``
 * envelope, parameterised on roles to exercise the system_admin gate.
 */
import type { ComponentType } from "react";
import type { Meta, StoryObj } from "@storybook/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "antd";

import { SettingsMcpCatalog } from "./SettingsMcpCatalog";
import type { McpCatalogEntry } from "../api/mcp-catalog";
import { AuthProvider } from "../auth/AuthContext";
import { apiClient, setStoredToken } from "../api/client";
import "../i18n";

const ENTRIES: McpCatalogEntry[] = [
  {
    id: "cat-1",
    name: "github",
    display_name: "GitHub",
    description: "Issues, PRs and repo search for your agents.",
    category: "dev-tools",
    icon: "",
    transport: "sse",
    url_template: "https://mcp.github.com/sse",
    auth_type: "bearer",
    required_tier: "pro",
    enabled: true,
    created_at: "2026-05-01T10:00:00Z",
    updated_at: "2026-05-01T10:00:00Z",
    updated_by: "u1",
  },
  {
    id: "cat-2",
    name: "linear",
    display_name: "Linear",
    description: "Read and write Linear issues.",
    category: "dev-tools",
    icon: "",
    transport: "streamable_http",
    url_template: "https://mcp.linear.app/{workspace}/mcp",
    auth_type: "bearer",
    required_tier: "enterprise",
    enabled: true,
    created_at: "2026-05-10T08:00:00Z",
    updated_at: "2026-05-10T08:00:00Z",
    updated_by: "u1",
  },
  {
    id: "cat-3",
    name: "filesystem",
    display_name: "Filesystem",
    description: "A read-only filesystem connector.",
    category: "infra",
    icon: "",
    transport: "sse",
    url_template: "https://mcp.internal.example.com/sse",
    auth_type: "none",
    required_tier: "free",
    enabled: false,
    created_at: "2026-04-20T12:00:00Z",
    updated_at: "2026-05-15T09:00:00Z",
    updated_by: "u1",
  },
];

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.`;
}

function withFixture(roles: string[], data: McpCatalogEntry[]) {
  return (Story: ComponentType) => {
    setStoredToken(makeJwt({ sub: "u1", tenant_id: "t1", roles }));
    apiClient.defaults.adapter = (config) =>
      Promise.resolve({
        data: { success: true, data, error: null },
        status: 200,
        statusText: "OK",
        headers: {},
        config,
        request: {},
      });
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

const meta: Meta<typeof SettingsMcpCatalog> = {
  title: "Pages/SettingsMcpCatalog",
  component: SettingsMcpCatalog,
};

export default meta;

type Story = StoryObj<typeof SettingsMcpCatalog>;

/** system_admin with a populated connector catalog. */
export const SystemAdmin: Story = {
  decorators: [withFixture(["system_admin"], ENTRIES)],
};

/** system_admin with no connectors — guided empty state. */
export const Empty: Story = {
  decorators: [withFixture(["system_admin"], [])],
};

/** Non-admin — system-admin-only notice. */
export const NotSystemAdmin: Story = {
  decorators: [withFixture(["admin"], ENTRIES)],
};
