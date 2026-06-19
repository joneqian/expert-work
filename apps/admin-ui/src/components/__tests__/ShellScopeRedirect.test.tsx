/**
 * Scope-alignment tests — admin-ui-nav-ia §4 (deep-link friendly).
 *
 * The route's group implies an operating level; the Shell *aligns the
 * scope to the page* instead of bouncing the user off a deep link:
 *
 *   - platform route + system_admin not at platform level → setScope("*"),
 *     stay on the page.
 *   - platform route + non-admin → redirect to /agents (no access).
 *   - tenant route while at platform level → setScope("home"), stay.
 *   - already-aligned routes → no scope change, no redirect.
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

  it("non-admin on a platform route is redirected to /agents", () => {
    mockScope = "home";
    mockIsSystemAdmin = false;
    renderAt("/settings/tenants");
    expect(setScope).not.toHaveBeenCalled();
    expect(screen.getByTestId("pathname").textContent).toBe("/agents");
  });

  it("a tenant route while at platform level drops back to the home tenant, stays put", () => {
    mockScope = "*";
    mockIsSystemAdmin = true;
    renderAt("/runs");
    expect(setScope).toHaveBeenCalledWith("home");
    expect(screen.getByTestId("pathname").textContent).toBe("/runs");
  });

  it("leaves an already-aligned tenant route untouched", () => {
    mockScope = "home";
    mockIsSystemAdmin = true;
    renderAt("/runs");
    expect(setScope).not.toHaveBeenCalled();
    expect(screen.getByTestId("pathname").textContent).toBe("/runs");
  });

  it("leaves an already-aligned platform route untouched", () => {
    mockScope = "*";
    mockIsSystemAdmin = true;
    renderAt("/settings/rate-card");
    expect(setScope).not.toHaveBeenCalled();
    expect(screen.getByTestId("pathname").textContent).toBe("/settings/rate-card");
  });
});
