import type { Meta, StoryObj } from "@storybook/react";

import { EventStreamPanel } from "./EventStreamPanel";
import { apiClient } from "../../api/client";
import "../../i18n";

const LOCAL_STORAGE_KEY = "expert_work.runDetail.eventStream.expanded";

function setExpanded(expanded: boolean) {
  if (typeof window !== "undefined") {
    window.localStorage.setItem(LOCAL_STORAGE_KEY, expanded ? "1" : "0");
  }
}

/**
 * Stories use a stub fetch + axios adapter combo: the panel only opens
 * the SSE pipe when expanded, so for the collapsed story we don't need
 * a fixture. For the expanded story we feed a couple of pre-canned
 * frames so the visual is stable.
 */
function withStubEvents(frames: string[]) {
  return (Story: React.ComponentType) => {
    apiClient.defaults.adapter = (config) =>
      Promise.resolve({
        data: frames.join(""),
        status: 200,
        statusText: "OK",
        headers: {},
        config,
        request: {},
      });
    return <Story />;
  };
}

const meta: Meta<typeof EventStreamPanel> = {
  title: "RunDetail/EventStreamPanel",
  component: EventStreamPanel,
};

export default meta;

type Story = StoryObj<typeof EventStreamPanel>;

export const Collapsed: Story = {
  decorators: [
    (Story) => {
      setExpanded(false);
      return <Story />;
    },
  ],
  args: { threadId: "t-1", runId: "r-1" },
};

export const Expanded: Story = {
  decorators: [
    (Story) => {
      setExpanded(true);
      return <Story />;
    },
    withStubEvents([
      'id: 0-1\nevent: metadata\ndata: {"run_id":"r-1"}\n\n',
      'id: 1-2\nevent: updates\ndata: {"role":"assistant","content":"hello"}\n\n',
      'id: 2-3\nevent: end\ndata: {}\n\n',
    ]),
  ],
  args: { threadId: "t-1", runId: "r-1" },
};
