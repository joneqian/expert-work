/**
 * Scope-switch redirect tests — admin-ui-nav-ia §4.
 *
 * When the operator changes the tenant scope while sitting on a route
 * the new scope can't see, the Shell redirects to the matching landing
 * page so they never stall on a forbidden/empty page:
 *
 *   - on a tenant route + switch to "*"  → /settings/tenants
 *   - on a platform route + switch to a tenant → /agents
 *
 * Routes already in a group the scope owns are left untouched.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import { render, screen } from "@testing-library/react";

import { Shell } from "../Shell";

// Mock the heavy children — we only exercise the redirect effect.
vi.mock("../Sidebar", () => ({ Sidebar: () => <div /> }));
vi.mock("../Topbar", () => ({ Topbar: () => <div /> }));

let mockScope: string;
vi.mock("../../tenant/TenantScopeContext", () => ({
  SCOPE_ALL: "*",
  SCOPE_HOME: "home",
  useTenantScope: () => ({ scope: mockScope }),
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
  vi.clearAllMocks();
});

describe("Shell — scope-switch redirect", () => {
  it("switching to platform scope on a tenant route lands on /settings/tenants", () => {
    mockScope = "*";
    renderAt("/agents");
    expect(screen.getByTestId("pathname").textContent).toBe("/settings/tenants");
  });

  it("switching to a tenant scope on a platform route lands on /agents", () => {
    mockScope = "home";
    renderAt("/settings/tenants");
    expect(screen.getByTestId("pathname").textContent).toBe("/agents");
  });

  it("leaves a tenant route untouched while on a tenant scope", () => {
    mockScope = "home";
    renderAt("/runs");
    expect(screen.getByTestId("pathname").textContent).toBe("/runs");
  });

  it("leaves a platform route untouched while on the platform scope", () => {
    mockScope = "*";
    renderAt("/settings/rate-card");
    expect(screen.getByTestId("pathname").textContent).toBe("/settings/rate-card");
  });
});
