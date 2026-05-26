import type { Meta, StoryObj } from "@storybook/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "antd";

import { TriggersList } from "./TriggersList";
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

const meta: Meta<typeof TriggersList> = {
  title: "Pages/TriggersList",
  component: TriggersList,
};

export default meta;

type Story = StoryObj<typeof TriggersList>;

const cron = (
  id: string,
  name: string,
  expr: string,
  enabled: boolean,
) => ({
  id,
  tenant_id: "t1",
  user_id: null,
  agent_name: "research_agent",
  agent_version: "2.1.0",
  name,
  kind: "cron" as const,
  config: { expr },
  enabled,
  source: "api",
  created_at: "2026-05-20T10:00:00Z",
  updated_at: "2026-05-26T10:00:00Z",
});

const webhook = (id: string, name: string, enabled: boolean) => ({
  ...cron(id, name, "—", enabled),
  kind: "webhook" as const,
  config: {},
});

export const Empty: Story = {
  decorators: [withFixture({ items: [] })],
};

export const CronOnly: Story = {
  decorators: [
    withFixture({
      items: [
        cron("t1", "daily_summary", "0 9 * * *", true),
        cron("t2", "hourly_health_check", "0 * * * *", true),
        cron("t3", "weekly_disabled", "0 8 * * MON", false),
      ],
    }),
  ],
};

export const WebhookOnly: Story = {
  decorators: [
    withFixture({
      items: [
        webhook("t4", "external_event_handler", true),
        webhook("t5", "crm_sync_disabled", false),
      ],
    }),
  ],
};

export const Mixed: Story = {
  decorators: [
    withFixture({
      items: [
        cron("t1", "daily_summary", "0 9 * * *", true),
        webhook("t2", "external_event", true),
        cron("t3", "weekly_report", "0 8 * * MON", false),
      ],
    }),
  ],
};

export const CrossTenant: Story = {
  decorators: [
    withFixture({
      items: [
        cron("t1", "daily_summary", "0 9 * * *", true),
        webhook("t2", "globex_webhook", true),
      ],
      crossTenant: true,
    }),
  ],
};
