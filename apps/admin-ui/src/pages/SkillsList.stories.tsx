import type { Meta, StoryObj } from "@storybook/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { App } from "antd";

import { SkillsList } from "./SkillsList";
import { SkillDetail } from "./SkillDetail";
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
  hasMore?: boolean;
  crossTenant?: boolean;
}

function withFixture({ items, hasMore = false, crossTenant = false }: FixtureOptions) {
  return (Story: React.ComponentType) => {
    setStoredToken(makeJwt({ sub: "u1", tenant_id: "t1", roles: ["admin"] }));
    apiClient.defaults.adapter = (config) =>
      Promise.resolve({
        data: {
          items,
          next_cursor: hasMore ? "next-cursor" : null,
          cross_tenant: crossTenant,
        },
        status: 200,
        statusText: "OK",
        headers: {},
        config,
        request: {},
      });
    return (
      <MemoryRouter initialEntries={["/skills"]}>
        <AuthProvider>
          <TenantScopeProvider>
            <App>
              <Routes>
                <Route path="/skills" element={<Story />} />
                <Route path="/skills/:skillId" element={<SkillDetail />} />
              </Routes>
            </App>
          </TenantScopeProvider>
        </AuthProvider>
      </MemoryRouter>
    );
  };
}

const meta: Meta<typeof SkillsList> = {
  title: "Pages/SkillsList",
  component: SkillsList,
};

export default meta;

type Story = StoryObj<typeof SkillsList>;

const skill = (
  id: string,
  name: string,
  status: "draft" | "active" | "archived",
  category: string,
) => ({
  id,
  name,
  status,
  latest_version: status === "draft" ? null : 1,
  description: `${name} skill — auto-generated.`,
  category,
  created_at: "2026-05-20T10:00:00Z",
  updated_at: "2026-05-26T10:00:00Z",
});

export const Empty: Story = {
  decorators: [withFixture({ items: [] })],
};

export const Populated: Story = {
  decorators: [
    withFixture({
      items: [
        skill("sk1", "web_search", "active", "web"),
        skill("sk2", "sql_query", "active", "data"),
        skill("sk3", "slack_notify", "draft", "integration"),
        skill("sk4", "legacy_python_exec", "archived", "data"),
      ],
    }),
  ],
};

export const HasMore: Story = {
  decorators: [
    withFixture({
      items: [
        skill("sk1", "web_search", "active", "web"),
        skill("sk2", "sql_query", "active", "data"),
      ],
      hasMore: true,
    }),
  ],
};

export const CrossTenant: Story = {
  decorators: [
    withFixture({
      items: [
        skill("sk1", "web_search", "active", "web"),
        skill("sk2", "globex_crm", "active", "integration"),
      ],
      crossTenant: true,
    }),
  ],
};
