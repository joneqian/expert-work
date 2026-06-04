/**
 * MCP Catalog UI tests — Stream W.
 *
 * Covers the platform catalog page (admin gate + table), the catalog browser
 * entitlement lock, the auth_schema field builder (add/remove), and the
 * bearer-one-secret client guard.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { useState } from "react";
import { MemoryRouter } from "react-router-dom";
import { App } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../i18n";

import { SettingsMcpCatalog } from "../SettingsMcpCatalog";
import { CatalogBrowser } from "../../components/mcp_catalog/CatalogBrowser";
import { CatalogEntryDrawer } from "../../components/mcp_catalog/CatalogEntryDrawer";
import { InstantiateCatalogForm } from "../../components/mcp_catalog/InstantiateCatalogForm";
import { AuthSchemaBuilder } from "../../components/mcp_catalog/AuthSchemaBuilder";
import { validateAuthSchemaSecrets } from "../../components/mcp_catalog/validation";
import { AuthProvider } from "../../auth/AuthContext";
import { apiClient, setStoredToken } from "../../api/client";
import type { McpCatalogAuthField, TenantCatalogEntry } from "../../api/mcp-catalog";

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
  auth_schema: { fields: [{ key: "token", label: "Token", kind: "secret" as const, required: true }] },
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
      { match: (u) => u.endsWith("/mcp-catalog"), respond: () => ({ success: true, data: [], error: null }) },
    ]);
    renderCatalog(["admin"]);
    await waitFor(() => expect(screen.getByTestId("cat-not-admin")).toBeInTheDocument());
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
    await waitFor(() => expect(screen.getByTestId("cat-table")).toBeInTheDocument());
    expect(screen.getByText("GitHub")).toBeInTheDocument();
    expect(screen.getByTestId("cat-toggle-github")).toBeInTheDocument();
    expect(screen.getByTestId("cat-edit-github")).toBeInTheDocument();
  });

  it("opening New connector reveals the field builder + form", async () => {
    installAdapter([
      { match: (u) => u.endsWith("/mcp-catalog"), respond: () => ({ success: true, data: [], error: null }) },
    ]);
    const user = userEvent.setup();
    renderCatalog(["system_admin"]);
    await waitFor(() => expect(screen.getByTestId("cat-add")).toBeInTheDocument());
    await user.click(screen.getByTestId("cat-add"));
    await waitFor(() => expect(screen.getByTestId("cce-form")).toBeInTheDocument());
    expect(screen.getByTestId("asb-add")).toBeInTheDocument();
  });
});

describe("CatalogEntryDrawer edit mode", () => {
  it("disables the immutable name/transport/auth_type selects when editing", async () => {
    render(
      <App>
        <CatalogEntryDrawer open onClose={() => {}} onSaved={() => {}} editing={ENTRY} />
      </App>,
    );
    await waitFor(() => expect(screen.getByTestId("cce-form")).toBeInTheDocument());
    // auth_type Select is immutable post-create (backend patch omits it).
    const authSelect = screen.getByTestId("cce-auth");
    expect(authSelect.className).toContain("ant-select-disabled");
    // name input is disabled too (sanity: confirm we matched the right gate).
    expect(screen.getByTestId("cce-name")).toBeDisabled();
    const transportSelect = screen.getByTestId("cce-transport");
    expect(transportSelect.className).toContain("ant-select-disabled");
  });

  it("does not send auth_type or _uid in the PATCH body", async () => {
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
        <CatalogEntryDrawer open onClose={() => {}} onSaved={onSaved} editing={ENTRY} />
      </App>,
    );
    await waitFor(() => expect(screen.getByTestId("cce-form")).toBeInTheDocument());
    // Append a brand-new builder row (gets an internal _uid) then fill it so
    // the bearer-one-secret guard still passes (ENTRY already has one secret;
    // make the new one a param).
    await user.click(screen.getByTestId("asb-add"));
    await waitFor(() => expect(screen.getByTestId("asb-row-1")).toBeInTheDocument());
    fireEvent.change(screen.getByTestId("asb-key-1"), { target: { value: "workspace" } });
    fireEvent.change(screen.getByTestId("asb-label-1"), { target: { value: "Workspace" } });
    await user.click(screen.getByTestId("cce-submit"));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(captured.method).toBe("patch");
    const body = JSON.parse(captured.body as string) as {
      auth_type?: unknown;
      auth_schema: { fields: Record<string, unknown>[] };
    };
    expect(body.auth_type).toBeUndefined();
    for (const field of body.auth_schema.fields) {
      expect(field).not.toHaveProperty("_uid");
    }
  });
});

describe("CatalogBrowser entitlement lock", () => {
  function makeEntry(over: Partial<TenantCatalogEntry>): TenantCatalogEntry {
    return { ...ENTRY, entitled: true, ...over };
  }

  it("entitled entry exposes an enabled select CTA", () => {
    render(
      <App>
        <CatalogBrowser
          entries={[makeEntry({ id: "e1", name: "ok", entitled: true })]}
          loading={false}
          error={null}
          onSelect={() => {}}
        />
      </App>,
    );
    expect(screen.getByTestId("cb-select-ok")).toBeEnabled();
  });

  it("non-entitled entry disables the CTA (lock badge)", () => {
    render(
      <App>
        <CatalogBrowser
          entries={[makeEntry({ id: "e2", name: "locked", entitled: false, required_tier: "enterprise" })]}
          loading={false}
          error={null}
          onSelect={() => {}}
        />
      </App>,
    );
    expect(screen.getByTestId("cb-locked-locked")).toBeDisabled();
    expect(screen.queryByTestId("cb-select-locked")).not.toBeInTheDocument();
  });
});

describe("AuthSchemaBuilder", () => {
  it("add appends a row and remove drops it", async () => {
    const user = userEvent.setup();
    let current: McpCatalogAuthField[] = [];
    function Harness() {
      const [fields, setFields] = useState<McpCatalogAuthField[]>([]);
      current = fields;
      return <AuthSchemaBuilder value={fields} onChange={setFields} />;
    }
    render(
      <App>
        <Harness />
      </App>,
    );
    await user.click(screen.getByTestId("asb-add"));
    await waitFor(() => expect(screen.getByTestId("asb-row-0")).toBeInTheDocument());
    expect(current).toHaveLength(1);
    await user.click(screen.getByTestId("asb-remove-0"));
    await waitFor(() => expect(screen.queryByTestId("asb-row-0")).not.toBeInTheDocument());
    expect(current).toHaveLength(0);
  });

  it("editing the key field flows through onChange", async () => {
    let current: McpCatalogAuthField[] = [
      { key: "", label: "", kind: "param", required: true },
    ];
    const onChange = vi.fn((next: McpCatalogAuthField[]) => {
      current = next;
    });
    render(
      <App>
        <AuthSchemaBuilder value={current} onChange={onChange} />
      </App>,
    );
    fireEvent.change(screen.getByTestId("asb-key-0"), { target: { value: "workspace" } });
    expect(onChange).toHaveBeenCalled();
    expect(current[0].key).toBe("workspace");
  });
});

describe("InstantiateCatalogForm", () => {
  const entry: TenantCatalogEntry = {
    ...ENTRY,
    entitled: true,
    auth_schema: {
      fields: [
        { key: "workspace", label: "Workspace", kind: "param", required: true },
        { key: "token", label: "API Token", kind: "secret", required: true },
      ],
    },
  };

  it("renders an input per auth_schema field (param=text, secret=password)", () => {
    render(
      <App>
        <InstantiateCatalogForm entry={entry} onCreated={() => {}} onBack={() => {}} />
      </App>,
    );
    expect(screen.getByTestId("icf-field-workspace")).toBeInTheDocument();
    const secret = screen.getByTestId("icf-field-token");
    expect(secret).toHaveAttribute("type", "password");
  });

  it("submitting calls instantiate with split params/secrets", async () => {
    let captured: { url?: string; body?: unknown } = {};
    apiClient.defaults.adapter = (config) => {
      captured = { url: config.url, body: config.data };
      return Promise.resolve({
        data: { success: true, data: { ...ENTRY, name: "github", url: "https://x" }, error: null },
        status: 201,
        statusText: "Created",
        headers: {},
        config,
        request: {},
      });
    };
    const onCreated = vi.fn();
    const user = userEvent.setup();
    render(
      <App>
        <InstantiateCatalogForm entry={entry} onCreated={onCreated} onBack={() => {}} />
      </App>,
    );
    await user.type(screen.getByTestId("icf-field-workspace"), "acme");
    await user.type(screen.getByTestId("icf-field-token"), "secret-xyz");
    await user.click(screen.getByTestId("icf-create"));
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(captured.url).toContain("/mcp-servers/catalog/cat-1/instances");
    const body = JSON.parse(captured.body as string) as {
      params: Record<string, string>;
      secrets: Record<string, string>;
    };
    expect(body.params.workspace).toBe("acme");
    expect(body.secrets.token).toBe("secret-xyz");
  });

  it("omits an unfilled optional param instead of sending an empty string", async () => {
    const optionalEntry: TenantCatalogEntry = {
      ...ENTRY,
      entitled: true,
      auth_schema: {
        fields: [
          { key: "workspace", label: "Workspace", kind: "param", required: false },
          { key: "token", label: "API Token", kind: "secret", required: true },
        ],
      },
    };
    let captured: { body?: unknown } = {};
    apiClient.defaults.adapter = (config) => {
      captured = { body: config.data };
      return Promise.resolve({
        data: { success: true, data: { ...ENTRY, name: "github", url: "https://x" }, error: null },
        status: 201,
        statusText: "Created",
        headers: {},
        config,
        request: {},
      });
    };
    const onCreated = vi.fn();
    const user = userEvent.setup();
    render(
      <App>
        <InstantiateCatalogForm entry={optionalEntry} onCreated={onCreated} onBack={() => {}} />
      </App>,
    );
    // Leave the optional "workspace" param blank; only fill the secret.
    await user.type(screen.getByTestId("icf-field-token"), "secret-xyz");
    await user.click(screen.getByTestId("icf-create"));
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    const body = JSON.parse(captured.body as string) as {
      params: Record<string, string>;
      secrets: Record<string, string>;
    };
    expect(body.params).not.toHaveProperty("workspace");
    expect(body.secrets.token).toBe("secret-xyz");
  });
});

describe("validateAuthSchemaSecrets guard", () => {
  it("bearer requires exactly one secret", () => {
    expect(validateAuthSchemaSecrets("bearer", [])).toBe("mcp_catalog.guard_bearer_one_secret");
    expect(
      validateAuthSchemaSecrets("bearer", [
        { key: "a", label: "A", kind: "secret", required: true },
        { key: "b", label: "B", kind: "secret", required: true },
      ]),
    ).toBe("mcp_catalog.guard_bearer_one_secret");
    expect(
      validateAuthSchemaSecrets("bearer", [{ key: "a", label: "A", kind: "secret", required: true }]),
    ).toBeNull();
  });

  it("none forbids any secret", () => {
    expect(
      validateAuthSchemaSecrets("none", [{ key: "a", label: "A", kind: "secret", required: true }]),
    ).toBe("mcp_catalog.guard_none_zero_secret");
    expect(
      validateAuthSchemaSecrets("none", [{ key: "a", label: "A", kind: "param", required: true }]),
    ).toBeNull();
  });
});
