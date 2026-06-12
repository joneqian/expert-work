import type { ComponentType } from "react";
import type { Meta, StoryObj } from "@storybook/react";
import { MemoryRouter } from "react-router-dom";

import { MemoryTab } from "./MemoryTab";
import { apiClient } from "../../api/client";
import "../../i18n";

const items = [
  {
    id: "77777777-7777-7777-7777-777777777777",
    tenant_id: "22222222-2222-2222-2222-222222222222",
    user_id: "88888888-8888-8888-8888-888888888888",
    kind: "fact",
    content: "prefers terse answers",
    created_at: "2026-06-12T00:00:00Z",
  },
  {
    id: "77777777-7777-7777-7777-777777777778",
    tenant_id: "22222222-2222-2222-2222-222222222222",
    user_id: "88888888-8888-8888-8888-888888888889",
    kind: "episodic",
    content: "asked for the Q2 revenue report on 2026-06-10",
    created_at: "2026-06-10T00:00:00Z",
  },
];

/** ``GET /v1/memory`` is an envelope endpoint — respond ``{success,data}``. */
function withStubs(Story: ComponentType) {
  apiClient.defaults.adapter = (config) =>
    Promise.resolve({
      data: {
        success: true,
        data: { items, total: items.length, cross_tenant: false },
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
}

const meta: Meta<typeof MemoryTab> = {
  title: "AgentDetail/MemoryTab",
  component: MemoryTab,
  decorators: [withStubs],
};
export default meta;

type Story = StoryObj<typeof MemoryTab>;

export const Default: Story = {};
