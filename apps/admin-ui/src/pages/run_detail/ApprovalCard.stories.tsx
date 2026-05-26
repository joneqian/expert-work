import type { Meta, StoryObj } from "@storybook/react";
import { App } from "antd";

import { ApprovalCard } from "./ApprovalCard";
import type { PendingApproval } from "../../api/runs";
import "../../i18n";

const approval: PendingApproval = {
  request_id: "req-1",
  node: "delete_records",
  reason_kind: "destructive",
  action_summary: "Delete 50 rows from users",
  proposed_args: { table: "users", limit: 50 },
  requested_at: "2026-05-26T08:00:00Z",
  timeout_at: "2026-05-27T08:00:00Z",
};

const meta: Meta<typeof ApprovalCard> = {
  title: "RunDetail/ApprovalCard",
  component: ApprovalCard,
  decorators: [
    (Story) => (
      <App>
        <div style={{ maxWidth: 720 }}>
          <Story />
        </div>
      </App>
    ),
  ],
};

export default meta;

type Story = StoryObj<typeof ApprovalCard>;

export const Pristine: Story = {
  args: {
    threadId: "t-1",
    runId: "r-1",
    approval,
    onResolved: () => undefined,
  },
};

export const ManyArgs: Story = {
  args: {
    threadId: "t-1",
    runId: "r-1",
    approval: {
      ...approval,
      action_summary: "Update agent_run rows in tenant t-acme",
      proposed_args: {
        table: "agent_run",
        where: { tenant_id: "t-acme", status: "error" },
        set: { status: "cancelled" },
        limit: 1000,
      },
    },
    onResolved: () => undefined,
  },
};
