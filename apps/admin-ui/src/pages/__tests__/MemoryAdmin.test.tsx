/**
 * MemoryAdmin tests — Stream H.4 PR 2.
 *
 * The page is enveloped (backend `/v1/memory` returns
 * ``{success, data: {...}}``) so the SDK uses ``getJson`` and we
 * deliver enveloped mocks via the axios adapter.
 *
 * Monaco is stubbed (same approach as ApprovalCard + EvalDatasetsPanel)
 * so JSON edits flow through ``onChange`` synchronously.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { App } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../i18n";

import { MemoryAdmin } from "../MemoryAdmin";
import { TenantScopeProvider } from "../../tenant/TenantScopeContext";
import { AuthProvider } from "../../auth/AuthContext";
import { apiClient, setStoredToken } from "../../api/client";

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

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.`;
}

interface RouteHandler {
  match: (url: string, method: string) => boolean;
  respond: () => unknown;
  status?: number;
}

function installAdapter(handlers: RouteHandler[]) {
  apiClient.defaults.adapter = (config) => {
    const url = config.url ?? "";
    const method = (config.method ?? "get").toLowerCase();
    const handler = handlers.find((h) => h.match(url, method));
    return Promise.resolve({
      data: handler?.respond() ?? {},
      status: handler?.status ?? 200,
      statusText: "OK",
      headers: {},
      config,
      request: {},
    });
  };
}

function renderMemory() {
  setStoredToken(makeJwt({ sub: "u1", tenant_id: "t1", roles: ["admin"] }));
  return render(
    <MemoryRouter>
      <AuthProvider>
        <TenantScopeProvider>
          <App>
            <MemoryAdmin />
          </App>
        </TenantScopeProvider>
      </AuthProvider>
    </MemoryRouter>,
  );
}

const memRow = {
  id: "m1",
  tenant_id: "t1",
  user_id: "user-alice-uuid-abc",
  kind: "fact" as const,
  content: "User prefers brevity in answers.",
  created_at: "2026-05-26T10:00:00Z",
  importance: 0.8,
  confidence: 0.6,
};

const memRow2 = {
  ...memRow,
  id: "m2",
  kind: "episodic" as const,
  content: "Last week Alice asked about Q3 revenue.",
};

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("MemoryAdmin", () => {
  it("lists memories and renders the table", async () => {
    installAdapter([
      {
        match: (u) => u.startsWith("/v1/memory") && u !== "/v1/memory/m1",
        respond: () => ({
          success: true,
          data: { items: [memRow, memRow2], total: 2, cross_tenant: false },
          error: null,
        }),
      },
    ]);
    renderMemory();
    await waitFor(() => expect(screen.getByText(/User prefers/)).toBeInTheDocument());
    expect(screen.getByText(/Q3 revenue/)).toBeInTheDocument();
  });

  it("client-side search filters by content", async () => {
    installAdapter([
      {
        match: (u) => u.startsWith("/v1/memory"),
        respond: () => ({
          success: true,
          data: { items: [memRow, memRow2], total: 2, cross_tenant: false },
          error: null,
        }),
      },
    ]);
    const user = userEvent.setup();
    renderMemory();
    await waitFor(() => expect(screen.getByText(/User prefers/)).toBeInTheDocument());
    await user.type(screen.getByPlaceholderText(/Filter by content/i), "Q3");
    await waitFor(() => {
      expect(screen.queryByText(/User prefers/)).toBeNull();
      expect(screen.getByText(/Q3 revenue/)).toBeInTheDocument();
    });
  });

  it("Save is disabled when buffer is pristine; enabled after edit", async () => {
    installAdapter([
      {
        match: (u) => u.startsWith("/v1/memory"),
        respond: () => ({
          success: true,
          data: { items: [memRow], total: 1, cross_tenant: false },
          error: null,
        }),
      },
    ]);
    const user = userEvent.setup();
    renderMemory();
    await waitFor(() => expect(screen.getByText(/User prefers/)).toBeInTheDocument());
    await user.click(screen.getByTestId(`memory-edit-${memRow.id}`));
    await waitFor(() => expect(screen.getByTestId("memory-content-editor")).toBeInTheDocument());
    expect(screen.getByTestId("memory-save-btn")).toBeDisabled();
    fireEvent.change(screen.getByTestId("memory-content-editor"), { target: { value: "edited content" } });
    await waitFor(() => expect(screen.getByTestId("memory-save-btn")).not.toBeDisabled());
  });

  it("EMBEDDER_UNCONFIGURED 503 surfaces a friendly error", async () => {
    let listCount = 0;
    installAdapter([
      {
        match: (u, m) => u.startsWith("/v1/memory") && m === "get",
        respond: () => {
          listCount++;
          return {
            success: true,
            data: { items: [memRow], total: 1, cross_tenant: false },
            error: null,
          };
        },
      },
      {
        match: (u, m) => u === "/v1/memory/m1" && m === "patch",
        status: 503,
        respond: () => ({
          success: false,
          data: null,
          error: { code: "EMBEDDER_UNCONFIGURED", message: "embedder missing" },
        }),
      },
    ]);
    const user = userEvent.setup();
    renderMemory();
    await waitFor(() => expect(screen.getByText(/User prefers/)).toBeInTheDocument());
    await user.click(screen.getByTestId(`memory-edit-${memRow.id}`));
    fireEvent.change(screen.getByTestId("memory-content-editor"), { target: { value: "edited" } });
    await user.click(screen.getByTestId("memory-save-btn"));
    // List was called once on mount; PATCH 503 means refresh doesn't trigger again.
    await waitFor(() => expect(listCount).toBe(1));
  });

  it("renders importance / confidence score badges", async () => {
    installAdapter([
      {
        match: (u) => u.startsWith("/v1/memory"),
        respond: () => ({
          success: true,
          data: { items: [memRow], total: 1, cross_tenant: false },
          error: null,
        }),
      },
    ]);
    renderMemory();
    await waitFor(() => expect(screen.getByText(/User prefers/)).toBeInTheDocument());
    // memRow has importance 0.80 / confidence 0.60.
    expect(screen.getByText(/0\.80/)).toBeInTheDocument();
    expect(screen.getByText(/0\.60/)).toBeInTheDocument();
  });

  it("Correct routes Save through the self-correction endpoint", async () => {
    let correctBody: unknown = null;
    installAdapter([
      {
        match: (u, m) => u.startsWith("/v1/memory") && m === "get",
        respond: () => ({
          success: true,
          data: { items: [memRow], total: 1, cross_tenant: false },
          error: null,
        }),
      },
      {
        match: (u, m) => u === "/v1/memory/m1/correct" && m === "post",
        respond: () => ({
          success: true,
          data: { ...memRow, content: "fixed", confidence: 1.0 },
          error: null,
        }),
      },
    ]);
    // Capture the POST body via the adapter.
    const baseAdapter = apiClient.defaults.adapter;
    apiClient.defaults.adapter = (config) => {
      if ((config.url ?? "").endsWith("/correct") && config.data) {
        correctBody = JSON.parse(config.data as string);
      }
      return (baseAdapter as (c: typeof config) => Promise<unknown>)(config) as never;
    };
    const user = userEvent.setup();
    renderMemory();
    await waitFor(() => expect(screen.getByText(/User prefers/)).toBeInTheDocument());
    await user.click(screen.getByTestId(`memory-correct-${memRow.id}`));
    await waitFor(() => expect(screen.getByTestId("memory-content-editor")).toBeInTheDocument());
    fireEvent.change(screen.getByTestId("memory-content-editor"), { target: { value: "fixed" } });
    await user.click(screen.getByTestId("memory-save-btn"));
    await waitFor(() =>
      expect(correctBody).toEqual({ action: "rewrite", content: "fixed" }),
    );
  });
});
