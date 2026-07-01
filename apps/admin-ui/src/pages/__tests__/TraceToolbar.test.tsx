/**
 * TraceToolbar tests — Stream H.3 PR 6 + observability gating.
 *
 * The Langfuse deep link is ``system_admin`` only (Langfuse has no per-tenant
 * isolation), so the render is wrapped in an ``AuthProvider`` fed a JWT whose
 * roles decide ``isSystemAdmin``.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "antd";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../i18n";

import { TraceToolbar } from "../run_detail/TraceToolbar";
import { AuthProvider } from "../../auth/AuthContext";
import { setStoredToken } from "../../api/client";

function jwt(roles: string[]): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(
    JSON.stringify({ sub: "u", tenant_id: "11111111-1111-1111-1111-111111111111", roles }),
  );
  return `${header}.${body}.`;
}

function renderToolbar(traceId: string | null, { admin = true }: { admin?: boolean } = {}) {
  setStoredToken(jwt(admin ? ["system_admin"] : ["admin"]));
  return render(
    <App>
      <AuthProvider>
        <TraceToolbar traceId={traceId} />
      </AuthProvider>
    </App>,
  );
}

beforeEach(() => {
  vi.unstubAllEnvs();
});

afterEach(() => {
  vi.unstubAllEnvs();
  setStoredToken(null);
});

describe("TraceToolbar", () => {
  it("shows the no-trace placeholder when trace_id is null", () => {
    renderToolbar(null);
    expect(screen.getByTestId("trace-toolbar-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("trace-toolbar-id")).toBeNull();
    expect(screen.queryByTestId("trace-toolbar-langfuse")).toBeNull();
  });

  it("renders the chip + copy button when trace_id is present (no Langfuse url)", async () => {
    vi.stubEnv("VITE_LANGFUSE_BASE_URL", "");
    renderToolbar("abc123");
    expect(screen.getByTestId("trace-toolbar-id")).toHaveTextContent("abc123");
    expect(screen.getByTestId("trace-toolbar-copy")).toBeInTheDocument();
    expect(screen.queryByTestId("trace-toolbar-langfuse")).toBeNull();
  });

  it("renders the Langfuse link for a system_admin when the base URL is set", () => {
    vi.stubEnv("VITE_LANGFUSE_BASE_URL", "https://langfuse.example.com/");
    renderToolbar("abc123", { admin: true });
    const link = screen.getByTestId("trace-toolbar-langfuse");
    expect(link).toHaveAttribute("href", "https://langfuse.example.com/trace/abc123");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("hides the Langfuse link for a non-system-admin even when the URL is set", () => {
    vi.stubEnv("VITE_LANGFUSE_BASE_URL", "https://langfuse.example.com/");
    renderToolbar("abc123", { admin: false });
    // Still gets the trace_id + copy, but no cross-tenant deep link.
    expect(screen.getByTestId("trace-toolbar-id")).toBeInTheDocument();
    expect(screen.queryByTestId("trace-toolbar-langfuse")).toBeNull();
  });

  it("copies the trace_id to the clipboard when the copy button is clicked", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    renderToolbar("abc123");
    await user.click(screen.getByTestId("trace-toolbar-copy"));
    expect(writeText).toHaveBeenCalledWith("abc123");
  });
});
