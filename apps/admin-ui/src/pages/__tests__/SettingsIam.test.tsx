/**
 * Settings IAM tests — Stream H.4 PR 7.
 *
 * SA + RB backends are *enveloped* (``{success, data, error}``) so the
 * adapter mocks deliver enveloped responses.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { App } from "antd";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../i18n";

import { SettingsServiceAccounts } from "../SettingsServiceAccounts";
import { SettingsRoleBindings } from "../SettingsRoleBindings";
import { TenantScopeProvider } from "../../tenant/TenantScopeContext";
import { AuthProvider } from "../../auth/AuthContext";
import { apiClient, setStoredToken } from "../../api/client";

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.`;
}

interface RouteHandler {
  match: (url: string, method: string) => boolean;
  respond: (config: { data?: unknown }) => unknown;
  status?: number;
}

function installAdapter(handlers: RouteHandler[]) {
  apiClient.defaults.adapter = (config) => {
    const url = config.url ?? "";
    const method = (config.method ?? "get").toLowerCase();
    const handler = handlers.find((h) => h.match(url, method));
    return Promise.resolve({
      data: handler?.respond({ data: config.data }) ?? {},
      status: handler?.status ?? 200,
      statusText: "OK",
      headers: {},
      config,
      request: {},
    });
  };
}

function renderSAs(systemAdmin = false) {
  const roles = systemAdmin ? ["admin", "system_admin"] : ["admin"];
  setStoredToken(makeJwt({ sub: "u1", tenant_id: "t1", roles }));
  return render(
    <MemoryRouter>
      <AuthProvider>
        <TenantScopeProvider>
          <App>
            <SettingsServiceAccounts />
          </App>
        </TenantScopeProvider>
      </AuthProvider>
    </MemoryRouter>,
  );
}

function renderRBs(systemAdmin = false) {
  const roles = systemAdmin ? ["admin", "system_admin"] : ["admin"];
  setStoredToken(makeJwt({ sub: "u1", tenant_id: "t1", roles }));
  return render(
    <MemoryRouter>
      <AuthProvider>
        <TenantScopeProvider>
          <App>
            <SettingsRoleBindings />
          </App>
        </TenantScopeProvider>
      </AuthProvider>
    </MemoryRouter>,
  );
}

const saRow = {
  id: "sa-1",
  tenant_id: "t1",
  name: "sa_data_pipeline",
  description: "Data pipeline service account",
  is_active: true,
  created_by: "u1",
  created_at: "2026-05-26T10:00:00Z",
};

const rbRow = {
  id: "rb-1",
  tenant_id: "t1",
  subject_type: "user" as const,
  subject_id: "00000000-0000-0000-0000-000000000001",
  role: "developer" as const,
  platform_scope: false,
  granted_by: "u1",
  granted_at: "2026-05-26T10:00:00Z",
};

const rbPlatform = {
  id: "rb-2",
  tenant_id: null,
  subject_type: "user" as const,
  subject_id: "00000000-0000-0000-0000-000000000002",
  role: "system_admin" as const,
  platform_scope: true,
  granted_by: "u1",
  granted_at: "2026-05-26T10:00:00Z",
};

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("SettingsServiceAccounts", () => {
  it("lists service accounts", async () => {
    installAdapter([
      {
        match: (u) => u === "/v1/service_accounts",
        respond: () => ({
          success: true,
          data: { items: [saRow], total: 1, cross_tenant: false },
          error: null,
        }),
      },
    ]);
    renderSAs();
    await waitFor(() => expect(screen.getByText("sa_data_pipeline")).toBeInTheDocument());
  });

  it("Create modal POSTs name + description", async () => {
    let postBody: unknown = null;
    installAdapter([
      {
        match: (u, m) => u === "/v1/service_accounts" && m === "get",
        respond: () => ({
          success: true,
          data: { items: [], total: 0, cross_tenant: false },
          error: null,
        }),
      },
      {
        match: (u, m) => u === "/v1/service_accounts" && m === "post",
        respond: ({ data }) => {
          postBody = JSON.parse(data as string);
          return { success: true, data: saRow, error: null };
        },
        status: 201,
      },
    ]);
    const user = userEvent.setup();
    renderSAs();
    await waitFor(() => expect(screen.getByTestId("sa-create-btn")).toBeInTheDocument());
    await user.click(screen.getByTestId("sa-create-btn"));
    await waitFor(() => expect(screen.getByTestId("sa-name-input")).toBeInTheDocument());
    await user.type(screen.getByTestId("sa-name-input"), "sa_new");
    await user.click(screen.getByRole("button", { name: /^OK$/ }));
    await waitFor(() => expect((postBody as { name: string }).name).toBe("sa_new"));
  });
});

describe("SettingsRoleBindings", () => {
  it("lists role bindings + shows platform tag", async () => {
    installAdapter([
      {
        match: (u) => u === "/v1/role_bindings",
        respond: () => ({
          success: true,
          data: { items: [rbRow, rbPlatform], total: 2, cross_tenant: false },
          error: null,
        }),
      },
    ]);
    renderRBs(true);
    await waitFor(() => expect(screen.getByText("developer")).toBeInTheDocument());
    expect(screen.getByText("system_admin")).toBeInTheDocument();
    // Platform tag exposed for the platform-scope row.
    expect(screen.getByText("platform")).toBeInTheDocument();
  });

  it("Create drawer hides platform_scope checkbox for non-system_admin", async () => {
    installAdapter([
      {
        match: (u) => u === "/v1/role_bindings",
        respond: () => ({
          success: true,
          data: { items: [], total: 0, cross_tenant: false },
          error: null,
        }),
      },
    ]);
    const user = userEvent.setup();
    renderRBs(false);
    await waitFor(() => expect(screen.getByTestId("rb-create-btn")).toBeInTheDocument());
    await user.click(screen.getByTestId("rb-create-btn"));
    await waitFor(() => expect(screen.getByTestId("rb-subject-id-input")).toBeInTheDocument());
    expect(screen.queryByTestId("rb-platform-scope-checkbox")).toBeNull();
    // Filter chip also hidden.
    expect(screen.queryByTestId("rb-platform-scope-filter")).toBeNull();
  });

  it("renders an ABAC conditions summary on a conditioned binding", async () => {
    const conditioned = {
      ...rbRow,
      id: "rb-9",
      conditions: { resource_ids: ["agent-foo", "agent-bar"], labels: { team: "支持" } },
    };
    installAdapter([
      {
        match: (u) => u === "/v1/role_bindings",
        respond: () => ({
          success: true,
          data: { items: [conditioned], total: 1, cross_tenant: false },
          error: null,
        }),
      },
    ]);
    renderRBs(false);
    await waitFor(() =>
      expect(screen.getByTestId("rb-conditions-rb-9")).toBeInTheDocument(),
    );
    // ids count + label summarised compactly.
    expect(screen.getByTestId("rb-conditions-rb-9").textContent).toContain("ids:2");
    expect(screen.getByTestId("rb-conditions-rb-9").textContent).toContain("team=支持");
  });

  it("Create drawer shows the ABAC conditions editor for tenant bindings", async () => {
    installAdapter([
      {
        match: (u) => u === "/v1/role_bindings",
        respond: () => ({
          success: true,
          data: { items: [], total: 0, cross_tenant: false },
          error: null,
        }),
      },
    ]);
    const user = userEvent.setup();
    renderRBs(false);
    await waitFor(() => expect(screen.getByTestId("rb-create-btn")).toBeInTheDocument());
    await user.click(screen.getByTestId("rb-create-btn"));
    await waitFor(() =>
      expect(screen.getByTestId("rb-conditions-section")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("rb-labels-input")).toBeInTheDocument();
    expect(screen.getByTestId("rb-owner-only-checkbox")).toBeInTheDocument();
  });

  it("Create platform-scope binding requires the type-to-confirm phrase", async () => {
    installAdapter([
      {
        match: (u) => u === "/v1/role_bindings",
        respond: () => ({
          success: true,
          data: { items: [], total: 0, cross_tenant: false },
          error: null,
        }),
      },
    ]);
    const user = userEvent.setup();
    renderRBs(true);
    await user.click(screen.getByTestId("rb-create-btn"));
    await waitFor(() =>
      expect(screen.getByTestId("rb-platform-scope-checkbox")).toBeInTheDocument(),
    );
    // Tick platform_scope — the confirm input appears + warn Alert.
    await user.click(screen.getByTestId("rb-platform-scope-checkbox"));
    await waitFor(() =>
      expect(screen.getByTestId("rb-confirm-input")).toBeInTheDocument(),
    );
  });
});
