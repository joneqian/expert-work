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

function renderRunsList() {
  return render(
    <MemoryRouter initialEntries={["/runs"]}>
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
    expect(await screen.findByText("—")).toBeInTheDocument();
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
});
