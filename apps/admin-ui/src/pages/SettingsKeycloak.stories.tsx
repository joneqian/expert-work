/**
 * Storybook stories for SettingsKeycloak — the platform-ops Keycloak (IAM)
 * console launcher. Parameterised on roles to exercise the system_admin gate.
 * The base URL comes from build-time env (unset in Storybook), so the card
 * renders its "configure" hint — the layout + gate are what these stories show.
 */
import type { ComponentType } from "react";
import type { Meta, StoryObj } from "@storybook/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "antd";

import { SettingsKeycloak } from "./SettingsKeycloak";
import { AuthProvider } from "../auth/AuthContext";
import { apiClient, setStoredToken } from "../api/client";
import "../i18n";

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.`;
}

function withRoles(roles: string[], isSystemAdmin: boolean) {
  return (Story: ComponentType) => {
    setStoredToken(makeJwt({ sub: "u1", tenant_id: "t1", roles }));
    apiClient.defaults.adapter = (config) =>
      Promise.resolve({
        data: {
          success: true,
          data: {
            subject_id: "u1",
            subject_type: "user",
            tenant_id: "t1",
            auth_method: "jwt",
            roles,
            scopes: [],
            is_system_admin: isSystemAdmin,
            allowed_tenants: ["t1"],
          },
          error: null,
        },
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

const meta: Meta<typeof SettingsKeycloak> = {
  title: "Pages/SettingsKeycloak",
  component: SettingsKeycloak,
};

export default meta;

type Story = StoryObj<typeof SettingsKeycloak>;

/** system_admin — the console launcher card (unset env → "configure" hint). */
export const SystemAdmin: Story = {
  decorators: [withRoles(["system_admin"], true)],
};

/** Non-admin — platform-ops-only notice, no launcher card. */
export const NotSystemAdmin: Story = {
  decorators: [withRoles(["admin"], false)],
};
