/**
 * Storybook stories for SettingsPlatformSkills — Stream X (X5).
 *
 * Mirrors ``SettingsMcpCatalog.stories.tsx``: JWT via ``setStoredToken`` +
 * ``apiClient.defaults.adapter`` returning the ``{success,data,error}``
 * envelope, parameterised on roles to exercise the system_admin gate.
 */
import type { ComponentType } from "react";
import type { Meta, StoryObj } from "@storybook/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "antd";

import { SettingsPlatformSkills } from "./SettingsPlatformSkills";
import type { PlatformSkill } from "../api/platform-skills";
import { AuthProvider } from "../auth/AuthContext";
import { apiClient, setStoredToken } from "../api/client";
import "../i18n";

const SKILLS: PlatformSkill[] = [
  {
    id: "psk-1",
    name: "web_search",
    status: "active",
    latest_version: 3,
    description: "Search the web and return top N results with citations.",
    category: "web",
    pinned: true,
    required_tier: "pro",
    last_used_at: "2026-05-25T10:00:00Z",
    state_changed_at: "2026-05-20T10:00:00Z",
    created_at: "2026-05-20T10:00:00Z",
    updated_at: "2026-05-26T10:00:00Z",
  },
  {
    id: "psk-2",
    name: "sql_analyst",
    status: "active",
    latest_version: 1,
    description: "Write and explain SQL against a connected warehouse.",
    category: "data",
    pinned: false,
    required_tier: "enterprise",
    last_used_at: null,
    state_changed_at: "2026-05-22T10:00:00Z",
    created_at: "2026-05-22T10:00:00Z",
    updated_at: "2026-05-22T10:00:00Z",
  },
  {
    id: "psk-3",
    name: "translate",
    status: "draft",
    latest_version: null,
    description: "Translate text between languages.",
    category: "language",
    pinned: false,
    required_tier: "free",
    last_used_at: null,
    state_changed_at: "2026-05-23T10:00:00Z",
    created_at: "2026-05-23T10:00:00Z",
    updated_at: "2026-05-23T10:00:00Z",
  },
];

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.`;
}

function withFixture(roles: string[], data: PlatformSkill[]) {
  return (Story: ComponentType) => {
    setStoredToken(makeJwt({ sub: "u1", tenant_id: "t1", roles }));
    apiClient.defaults.adapter = (config) =>
      Promise.resolve({
        data: { success: true, data: { items: data, next_cursor: null }, error: null },
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

const meta: Meta<typeof SettingsPlatformSkills> = {
  title: "Pages/SettingsPlatformSkills",
  component: SettingsPlatformSkills,
};

export default meta;

type Story = StoryObj<typeof SettingsPlatformSkills>;

/** system_admin with a populated platform skill catalog. */
export const SystemAdmin: Story = {
  decorators: [withFixture(["system_admin"], SKILLS)],
};

/** system_admin with no skills — guided empty state. */
export const Empty: Story = {
  decorators: [withFixture(["system_admin"], [])],
};

/** Non-admin — system-admin-only notice. */
export const NotSystemAdmin: Story = {
  decorators: [withFixture(["admin"], SKILLS)],
};
