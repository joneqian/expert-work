/**
 * ManifestTab tests — visual-first config.
 *
 * The tab renders the visual <ManifestEditor> (form tabs + a raw YAML
 * escape-hatch) by default — no view/edit toggle. Monaco is mocked to a
 * textarea; the schema and model-catalog SDKs are stubbed because the tab
 * mounts ManifestEditor immediately.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../i18n";

vi.mock("@monaco-editor/react", () => {
  const Editor = ({
    value,
    onChange,
    options,
    ["data-testid"]: testId,
  }: {
    value?: string;
    onChange?: (v: string | undefined) => void;
    options?: { readOnly?: boolean };
    "data-testid"?: string;
  }) => (
    <textarea
      data-testid={testId ?? "monaco-stub"}
      readOnly={options?.readOnly}
      value={value}
      onChange={(e) => onChange?.(e.target.value)}
    />
  );
  return { default: Editor };
});

import { ApiError } from "../../api/client";
import * as agentsSdk from "../../api/agents";
import * as schemaSdk from "../../api/manifest_schema";
import * as catalogSdk from "../../api/model_catalog";
import { __resetSchemaCacheForTest } from "../../components/manifest-editor/schema";
import { __resetCatalogCacheForTest } from "../../components/manifest-editor/catalog";
import { ManifestTab } from "../agent_detail/ManifestTab";
import type { AgentDetailResponse } from "../../api/agents";

const sampleDetail: AgentDetailResponse = {
  record: {
    id: "11111111-1111-1111-1111-111111111111",
    tenant_id: "22222222-2222-2222-2222-222222222222",
    name: "demo-agent",
    version: "1.0.0",
    status: "active",
    spec_sha256: "abc123def456abc123def456abc123def456abc123def456abc123def456abcd",
    created_by: "user-1",
    created_at: "2026-05-25T00:00:00Z",
    updated_at: "2026-05-25T00:00:00Z",
    spec: {
      apiVersion: "expert_work.io/v1",
      kind: "Agent",
      metadata: { name: "demo-agent", version: "1.0.0" },
      spec: { model: { provider: "anthropic", name: "claude-sonnet-4-6" } },
    },
  },
} as AgentDetailResponse;

const onSaved = vi.fn();
// Re-installed in beforeEach: afterEach() runs vi.restoreAllMocks(), which would
// otherwise permanently restore a module-level spy after the first test.
let updateAgentMock: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  onSaved.mockClear();
  updateAgentMock = vi.spyOn(agentsSdk, "updateAgent");
  __resetSchemaCacheForTest();
  __resetCatalogCacheForTest();
  vi.spyOn(schemaSdk, "fetchAgentSchema").mockResolvedValue({
    type: "object",
    properties: {
      metadata: { type: "object", properties: { name: { type: "string" } } },
      spec: { type: "object", properties: { model: { type: "object", properties: { provider: { type: "string" }, name: { type: "string" } } } } },
    },
  });
  vi.spyOn(catalogSdk, "fetchModelCatalog").mockResolvedValue({
    providers: [
      { provider: "anthropic", models: [{ name: "claude-sonnet-4-6", vision: true, embeddings: false, context_window: 200000, deprecated: false }] },
    ],
  });
});

afterEach(() => vi.restoreAllMocks());

describe("ManifestTab", () => {
  it("renders the visual ManifestEditor form by default (no view/edit toggle)", async () => {
    render(<ManifestTab detail={sampleDetail} onSaved={onSaved} />);
    await waitFor(() => expect(screen.getByTestId("manifest-editor-edit")).toBeInTheDocument());
    // Save + Reset are always present; there is no separate read-only mode.
    expect(screen.getByTestId("manifest-save-btn")).toBeInTheDocument();
    expect(screen.getByTestId("manifest-reset-btn")).toBeInTheDocument();
    expect(screen.queryByTestId("manifest-edit-btn")).not.toBeInTheDocument();
  });

  it("exposes the raw YAML escape-hatch tab", async () => {
    render(<ManifestTab detail={sampleDetail} onSaved={onSaved} />);
    await screen.findByTestId("manifest-editor-edit");
    expect(screen.getByTestId("manifest-tab-yaml")).toBeInTheDocument();
  });

  it("saves edits via updateAgent and stays on the editor", async () => {
    const user = userEvent.setup();
    updateAgentMock.mockResolvedValue(sampleDetail);
    render(<ManifestTab detail={sampleDetail} onSaved={onSaved} />);
    await screen.findByTestId("manifest-editor-edit");
    // edit via the YAML tab for a deterministic buffer
    await user.click(screen.getByTestId("manifest-tab-yaml"));
    const ta = screen.getByTestId("monaco-stub") as HTMLTextAreaElement;
    await user.clear(ta);
    await user.type(ta, "edited: yaml");
    await user.click(screen.getByTestId("manifest-save-btn"));
    await waitFor(() =>
      expect(updateAgentMock).toHaveBeenCalledWith("demo-agent", "1.0.0", { manifest_yaml: "edited: yaml" }),
    );
    expect(onSaved).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("manifest-editor-edit")).toBeInTheDocument();
  });

  it("surfaces an error alert when updateAgent rejects", async () => {
    const user = userEvent.setup();
    updateAgentMock.mockRejectedValue(new ApiError("name mismatch", "MANIFEST_PATH_MISMATCH", 422));
    render(<ManifestTab detail={sampleDetail} onSaved={onSaved} />);
    await screen.findByTestId("manifest-editor-edit");
    await user.click(screen.getByTestId("manifest-save-btn"));
    const alert = await screen.findByTestId("manifest-error");
    expect(alert).toHaveTextContent("MANIFEST_PATH_MISMATCH");
    expect(onSaved).not.toHaveBeenCalled();
  });

  it("Reset re-seeds from the snapshot without calling updateAgent", async () => {
    const user = userEvent.setup();
    render(<ManifestTab detail={sampleDetail} onSaved={onSaved} />);
    await screen.findByTestId("manifest-editor-edit");
    await user.click(screen.getByTestId("manifest-tab-yaml"));
    const ta = screen.getByTestId("monaco-stub") as HTMLTextAreaElement;
    await user.clear(ta);
    await user.type(ta, "edited: yaml");
    await user.click(screen.getByTestId("manifest-reset-btn"));
    expect(updateAgentMock).not.toHaveBeenCalled();
    // Editor remounts and re-seeds from the server snapshot.
    await screen.findByTestId("manifest-editor-edit");
  });
});
