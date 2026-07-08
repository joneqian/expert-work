/**
 * TenantScopeContext — apiTenantScope wire-value gating (transient-403 fix).
 *
 * ``apiTenantScope`` is what pages thread onto the SDK as ``?tenant_id=``. A
 * non-home scope ("*" cross-tenant, or a specific tenant UUID) is
 * system_admin-only: the backend 403s any non-home scope for a non-admin
 * (``CROSS_TENANT_FORBIDDEN`` / ``TENANT_NOT_ALLOWED``). So the wire value must
 * stay ``undefined`` (home) until ``/v1/me`` CONFIRMS the caller is a
 * system_admin — otherwise a stale "*" left in sessionStorage by a prior admin
 * session races ahead of identity resolution and fires ``?tenant_id=*`` → a
 * transient 403 that only "fixes itself" on refresh.
 */
import { afterEach, describe, expect, it, vi, type Mock } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import {
  SCOPE_ALL,
  TenantScopeProvider,
  useTenantScope,
} from "../TenantScopeContext";
import { useAuth } from "../../auth/AuthContext";

vi.mock("../../auth/AuthContext", () => ({ useAuth: vi.fn() }));

const STORAGE_KEY = "expert_work.admin.tenantScope";
const HOME = "<home>";

afterEach(() => {
  window.sessionStorage.clear();
  vi.clearAllMocks();
});

function WireProbe() {
  const { apiTenantScope } = useTenantScope();
  return <div data-testid="wire">{apiTenantScope ?? HOME}</div>;
}

function renderWithIdentity(identity: unknown) {
  (useAuth as unknown as Mock).mockReturnValue({ identity });
  return render(
    <TenantScopeProvider>
      <WireProbe />
    </TenantScopeProvider>,
  );
}

describe("TenantScopeContext — apiTenantScope wire gating", () => {
  it("does NOT emit '*' before the server resolves identity (stale-scope race)", async () => {
    window.sessionStorage.setItem(STORAGE_KEY, SCOPE_ALL);
    renderWithIdentity({
      serverResolved: false,
      isSystemAdmin: false,
      homeIsPlatform: false,
    });
    // Optimistic window: stored scope is "*", but the wire value stays home
    // until identity is confirmed — no ?tenant_id=* → no transient 403.
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.getByTestId("wire").textContent).toBe(HOME);
  });

  it("does NOT emit a specific-tenant UUID before identity is confirmed", async () => {
    window.sessionStorage.setItem(
      STORAGE_KEY,
      "866c25e8-8f27-48f1-8733-485e171bb576",
    );
    renderWithIdentity({
      serverResolved: false,
      isSystemAdmin: false,
      homeIsPlatform: false,
    });
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.getByTestId("wire").textContent).toBe(HOME);
  });

  it("does NOT emit '*' for a confirmed non-admin (stale scope)", async () => {
    window.sessionStorage.setItem(STORAGE_KEY, SCOPE_ALL);
    renderWithIdentity({
      serverResolved: true,
      isSystemAdmin: false,
      homeIsPlatform: false,
    });
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.getByTestId("wire").textContent).toBe(HOME);
  });

  it("emits '*' for a confirmed system_admin on the '*' scope", async () => {
    window.sessionStorage.setItem(STORAGE_KEY, SCOPE_ALL);
    renderWithIdentity({
      serverResolved: true,
      isSystemAdmin: true,
      homeIsPlatform: false,
    });
    await waitFor(() => {
      expect(screen.getByTestId("wire").textContent).toBe(SCOPE_ALL);
    });
  });
});
