/**
 * SettingsObservability tests — the platform-ops observability hub.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "../../i18n";

import { SettingsObservability } from "../SettingsObservability";
import { AuthProvider } from "../../auth/AuthContext";
import { setStoredToken } from "../../api/client";

function jwt(roles: string[]): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(
    JSON.stringify({ sub: "u", tenant_id: "11111111-1111-1111-1111-111111111111", roles }),
  );
  return `${header}.${body}.`;
}

function renderPage({ admin = true }: { admin?: boolean } = {}) {
  setStoredToken(jwt(admin ? ["system_admin"] : ["admin"]));
  return render(
    <MemoryRouter>
      <AuthProvider>
        <SettingsObservability />
      </AuthProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => vi.unstubAllEnvs());
afterEach(() => {
  vi.unstubAllEnvs();
  setStoredToken(null);
});

describe("SettingsObservability", () => {
  it("lists the observability tools for a system_admin", () => {
    vi.stubEnv("VITE_LANGFUSE_BASE_URL", "https://langfuse.example.com/");
    renderPage({ admin: true });
    expect(screen.getByTestId("obs-tool-langfuse")).toBeInTheDocument();
    expect(screen.getByTestId("obs-tool-grafana")).toBeInTheDocument();
    expect(screen.getByTestId("obs-tool-tempo")).toBeInTheDocument();
    // Configured URL → open link (trailing slash normalised).
    expect(screen.getByTestId("obs-open-langfuse")).toHaveAttribute(
      "href",
      "https://langfuse.example.com",
    );
    // Unset URL → a "configure" hint instead of a dead link.
    expect(screen.getByTestId("obs-unconfigured-grafana")).toBeInTheDocument();
  });

  it("blocks a non-system-admin", () => {
    renderPage({ admin: false });
    expect(screen.getByTestId("obs-not-admin")).toBeInTheDocument();
    expect(screen.queryByTestId("obs-tool-langfuse")).toBeNull();
  });
});
