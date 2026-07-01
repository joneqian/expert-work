import type { Meta, StoryObj } from "@storybook/react";
import { App } from "antd";

import { SessionHistoryDrawer } from "./SessionHistoryDrawer";
import { apiClient } from "../api/client";
import "../i18n";

const now = Date.now();
const iso = (minsAgo: number) => new Date(now - minsAgo * 60_000).toISOString();

const SAMPLE = [
  {
    thread_id: "aaaaaaaa-0000-0000-0000-00000000000a",
    tenant_id: "t",
    agent_name: "demo-agent",
    agent_version: "1.0.0",
    user_id: "11111111-1111-1111-1111-111111111111",
    status: "active",
    title: "季度经营分析报告",
    created_by: "u",
    created_at: iso(180),
    updated_at: iso(3),
  },
  {
    thread_id: "bbbbbbbb-0000-0000-0000-00000000000b",
    tenant_id: "t",
    agent_name: "demo-agent",
    agent_version: "1.0.0",
    user_id: null,
    status: "paused",
    title: "帮我查一下最新的汇率",
    created_by: "u",
    created_at: iso(1440),
    updated_at: iso(90),
  },
  {
    thread_id: "cccccccc-0000-0000-0000-00000000000c",
    tenant_id: "t",
    agent_name: "demo-agent",
    agent_version: "1.0.0",
    user_id: null,
    status: "completed",
    title: null,
    created_by: "u",
    created_at: iso(4320),
    updated_at: iso(2880),
  },
];

function withMockedList(items: unknown[]) {
  return (Story: React.ComponentType) => {
    apiClient.defaults.adapter = (config) =>
      Promise.resolve({
        data: {
          success: true,
          data: { items, total: items.length },
          error: null,
        },
        status: 200,
        statusText: "OK",
        headers: {},
        config,
        request: {},
      });
    return (
      <App>
        <Story />
      </App>
    );
  };
}

const meta: Meta<typeof SessionHistoryDrawer> = {
  title: "Components/SessionHistoryDrawer",
  component: SessionHistoryDrawer,
  args: {
    open: true,
    agentName: "demo-agent",
    currentThreadId: "aaaaaaaa-0000-0000-0000-00000000000a",
    onClose: () => {},
    onResume: () => {},
  },
};

export default meta;

type Story = StoryObj<typeof SessionHistoryDrawer>;

export const WithSessions: Story = {
  decorators: [withMockedList(SAMPLE)],
};

export const Empty: Story = {
  decorators: [withMockedList([])],
};
