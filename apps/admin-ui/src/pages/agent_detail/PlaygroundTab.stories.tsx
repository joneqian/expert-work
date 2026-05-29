import type { ComponentType } from "react";
import type { Meta, StoryObj } from "@storybook/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "antd";

import { PlaygroundTab } from "./PlaygroundTab";
import type { AgentDetailResponse } from "../../api/agents";
import { apiClient } from "../../api/client";
import "../../i18n";

const detail: AgentDetailResponse = {
  record: {
    id: "11111111-1111-1111-1111-111111111111",
    tenant_id: "22222222-2222-2222-2222-222222222222",
    name: "demo-agent",
    version: "1.0.0",
    status: "active",
    spec_sha256: "abc",
    created_by: "u",
    created_at: "2026-05-25T00:00:00Z",
    updated_at: "2026-05-25T00:00:00Z",
    spec: {},
  },
};

/** Stub the axios layer so the on-mount ``createSession`` resolves and an
 *  upload returns a fake ref — the Playground renders its ready state with
 *  the attach-image affordance visible. The SSE run uses ``fetch`` (not
 *  axios) so it stays inert in Storybook, which is fine for the visual. */
function withStubs(Story: ComponentType) {
  apiClient.defaults.adapter = (config) => {
    const url = config.url ?? "";
    if (url.endsWith("/uploads")) {
      return Promise.resolve({
        data: { image_ref: "helix://image/demo.png" },
        status: 201,
        statusText: "Created",
        headers: {},
        config,
        request: {},
      });
    }
    return Promise.resolve({
      data: {
        success: true,
        data: {
          thread_id: "33333333-3333-3333-3333-333333333333",
          tenant_id: "22222222-2222-2222-2222-222222222222",
          agent_name: "demo-agent",
          agent_version: "1.0.0",
          user_id: null,
          status: "active",
          created_by: "u",
          created_at: "2026-05-25T00:00:00Z",
          updated_at: "2026-05-25T00:00:00Z",
        },
        error: null,
      },
      status: 200,
      statusText: "OK",
      headers: {},
      config,
      request: {},
    });
  };
  return (
    <MemoryRouter>
      <App>
        <Story />
      </App>
    </MemoryRouter>
  );
}

const meta: Meta<typeof PlaygroundTab> = {
  title: "Pages/AgentDetail/PlaygroundTab",
  component: PlaygroundTab,
  decorators: [withStubs],
};
export default meta;

type Story = StoryObj<typeof PlaygroundTab>;

export const Ready: Story = {
  args: { detail },
};
