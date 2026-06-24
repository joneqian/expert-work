/**
 * Storybook stories for the tenant "Add MCP server" catalog flow — Stream W.
 *
 * Renders the drawer open, with ``GET /v1/mcp-servers/catalog`` stubbed via
 * ``apiClient.defaults.adapter``. Mirrors the envelope-adapter fixture pattern
 * used across the page stories.
 */
import type { ComponentType } from "react";
import type { Meta, StoryObj } from "@storybook/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "antd";

import { AddMcpServerDrawer } from "./AddMcpServerDrawer";
import type { TenantCatalogEntry } from "../../api/mcp-catalog";
import { AuthProvider } from "../../auth/AuthContext";
import { apiClient, setStoredToken } from "../../api/client";
import "../../i18n";

const ENTRIES: TenantCatalogEntry[] = [
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
    required_tier: "free",
    enabled: true,
    created_at: "2026-05-01T10:00:00Z",
    updated_at: "2026-05-01T10:00:00Z",
    updated_by: "u1",
    entitled: true,
    tenant_enabled: false,
  },
  {
    id: "cat-2",
    name: "linear",
    display_name: "Linear",
    description: "Read and write Linear issues. Requires the Enterprise plan.",
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
    entitled: false,
    tenant_enabled: false,
  },
];

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.`;
}

function withFixture(data: TenantCatalogEntry[]) {
  return (Story: ComponentType) => {
    setStoredToken(makeJwt({ sub: "u1", tenant_id: "t1", roles: ["admin"] }));
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

const meta: Meta<typeof AddMcpServerDrawer> = {
  title: "Components/AddMcpServerDrawer",
  component: AddMcpServerDrawer,
  args: { open: true, onClose: () => {}, onSaved: () => {} },
};

export default meta;

type Story = StoryObj<typeof AddMcpServerDrawer>;

/** Catalog browser with a mix of entitled + locked connectors. */
export const Browse: Story = {
  decorators: [withFixture(ENTRIES)],
};

/** Empty catalog — no connectors available for the plan. */
export const Empty: Story = {
  decorators: [withFixture([])],
};
