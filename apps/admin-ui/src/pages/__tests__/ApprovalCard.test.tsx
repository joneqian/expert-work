/**
 * ApprovalCard tests — Stream H.3 PR 5.
 *
 * Monaco is mocked the same way ManifestTab does it (textarea stub),
 * and ``resumeRun`` is spied on so each test verifies the decision +
 * modified_args wire shape independent of the network.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../i18n";

import * as runsSdk from "../../api/runs";
import { ApprovalCard } from "../run_detail/ApprovalCard";
import type { PendingApproval } from "../../api/runs";

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

const resumeRunMock = vi.spyOn(runsSdk, "resumeRun");

beforeEach(() => {
  resumeRunMock.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

const approval: PendingApproval = {
  request_id: "req-1",
  node: "delete_records",
  reason_kind: "destructive",
  action_summary: "Delete 50 rows from users",
  proposed_args: { table: "users", limit: 50 },
  requested_at: "2026-05-26T08:00:00Z",
  timeout_at: "2026-05-27T08:00:00Z",
};

function renderCard(onResolved = vi.fn()) {
  return render(
    <App>
      <ApprovalCard threadId="t-1" runId="r-1" approval={approval} onResolved={onResolved} />
    </App>,
  );
}

describe("ApprovalCard", () => {
  it("renders the proposed_args read-only by default", () => {
    renderCard();
    const editor = screen.getByTestId("approval-editor") as HTMLTextAreaElement;
    expect(editor.readOnly).toBe(true);
    expect(editor.value).toContain('"table": "users"');
    // Approve label is the plain form when the buffer is pristine.
    expect(screen.getByTestId("approval-approve")).toHaveTextContent(/^Approve$/);
  });

  it("Edit arguments unlocks the editor and shows the right Approve label", async () => {
    const user = userEvent.setup();
    renderCard();
    await user.click(screen.getByTestId("approval-edit"));
    const editor = screen.getByTestId("approval-editor") as HTMLTextAreaElement;
    expect(editor.readOnly).toBe(false);
    // Touch the buffer — Approve flips to "Approve with edits".
    // ``userEvent.type`` treats ``{`` as a special key; use fireEvent.change
    // directly to bypass the keyboard-shortcut parser.
    fireEvent.change(editor, { target: { value: '{"table":"users","limit":10}' } });
    expect(screen.getByTestId("approval-approve")).toHaveTextContent(/with edits/i);
  });

  it("Approve sends decision='approve' with no modified_args when pristine", async () => {
    const user = userEvent.setup();
    const onResolved = vi.fn();
    resumeRunMock.mockResolvedValue({
      run_id: "r-1",
      thread_id: "t-1",
      status: "running",
      pending_approval: null,
    });
    renderCard(onResolved);
    await user.click(screen.getByTestId("approval-approve"));
    await waitFor(() =>
      expect(resumeRunMock).toHaveBeenCalledWith("t-1", "r-1", {
        decision: "approve",
      }),
    );
    expect(onResolved).toHaveBeenCalledTimes(1);
  });

  it("Approve with edits sends decision='modify' + modified_args", async () => {
    const user = userEvent.setup();
    const onResolved = vi.fn();
    resumeRunMock.mockResolvedValue({
      run_id: "r-1",
      thread_id: "t-1",
      status: "running",
      pending_approval: null,
    });
    renderCard(onResolved);
    await user.click(screen.getByTestId("approval-edit"));
    const editor = screen.getByTestId("approval-editor") as HTMLTextAreaElement;
    fireEvent.change(editor, { target: { value: '{"table":"users","limit":10}' } });
    await user.click(screen.getByTestId("approval-approve"));
    await waitFor(() =>
      expect(resumeRunMock).toHaveBeenCalledWith("t-1", "r-1", {
        decision: "modify",
        modified_args: { table: "users", limit: 10 },
      }),
    );
    expect(onResolved).toHaveBeenCalledTimes(1);
  });

  it("Approve is disabled when the buffer is invalid JSON", async () => {
    const user = userEvent.setup();
    renderCard();
    await user.click(screen.getByTestId("approval-edit"));
    const editor = screen.getByTestId("approval-editor") as HTMLTextAreaElement;
    fireEvent.change(editor, { target: { value: "{not valid json" } });
    expect(screen.getByTestId("approval-json-error")).toBeInTheDocument();
    expect(screen.getByTestId("approval-approve")).toBeDisabled();
  });

  it("Reject sends decision='reject' regardless of edit state", async () => {
    const user = userEvent.setup();
    const onResolved = vi.fn();
    resumeRunMock.mockResolvedValue({
      run_id: "r-1",
      thread_id: "t-1",
      status: "cancelled",
      pending_approval: null,
    });
    renderCard(onResolved);
    await user.click(screen.getByTestId("approval-reject"));
    await waitFor(() =>
      expect(resumeRunMock).toHaveBeenCalledWith("t-1", "r-1", { decision: "reject" }),
    );
    expect(onResolved).toHaveBeenCalledTimes(1);
  });
});
