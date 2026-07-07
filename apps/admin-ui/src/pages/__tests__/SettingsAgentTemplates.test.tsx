/**
 * Platform Agent Templates UI tests — Stream Agent-Templates (M1-6).
 *
 * Covers the system_admin gate (non-admin → notice, no table) and the admin
 * table render. Mirrors SettingsMcpCatalog.test.tsx.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { App } from "antd";
import { render, screen, waitFor } from "@testing-library/react";
import "../../i18n";

import { SettingsAgentTemplates } from "../SettingsAgentTemplates";
import { AuthProvider } from "../../auth/AuthContext";
import { apiClient, setStoredToken } from "../../api/client";

const TENANT = "00000000-0000-0000-0000-00000000acme";

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.`;
}

interface RouteHandler {
  match: (url: string) => boolean;
  respond: () => unknown;
}

function installAdapter(handlers: RouteHandler[]) {
  apiClient.defaults.adapter = (config) => {
    const url = config.url ?? "";
    const handler = handlers.find((h) => h.match(url));
    return Promise.resolve({
      data: handler?.respond() ?? {},
      status: 200,
      statusText: "OK",
      headers: {},
      config,
      request: {},
    });
  };
}

const TEMPLATE = {
  id: "tmpl-1",
  tenant_id: null,
  name: "support-bot",
  version: "1.0.0",
  spec: { apiVersion: "expert_work.io/v1", kind: "Agent", metadata: {}, spec: {} },
  spec_sha256: "a".repeat(64),
  display_name: "Support Bot",
  description: "Customer support",
  category: "support",
  icon: null,
  required_tier: "free" as const,
  status: "published" as const,
  enabled: true,
  created_by: "u1",
  created_at: "2026-06-01T10:00:00Z",
  updated_at: "2026-06-01T10:00:00Z",
};

function renderPage(roles: string[]) {
  setStoredToken(makeJwt({ sub: "u1", tenant_id: TENANT, roles }));
  return render(
    <MemoryRouter>
      <AuthProvider>
        <App>
          <SettingsAgentTemplates />
        </App>
      </AuthProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("SettingsAgentTemplates page", () => {
  it("non-system-admin sees the admin-only notice, no table", async () => {
    installAdapter([
      { match: (u) => u.endsWith("/agent-templates"), respond: () => ({ success: true, data: [], error: null }) },
    ]);
    renderPage(["admin"]);
    await waitFor(() => expect(screen.getByTestId("at-not-admin")).toBeInTheDocument());
    expect(screen.queryByTestId("at-table")).not.toBeInTheDocument();
  });

  it("system_admin sees the table with template rows", async () => {
    installAdapter([
      {
        match: (u) => u.endsWith("/agent-templates"),
        respond: () => ({ success: true, data: [TEMPLATE], error: null }),
      },
    ]);
    renderPage(["system_admin"]);
    await waitFor(() => expect(screen.getByTestId("at-table")).toBeInTheDocument());
    expect(screen.getByText("Support Bot")).toBeInTheDocument();
    expect(screen.getByText("support-bot@1.0.0")).toBeInTheDocument();
    expect(screen.getByTestId("at-edit-support-bot")).toBeInTheDocument();
    // Category renders the i18n label, not the raw slug.
    expect(screen.getByText("Support")).toBeInTheDocument();
  });
});
