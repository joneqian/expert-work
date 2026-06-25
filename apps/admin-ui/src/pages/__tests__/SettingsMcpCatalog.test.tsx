/**
 * MCP Catalog UI tests — Stream W.
 *
 * Covers the platform catalog page (admin gate + table), the platform-server
 * config drawer (edit mode), and the tenant catalog browser (entitlement lock +
 * enable toggle + oauth authorize).
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { App } from "antd";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../i18n";

import { SettingsMcpCatalog } from "../SettingsMcpCatalog";
import { CatalogBrowser } from "../../components/mcp_catalog/CatalogBrowser";
import { CatalogEntryDrawer } from "../../components/mcp_catalog/CatalogEntryDrawer";
import { AuthProvider } from "../../auth/AuthContext";
import { apiClient, setStoredToken } from "../../api/client";
import type { TenantCatalogEntry } from "../../api/mcp-catalog";

const TENANT = "00000000-0000-0000-0000-00000000acme";

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

const ENTRY = {
  id: "cat-1",
  name: "github",
  display_name: "GitHub",
  description: "GitHub MCP connector",
  category: "dev-tools",
  icon: "",
  transport: "sse" as const,
  url_template: "https://mcp.github.com/sse",
  auth_type: "bearer" as const,
  required_tier: "pro" as const,
  enabled: true,
  created_at: "2026-05-01T10:00:00Z",
  updated_at: "2026-05-01T10:00:00Z",
  updated_by: "u1",
};

function renderCatalog(roles: string[]) {
  setStoredToken(makeJwt({ sub: "u1", tenant_id: TENANT, roles }));
  return render(
    <MemoryRouter>
      <AuthProvider>
        <App>
          <SettingsMcpCatalog />
        </App>
      </AuthProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("SettingsMcpCatalog page", () => {
  it("non-system-admin sees the admin-only notice, no table", async () => {
    installAdapter([
      {
        match: (u) => u.endsWith("/mcp-catalog"),
        respond: () => ({ success: true, data: [], error: null }),
      },
    ]);
    renderCatalog(["admin"]);
    await waitFor(() =>
      expect(screen.getByTestId("cat-not-admin")).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("cat-table")).not.toBeInTheDocument();
  });

  it("system_admin sees the catalog table with connector rows", async () => {
    installAdapter([
      {
        match: (u) => u.endsWith("/mcp-catalog"),
        respond: () => ({ success: true, data: [ENTRY], error: null }),
      },
    ]);
    renderCatalog(["system_admin"]);
    await waitFor(() =>
      expect(screen.getByTestId("cat-table")).toBeInTheDocument(),
    );
    expect(screen.getByText("GitHub")).toBeInTheDocument();
    expect(screen.getByTestId("cat-toggle-github")).toBeInTheDocument();
    expect(screen.getByTestId("cat-edit-github")).toBeInTheDocument();
  });

  it("opening New connector reveals the tabbed form", async () => {
    installAdapter([
      {
        match: (u) => u.endsWith("/mcp-catalog"),
        respond: () => ({ success: true, data: [], error: null }),
      },
    ]);
    const user = userEvent.setup();
    renderCatalog(["system_admin"]);
    await waitFor(() =>
      expect(screen.getByTestId("cat-add")).toBeInTheDocument(),
    );
    await user.click(screen.getByTestId("cat-add"));
    await waitFor(() =>
      expect(screen.getByTestId("cce-form")).toBeInTheDocument(),
    );
    // Tabs force-render: basic (name/url) + auth (auth_type) fields are present.
    expect(screen.getByTestId("cce-name")).toBeInTheDocument();
    expect(screen.getByTestId("cce-auth")).toBeInTheDocument();
  });
});

describe("CatalogEntryDrawer edit mode", () => {
  it("disables the immutable name/transport/auth_type selects when editing", async () => {
    render(
      <App>
        <CatalogEntryDrawer
          open
          onClose={() => {}}
          onSaved={() => {}}
          editing={ENTRY}
        />
      </App>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("cce-form")).toBeInTheDocument(),
    );
    // auth_type Select is immutable post-create (backend patch omits it).
    const authSelect = screen.getByTestId("cce-auth");
    expect(authSelect.className).toContain("ant-select-disabled");
    // name input is disabled too (sanity: confirm we matched the right gate).
    expect(screen.getByTestId("cce-name")).toBeDisabled();
    const transportSelect = screen.getByTestId("cce-transport");
    expect(transportSelect.className).toContain("ant-select-disabled");
  });

  it("PATCH body omits immutable auth_type / auth_schema / name; keeps token blank", async () => {
    let captured: { method?: string; body?: unknown } = {};
    apiClient.defaults.adapter = (config) => {
      captured = { method: config.method, body: config.data };
      return Promise.resolve({
        data: { success: true, data: { ...ENTRY }, error: null },
        status: 200,
        statusText: "OK",
        headers: {},
        config,
        request: {},
      });
    };
    const onSaved = vi.fn();
    const user = userEvent.setup();
    render(
      <App>
        <CatalogEntryDrawer
          open
          onClose={() => {}}
          onSaved={onSaved}
          editing={ENTRY}
        />
      </App>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("cce-form")).toBeInTheDocument(),
    );
    // Submit without re-pasting the bearer token (blank = keep stored one).
    await user.click(screen.getByTestId("cce-submit"));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(captured.method).toBe("patch");
    const body = JSON.parse(captured.body as string) as Record<string, unknown>;
    // Immutable / dropped fields never appear in the patch.
    expect(body.auth_type).toBeUndefined();
    expect(body.auth_schema).toBeUndefined();
    expect(body.name).toBeUndefined();
    // Token not re-pasted → not sent (write-only, blank-to-keep).
    expect(body.bearer_token).toBeUndefined();
  });

  it("create mode renders category select, icon upload, and timeout fields", async () => {
    render(
      <App>
        <CatalogEntryDrawer
          open
          onClose={() => {}}
          onSaved={() => {}}
          editing={null}
        />
      </App>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("cce-form")).toBeInTheDocument(),
    );
    // #2 category dropdown / #3 icon upload / #4 timeout + SSE-read timeout.
    expect(screen.getByTestId("cce-category")).toBeInTheDocument();
    expect(screen.getByTestId("cce-icon-upload")).toBeInTheDocument();
    expect(screen.getByTestId("cce-timeout")).toBeInTheDocument();
    expect(screen.getByTestId("cce-sse-timeout")).toBeInTheDocument();
  });
});

describe("CatalogBrowser entitlement lock", () => {
  function makeEntry(over: Partial<TenantCatalogEntry>): TenantCatalogEntry {
    return { ...ENTRY, entitled: true, tenant_enabled: false, ...over };
  }

  const noop = {
    onToggleEnable: async () => {},
    onAuthorize: () => {},
  };

  it("entitled entry exposes an enable toggle", () => {
    render(
      <App>
        <CatalogBrowser
          entries={[makeEntry({ id: "e1", name: "ok", entitled: true })]}
          loading={false}
          error={null}
          {...noop}
        />
      </App>,
    );
    expect(screen.getByTestId("cb-toggle-ok")).toBeEnabled();
  });

  it("non-entitled entry shows a lock badge instead of a toggle", () => {
    render(
      <App>
        <CatalogBrowser
          entries={[
            makeEntry({
              id: "e2",
              name: "locked",
              entitled: false,
              required_tier: "enterprise",
            }),
          ]}
          loading={false}
          error={null}
          {...noop}
        />
      </App>,
    );
    expect(screen.getByTestId("cb-locked-locked")).toBeDisabled();
    expect(screen.queryByTestId("cb-toggle-locked")).not.toBeInTheDocument();
  });

  it("oauth2 entry surfaces Authorize only once enabled", () => {
    const { rerender } = render(
      <App>
        <CatalogBrowser
          entries={[
            makeEntry({
              id: "e3",
              name: "lin",
              auth_type: "oauth2",
              tenant_enabled: false,
            }),
          ]}
          loading={false}
          error={null}
          {...noop}
        />
      </App>,
    );
    expect(screen.queryByTestId("cb-authorize-lin")).not.toBeInTheDocument();
    rerender(
      <App>
        <CatalogBrowser
          entries={[
            makeEntry({
              id: "e3",
              name: "lin",
              auth_type: "oauth2",
              tenant_enabled: true,
            }),
          ]}
          loading={false}
          error={null}
          {...noop}
        />
      </App>,
    );
    expect(screen.getByTestId("cb-authorize-lin")).toBeInTheDocument();
  });
});
