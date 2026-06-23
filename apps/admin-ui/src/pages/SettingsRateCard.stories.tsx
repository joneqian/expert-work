import type { ComponentType } from "react";
import type { Meta, StoryObj } from "@storybook/react";
import { App } from "antd";
import { MemoryRouter } from "react-router-dom";

import { SettingsRateCard } from "./SettingsRateCard";
import { apiClient } from "../api/client";
import "../i18n";

const RECORDS = [
  {
    id: "11111111-1111-1111-1111-111111111111",
    tenant_id: null,
    provider: "anthropic",
    model: "claude-opus-4-8",
    input_per_mtok_micros: 5_000_000,
    output_per_mtok_micros: 25_000_000,
    cache_creation_per_mtok_micros: 6_000_000,
    cache_read_per_mtok_micros: 1_000_000,
  },
  {
    id: "11111111-1111-1111-1111-111111111112",
    tenant_id: null,
    provider: "openai",
    model: "gpt-5.5",
    input_per_mtok_micros: 3_000_000,
    output_per_mtok_micros: 12_000_000,
    cache_creation_per_mtok_micros: 0,
    cache_read_per_mtok_micros: 0,
  },
];

/** Rate-card endpoints are enveloped — respond ``{success,data,error}``. */
function withStubs(Story: ComponentType) {
  apiClient.defaults.adapter = (config) =>
    Promise.resolve({
      data: { success: true, data: RECORDS, error: null },
      status: 200,
      statusText: "OK",
      headers: {},
      config,
      request: {},
    });
  return (
    <MemoryRouter>
      <App>
        <Story />
      </App>
    </MemoryRouter>
  );
}

const meta: Meta<typeof SettingsRateCard> = {
  title: "Pages/SettingsRateCard",
  component: SettingsRateCard,
  decorators: [withStubs],
};
export default meta;

type Story = StoryObj<typeof SettingsRateCard>;

/** NOTE: the story renders the forbidden state unless the auth context
 *  reports system_admin — Storybook has no real login, so this story is
 *  primarily for the gate + table layout's visual baseline. */
export const Default: Story = {};
