/**
 * RunsList page tests — Stream H.3 PR 1.
 *
 * Stubs ``listRuns`` so the cross-tenant scope, status filter, and
 * error states are exercised in isolation. The shared axios stub
 * adapter from ``src/test/setup.ts`` keeps any forgotten network call
 * from hitting the wire.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import "../../i18n";

import { ApiError } from "../../api/client";
import * as runsSdk from "../../api/runs";
import { AuthProvider } from "../../auth/AuthContext";
import { TenantScopeProvider } from "../../tenant/TenantScopeContext";
import { setStoredToken } from "../../api/client";
import { RunsList } from "../RunsList";
import type { RunList } from "../../api/runs";

const listRunsMock = vi.spyOn(runsSdk, "listRuns");

function jwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.`;
}

beforeEach(() => {
  setStoredToken(
    jwt({ sub: "u", tenant_id: "11111111-1111-1111-1111-111111111111", roles: ["admin"] }),
  );
  listRunsMock.mockReset();
});

afterEach(() => {
  setStoredToken(null);
  vi.clearAllMocks();
});

function renderRunsList(entry = "/runs") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <AuthProvider>
        <TenantScopeProvider>
          <RunsList />
        </TenantScopeProvider>
      </AuthProvider>
    </MemoryRouter>,
  );
}

const sampleRow: RunList["items"][0] = {
  run_id: "11111111-1111-1111-1111-111111111111",
  tenant_id: "22222222-2222-2222-2222-222222222222",
  thread_id: "33333333-3333-3333-3333-333333333333",
  user_id: null,
  status: "success",
  is_resume: false,
  error: null,
  agent_name: "customer-support-bot",
  agent_version: "3.4.2",
  created_at: "2026-05-26T08:00:00Z",
  updated_at: "2026-05-26T08:00:32Z",
  finished_at: "2026-05-26T08:00:32Z",
  trace_id: "cafef00d".repeat(4),
};

describe("RunsList", () => {
  it("renders rows with agent name + version", async () => {
    listRunsMock.mockResolvedValue({
      items: [sampleRow],
      total: 1,
      cross_tenant: false,
    });
    renderRunsList();
    await waitFor(() => expect(listRunsMock).toHaveBeenCalled());
    expect(await screen.findByText("customer-support-bot")).toBeInTheDocument();
    expect(screen.getByText("v3.4.2")).toBeInTheDocument();
  });

  it("falls back to em-dash when agent_name is null (thread deleted)", async () => {
    listRunsMock.mockResolvedValue({
      items: [{ ...sampleRow, agent_name: null, agent_version: null }],
      total: 1,
      cross_tenant: false,
    });
    renderRunsList();
    await waitFor(() => expect(listRunsMock).toHaveBeenCalled());
    // Agent + tokens columns both em-dash when empty; assert the fallback renders.
    expect((await screen.findAllByText("—")).length).toBeGreaterThan(0);
  });

  it("shows the cross-tenant banner when the response flag is true", async () => {
    listRunsMock.mockResolvedValue({
      items: [sampleRow],
      total: 1,
      cross_tenant: true,
    });
    renderRunsList();
    expect(await screen.findByTestId("cross-tenant-banner")).toBeInTheDocument();
  });

  it("first listRuns call uses no status filter", async () => {
    listRunsMock.mockResolvedValue({ items: [], total: 0, cross_tenant: false });
    renderRunsList();
    await waitFor(() => expect(listRunsMock).toHaveBeenCalledTimes(1));
    expect(listRunsMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ status: undefined }),
    );
    // The Antd Select renders into a portal; jsdom can't reliably click
    // through its virtual list, so we assert the testid exists at the
    // surface level. The status → SDK wiring itself is covered by the
    // initial-call assertion above (and by the page's lifecycle: the
    // effect depends on ``statusFilter``, so a re-render with a new
    // status would refetch).
    expect(screen.getByTestId("runs-status-filter")).toBeInTheDocument();
  });

  it("renders error Alert when listRuns rejects", async () => {
    listRunsMock.mockRejectedValue(new ApiError("DB down", "DB_DOWN", 500));
    renderRunsList();
    expect(await screen.findByTestId("runs-error")).toHaveTextContent("DB_DOWN");
  });

  it("renders duration and compact token totals", async () => {
    listRunsMock.mockResolvedValue({
      items: [
        {
          ...sampleRow,
          tokens: {
            input_tokens: 1200,
            output_tokens: 300,
            cache_creation_tokens: 0,
            cache_read_tokens: 0,
            total_tokens: 1500,
            llm_calls: 3,
            models: ["m1"],
          },
        },
      ],
      total: 1,
      cross_tenant: false,
    });
    renderRunsList();
    // created 08:00:00 → finished 08:00:32 = 32s.
    expect(await screen.findByText("32s")).toBeInTheDocument();
    expect(screen.getByTestId(`run-tokens-${sampleRow.run_id}`)).toHaveTextContent("1.5k");
  });

  it("surfaces an error indicator for failed runs", async () => {
    listRunsMock.mockResolvedValue({
      items: [{ ...sampleRow, status: "error", error: "boom exploded" }],
      total: 1,
      cross_tenant: false,
    });
    renderRunsList();
    expect(
      await screen.findByTestId(`run-error-${sampleRow.run_id}`),
    ).toBeInTheDocument();
  });

  it("debounces the search box into the q param", async () => {
    const user = userEvent.setup();
    listRunsMock.mockResolvedValue({ items: [], total: 0, cross_tenant: false });
    renderRunsList();
    await waitFor(() => expect(listRunsMock).toHaveBeenCalled());
    await user.type(screen.getByRole("textbox"), "abc123");
    await waitFor(() =>
      expect(listRunsMock).toHaveBeenCalledWith(expect.objectContaining({ q: "abc123" })),
    );
  });

  it("applies ?user_id from the URL and shows a clearable filter chip", async () => {
    const uid = "99999999-9999-9999-9999-999999999999";
    listRunsMock.mockResolvedValue({ items: [], total: 0, cross_tenant: false });
    renderRunsList(`/runs?user_id=${uid}`);
    await waitFor(() =>
      expect(listRunsMock).toHaveBeenCalledWith(expect.objectContaining({ userId: uid })),
    );
    expect(screen.getByTestId("runs-user-filter-chip")).toBeInTheDocument();
  });

  it("clicking a run's user filters the list to that user", async () => {
    const uid = "77777777-7777-7777-7777-777777777777";
    listRunsMock.mockResolvedValue({
      items: [{ ...sampleRow, user_id: uid }],
      total: 1,
      cross_tenant: false,
    });
    const user = userEvent.setup();
    renderRunsList();
    await user.click(await screen.findByTestId(`run-user-${uid}`));
    await waitFor(() =>
      expect(listRunsMock).toHaveBeenCalledWith(expect.objectContaining({ userId: uid })),
    );
  });
});
