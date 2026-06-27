import type { ComponentType } from "react";
import type { Meta, StoryObj } from "@storybook/react";
import { App } from "antd";
import { MemoryRouter } from "react-router-dom";

import { KnowledgeAdmin } from "./KnowledgeAdmin";
import { apiClient } from "../api/client";
import { TenantScopeProvider } from "../tenant/TenantScopeContext";
import "../i18n";

const BASES_RESPONSE = {
  bases: [
    {
      id: "11111111-1111-1111-1111-111111111111",
      name: "support-docs",
      description: "Customer support FAQ + handbooks",
      chunk_max_tokens: 512,
      chunk_overlap_tokens: 64,
      created_at: "2026-06-12T00:00:00Z",
      needs_reindex: false,
      reindexing: false,
      stats: { document_count: 12, chunk_count: 480 },
    },
    {
      id: "11111111-1111-1111-1111-111111111112",
      name: "product-manuals",
      description: "Hardware manuals (PDF)",
      chunk_max_tokens: 1024,
      chunk_overlap_tokens: 128,
      created_at: "2026-06-10T00:00:00Z",
      needs_reindex: true,
      reindexing: false,
      stats: { document_count: 4, chunk_count: 96 },
    },
  ],
};

/** All ``/v1/knowledge`` endpoints are raw — respond with bodies directly. */
function withStubs(bases: unknown) {
  return (Story: ComponentType) => {
    apiClient.defaults.adapter = (config) =>
      Promise.resolve({
        data: bases,
        status: 200,
        statusText: "OK",
        headers: {},
        config,
        request: {},
      });
    return (
      <MemoryRouter>
        <TenantScopeProvider>
          <App>
            <Story />
          </App>
        </TenantScopeProvider>
      </MemoryRouter>
    );
  };
}

const meta: Meta<typeof KnowledgeAdmin> = {
  title: "Pages/KnowledgeAdmin",
  component: KnowledgeAdmin,
};
export default meta;

type Story = StoryObj<typeof KnowledgeAdmin>;

/** Bases list with stats + a needs-reindex tag; click a row to open detail. */
export const Default: Story = { decorators: [withStubs(BASES_RESPONSE)] };

/** No bases yet. */
export const Empty: Story = { decorators: [withStubs({ bases: [] })] };
