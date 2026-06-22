import type { Meta, StoryObj } from "@storybook/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "antd";

import { SettingsEgressAudit } from "./SettingsEgressAudit";
import { TenantScopeProvider } from "../tenant/TenantScopeContext";
import { AuthProvider } from "../auth/AuthContext";
import { apiClient, setStoredToken } from "../api/client";
import "../i18n";

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.`;
}

const SAMPLE = [
  {
    id: 3,
    tenant_id: "t1",
    agent_name: "pptx-agent",
    agent_version: "1.0.0",
    sandbox_id: "sbx-3",
    target_host: "api.openai.com",
    target_port: 443,
    verdict: "allowed",
    bytes_up: 1840,
    bytes_down: 124000,
    duration_ms: 312,
    error_msg: null,
    occurred_at: "2026-06-22T03:00:00Z",
  },
  {
    id: 2,
    tenant_id: "t1",
    agent_name: "scraper",
    agent_version: "2.1.0",
    sandbox_id: "sbx-2",
    target_host: "evil.example.com",
    target_port: 443,
    verdict: "blocked_allowlist",
    bytes_up: 0,
    bytes_down: 0,
    duration_ms: null,
    error_msg: null,
    occurred_at: "2026-06-22T02:55:00Z",
  },
  {
    id: 1,
    tenant_id: "t1",
    agent_name: "scraper",
    agent_version: "2.1.0",
    sandbox_id: "sbx-1",
    target_host: "169.254.169.254",
    target_port: 80,
    verdict: "blocked_ssrf",
    bytes_up: 0,
    bytes_down: 0,
    duration_ms: null,
    error_msg: "host resolves to a blocked address",
    occurred_at: "2026-06-22T02:50:00Z",
  },
];

interface FixtureOptions {
  items: unknown[];
  appliedScope?: string;
  error?: boolean;
}

function withFixture({ items, appliedScope = "t1", error = false }: FixtureOptions) {
  return (Story: React.ComponentType) => {
    setStoredToken(makeJwt({ sub: "u1", tenant_id: "t1", roles: ["admin"] }));
    apiClient.defaults.adapter = (config) => {
      if (error) {
        return Promise.reject({
          isAxiosError: true,
          response: { status: 500, data: { detail: { code: "INTERNAL", message: "boom" } } },
          message: "Request failed",
          config,
        });
      }
      return Promise.resolve({
        data: { items, next_cursor: null, has_more: false, applied_scope: appliedScope },
        status: 200,
        statusText: "OK",
        headers: {},
        config,
        request: {},
      });
    };
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

const meta: Meta<typeof SettingsEgressAudit> = {
  title: "Pages/SettingsEgressAudit",
  component: SettingsEgressAudit,
};
export default meta;

type Story = StoryObj<typeof SettingsEgressAudit>;

export const Default: Story = { decorators: [withFixture({ items: SAMPLE })] };

export const Empty: Story = { decorators: [withFixture({ items: [] })] };

export const CrossTenant: Story = {
  decorators: [withFixture({ items: SAMPLE, appliedScope: "cross_tenant" })],
};

export const LoadError: Story = { decorators: [withFixture({ items: [], error: true })] };
