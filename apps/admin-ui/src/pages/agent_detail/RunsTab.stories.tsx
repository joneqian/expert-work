import type { ComponentType } from "react";
import type { Meta, StoryObj } from "@storybook/react";
import { MemoryRouter } from "react-router-dom";

import { RunsTab } from "./RunsTab";
import type { AgentDetailResponse } from "../../api/agents";
import { apiClient } from "../../api/client";
import "../../i18n";

const detail: AgentDetailResponse = {
  record: {
    id: "11111111-1111-1111-1111-111111111111",
    tenant_id: "22222222-2222-2222-2222-222222222222",
    name: "code-reviewer",
    version: "1.0.0",
    status: "active",
    spec_sha256: "a".repeat(64),
    created_by: "u",
    created_at: "2026-06-12T00:00:00Z",
    updated_at: "2026-06-12T00:00:00Z",
    spec: {},
  },
};

const items = [
  {
    run_id: "33333333-3333-3333-3333-333333333333",
    tenant_id: detail.record.tenant_id,
    thread_id: "44444444-4444-4444-4444-444444444444",
    user_id: null,
    status: "success",
    is_resume: false,
    error: null,
    agent_name: "code-reviewer",
    agent_version: "1.0.0",
    created_at: "2026-06-12T01:00:00Z",
    updated_at: "2026-06-12T01:01:00Z",
    finished_at: "2026-06-12T01:01:00Z",
    trace_id: null,
  },
  {
    run_id: "33333333-3333-3333-3333-333333333334",
    tenant_id: detail.record.tenant_id,
    thread_id: "44444444-4444-4444-4444-444444444445",
    user_id: null,
    status: "running",
    is_resume: false,
    error: null,
    agent_name: "code-reviewer",
    agent_version: "1.0.0",
    created_at: "2026-06-12T02:00:00Z",
    updated_at: "2026-06-12T02:00:00Z",
    finished_at: null,
    trace_id: null,
  },
];

/** ``GET /v1/runs`` is an envelope endpoint — respond ``{success,data}``. */
function withStubs(capped: boolean) {
  return (Story: ComponentType) => {
    apiClient.defaults.adapter = (config) =>
      Promise.resolve({
        data: {
          success: true,
          data: {
            items,
            total: items.length,
            cross_tenant: false,
            thread_window_capped: capped,
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
        <Story />
      </MemoryRouter>
    );
  };
}

const meta: Meta<typeof RunsTab> = {
  title: "AgentDetail/RunsTab",
  component: RunsTab,
  args: { detail },
};
export default meta;

type Story = StoryObj<typeof RunsTab>;

export const Default: Story = { decorators: [withStubs(false)] };

/** The agent has more threads than the server window — warning shown. */
export const WindowCapped: Story = { decorators: [withStubs(true)] };
