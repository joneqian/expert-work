/**
 * ManifestTab tests — Stream H.2 PR 1.
 *
 * Monaco is mocked to a plain ``<textarea>`` so jsdom can exercise the
 * view ↔ edit ↔ save state machine without spinning up workers. The
 * ``updateAgent`` SDK call is stubbed so the test stays pure-frontend.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../i18n";

import { ApiError } from "../../api/client";
import * as agentsSdk from "../../api/agents";
import { ManifestTab } from "../agent_detail/ManifestTab";
import type { AgentDetailResponse } from "../../api/agents";

vi.mock("@monaco-editor/react", () => {
  const Editor = ({
    value,
    onChange,
    options,
    ["data-testid"]: testId,
  }: {
    value: string;
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
      apiVersion: "helix.io/v1",
      kind: "Agent",
      metadata: { name: "demo-agent", version: "1.0.0" },
      spec: { model: "claude-sonnet-4-6" },
    },
  },
};

const onSaved = vi.fn();
const updateAgentMock = vi.spyOn(agentsSdk, "updateAgent");

beforeEach(() => {
  onSaved.mockClear();
  updateAgentMock.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("ManifestTab", () => {
  it("starts in view mode with a read-only editor and an Edit button", () => {
    render(<ManifestTab detail={sampleDetail} onSaved={onSaved} />);
    const editor = screen.getByTestId("manifest-editor") as HTMLTextAreaElement;
    expect(editor.readOnly).toBe(true);
    expect(editor.value).toContain("demo-agent");
    expect(editor.value).toContain("claude-sonnet-4-6");
    expect(screen.getByTestId("manifest-edit-btn")).toBeInTheDocument();
    expect(screen.queryByTestId("manifest-save-btn")).not.toBeInTheDocument();
  });

  it("clicking Edit reveals Save + Cancel and makes the editor writable", async () => {
    const user = userEvent.setup();
    render(<ManifestTab detail={sampleDetail} onSaved={onSaved} />);
    await user.click(screen.getByTestId("manifest-edit-btn"));
    const editor = screen.getByTestId("manifest-editor") as HTMLTextAreaElement;
    expect(editor.readOnly).toBe(false);
    expect(screen.getByTestId("manifest-save-btn")).toBeInTheDocument();
    expect(screen.getByTestId("manifest-cancel-btn")).toBeInTheDocument();
  });

  it("saves edits via updateAgent and returns to view mode on success", async () => {
    const user = userEvent.setup();
    updateAgentMock.mockResolvedValue(sampleDetail);
    render(<ManifestTab detail={sampleDetail} onSaved={onSaved} />);
    await user.click(screen.getByTestId("manifest-edit-btn"));
    const editor = screen.getByTestId("manifest-editor") as HTMLTextAreaElement;
    await user.clear(editor);
    await user.type(editor, "edited yaml");
    await user.click(screen.getByTestId("manifest-save-btn"));
    await waitFor(() => {
      expect(updateAgentMock).toHaveBeenCalledWith("demo-agent", "1.0.0", {
        manifest_yaml: "edited yaml",
      });
    });
    expect(onSaved).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("manifest-edit-btn")).toBeInTheDocument();
  });

  it("surfaces an error alert when updateAgent rejects, stays in edit mode", async () => {
    const user = userEvent.setup();
    updateAgentMock.mockRejectedValue(
      new ApiError("name mismatch", "MANIFEST_PATH_MISMATCH", 422),
    );
    render(<ManifestTab detail={sampleDetail} onSaved={onSaved} />);
    await user.click(screen.getByTestId("manifest-edit-btn"));
    await user.click(screen.getByTestId("manifest-save-btn"));
    const alert = await screen.findByTestId("manifest-error");
    expect(alert).toHaveTextContent("MANIFEST_PATH_MISMATCH");
    expect(onSaved).not.toHaveBeenCalled();
    expect(screen.getByTestId("manifest-save-btn")).toBeInTheDocument();
  });

  it("Cancel reverts buffer changes and returns to view mode", async () => {
    const user = userEvent.setup();
    render(<ManifestTab detail={sampleDetail} onSaved={onSaved} />);
    await user.click(screen.getByTestId("manifest-edit-btn"));
    const editor = screen.getByTestId("manifest-editor") as HTMLTextAreaElement;
    await user.clear(editor);
    await user.type(editor, "discard-me");
    await user.click(screen.getByTestId("manifest-cancel-btn"));
    expect(updateAgentMock).not.toHaveBeenCalled();
    expect(screen.getByTestId("manifest-edit-btn")).toBeInTheDocument();
    expect(
      (screen.getByTestId("manifest-editor") as HTMLTextAreaElement).value,
    ).toContain("demo-agent");
  });
});
