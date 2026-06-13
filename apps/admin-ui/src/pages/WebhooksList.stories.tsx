import type { Meta, StoryObj } from "@storybook/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "antd";

import { WebhooksList } from "./WebhooksList";
import { TenantScopeProvider } from "../tenant/TenantScopeContext";
import { AuthProvider } from "../auth/AuthContext";
import { apiClient, setStoredToken } from "../api/client";
import "../i18n";

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.`;
}

interface FixtureOptions {
  items: unknown[];
  crossTenant?: boolean;
}

function withFixture({ items, crossTenant = false }: FixtureOptions) {
  return (Story: React.ComponentType) => {
    setStoredToken(makeJwt({ sub: "u1", tenant_id: "t1", roles: ["admin"] }));
    apiClient.defaults.adapter = (config) =>
      Promise.resolve({
        data: { items, total: items.length, cross_tenant: crossTenant },
        status: 200,
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

const meta: Meta<typeof WebhooksList> = {
  title: "Pages/WebhooksList",
  component: WebhooksList,
};

export default meta;

type Story = StoryObj<typeof WebhooksList>;

const endpoint = (
  id: string,
  name: string,
  events: string[],
  agentName: string | null,
  enabled: boolean,
) => ({
  id,
  name,
  url: `https://hooks.example.com/${name}`,
  event_types: events,
  agent_name: agentName,
  enabled,
  source: "api",
  created_at: "2026-06-13T10:00:00Z",
  updated_at: "2026-06-13T10:00:00Z",
});

export const Empty: Story = {
  decorators: [withFixture({ items: [] })],
};

export const Populated: Story = {
  decorators: [
    withFixture({
      items: [
        endpoint("w1", "ops-notify", ["run.completed", "run.failed"], null, true),
        endpoint("w2", "approval-to-slack", ["approval.requested"], "research_agent", true),
        endpoint("w3", "etl-trigger", ["artifact.saved"], null, false),
      ],
    }),
  ],
};

export const CrossTenant: Story = {
  decorators: [
    withFixture({
      items: [
        endpoint("w1", "globex-ops", ["run.completed"], null, true),
        endpoint("w2", "initech-etl", ["artifact.saved"], "reporter", true),
      ],
      crossTenant: true,
    }),
  ],
};
