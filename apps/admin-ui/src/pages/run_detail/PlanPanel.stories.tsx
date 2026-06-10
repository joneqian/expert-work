import type { Meta, StoryObj } from "@storybook/react";
import { App } from "antd";

import { PlanPanel } from "./PlanPanel";
import type { ThreadPlan } from "../../api/plan";
import "../../i18n";

const plan: ThreadPlan = {
  goal: "ship the recoverable-compression feature",
  steps: [
    { id: "1", description: "design the overflow seam", status: "completed" },
    { id: "2", description: "implement the pure core", status: "completed" },
    { id: "3", description: "wire the tools node", status: "in_progress" },
    { id: "4", description: "write the integration tests", status: "pending" },
  ],
};

const meta: Meta<typeof PlanPanel> = {
  title: "RunDetail/PlanPanel",
  component: PlanPanel,
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

type Story = StoryObj<typeof PlanPanel>;

export const ReadView: Story = {
  args: {
    threadId: "t-1",
    runStatus: "success",
    fetchPlan: () => Promise.resolve(plan),
    savePlan: (_id, next) => Promise.resolve(next),
  },
};

export const EmptyState: Story = {
  args: {
    threadId: "t-2",
    runStatus: "success",
    fetchPlan: () => Promise.resolve(null),
  },
};

export const LockedWhileRunning: Story = {
  args: {
    threadId: "t-3",
    runStatus: "running",
    fetchPlan: () => Promise.resolve(plan),
  },
};
