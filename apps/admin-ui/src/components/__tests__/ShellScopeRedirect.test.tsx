/**
 * Scope-alignment tests — admin-ui-nav-ia §4 (minimal, deep-link friendly).
 *
 * Only one alignment: a system_admin deep-linking a platform page enters
 * platform scope and stays put. Everything else is left to the pages
 * (non-admins get the page's own notice; scope-adaptive pages keep their
 * scope) — no bounce, no tenant-route force-switch.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import { render, screen } from "@testing-library/react";

import { Shell } from "../Shell";

vi.mock("../Sidebar", () => ({ Sidebar: () => <div /> }));
vi.mock("../Topbar", () => ({ Topbar: () => <div /> }));

let mockScope: string;
const setScope = vi.fn();
vi.mock("../../tenant/TenantScopeContext", () => ({
  SCOPE_ALL: "*",
  SCOPE_HOME: "home",
  useTenantScope: () => ({ scope: mockScope, setScope }),
}));

let mockIsSystemAdmin: boolean;
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ identity: { isSystemAdmin: mockIsSystemAdmin } }),
}));

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="pathname">{loc.pathname}</div>;
}

function renderAt(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Shell>
        <Routes>
          <Route path="*" element={<LocationProbe />} />
        </Routes>
      </Shell>
    </MemoryRouter>,
  );
}

afterEach(() => {
  setScope.mockClear();
});

describe("Shell — scope alignment", () => {
  it("system_admin deep-linking a platform page enters platform scope, stays put", () => {
    mockScope = "home";
    mockIsSystemAdmin = true;
    renderAt("/settings/tenants");
    expect(setScope).toHaveBeenCalledWith("*");
    expect(screen.getByTestId("pathname").textContent).toBe("/settings/tenants");
  });

  it("non-admin on a platform route is NOT bounced (page shows its own notice)", () => {
    mockScope = "home";
    mockIsSystemAdmin = false;
    renderAt("/settings/tenants");
    expect(setScope).not.toHaveBeenCalled();
    expect(screen.getByTestId("pathname").textContent).toBe("/settings/tenants");
  });

  it("does not force-switch on a tenant route (scope-adaptive pages keep scope)", () => {
    // e.g. cross-tenant Members at "*" stays "*"; /settings/members is a tenant route.
    mockScope = "*";
    mockIsSystemAdmin = true;
    renderAt("/settings/members");
    expect(setScope).not.toHaveBeenCalled();
    expect(screen.getByTestId("pathname").textContent).toBe("/settings/members");
  });

  it("leaves an already-aligned platform route untouched", () => {
    mockScope = "*";
    mockIsSystemAdmin = true;
    renderAt("/settings/rate-card");
    expect(setScope).not.toHaveBeenCalled();
    expect(screen.getByTestId("pathname").textContent).toBe("/settings/rate-card");
  });
});
