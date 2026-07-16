import type { Meta, StoryObj } from "@storybook/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "antd";

import { SettingsTenantQuotas } from "./SettingsTenantQuotas";
import { SettingsTenantConfig } from "./SettingsTenantConfig";
import { TenantScopeProvider } from "../tenant/TenantScopeContext";
import { AuthProvider } from "../auth/AuthContext";
import { apiClient, setStoredToken } from "../api/client";
import "../i18n";

const TENANT = "00000000-0000-0000-0000-00000000acme";

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.`;
}

function withFixture(envelope: unknown, status = 200) {
  return (Story: React.ComponentType) => {
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

const quotaRow = (dim: string, limit: number, burst: number | null) => ({
  id: `q-${dim}`,
  tenant_id: TENANT,
  dimension: dim,
  scope: {},
  limit_value: limit,
  burst,
  effective_from: "2026-05-26T10:00:00Z",
  effective_until: null,
  updated_by: "u1",
  updated_at: "2026-05-26T10:00:00Z",
});

const configRow = {
  tenant_id: TENANT,
  display_name: "Acme",
  plan: "pro",
  credentials_mode: "platform",
  model_credentials_ref: { openai: "vault://acme/openai" },
  tool_credentials: {},
  mcp_allowlist: ["filesystem", "git"],
  rate_limit_override: {},
  pii_fields: ["email", "phone"],
  http_tool_allowlist: ["https://api.github.com/*"],
  http_tool_denylist: ["internal.example.com"],
  mcp_servers: [],
  audit_retention_days: 365,
  event_log_retention_days: 90,
  created_at: "2026-04-01T10:00:00Z",
  updated_at: "2026-05-26T10:00:00Z",
  updated_by: "u1",
};

export const QuotasEmpty: StoryObj<typeof SettingsTenantQuotas> = {
  decorators: [withFixture({ success: true, data: [], error: null })],
};
QuotasEmpty.render = () => <SettingsTenantQuotas />;

export const QuotasPopulated: StoryObj<typeof SettingsTenantQuotas> = {
  decorators: [
    withFixture({
      success: true,
      data: [
        quotaRow("qps", 100, 20),
        quotaRow("tokens_per_day", 1_000_000, null),
        quotaRow("sandboxes", 10, null),
      ],
      error: null,
    }),
  ],
};
QuotasPopulated.render = () => <SettingsTenantQuotas />;

export const ConfigPopulated: StoryObj<typeof SettingsTenantConfig> = {
  decorators: [withFixture({ success: true, data: configRow, error: null })],
};
ConfigPopulated.render = () => <SettingsTenantConfig />;

const meta: Meta = {
  title: "Pages/SettingsOps",
};

export default meta;
