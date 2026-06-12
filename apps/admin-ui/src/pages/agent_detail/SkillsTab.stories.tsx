import type { ComponentType } from "react";
import type { Meta, StoryObj } from "@storybook/react";
import { MemoryRouter } from "react-router-dom";

import { SkillsTab } from "./SkillsTab";
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
    id: "55555555-5555-5555-5555-555555555555",
    tenant_id: detail.record.tenant_id,
    name: "summarise-prs",
    status: "active",
    latest_version: 3,
    description: "",
    category: "data",
    visibility: "tenant",
    pinned: false,
    last_used_at: null,
    state_changed_at: "2026-06-12T00:00:00Z",
    created_at: "2026-06-12T00:00:00Z",
    updated_at: "2026-06-12T00:00:00Z",
  },
  {
    id: "55555555-5555-5555-5555-555555555556",
    tenant_id: detail.record.tenant_id,
    name: "lint-rules-digest",
    status: "draft",
    latest_version: 1,
    description: "",
    category: "ops",
    visibility: "agent_private",
    pinned: false,
    last_used_at: null,
    state_changed_at: "2026-06-12T00:00:00Z",
    created_at: "2026-06-12T00:00:00Z",
    updated_at: "2026-06-12T00:00:00Z",
  },
];

/** ``GET /v1/skills`` is a raw (non-envelope) endpoint. */
function withStubs(Story: ComponentType) {
  apiClient.defaults.adapter = (config) =>
    Promise.resolve({
      data: { items, platform_items: [], next_cursor: null, cross_tenant: false },
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

const meta: Meta<typeof SkillsTab> = {
  title: "AgentDetail/SkillsTab",
  component: SkillsTab,
  args: { detail },
  decorators: [withStubs],
};
export default meta;

type Story = StoryObj<typeof SkillsTab>;

export const Default: Story = {};
