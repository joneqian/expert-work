import type { ComponentType } from "react";
import type { Meta, StoryObj } from "@storybook/react";
import { MemoryRouter } from "react-router-dom";

import { TriggersTab } from "./TriggersTab";
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
    id: "66666666-6666-6666-6666-666666666666",
    tenant_id: detail.record.tenant_id,
    user_id: null,
    agent_name: "code-reviewer",
    agent_version: "1.0.0",
    name: "nightly-review",
    kind: "cron",
    config: { expr: "0 9 * * *" },
    enabled: true,
    source: "api",
    created_at: "2026-06-12T00:00:00Z",
    updated_at: "2026-06-12T00:00:00Z",
  },
  {
    id: "66666666-6666-6666-6666-666666666667",
    tenant_id: detail.record.tenant_id,
    user_id: null,
    agent_name: "code-reviewer",
    agent_version: "1.0.0",
    name: "on-pr-webhook",
    kind: "webhook",
    config: {},
    enabled: false,
    source: "api",
    created_at: "2026-06-12T00:00:00Z",
    updated_at: "2026-06-12T00:00:00Z",
  },
];

/** ``GET /v1/triggers`` is a raw (non-envelope) endpoint. */
function withStubs(Story: ComponentType) {
  apiClient.defaults.adapter = (config) =>
    Promise.resolve({
      data: { items, total: items.length, cross_tenant: false },
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
}

const meta: Meta<typeof TriggersTab> = {
  title: "AgentDetail/TriggersTab",
  component: TriggersTab,
  args: { detail },
  decorators: [withStubs],
};
export default meta;

type Story = StoryObj<typeof TriggersTab>;

export const Default: Story = {};
