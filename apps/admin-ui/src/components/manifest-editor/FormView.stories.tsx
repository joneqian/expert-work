/**
 * Storybook stories for FormView — Stream V-G.
 *
 * Two stories:
 *   - ``Default``: a blank manifest with no tools enabled.
 *   - ``WithMcpPicker``: the MCP toggle is on, two servers are available
 *     (github=tenant, fs=platform); ``listMcpServerTools("github")``
 *     returns two tools so the picker's tool collapse can be exercised.
 *
 * Mirrors the ``SettingsMcpServers.stories.tsx`` fixture decorator pattern:
 * ``setStoredToken`` + ``apiClient.defaults.adapter`` returning the full
 * ``{success,data,error}`` envelope. The adapter is URL-aware so
 * ``/v1/mcp-servers/available`` and ``/v1/mcp-servers/github/tools`` each
 * get their own response while ``/v1/model-catalog`` gets the catalog.
 */
import type { ComponentType } from "react";
import type { Meta, StoryObj } from "@storybook/react";

import { FormView } from "./FormView";
import { apiClient, setStoredToken } from "../../api/client";
import "../../i18n";
import type { AgentManifest } from "./form_model";
import { setReflectionEvaluator, setTool } from "./form_model";

// ── JWT helper ────────────────────────────────────────────────────────────

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.`;
}

// ── Shared fixture data ───────────────────────────────────────────────────

const CATALOG_ENVELOPE = {
  success: true,
  data: {
    providers: [
      {
        provider: "openai",
        models: [
          {
            name: "gpt-4o",
            vision: true,
            embeddings: false,
            context_window: 128000,
            deprecated: false,
          },
        ],
      },
    ],
  },
  error: null,
};

const AVAILABLE_SERVERS_ENVELOPE = {
  success: true,
  data: [
    { name: "github", source: "tenant", enabled: true },
    { name: "fs", source: "platform", enabled: true },
  ],
  error: null,
};

const GITHUB_TOOLS_ENVELOPE = {
  success: true,
  data: [
    { name: "create_issue", description: "Create a new GitHub issue" },
    { name: "list_repos", description: "List repositories" },
  ],
  error: null,
};

// ── Decorator factory ─────────────────────────────────────────────────────

function withMcpFixture(Story: ComponentType) {
  setStoredToken(makeJwt({ sub: "u1", tenant_id: "t1", roles: ["admin"] }));
  apiClient.defaults.adapter = (config) => {
    const url = typeof config.url === "string" ? config.url : "";
    let data: unknown = CATALOG_ENVELOPE;
    if (url.includes("/v1/mcp-servers/available")) {
      data = AVAILABLE_SERVERS_ENVELOPE;
    } else if (url.match(/\/v1\/mcp-servers\/github\/tools/)) {
      data = GITHUB_TOOLS_ENVELOPE;
    } else if (url.match(/\/v1\/mcp-servers\/[^/]+\/tools/)) {
      data = { success: true, data: [], error: null };
    } else if (url.includes("/v1/model-catalog")) {
      data = CATALOG_ENVELOPE;
    }
    return Promise.resolve({
      data,
      status: 200,
      statusText: "OK",
      headers: {},
      config,
      request: {},
    });
  };
  return <Story />;
}

// ── Seed manifest helpers ─────────────────────────────────────────────────

const BLANK_MANIFEST: AgentManifest = {
  apiVersion: "helix/v1",
  kind: "Agent",
  metadata: { name: "" },
  spec: {},
};

const MCP_ON_MANIFEST: AgentManifest = (() => {
  const withMcp = setTool(BLANK_MANIFEST, "mcp", true) as AgentManifest;
  return { ...withMcp, metadata: { name: "mcp-agent" } };
})();

const EVALUATOR_ON_MANIFEST: AgentManifest = (() => {
  const withEval = setReflectionEvaluator(BLANK_MANIFEST, {
    provider: "openai",
    name: "gpt-4o",
  });
  return { ...withEval, metadata: { name: "evaluator-agent" } };
})();

// ── Meta ──────────────────────────────────────────────────────────────────

const meta: Meta<typeof FormView> = {
  title: "ManifestEditor/FormView",
  component: FormView,
  parameters: { layout: "padded" },
};

export default meta;

type Story = StoryObj<typeof FormView>;

// ── Stories ───────────────────────────────────────────────────────────────

/** Blank manifest — shows the curated sections with no tools enabled. */
export const Default: Story = {
  decorators: [withMcpFixture],
  args: {
    formData: BLANK_MANIFEST,
    onChange: () => {},
    section: "basic",
  },
};

/**
 * MCP toggle is ON; two servers are available (github=tenant, fs=platform).
 * Checking the github server and expanding its Tools collapse fetches
 * create_issue + list_repos (mocked). Demonstrates the McpToolPicker
 * in its live-data state.
 */
export const WithMcpPicker: Story = {
  decorators: [withMcpFixture],
  args: {
    formData: MCP_ON_MANIFEST,
    onChange: () => {},
    section: "mcp",
  },
};

/**
 * An independent reflection evaluator is set (openai/gpt-4o) via a
 * ``routing[when=reflection]`` rule — the curated control shows the picked
 * model plus the "clear (use the agent's own model)" affordance.
 */
export const WithReflectionEvaluator: Story = {
  decorators: [withMcpFixture],
  args: {
    formData: EVALUATOR_ON_MANIFEST,
    onChange: () => {},
    section: "model",
  },
};
