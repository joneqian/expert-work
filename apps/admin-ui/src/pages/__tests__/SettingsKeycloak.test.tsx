/**
 * SettingsKeycloak tests — the platform-ops Keycloak (IAM) console launcher.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "../../i18n";

import { SettingsKeycloak } from "../SettingsKeycloak";
import { AuthProvider } from "../../auth/AuthContext";
import { setStoredToken } from "../../api/client";

function jwt(roles: string[]): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(
    JSON.stringify({
      sub: "u",
      tenant_id: "11111111-1111-1111-1111-111111111111",
      roles,
    }),
  );
  return `${header}.${body}.`;
}

function renderPage({ admin = true }: { admin?: boolean } = {}) {
  setStoredToken(jwt(admin ? ["system_admin"] : ["admin"]));
  return render(
    <MemoryRouter>
      <AuthProvider>
        <SettingsKeycloak />
      </AuthProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => vi.unstubAllEnvs());
afterEach(() => {
  vi.unstubAllEnvs();
  setStoredToken(null);
});

describe("SettingsKeycloak", () => {
  it("links to the admin console for a system_admin when configured", () => {
    // Trailing slash is normalised; the launcher opens the ``/admin/`` console.
    vi.stubEnv("VITE_KEYCLOAK_BASE_URL", "http://localhost:8080/");
    renderPage({ admin: true });
    expect(screen.getByTestId("kc-card")).toBeInTheDocument();
    expect(screen.getByTestId("kc-open")).toHaveAttribute(
      "href",
      "http://localhost:8080/admin/",
    );
  });

  it("shows a configure hint when the base URL is unset", () => {
    renderPage({ admin: true });
    expect(screen.getByTestId("kc-card")).toBeInTheDocument();
    expect(screen.getByTestId("kc-unconfigured")).toBeInTheDocument();
    expect(screen.queryByTestId("kc-open")).toBeNull();
  });

  it("blocks a non-system-admin", () => {
    renderPage({ admin: false });
    expect(screen.getByTestId("kc-not-admin")).toBeInTheDocument();
    expect(screen.queryByTestId("kc-card")).toBeNull();
  });
});
