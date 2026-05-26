/**
 * SkillsList + SkillDetail tests — Stream H.4 PR 5.
 *
 * Backend skills endpoints return raw (un-enveloped) payloads, so
 * adapter mocks deliver ``{items, next_cursor, cross_tenant}`` /
 * raw skill objects directly.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { App } from "antd";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../i18n";

import { SkillsList } from "../SkillsList";
import { SkillDetail } from "../SkillDetail";
import { TenantScopeProvider } from "../../tenant/TenantScopeContext";
import { AuthProvider } from "../../auth/AuthContext";
import { apiClient, setStoredToken } from "../../api/client";

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.`;
}

interface RouteHandler {
  match: (url: string, method: string) => boolean;
  respond: () => unknown;
  status?: number;
}

function installAdapter(handlers: RouteHandler[]) {
  apiClient.defaults.adapter = (config) => {
    const url = config.url ?? "";
    const method = (config.method ?? "get").toLowerCase();
    const handler = handlers.find((h) => h.match(url, method));
    return Promise.resolve({
      data: handler?.respond() ?? {},
      status: handler?.status ?? 200,
      statusText: "OK",
      headers: {},
      config,
      request: {},
    });
  };
}

function renderSkillsRouter(initialEntries: string[] = ["/skills"]) {
  setStoredToken(makeJwt({ sub: "u1", tenant_id: "t1", roles: ["admin"] }));
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <AuthProvider>
        <TenantScopeProvider>
          <App>
            <Routes>
              <Route path="/skills" element={<SkillsList />} />
              <Route path="/skills/:skillId" element={<SkillDetail />} />
            </Routes>
          </App>
        </TenantScopeProvider>
      </AuthProvider>
    </MemoryRouter>,
  );
}

const skillRow = {
  id: "sk1",
  name: "web_search",
  status: "active" as const,
  latest_version: 3,
  description: "Search the web and return top N results.",
  category: "web",
  created_at: "2026-05-20T10:00:00Z",
  updated_at: "2026-05-26T10:00:00Z",
};

const versionRow = {
  id: "v1",
  skill_id: "sk1",
  version: 1,
  prompt_fragment: "Always cite sources.",
  tool_names: ["web_search"],
  description: "First cut.",
  category: "web",
  required_models: [],
  authored_by: "human",
  created_at: "2026-05-20T10:00:00Z",
};

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("SkillsList", () => {
  it("renders the table with rows", async () => {
    installAdapter([
      {
        match: (u) => u === "/v1/skills",
        respond: () => ({
          items: [skillRow],
          next_cursor: null,
          cross_tenant: false,
        }),
      },
    ]);
    renderSkillsRouter();
    await waitFor(() => expect(screen.getByText("web_search")).toBeInTheDocument());
    expect(screen.getByText("web")).toBeInTheDocument();
  });

  it("shows Load more when next_cursor is non-null", async () => {
    let calls = 0;
    installAdapter([
      {
        match: (u) => u === "/v1/skills",
        respond: () => {
          calls += 1;
          if (calls === 1) {
            return {
              items: [skillRow],
              next_cursor: "cursor-2",
              cross_tenant: false,
            };
          }
          return {
            items: [{ ...skillRow, id: "sk2", name: "sql_query" }],
            next_cursor: null,
            cross_tenant: false,
          };
        },
      },
    ]);
    const user = userEvent.setup();
    renderSkillsRouter();
    await waitFor(() => expect(screen.getByText("web_search")).toBeInTheDocument());
    expect(screen.getByTestId("skills-load-more")).toBeInTheDocument();
    await user.click(screen.getByTestId("skills-load-more"));
    await waitFor(() => expect(screen.getByText("sql_query")).toBeInTheDocument());
    // First page still visible — Load more appends.
    expect(screen.getByText("web_search")).toBeInTheDocument();
  });

  it("opens the Create drawer + validates required fields", async () => {
    installAdapter([
      {
        match: (u) => u === "/v1/skills",
        respond: () => ({ items: [], next_cursor: null, cross_tenant: false }),
      },
    ]);
    const user = userEvent.setup();
    renderSkillsRouter();
    await waitFor(() => expect(screen.getByTestId("skills-create-btn")).toBeInTheDocument());
    await user.click(screen.getByTestId("skills-create-btn"));
    await waitFor(() => expect(screen.getByTestId("skills-name-input")).toBeInTheDocument());
    expect(screen.getByTestId("skills-category-input")).toBeInTheDocument();
    expect(screen.getByTestId("skills-description-input")).toBeInTheDocument();
  });
});

describe("SkillDetail", () => {
  it("renders skill hero + versions table", async () => {
    installAdapter([
      {
        match: (u, m) => u === "/v1/skills/sk1" && m === "get",
        respond: () => skillRow,
      },
      {
        match: (u) => u === "/v1/skills/sk1/versions",
        respond: () => ({ items: [versionRow] }),
      },
    ]);
    renderSkillsRouter(["/skills/sk1"]);
    // Name renders both in breadcrumb (Text code) and h1 hero — multiple matches OK.
    await waitFor(() => expect(screen.getAllByText("web_search").length).toBeGreaterThan(0));
    // Skill description is shown in the Metadata card.
    expect(screen.getByText(/Search the web/)).toBeInTheDocument();
    // Versions table — export button per version row.
    expect(screen.getByTestId("skill-export-1")).toBeInTheDocument();
  });

  it("404 / error path renders Alert", async () => {
    apiClient.defaults.adapter = (config) =>
      Promise.reject({
        isAxiosError: true,
        response: { status: 404, data: { detail: "skill not found" } },
        message: "skill not found",
        config,
      });
    renderSkillsRouter(["/skills/missing"]);
    await waitFor(() => expect(screen.getByTestId("skill-detail-error")).toBeInTheDocument());
  });
});
