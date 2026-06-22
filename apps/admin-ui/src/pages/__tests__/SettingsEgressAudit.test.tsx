/**
 * SettingsEgressAudit tests — sandbox-egress §3.1 Phase 3.
 *
 * Backend returns raw (un-enveloped) payloads (sandbox_egress_audit.py), so the
 * axios adapter mock delivers ``{items, next_cursor, has_more, applied_scope}``.
 */
import { describe, expect, it, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { App } from "antd";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../i18n";

import { SettingsEgressAudit } from "../SettingsEgressAudit";
import { TenantScopeProvider } from "../../tenant/TenantScopeContext";
import { AuthProvider } from "../../auth/AuthContext";
import { apiClient, setStoredToken } from "../../api/client";

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.`;
}

function row(id: number, overrides: Record<string, unknown> = {}) {
  return {
    id,
    tenant_id: "t1",
    agent_name: "pptx-agent",
    agent_version: "1.0.0",
    sandbox_id: `sbx-${id}`,
    target_host: "api.openai.com",
    target_port: 443,
    verdict: "allowed",
    bytes_up: 100,
    bytes_down: 200,
    duration_ms: 12,
    error_msg: null,
    occurred_at: "2026-06-22T03:00:00Z",
    ...overrides,
  };
}

function installAdapter(payload: { items: unknown[]; applied_scope?: string }) {
  apiClient.defaults.adapter = (config) =>
    Promise.resolve({
      data: {
        items: payload.items,
        next_cursor: null,
        has_more: false,
        applied_scope: payload.applied_scope ?? "t1",
      },
      status: 200,
      statusText: "OK",
      headers: {},
      config,
      request: {},
    });
}

function renderPage() {
  setStoredToken(makeJwt({ sub: "u1", tenant_id: "t1", roles: ["admin"] }));
  return render(
    <MemoryRouter>
      <AuthProvider>
        <TenantScopeProvider>
          <App>
            <SettingsEgressAudit />
          </App>
        </TenantScopeProvider>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("SettingsEgressAudit", () => {
  beforeEach(() => {
    apiClient.defaults.adapter = undefined;
  });

  it("renders egress rows with verdict + host", async () => {
    installAdapter({ items: [row(1), row(2, { verdict: "blocked_ssrf", target_host: "evil.com" })] });
    renderPage();
    await waitFor(() => {
      expect(screen.getAllByTestId("egress-audit-row")).toHaveLength(2);
    });
    expect(screen.getByText("api.openai.com:443")).toBeInTheDocument();
    expect(screen.getByText("blocked_ssrf")).toBeInTheDocument();
  });

  it("shows the empty state when there are no rows", async () => {
    installAdapter({ items: [] });
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("egress-audit-empty")).toBeInTheDocument();
    });
  });

  it("shows the cross-tenant banner for the wildcard scope", async () => {
    installAdapter({ items: [row(1)], applied_scope: "cross_tenant" });
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("egress-audit-cross-banner")).toBeInTheDocument();
    });
  });

  it("opens the detail drawer on row click", async () => {
    installAdapter({ items: [row(1, { error_msg: "boom" })] });
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("egress-audit-row")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("egress-audit-row"));
    await waitFor(() => {
      expect(screen.getByTestId("egress-audit-drawer")).toBeInTheDocument();
    });
  });
});
