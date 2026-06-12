/**
 * Platform Credentials page — per-tenant override drawer tests (Stream HX-8).
 *
 * Covers the override-count column, the drawer flow (pick tenant → load the
 * tenant-effective view), creating an override through the tenant API, and
 * deleting one back to fallback. SDK calls are spied directly (the page
 * imports them by name).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { App } from "antd";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../i18n";

import { SettingsPlatformConfig } from "../SettingsPlatformConfig";
import * as sdk from "../../api/platform_config";
import * as tenantsSdk from "../../api/tenants";
import type {
  PlatformCredentialsView,
  TenantCredentialsView,
} from "../../api/platform_config";
import { AuthProvider } from "../../auth/AuthContext";
import { setStoredToken } from "../../api/client";

const TENANT = "00000000-0000-0000-0000-00000000acce";

const VIEW: PlatformCredentialsView = {
  providers: [
    {
      provider: "anthropic",
      source: "db",
      secret_ref: "kms://platform/anthropic",
      enabled: true,
      used_by_agents: 3,
      tenant_override_count: 1,
    },
  ],
  tools: [
    {
      tool: "web_search",
      source: "unset",
      secret_ref: null,
      enabled: false,
      used_by_agents: 0,
      tenant_override_count: 0,
    },
  ],
};

const TENANT_VIEW: TenantCredentialsView = {
  tenant_id: TENANT,
  providers: [
    {
      provider: "anthropic",
      override: {
        tenant_id: TENANT,
        provider: "anthropic",
        secret_ref: "kms://tenant/anthropic",
        enabled: true,
        created_at: "2026-06-12T10:00:00Z",
        updated_at: "2026-06-12T10:00:00Z",
        updated_by: "admin",
      },
      effective_source: "tenant",
      effective_ref: "kms://tenant/anthropic",
    },
  ],
  tools: [
    {
      tool: "web_search",
      override: null,
      effective_source: "unset",
      effective_ref: null,
    },
  ],
};

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.`;
}

function renderPage() {
  setStoredToken(makeJwt({ sub: "u1", tenant_id: TENANT, roles: ["system_admin"] }));
  vi.spyOn(sdk, "getPlatformCredentials").mockResolvedValue(VIEW);
  vi.spyOn(tenantsSdk, "listTenants").mockResolvedValue([
    {
      tenant_id: TENANT,
      display_name: "Acme",
      plan: "pro",
      created_at: "2026-06-01T00:00:00Z",
      status: "active",
    },
  ] as Awaited<ReturnType<typeof tenantsSdk.listTenants>>);
  vi.spyOn(sdk, "getTenantCredentials").mockResolvedValue(TENANT_VIEW);
  return render(
    <MemoryRouter>
      <AuthProvider>
        <App>
          <SettingsPlatformConfig />
        </App>
      </AuthProvider>
    </MemoryRouter>,
  );
}

async function openDrawerAndPickTenant(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByTestId("pc-tenant-overrides-btn"));
  await screen.findByTestId("pc-tenant-drawer");
  await user.click(within(screen.getByTestId("pc-tenant-select")).getByRole("combobox"));
  const opts = await screen.findAllByText(/Acme/);
  const visible =
    opts.find((el) => el.className?.includes("ant-select-item-option-content")) ?? opts[0];
  await user.click(visible);
  await waitFor(() => expect(sdk.getTenantCredentials).toHaveBeenCalledWith(TENANT));
}

afterEach(() => vi.restoreAllMocks());

describe("SettingsPlatformConfig — tenant overrides (HX-8)", () => {
  it("renders the tenant override count column", async () => {
    renderPage();
    const table = await screen.findByTestId("pc-providers-table");
    await waitFor(() => expect(within(table).getByText("1")).toBeInTheDocument());
  });

  it("drawer loads the tenant-effective view after picking a tenant", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("pc-providers-table");
    await openDrawerAndPickTenant(user);
    const providers = await screen.findByTestId("pc-tenant-providers-table");
    expect(within(providers).getByText("kms://tenant/anthropic")).toBeInTheDocument();
  });

  it("creates an override through the tenant API", async () => {
    const user = userEvent.setup();
    const upsert = vi
      .spyOn(sdk, "upsertTenantToolOverride")
      .mockResolvedValue(TENANT_VIEW.providers[0].override!);
    renderPage();
    await screen.findByTestId("pc-providers-table");
    await openDrawerAndPickTenant(user);
    await screen.findByTestId("pc-tenant-tools-table");
    await user.click(screen.getByTestId("pc-tenant-edit-web_search"));
    await screen.findByTestId("pc-edit-modal");
    await user.type(screen.getByTestId("pc-edit-value"), "tvly-REAL-KEY");
    await user.click(screen.getByText("Save"));
    await waitFor(() =>
      expect(upsert).toHaveBeenCalledWith(
        TENANT,
        "web_search",
        expect.objectContaining({ value: "tvly-REAL-KEY" }),
      ),
    );
  });

  it("deletes an override back to fallback", async () => {
    const user = userEvent.setup();
    const del = vi.spyOn(sdk, "deleteTenantProviderOverride").mockResolvedValue(undefined);
    renderPage();
    await screen.findByTestId("pc-providers-table");
    await openDrawerAndPickTenant(user);
    await screen.findByTestId("pc-tenant-providers-table");
    await user.click(screen.getByTestId("pc-tenant-delete-anthropic"));
    // Popconfirm's confirm button shares the row button's "Delete" label;
    // the popover mounts last in the document body.
    const deleteButtons = await screen.findAllByRole("button", { name: "Delete" });
    await user.click(deleteButtons[deleteButtons.length - 1]);
    await waitFor(() => expect(del).toHaveBeenCalledWith(TENANT, "anthropic"));
  });
});
