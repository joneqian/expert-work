/**
 * CreateAgentDrawer tests — Stream H.2 PR 2.
 *
 * Monaco is mocked to a plain ``<textarea>`` (same approach as
 * ``ManifestTab``); the ``createAgent`` SDK call is spied on so the
 * drawer's submit/cancel state machine is tested in isolation.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../i18n";

import { ApiError } from "../../api/client";
import * as agentsSdk from "../../api/agents";
import {
  CreateAgentDrawer,
  DEFAULT_AGENT_YAML,
} from "../CreateAgentDrawer";
import type { AgentDetailResponse } from "../../api/agents";

vi.mock("@monaco-editor/react", () => {
  const Editor = ({
    value,
    onChange,
    ["data-testid"]: testId,
  }: {
    value: string;
    onChange?: (v: string | undefined) => void;
    "data-testid"?: string;
  }) => (
    <textarea
      data-testid={testId ?? "monaco-stub"}
      value={value}
      onChange={(e) => onChange?.(e.target.value)}
    />
  );
  return { default: Editor };
});

const onClose = vi.fn();
const onCreated = vi.fn();
const createAgentMock = vi.spyOn(agentsSdk, "createAgent");

beforeEach(() => {
  onClose.mockClear();
  onCreated.mockClear();
  createAgentMock.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

const sampleCreated: AgentDetailResponse = {
  record: {
    id: "11111111-1111-1111-1111-111111111111",
    tenant_id: "22222222-2222-2222-2222-222222222222",
    name: "my-agent",
    version: "1.0.0",
    status: "active",
    spec_sha256: "deadbeef".repeat(8),
    created_by: "user-1",
    created_at: "2026-05-25T00:00:00Z",
    updated_at: "2026-05-25T00:00:00Z",
    spec: {},
  },
};

describe("CreateAgentDrawer", () => {
  it("renders with the default manifest stub when open", () => {
    render(<CreateAgentDrawer open onClose={onClose} onCreated={onCreated} />);
    const editor = screen.getByTestId("create-agent-editor") as HTMLTextAreaElement;
    expect(editor.value).toBe(DEFAULT_AGENT_YAML);
    expect(editor.value).toContain("apiVersion: helix.io/v1");
    expect(screen.getByTestId("create-agent-submit")).toBeInTheDocument();
  });

  it("submits the current buffer via createAgent and propagates the result", async () => {
    const user = userEvent.setup();
    createAgentMock.mockResolvedValue(sampleCreated);
    render(<CreateAgentDrawer open onClose={onClose} onCreated={onCreated} />);
    const editor = screen.getByTestId("create-agent-editor") as HTMLTextAreaElement;
    await user.clear(editor);
    await user.type(editor, "tweaked yaml");
    await user.click(screen.getByTestId("create-agent-submit"));
    await waitFor(() => {
      expect(createAgentMock).toHaveBeenCalledWith({ manifest_yaml: "tweaked yaml" });
    });
    expect(onCreated).toHaveBeenCalledWith(sampleCreated);
  });

  it("surfaces server errors and keeps the drawer open", async () => {
    const user = userEvent.setup();
    createAgentMock.mockRejectedValue(
      new ApiError("name + version already exists", "MANIFEST_DUPLICATE", 409),
    );
    render(<CreateAgentDrawer open onClose={onClose} onCreated={onCreated} />);
    await user.click(screen.getByTestId("create-agent-submit"));
    const alert = await screen.findByTestId("create-agent-error");
    expect(alert).toHaveTextContent("MANIFEST_DUPLICATE");
    expect(onCreated).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("Cancel closes the drawer without calling createAgent", async () => {
    const user = userEvent.setup();
    render(<CreateAgentDrawer open onClose={onClose} onCreated={onCreated} />);
    await user.click(screen.getByTestId("create-agent-cancel"));
    expect(createAgentMock).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
