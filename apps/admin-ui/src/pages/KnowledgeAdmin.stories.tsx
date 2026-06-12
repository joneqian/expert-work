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
      chunk_max_tokens: 512,
      chunk_overlap_tokens: 64,
      created_at: "2026-06-12T00:00:00Z",
    },
    {
      id: "11111111-1111-1111-1111-111111111112",
      name: "product-manuals",
      chunk_max_tokens: 1024,
      chunk_overlap_tokens: 128,
      created_at: "2026-06-10T00:00:00Z",
    },
  ],
};

const DOCUMENTS_RESPONSE = {
  documents: [
    {
      id: "22222222-2222-2222-2222-222222222221",
      filename: "faq.pdf",
      status: "ready",
      error: null,
      chunk_count: 12,
      created_at: "2026-06-12T00:00:00Z",
      updated_at: "2026-06-12T00:05:00Z",
    },
    {
      id: "22222222-2222-2222-2222-222222222222",
      filename: "handbook.docx",
      status: "ingesting",
      error: null,
      chunk_count: 0,
      created_at: "2026-06-12T01:00:00Z",
      updated_at: "2026-06-12T01:00:10Z",
    },
    {
      id: "22222222-2222-2222-2222-222222222223",
      filename: "broken.html",
      status: "failed",
      error: "parse error: empty document",
      chunk_count: 0,
      created_at: "2026-06-12T02:00:00Z",
      updated_at: "2026-06-12T02:00:05Z",
    },
  ],
};

/** All ``/v1/knowledge`` endpoints are raw — respond with bodies directly. */
function withStubs(bases: unknown) {
  return (Story: ComponentType) => {
    apiClient.defaults.adapter = (config) => {
      const url = config.url ?? "";
      const data = /\/documents$/.test(url) ? DOCUMENTS_RESPONSE : bases;
      return Promise.resolve({
        data,
        status: 200,
        statusText: "OK",
        headers: {},
        config,
        request: {},
      });
    };
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

/** Bases on the left; click one to load its documents (4-state tags). */
export const Default: Story = { decorators: [withStubs(BASES_RESPONSE)] };

/** No bases yet. */
export const Empty: Story = { decorators: [withStubs({ bases: [] })] };
