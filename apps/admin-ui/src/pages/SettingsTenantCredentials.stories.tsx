import type { ComponentType } from "react";
import type { Meta, StoryObj } from "@storybook/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "antd";

import { SettingsTenantCredentials } from "./SettingsTenantCredentials";
import type { CredentialsView } from "../api/tenant_config";
import { TenantScopeProvider } from "../tenant/TenantScopeContext";
import { AuthProvider } from "../auth/AuthContext";
import { apiClient, setStoredToken } from "../api/client";
import "../i18n";

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.`;
}

function withView(view: CredentialsView) {
  return (Story: ComponentType) => {
    setStoredToken(makeJwt({ sub: "u1", tenant_id: "t1", roles: ["admin"] }));
    apiClient.defaults.adapter = (config) =>
      Promise.resolve({
        data: { success: true, data: view, error: null },
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

const meta: Meta<typeof SettingsTenantCredentials> = {
  title: "Pages/SettingsTenantCredentials",
  component: SettingsTenantCredentials,
};

export default meta;

type Story = StoryObj<typeof SettingsTenantCredentials>;

const PLATFORM_VIEW: CredentialsView = {
  mode: "platform",
  providers: [
    { provider: "anthropic", platform_configured: true, tenant_secret_ref: null, used_by_agents: 3 },
    {
      provider: "openai",
      platform_configured: true,
      tenant_secret_ref: "kms://acme/openai",
      used_by_agents: 1,
    },
    { provider: "qwen", platform_configured: true, tenant_secret_ref: null, used_by_agents: 2 },
  ],
  tools: [
    { tool: "web_search", platform_configured: true, tenant_secret_ref: null, used_by_agents: 1 },
  ],
};

export const PlatformMode: Story = {
  decorators: [withView(PLATFORM_VIEW)],
};

export const TenantMode: Story = {
  decorators: [
    withView({
      mode: "tenant",
      providers: [
        {
          provider: "anthropic",
          platform_configured: true,
          tenant_secret_ref: "kms://acme/anthropic",
          used_by_agents: 3,
        },
        {
          provider: "openai",
          platform_configured: true,
          tenant_secret_ref: "kms://acme/openai",
          used_by_agents: 1,
        },
        {
          provider: "qwen",
          platform_configured: true,
          tenant_secret_ref: "kms://acme/qwen",
          used_by_agents: 2,
        },
      ],
      tools: [
        {
          tool: "web_search",
          platform_configured: true,
          tenant_secret_ref: "kms://acme/tavily",
          used_by_agents: 1,
        },
      ],
    }),
  ],
};

export const Empty: Story = {
  decorators: [withView({ mode: "platform", providers: [], tools: [] })],
};
