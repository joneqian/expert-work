import type { ComponentType } from "react";
import type { Meta, StoryObj } from "@storybook/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "antd";

import { SettingsPlatformConfig } from "./SettingsPlatformConfig";
import type { PlatformCredentialsView } from "../api/platform_config";
import { AuthProvider } from "../auth/AuthContext";
import { apiClient, setStoredToken } from "../api/client";
import "../i18n";

const VIEW: PlatformCredentialsView = {
  providers: [
    { provider: "anthropic", source: "db", secret_ref: "kms://platform/anthropic", enabled: true, used_by_agents: 3, tenant_override_count: 2 },
    { provider: "openai", source: "env", secret_ref: "secret://openai-env", enabled: true, used_by_agents: 1, tenant_override_count: 0 },
    { provider: "qwen", source: "unset", secret_ref: null, enabled: false, used_by_agents: 0, tenant_override_count: 0 },
  ],
  tools: [
    { tool: "web_search", source: "db", secret_ref: "kms://tavily", enabled: true, used_by_agents: 2, tenant_override_count: 1 },
  ],
};

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.`;
}

function withAuth(roles: string[]) {
  return (Story: ComponentType) => {
    setStoredToken(makeJwt({ sub: "u1", tenant_id: "t1", roles }));
    apiClient.defaults.adapter = (config) =>
      Promise.resolve({
        data: { success: true, data: VIEW, error: null },
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

const meta: Meta<typeof SettingsPlatformConfig> = {
  title: "Pages/SettingsPlatformConfig",
  component: SettingsPlatformConfig,
};

export default meta;

type Story = StoryObj<typeof SettingsPlatformConfig>;

export const SystemAdmin: Story = {
  decorators: [withAuth(["system_admin"])],
};

export const NotSystemAdmin: Story = {
  decorators: [withAuth(["admin"])],
};
