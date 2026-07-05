/**
 * Chargeback page tests — Stream Z3.
 *
 * Mirrors the SettingsMcpCatalog system_admin-gate harness: non-admins see
 * the notice (no table); system_admin sees the full per-tenant split.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { App } from "antd";
import { render, screen, waitFor, within } from "@testing-library/react";
import "../../i18n";

import { SettingsBillingChargeback } from "../SettingsBillingChargeback";
import { AuthProvider } from "../../auth/AuthContext";
import { apiClient, setStoredToken } from "../../api/client";

const TENANT = "00000000-0000-0000-0000-00000000acme";

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.`;
}

const CHARGEBACK = {
  month: "2026-06",
  as_of: "2026-06-03T10:00:00Z",
  total_base_cost_micros: 1_000_000,
  total_billed_cost_micros: 1_500_000,
  total_margin_micros: 500_000,
  tenants: [
    {
      tenant_id: "tenant-acme",
      input_tokens: 1000,
      output_tokens: 500,
      cache_creation_tokens: 0,
      cache_read_tokens: 0,
      base_cost_micros: 1_000_000,
      markup_cost_micros: 500_000,
      billed_cost_micros: 1_500_000,
      margin_micros: 500_000,
      unpriced_buckets: 2,
    },
  ],
};

function meEnvelope(isSystemAdmin: boolean, roles: string[]) {
  return {
    success: true,
    data: {
      subject_id: "u1",
      subject_type: "user",
      tenant_id: TENANT,
      auth_method: "jwt",
      roles,
      scopes: [],
      is_system_admin: isSystemAdmin,
      allowed_tenants: [TENANT],
    },
    error: null,
  };
}

function installAdapter(respond: () => unknown, isSystemAdmin: boolean, roles: string[]) {
  apiClient.defaults.adapter = (config) => {
    const url = config.url ?? "";
    const data = url.endsWith("/me") ? meEnvelope(isSystemAdmin, roles) : respond();
    return Promise.resolve({
      data,
      status: 200,
      statusText: "OK",
      headers: {},
      config,
      request: {},
    });
  };
}

function renderChargeback(roles: string[]) {
  setStoredToken(makeJwt({ sub: "u1", tenant_id: TENANT, roles }));
  return render(
    <MemoryRouter>
      <AuthProvider>
        <App>
          <SettingsBillingChargeback />
        </App>
      </AuthProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("SettingsBillingChargeback page", () => {
  it("non-system-admin sees the admin-only notice, no table", async () => {
    installAdapter(() => ({ success: true, data: CHARGEBACK, error: null }), false, ["admin"]);
    renderChargeback(["admin"]);
    await waitFor(() => expect(screen.getByTestId("chargeback-not-admin")).toBeInTheDocument());
    expect(screen.queryByTestId("chargeback-table")).not.toBeInTheDocument();
  });

  it("system_admin sees the full per-tenant split (base/markup/billed/margin)", async () => {
    installAdapter(() => ({ success: true, data: CHARGEBACK, error: null }), true, ["system_admin"]);
    renderChargeback(["system_admin"]);
    await waitFor(() => expect(screen.getByTestId("chargeback-table")).toBeInTheDocument());

    const table = screen.getByTestId("chargeback-table");
    const headers = within(table)
      .getAllByRole("columnheader")
      .map((h) => h.textContent);
    expect(headers).toContain("Base cost");
    expect(headers).toContain("Markup");
    expect(headers).toContain("Billed");
    expect(headers).toContain("Margin");
    // RT-3 — cache columns + client-derived hit rate alongside the cost split.
    expect(headers).toContain("Cache read tokens");
    expect(headers).toContain("Cache write tokens");
    expect(headers).toContain("Cache hit rate");

    expect(screen.getByText("tenant-acme")).toBeInTheDocument();
    // Summary totals (base $1.0000 / billed $1.5000 / margin $0.5000).
    const summary = screen.getByTestId("chargeback-summary");
    expect(within(summary).getByText("$1.0000")).toBeInTheDocument();
    expect(within(summary).getByText("$1.5000")).toBeInTheDocument();
    expect(within(summary).getByText("$0.5000")).toBeInTheDocument();
  });

  it("renders the empty state when no tenants returned", async () => {
    installAdapter(
      () => ({
        success: true,
        data: { ...CHARGEBACK, tenants: [] },
        error: null,
      }),
      true,
      ["system_admin"],
    );
    renderChargeback(["system_admin"]);
    await waitFor(() => expect(screen.getByTestId("chargeback-table")).toBeInTheDocument());
    expect(screen.getByText("No chargeback data for this month.")).toBeInTheDocument();
  });

  it("shows an error alert when the fetch fails", async () => {
    installAdapter(
      () => ({ success: false, data: null, error: { code: "BOOM", message: "nope" } }),
      true,
      ["system_admin"],
    );
    renderChargeback(["system_admin"]);
    await waitFor(() => expect(screen.getByTestId("chargeback-error")).toBeInTheDocument());
  });
});
