/**
 * TenantScopeContext — platform-homed admin promotion (Stream ACCT).
 *
 * The TenantSwitcher hides the home option when the home tenant is the
 * synthetic platform tenant. Without promotion, ``scope="home"`` would leave
 * the switcher pointing at a value that isn't an option. The provider promotes
 * a platform-homed system_admin to the ``"*"`` (platform) scope once the
 * server truth (``serverResolved`` + ``homeIsPlatform``) is in.
 */
import { afterEach, describe, expect, it, vi, type Mock } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import {
  SCOPE_ALL,
  SCOPE_HOME,
  TenantScopeProvider,
  useTenantScope,
} from "../TenantScopeContext";
import { useAuth } from "../../auth/AuthContext";

vi.mock("../../auth/AuthContext", () => ({ useAuth: vi.fn() }));

afterEach(() => {
  window.sessionStorage.clear();
  vi.clearAllMocks();
});

function ScopeProbe() {
  const { scope } = useTenantScope();
  return <div data-testid="scope">{scope}</div>;
}

function renderWithIdentity(identity: unknown) {
  (useAuth as unknown as Mock).mockReturnValue({ identity });
  return render(
    <TenantScopeProvider>
      <ScopeProbe />
    </TenantScopeProvider>,
  );
}

describe("TenantScopeContext — platform-homed promotion", () => {
  it("promotes a platform-homed system_admin from home to the '*' scope", async () => {
    renderWithIdentity({ serverResolved: true, isSystemAdmin: true, homeIsPlatform: true });
    await waitFor(() => {
      expect(screen.getByTestId("scope").textContent).toBe(SCOPE_ALL);
    });
    expect(window.sessionStorage.getItem("expert_work.admin.tenantScope")).toBe(SCOPE_ALL);
  });

  it("leaves a dual-role admin (real home) on the home scope", async () => {
    renderWithIdentity({ serverResolved: true, isSystemAdmin: true, homeIsPlatform: false });
    // Give the effect a chance to (not) fire.
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.getByTestId("scope").textContent).toBe(SCOPE_HOME);
  });

  it("does not promote before the server resolves (homeIsPlatform not yet authoritative)", async () => {
    renderWithIdentity({ serverResolved: false, isSystemAdmin: true, homeIsPlatform: true });
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.getByTestId("scope").textContent).toBe(SCOPE_HOME);
  });
});
