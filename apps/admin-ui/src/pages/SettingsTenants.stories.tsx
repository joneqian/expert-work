import type { ComponentType } from "react";
import type { Meta, StoryObj } from "@storybook/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "antd";

import { SettingsTenants } from "./SettingsTenants";
import { AuthProvider } from "../auth/AuthContext";
import { TenantScopeProvider } from "../tenant/TenantScopeContext";
import { apiClient, setStoredToken } from "../api/client";
import "../i18n";

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.`;
}

const SAMPLE_TENANTS = [
  {
    tenant_id: "11111111-1111-1111-1111-111111111111",
    display_name: "乐毅大公司",
    plan: "free",
    created_at: "2026-06-02T00:00:00Z",
  },
  {
    tenant_id: "22222222-2222-2222-2222-222222222222",
    display_name: "Acme Inc",
    plan: "pro",
    created_at: "2026-05-01T00:00:00Z",
  },
];

function withAuth(roles: string[]) {
  return (Story: ComponentType) => {
    setStoredToken(makeJwt({ sub: "u1", tenant_id: "t1", roles }));
    // Any GET /v1/tenants returns the sample list.
    apiClient.defaults.adapter = (config) =>
      Promise.resolve({
        data: { success: true, data: SAMPLE_TENANTS, error: null },
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

const meta: Meta<typeof SettingsTenants> = {
  title: "Pages/SettingsTenants",
  component: SettingsTenants,
};

export default meta;

type Story = StoryObj<typeof SettingsTenants>;

export const SystemAdmin: Story = {
  decorators: [withAuth(["system_admin"])],
};

export const NotSystemAdmin: Story = {
  decorators: [withAuth(["admin"])],
};
