import type { Meta, StoryObj } from "@storybook/react";
import { MemoryRouter } from "react-router-dom";

import { RunsList } from "./RunsList";
import { AuthProvider } from "../auth/AuthContext";
import { TenantScopeProvider } from "../tenant/TenantScopeContext";
import { apiClient, setStoredToken } from "../api/client";
import "../i18n";

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.`;
}

function withMockedList(items: unknown[], crossTenant = false) {
  return (Story: React.ComponentType) => {
    setStoredToken(makeJwt({ sub: "x", tenant_id: "t1", roles: ["admin"] }));
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
      <MemoryRouter initialEntries={["/runs"]}>
        <AuthProvider>
          <TenantScopeProvider>
            <Story />
          </TenantScopeProvider>
        </AuthProvider>
      </MemoryRouter>
    );
  };
}

function withMockedError() {
  return (Story: React.ComponentType) => {
    setStoredToken(makeJwt({ sub: "x", tenant_id: "t1", roles: ["admin"] }));
    apiClient.defaults.adapter = () =>
      Promise.reject({
        isAxiosError: true,
        response: {
          status: 500,
          data: { detail: { code: "DB_DOWN", message: "Postgres unreachable" } },
        },
        message: "DB_DOWN",
      });
    return (
      <MemoryRouter initialEntries={["/runs"]}>
        <AuthProvider>
          <TenantScopeProvider>
            <Story />
          </TenantScopeProvider>
        </AuthProvider>
      </MemoryRouter>
    );
  };
}

const sampleRuns = [
  {
    run_id: "11111111-1111-1111-1111-111111111111",
    tenant_id: "22222222-2222-2222-2222-222222222222",
    thread_id: "33333333-3333-3333-3333-333333333333",
    user_id: null,
    status: "paused",
    is_resume: false,
    error: null,
    agent_name: "customer-support-bot",
    agent_version: "3.4.2",
    created_at: "2026-05-26T08:00:00Z",
    updated_at: "2026-05-26T08:00:32Z",
    finished_at: null,
  },
  {
    run_id: "44444444-4444-4444-4444-444444444444",
    tenant_id: "22222222-2222-2222-2222-222222222222",
    thread_id: "55555555-5555-5555-5555-555555555555",
    user_id: null,
    status: "success",
    is_resume: false,
    error: null,
    agent_name: "ops-runbook",
    agent_version: "1.2.0",
    created_at: "2026-05-26T07:55:11Z",
    updated_at: "2026-05-26T07:55:48Z",
    finished_at: "2026-05-26T07:55:48Z",
  },
  {
    run_id: "66666666-6666-6666-6666-666666666666",
    tenant_id: "22222222-2222-2222-2222-222222222222",
    thread_id: "77777777-7777-7777-7777-777777777777",
    user_id: null,
    status: "error",
    is_resume: false,
    error: "RateLimitError: openai 429",
    agent_name: null,  // thread deleted between run end and list query
    agent_version: null,
    created_at: "2026-05-26T07:50:01Z",
    updated_at: "2026-05-26T07:50:08Z",
    finished_at: "2026-05-26T07:50:08Z",
  },
];

const meta: Meta<typeof RunsList> = {
  title: "Stream H/RunsList",
  component: RunsList,
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof RunsList>;

export const Populated: Story = {
  decorators: [withMockedList(sampleRuns)],
};

export const CrossTenant: Story = {
  decorators: [withMockedList(sampleRuns, true)],
};

export const Empty: Story = {
  decorators: [withMockedList([])],
};

export const ErrorState: Story = {
  decorators: [withMockedError()],
};
