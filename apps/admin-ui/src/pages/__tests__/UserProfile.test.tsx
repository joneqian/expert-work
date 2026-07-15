/**
 * UserProfile tests — the agent-agnostic top-level user page (M2).
 *
 * Covers the header resolving subject_id from the registry, and the memory
 * tab: default-descending sort by importance plus the admin edit/forget
 * mutations threading the surrogate userId. Auth + tenant-scope contexts
 * are mocked to an admin; each pane SDK is stubbed with vi.spyOn.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "antd";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import "../../i18n";

import * as usersSdk from "../../api/users";
import * as agentsSdk from "../../api/agents";
import * as convoSdk from "../../api/conversations";
import * as memorySdk from "../../api/memory";
import { ApiError } from "../../api/client";
import { UserProfile } from "../UserProfile";
import type { MemoryItem } from "../../api/memory";
import type { PurgeSummary } from "../../api/users";

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({
    identity: { isSystemAdmin: false, roles: ["admin"], serverResolved: true },
  }),
}));
vi.mock("../../tenant/TenantScopeContext", () => ({
  SCOPE_ALL: "*",
  useTenantScope: () => ({ scope: undefined, apiTenantScope: undefined }),
}));

const USER_ID = "aaaaaaaa-0000-0000-0000-000000000001";

const HIGH: MemoryItem = {
  id: "m-high",
  tenant_id: "t",
  user_id: USER_ID,
  kind: "fact",
  content: "High importance memory",
  created_at: "2026-06-01T10:00:00Z",
  importance: 0.9,
  confidence: 0.5,
};
const LOW: MemoryItem = {
  id: "m-low",
  tenant_id: "t",
  user_id: USER_ID,
  kind: "episodic",
  content: "Low importance memory",
  created_at: "2026-06-30T10:00:00Z",
  importance: 0.2,
  confidence: 0.9,
};

function stubCommon() {
  vi.spyOn(usersSdk, "getTenantUser").mockResolvedValue({
    user_id: USER_ID,
    subject_id: "ext-alice",
    display_name: "Alice",
    subject_type: "user",
    created_at: "2026-06-01T00:00:00Z",
    last_active_at: "2026-07-01T00:00:00Z",
  });
  vi.spyOn(agentsSdk, "listAgents").mockResolvedValue({
    items: [],
    total: 0,
    cross_tenant: false,
  });
  vi.spyOn(convoSdk, "listConversations").mockResolvedValue({
    items: [],
    total: 0,
    cross_tenant: false,
  });
  vi.spyOn(memorySdk, "listMemories").mockResolvedValue({
    items: [LOW, HIGH],
    total: 2,
    cross_tenant: false,
  });
}

const OK_SUMMARY: PurgeSummary = {
  tenant_id: "t",
  user_id: USER_ID,
  subject_id: "ext-alice",
  threads_purged: 1,
  runs_deleted: 0,
  threads_capped: false,
  deleted: {},
  anonymized: {},
  workspace_marked_deleted: true,
  deactivated: true,
  failures: {},
  ok: true,
};

function renderPage() {
  return render(
    <App>
      <MemoryRouter initialEntries={[`/users/${USER_ID}`]}>
        <Routes>
          <Route path="/users/:userId" element={<UserProfile />} />
          {/* Purge navigates back here on success. */}
          <Route path="/users" element={<div data-testid="users-roster-sentinel" />} />
        </Routes>
      </MemoryRouter>
    </App>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("UserProfile", () => {
  it("resolves and shows subject_id in the header on a direct URL open", async () => {
    stubCommon();
    renderPage();
    // display_name paints the title; subject_id is the copyable identifier.
    expect(await screen.findByText("Alice")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId("user-profile-subject-id")).toHaveTextContent("ext-alice"),
    );
    expect(usersSdk.getTenantUser).toHaveBeenCalledWith(USER_ID);
  });

  it("sorts memory by importance (descending) by default", async () => {
    stubCommon();
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Alice");
    await user.click(screen.getByRole("tab", { name: "Memory" }));

    const table = await screen.findByTestId("user-memory-table");
    // Default descending sort → the high-importance row comes first.
    const rows = within(table).getAllByRole("row");
    // rows[0] is the header; rows[1] is the first data row.
    expect(rows[1]).toHaveTextContent("High importance memory");
  });

  it("edits a memory through the modal, threading the userId", async () => {
    stubCommon();
    vi.spyOn(memorySdk, "updateMemory").mockResolvedValue(HIGH);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Alice");
    await user.click(screen.getByRole("tab", { name: "Memory" }));

    await user.click(await screen.findByTestId(`memory-edit-${HIGH.id}`));
    const textarea = await screen.findByTestId("memory-edit-content");
    await user.clear(textarea);
    await user.type(textarea, "Corrected content");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(memorySdk.updateMemory).toHaveBeenCalledWith(
        HIGH.id,
        expect.objectContaining({ content: "Corrected content", kind: "fact" }),
        USER_ID,
      ),
    );
  });

  it("forgets a memory, threading the userId", async () => {
    stubCommon();
    vi.spyOn(memorySdk, "deleteMemory").mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Alice");
    await user.click(screen.getByRole("tab", { name: "Memory" }));

    await user.click(await screen.findByTestId(`memory-forget-${LOW.id}`));
    // The Popconfirm confirm button (rendered last in a portal).
    const confirms = screen.getAllByRole("button", { name: "Forget" });
    await user.click(confirms[confirms.length - 1]);

    await waitFor(() =>
      expect(memorySdk.deleteMemory).toHaveBeenCalledWith(LOW.id, USER_ID),
    );
  });

  it("purges an external user only after typing the subject_id to confirm", async () => {
    stubCommon();
    const purgeSpy = vi.spyOn(usersSdk, "purgeUser").mockResolvedValue(OK_SUMMARY);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Alice");

    await user.click(screen.getByTestId("user-purge-btn"));
    const ok = await screen.findByTestId("purge-confirm-ok");
    expect(ok).toBeDisabled(); // armed only on an exact subject_id match

    const input = screen.getByTestId("purge-confirm-input");
    await user.type(input, "wrong");
    expect(ok).toBeDisabled();
    await user.clear(input);
    await user.type(input, "ext-alice");
    expect(ok).toBeEnabled();

    await user.click(ok);
    await waitFor(() => expect(purgeSpy).toHaveBeenCalledWith(USER_ID));
    // Success navigates back to the roster.
    expect(await screen.findByTestId("users-roster-sentinel")).toBeInTheDocument();
  });

  it("keeps the modal open on a partial purge so retry stays actionable", async () => {
    stubCommon();
    vi.spyOn(usersSdk, "purgeUser").mockResolvedValue({
      ...OK_SUMMARY,
      ok: false,
      failures: { workspace: "no supervisor client wired" },
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Alice");

    await user.click(screen.getByTestId("user-purge-btn"));
    await user.type(await screen.findByTestId("purge-confirm-input"), "ext-alice");
    await user.click(screen.getByTestId("purge-confirm-ok"));

    await waitFor(() => expect(usersSdk.purgeUser).toHaveBeenCalledWith(USER_ID));
    // Partial failure does not navigate away — the modal stays open to retry.
    expect(screen.queryByTestId("users-roster-sentinel")).not.toBeInTheDocument();
    expect(screen.getByTestId("purge-confirm-input")).toBeInTheDocument();
  });

  it("blocks purging an employee and points to the members page (409)", async () => {
    stubCommon();
    vi.spyOn(usersSdk, "purgeUser").mockRejectedValue(
      new ApiError("member", "CONFLICT", 409),
    );
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Alice");

    await user.click(screen.getByTestId("user-purge-btn"));
    await user.type(await screen.findByTestId("purge-confirm-input"), "ext-alice");
    await user.click(screen.getByTestId("purge-confirm-ok"));

    // A warning modal directs the admin to the members page; no navigation.
    expect(await screen.findByText(/members page/i)).toBeInTheDocument();
    expect(screen.queryByTestId("users-roster-sentinel")).not.toBeInTheDocument();
  });
});
