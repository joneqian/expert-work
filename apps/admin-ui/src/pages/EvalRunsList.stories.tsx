import type { Meta, StoryObj } from "@storybook/react";
import { App } from "antd";
import { MemoryRouter } from "react-router-dom";

import { EvalRunsList } from "./EvalRunsList";
import { AuthProvider } from "../auth/AuthContext";
import { TenantScopeProvider } from "../tenant/TenantScopeContext";
import { apiClient, setStoredToken } from "../api/client";
import "../i18n";

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.`;
}

// Eval-runs endpoints return raw (un-enveloped) payloads — mock ``data``
// as the bare ``{ items, total }`` the SDK reads directly.
function withMockedList(items: unknown[]) {
  return (Story: React.ComponentType) => {
    setStoredToken(makeJwt({ sub: "x", tenant_id: "t1", roles: ["admin"] }));
    apiClient.defaults.adapter = (config) =>
      Promise.resolve({
        data: { items, total: items.length },
        status: 200,
        statusText: "OK",
        headers: {},
        config,
        request: {},
      });
    return (
      <MemoryRouter initialEntries={["/eval-runs"]}>
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
      <MemoryRouter initialEntries={["/eval-runs"]}>
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

const sampleRuns = [
  {
    id: "11111111-1111-1111-1111-111111111111",
    suite: "m0_baseline",
    status: "passed",
    triggered_by: "manual",
    summary: { pass_count: 15, total: 15 },
    created_at: "2026-06-14T08:00:00Z",
    started_at: "2026-06-14T08:00:05Z",
    finished_at: "2026-06-14T08:02:30Z",
  },
  {
    id: "22222222-2222-2222-2222-222222222222",
    suite: "m0_baseline",
    status: "running",
    triggered_by: "manual",
    summary: null,
    created_at: "2026-06-14T07:50:00Z",
    started_at: "2026-06-14T07:50:03Z",
    finished_at: null,
  },
  {
    id: "33333333-3333-3333-3333-333333333333",
    suite: "m0_baseline",
    status: "failed",
    triggered_by: "ci",
    summary: { pass_count: 13, total: 15 },
    created_at: "2026-06-14T07:40:00Z",
    started_at: "2026-06-14T07:40:02Z",
    finished_at: "2026-06-14T07:42:10Z",
  },
];

const meta: Meta<typeof EvalRunsList> = {
  title: "Stream H/EvalRunsList",
  component: EvalRunsList,
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof EvalRunsList>;

export const Populated: Story = {
  decorators: [withMockedList(sampleRuns)],
};

export const Empty: Story = {
  decorators: [withMockedList([])],
};

export const ErrorState: Story = {
  decorators: [withMockedError()],
};
