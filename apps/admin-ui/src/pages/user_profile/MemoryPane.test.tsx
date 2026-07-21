/**
 * MemoryPane tests — P5b-2c T4: source-run column + jump-to-run.
 *
 * Focused unit test for the tenant-admin memory governance pane in
 * isolation (UserProfile.test.tsx already covers the pane end-to-end
 * through the Memory tab). ``useNavigate`` is mocked directly since the
 * pane needs no other router primitives.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "antd";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../i18n";

const mockNavigate = vi.fn();
vi.mock("react-router-dom", () => ({
  useNavigate: () => mockNavigate,
}));

import * as memorySdk from "../../api/memory";
import type { MemoryItem } from "../../api/memory";
import { MemoryPane } from "./MemoryPane";

const USER_ID = "aaaaaaaa-0000-0000-0000-000000000001";

const WITH_SOURCE: MemoryItem = {
  id: "m-src",
  tenant_id: "t",
  user_id: USER_ID,
  kind: "fact",
  content: "Memory with a source run",
  created_at: "2026-06-01T10:00:00Z",
  importance: 0.9,
  confidence: 0.5,
  source_thread_id: "thread-123",
  source_run_id: "run-456",
};

const NO_SOURCE: MemoryItem = {
  id: "m-nosrc",
  tenant_id: "t",
  user_id: USER_ID,
  kind: "episodic",
  content: "Memory without a source run",
  created_at: "2026-06-02T10:00:00Z",
  importance: 0.2,
  confidence: 0.9,
  source_thread_id: null,
  source_run_id: null,
};

function renderPane() {
  return render(
    <App>
      <MemoryPane userId={USER_ID} />
    </App>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("MemoryPane — source-run column", () => {
  it("navigates to the source run when both source ids are present", async () => {
    vi.spyOn(memorySdk, "listMemories").mockResolvedValue({
      items: [WITH_SOURCE],
      total: 1,
      cross_tenant: false,
    });
    const user = userEvent.setup();
    renderPane();

    const link = await screen.findByTestId(`memory-source-run-${WITH_SOURCE.id}`);
    await user.click(link);

    expect(mockNavigate).toHaveBeenCalledWith("/runs/thread-123/run-456");
  });

  it("renders no source link when source ids are null", async () => {
    vi.spyOn(memorySdk, "listMemories").mockResolvedValue({
      items: [NO_SOURCE],
      total: 1,
      cross_tenant: false,
    });
    renderPane();

    await screen.findByText(NO_SOURCE.content);
    expect(
      screen.queryByTestId(`memory-source-run-${NO_SOURCE.id}`),
    ).not.toBeInTheDocument();
  });
});
