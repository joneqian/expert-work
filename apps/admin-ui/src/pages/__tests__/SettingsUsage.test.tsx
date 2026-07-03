/**
 * Usage page tests — Stream Z3.
 *
 * Pins the monetization no-leak rule: the tenant usage page renders billed
 * cost + tokens only, never base/markup/margin. Also covers the month +
 * group_by refetch.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { App } from "antd";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../i18n";

import { SettingsUsage } from "../SettingsUsage";
import { AuthProvider } from "../../auth/AuthContext";
import { apiClient, setStoredToken } from "../../api/client";

const TENANT = "00000000-0000-0000-0000-00000000acme";

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.`;
}

const COST = {
  month: "2026-06",
  group_by: "agent",
  as_of: "2026-06-03T10:00:00Z",
  total_billed_cost_micros: 1_200_000,
  groups: [
    {
      key: "support-bot",
      input_tokens: 1000,
      output_tokens: 500,
      cache_creation_tokens: 0,
      cache_read_tokens: 0,
      billed_cost_micros: 800_000,
      unpriced: false,
    },
    {
      key: "research-bot",
      input_tokens: 200,
      output_tokens: 100,
      cache_creation_tokens: 0,
      cache_read_tokens: 0,
      billed_cost_micros: 400_000,
      unpriced: true,
    },
  ],
};

const TOKENS = {
  month: "2026-06",
  as_of: "2026-06-03T11:00:00Z",
  realtime: true,
  total: {
    input_tokens: 1200,
    output_tokens: 600,
    cache_creation_tokens: 10,
    cache_read_tokens: 20,
  },
  by_agent: [
    { key: "support-bot", input_tokens: 1000, output_tokens: 500, cache_creation_tokens: 0, cache_read_tokens: 0 },
  ],
  by_model: [
    { key: "claude-sonnet", input_tokens: 1200, output_tokens: 600, cache_creation_tokens: 10, cache_read_tokens: 20 },
  ],
  by_kind: [
    { key: "conversation", input_tokens: 900, output_tokens: 450, cache_creation_tokens: 10, cache_read_tokens: 20 },
    { key: "skill_evolution", input_tokens: 300, output_tokens: 150, cache_creation_tokens: 0, cache_read_tokens: 0 },
  ],
};

interface Captured {
  costMonth?: string;
  costGroupBy?: string;
}

function installAdapter(captured: Captured) {
  apiClient.defaults.adapter = (config) => {
    const url = config.url ?? "";
    const params = (config.params ?? {}) as Record<string, string>;
    let data: unknown = {};
    if (url.endsWith("/me")) {
      data = {
        success: true,
        data: {
          subject_id: "u1",
          subject_type: "user",
          tenant_id: TENANT,
          auth_method: "jwt",
          roles: ["member"],
          scopes: [],
          is_system_admin: false,
          allowed_tenants: [TENANT],
        },
        error: null,
      };
    } else if (url.endsWith("/usage/cost")) {
      captured.costMonth = params.month;
      captured.costGroupBy = params.group_by;
      data = { success: true, data: { ...COST, group_by: params.group_by ?? "agent" }, error: null };
    } else if (url.endsWith("/usage/tokens")) {
      data = { success: true, data: TOKENS, error: null };
    }
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

function renderUsage() {
  setStoredToken(makeJwt({ sub: "u1", tenant_id: TENANT, roles: ["member"] }));
  return render(
    <MemoryRouter>
      <AuthProvider>
        <App>
          <SettingsUsage />
        </App>
      </AuthProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("SettingsUsage page", () => {
  it("renders billed cost groups and token totals (not system_admin gated)", async () => {
    installAdapter({});
    renderUsage();
    await waitFor(() => expect(screen.getByTestId("usage-cost-table")).toBeInTheDocument());
    expect(
      within(screen.getByTestId("usage-cost-table")).getByText("support-bot"),
    ).toBeInTheDocument();
    // Total billed = 1_200_000 micros → $1.2000.
    const summary = screen.getByTestId("usage-summary");
    expect(within(summary).getByText("$1.2000")).toBeInTheDocument();
    // Unpriced tag on the research-bot row.
    expect(screen.getByTestId("usage-unpriced-research-bot")).toBeInTheDocument();
    // Token totals section is present.
    expect(screen.getByTestId("usage-token-totals")).toBeInTheDocument();
  });

  it("NEVER surfaces base/markup/margin (monetization no-leak)", async () => {
    installAdapter({});
    renderUsage();
    await waitFor(() => expect(screen.getByTestId("usage-cost-table")).toBeInTheDocument());
    // No column header or any rendered text mentions base/markup/margin.
    expect(screen.queryByText(/base cost/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/markup/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/margin/i)).not.toBeInTheDocument();
    // Column headers are exactly the billed-only set.
    const table = screen.getByTestId("usage-cost-table");
    const headers = within(table)
      .getAllByRole("columnheader")
      .map((h) => h.textContent);
    expect(headers).toContain("Billed cost");
    expect(headers.some((h) => /base|markup|margin/i.test(h ?? ""))).toBe(false);
  });

  it("changing month + group_by refetches with new params", async () => {
    const captured: Captured = {};
    installAdapter(captured);
    const user = userEvent.setup();
    renderUsage();
    await waitFor(() => expect(screen.getByTestId("usage-cost-table")).toBeInTheDocument());

    // Toggle to "By Model".
    await user.click(screen.getByText("By Model"));
    await waitFor(() => expect(captured.costGroupBy).toBe("model"));
  });

  it("shows an error alert when the cost fetch fails", async () => {
    apiClient.defaults.adapter = (config) =>
      Promise.resolve({
        data: { success: false, data: null, error: { code: "BOOM", message: "nope" } },
        status: 200,
        statusText: "OK",
        headers: {},
        config,
        request: {},
      });
    renderUsage();
    await waitFor(() => expect(screen.getByTestId("usage-error")).toBeInTheDocument());
  });
});

describe("usage kind split (SE-A43)", () => {
  it("renders the by-purpose table with evolution spend separable", async () => {
    installAdapter({});
    renderUsage();
    const kindTable = await screen.findByTestId("usage-token-kind-table");
    expect(within(kindTable).getByText(/技能自进化|Skill evolution/)).toBeInTheDocument();
    expect(within(kindTable).getByText("300")).toBeInTheDocument();
    expect(within(kindTable).getByText(/对话|Conversation/)).toBeInTheDocument();
  });
});
