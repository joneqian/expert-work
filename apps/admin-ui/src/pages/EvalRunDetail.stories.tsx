import type { Meta, StoryObj } from "@storybook/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { EvalRunDetail } from "./EvalRunDetail";
import { AuthProvider } from "../auth/AuthContext";
import { TenantScopeProvider } from "../tenant/TenantScopeContext";
import { apiClient, setStoredToken } from "../api/client";
import "../i18n";

const RUN_ID = "11111111-1111-1111-1111-111111111111";

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.`;
}

const sampleRun = {
  id: RUN_ID,
  suite: "m0_baseline",
  status: "passed",
  triggered_by: "manual",
  summary: { pass_count: 2, total: 2 },
  created_at: "2026-06-14T08:00:00Z",
  started_at: "2026-06-14T08:00:05Z",
  finished_at: "2026-06-14T08:02:30Z",
};

const sampleCases = [
  {
    id: 1,
    capability: "J.1_plan_execute",
    case_id: "J.1_plan_execute",
    passed: true,
    session_id: null,
    scores: { pass_rate: 1.0, judge_mean: 0.92 },
    session_metrics: null,
  },
  {
    id: 2,
    capability: "J.2_reflect",
    case_id: "J.2_reflect",
    passed: false,
    session_id: null,
    scores: { pass_rate: 0.0 },
    session_metrics: null,
  },
];

// Raw payloads, dispatched by URL: ``/cases`` → case list, else the run.
function withMockedDetail(run: unknown, cases: unknown[]) {
  return (Story: React.ComponentType) => {
    setStoredToken(makeJwt({ sub: "x", tenant_id: "t1", roles: ["admin"] }));
    apiClient.defaults.adapter = (config) => {
      const url = config.url ?? "";
      const data = url.endsWith("/cases") ? { cases } : run;
      return Promise.resolve({
        data,
        status: 200,
        statusText: "OK",
        headers: {},
        config,
        request: {},
      });
    };
    return (
      <MemoryRouter initialEntries={[`/eval-runs/${RUN_ID}`]}>
        <AuthProvider>
          <TenantScopeProvider>
            <Routes>
              <Route path="/eval-runs/:runId" element={<Story />} />
            </Routes>
          </TenantScopeProvider>
        </AuthProvider>
      </MemoryRouter>
    );
  };
}

const meta: Meta<typeof EvalRunDetail> = {
  title: "Stream H/EvalRunDetail",
  component: EvalRunDetail,
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof EvalRunDetail>;

export const Populated: Story = {
  decorators: [withMockedDetail(sampleRun, sampleCases)],
};

export const NoCases: Story = {
  decorators: [withMockedDetail({ ...sampleRun, status: "running", summary: null }, [])],
};
