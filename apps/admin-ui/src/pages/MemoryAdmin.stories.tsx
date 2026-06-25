import type { Meta, StoryObj } from "@storybook/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "antd";

import { MemoryAdmin } from "./MemoryAdmin";
import { TenantScopeProvider } from "../tenant/TenantScopeContext";
import { AuthProvider } from "../auth/AuthContext";
import { apiClient, setStoredToken } from "../api/client";
import "../i18n";

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.`;
}

function withFixture(items: unknown[], crossTenant = false) {
  return (Story: React.ComponentType) => {
    setStoredToken(makeJwt({ sub: "u1", tenant_id: "t1", roles: ["admin"] }));
    apiClient.defaults.adapter = (config) =>
      Promise.resolve({
        data: {
          success: true,
          data: { items, total: items.length, cross_tenant: crossTenant },
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

const meta: Meta<typeof MemoryAdmin> = {
  title: "Pages/MemoryAdmin",
  component: MemoryAdmin,
};

export default meta;

type Story = StoryObj<typeof MemoryAdmin>;

const sample = (idx: number, kind: "fact" | "episodic", content: string) => ({
  id: `mem-${idx}`,
  tenant_id: "t1",
  user_id: `user-alice-${idx}`,
  kind,
  content,
  created_at: "2026-05-26T10:00:00Z",
  importance: 0.7,
  confidence: 0.5,
});

export const Empty: Story = {
  decorators: [withFixture([])],
};

export const Populated: Story = {
  decorators: [
    withFixture([
      sample(1, "fact", "User prefers brevity in answers."),
      sample(2, "fact", "User's primary language is English."),
      sample(3, "episodic", "Last week the user asked about Q3 revenue."),
      sample(4, "episodic", "User mentioned they work in finance."),
    ]),
  ],
};

export const CrossTenant: Story = {
  decorators: [
    withFixture(
      [
        sample(1, "fact", "Alice (acme): prefers brevity."),
        sample(2, "fact", "Bob (globex): wants chart visualisations."),
        sample(3, "episodic", "Carol (initech): asked about Q3 revenue."),
      ],
      true,
    ),
  ],
};
