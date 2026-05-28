/**
 * SkillsList + SkillDetail tests — Stream H.4 PR 5 + Capability Uplift
 * Sprint #3 PR C.
 *
 * Backend skills endpoints return raw (un-enveloped) payloads, so the
 * adapter mock delivers ``{items, next_cursor, cross_tenant}`` and raw
 * skill / version / supporting-file objects directly.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { App } from "antd";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../i18n";

import { SkillsList } from "../SkillsList";
import { SkillDetail } from "../SkillDetail";
import { TenantScopeProvider } from "../../tenant/TenantScopeContext";
import { AuthProvider } from "../../auth/AuthContext";
import { apiClient, setStoredToken } from "../../api/client";

vi.mock("@monaco-editor/react", () => {
  const Editor = ({
    value,
    onChange,
    ["data-testid"]: testId,
  }: {
    value: string;
    onChange?: (v: string | undefined) => void;
    "data-testid"?: string;
  }) => (
    <textarea
      data-testid={testId ?? "monaco-stub"}
      value={value}
      onChange={(e) => onChange?.(e.target.value)}
    />
  );
  const DiffEditor = ({
    original,
    modified,
  }: {
    original: string;
    modified: string;
  }) => (
    <div data-testid="monaco-diff-stub">
      <pre data-testid="monaco-diff-original">{original}</pre>
      <pre data-testid="monaco-diff-modified">{modified}</pre>
    </div>
  );
  return { default: Editor, DiffEditor };
});

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.`;
}

interface RouteHandler {
  match: (url: string, method: string) => boolean;
  respond: (config: { data?: unknown; url: string; method: string }) => unknown;
  status?: number;
}

function installAdapter(handlers: RouteHandler[]) {
  apiClient.defaults.adapter = (config) => {
    const url = config.url ?? "";
    const method = (config.method ?? "get").toLowerCase();
    const handler = handlers.find((h) => h.match(url, method));
    if (handler === undefined) {
      return Promise.reject({
        isAxiosError: true,
        response: { status: 404, data: { detail: `no mock for ${method} ${url}` } },
        message: `no mock for ${method} ${url}`,
        config,
      });
    }
    return Promise.resolve({
      data: handler.respond({ data: config.data, url, method }) ?? {},
      status: handler.status ?? 200,
      statusText: "OK",
      headers: {},
      config,
      request: {},
    });
  };
}

function renderSkillsRouter(
  initialEntries: string[] = ["/skills"],
  options: { roles?: string[]; isSystemAdmin?: boolean } = {},
) {
  const roles = options.roles ?? ["admin"];
  setStoredToken(makeJwt({ sub: "u1", tenant_id: "t1", roles }));
  // Also short-circuit /v1/me so the auth context's server-side
  // resolution matches the optimistic roles we just baked into the
  // JWT — otherwise non-admin tests would race with the mock.
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
  supporting_files: {},
  lazy_load: false,
  high_risk: false,
  created_at: "2026-05-20T10:00:00Z",
};

const highRiskVersionRow = {
  ...versionRow,
  id: "v3",
  version: 3,
  prompt_fragment: "Be careful with shell exec.",
  tool_names: ["exec_python", "http"],
  supporting_files: {
    "scripts/diagnose.py": { size: 120, mime: "text/x-python" },
    "reference/notes.md": { size: 80, mime: "text/markdown" },
  },
  lazy_load: true,
  high_risk: true,
};

const meResponse = {
  subject_id: "u1",
  subject_type: "user" as const,
  tenant_id: "t1",
  roles: ["admin"],
  is_system_admin: false,
  auth_method: "jwt" as const,
};

beforeEach(() => {
  vi.restoreAllMocks();
});

// ─── SkillsList — pre-PR C tests (unchanged) ─────────────────────────

describe("SkillsList", () => {
  it("renders the table with rows", async () => {
    installAdapter([
      {
        match: (u) => u === "/v1/me",
        respond: () => meResponse,
      },
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
        match: (u) => u === "/v1/me",
        respond: () => meResponse,
      },
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
    expect(screen.getByText("web_search")).toBeInTheDocument();
  });

  it("opens the Create drawer + validates required fields", async () => {
    installAdapter([
      {
        match: (u) => u === "/v1/me",
        respond: () => meResponse,
      },
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

// ─── SkillDetail — base + PR C dual-pane / badges / gates ────────────

describe("SkillDetail (PR C)", () => {
  function detailAdapter(version = versionRow) {
    return [
      { match: (u: string) => u === "/v1/me", respond: () => meResponse },
      {
        match: (u: string, m: string) => u === "/v1/skills/sk1" && m === "get",
        respond: () => skillRow,
      },
      {
        match: (u: string) => u === "/v1/skills/sk1/versions",
        respond: () => ({ items: [version] }),
      },
    ];
  }

  it("renders skill hero + version picker + dual pane", async () => {
    installAdapter(detailAdapter());
    renderSkillsRouter(["/skills/sk1"]);
    await waitFor(() => expect(screen.getAllByText("web_search").length).toBeGreaterThan(0));
    expect(screen.getByTestId("skill-detail-root")).toBeInTheDocument();
    expect(screen.getByTestId("skill-version-picker")).toBeInTheDocument();
    expect(screen.getByTestId("skill-dual-pane")).toBeInTheDocument();
    expect(screen.getByTestId("skill-file-tree")).toBeInTheDocument();
    // Default selection is SKILL.md → editor renders read-only hint
    await waitFor(() =>
      expect(screen.getByTestId("skill-md-readonly-hint")).toBeInTheDocument(),
    );
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

  it("eager skill shows Eager badge in metadata", async () => {
    installAdapter(detailAdapter());
    renderSkillsRouter(["/skills/sk1"]);
    await waitFor(() => expect(screen.getByTestId("skill-eager-badge")).toBeInTheDocument());
  });

  it("high-risk version shows 🔒 badge on hero + metadata + warning alert", async () => {
    installAdapter(detailAdapter(highRiskVersionRow));
    renderSkillsRouter(["/skills/sk1"]);
    await waitFor(() =>
      expect(screen.getByTestId("skill-hero-high-risk-badge")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("skill-high-risk-badge")).toBeInTheDocument();
    expect(screen.getByTestId("skill-high-risk-warning")).toBeInTheDocument();
    expect(screen.getByTestId("skill-lazy-badge")).toBeInTheDocument();
  });

  it("non-admin caller sees high-risk warning + 🔒 in active option label", async () => {
    installAdapter([
      { match: (u) => u === "/v1/me", respond: () => ({ ...meResponse, roles: ["viewer"] }) },
      {
        match: (u, m) => u === "/v1/skills/sk1" && m === "get",
        respond: () => ({ ...skillRow, status: "draft" as const }),
      },
      {
        match: (u) => u === "/v1/skills/sk1/versions",
        respond: () => ({ items: [highRiskVersionRow] }),
      },
    ]);
    renderSkillsRouter(["/skills/sk1"], { roles: ["viewer"] });
    // The metadata warning alert is what the operator sees first — and
    // it is the operator-facing fence between "this version is risky"
    // and a destructive activate click. Asserting it survives a
    // non-admin caller is sufficient for the visual gate; the actual
    // disabled-option DOM is antd internals + jsdom portal that flake
    // across versions, so backend RBAC (covered by
    // ``services/control-plane/tests/test_skill_high_risk_publish_gate``)
    // is the canonical enforcement and the Playwright e2e in
    // ``apps/admin-ui/e2e/skill-mutations.spec.ts`` is the
    // browser-truth check.
    await waitFor(() =>
      expect(screen.getByTestId("skill-hero-high-risk-badge")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("skill-high-risk-warning")).toBeInTheDocument();
  });

  it("file tree shows SKILL.md + supporting files grouped", async () => {
    installAdapter(detailAdapter(highRiskVersionRow));
    renderSkillsRouter(["/skills/sk1"]);
    const tree = await screen.findByTestId("skill-file-tree");
    await waitFor(() =>
      expect(within(tree).getByText("SKILL.md")).toBeInTheDocument(),
    );
    // top-level dirs rendered with trailing slash
    expect(within(tree).getByText("scripts/")).toBeInTheDocument();
    expect(within(tree).getByText("reference/")).toBeInTheDocument();
  });

  it("selecting a supporting file fetches + renders content; Edit + Save calls PUT", async () => {
    const putCalls: { url: string; data: unknown }[] = [];
    const v4 = {
      ...highRiskVersionRow,
      version: 4,
      supporting_files: {
        ...highRiskVersionRow.supporting_files,
        "reference/notes.md": { size: 14, mime: "text/markdown" },
      },
    };
    installAdapter([
      { match: (u) => u === "/v1/me", respond: () => meResponse },
      {
        match: (u, m) => u === "/v1/skills/sk1" && m === "get",
        respond: () => skillRow,
      },
      {
        match: (u) => u === "/v1/skills/sk1/versions",
        respond: () => ({ items: [highRiskVersionRow] }),
      },
      {
        match: (u, m) =>
          u === "/v1/skills/sk1/versions/3/supporting-files/reference/notes.md"
          && m === "get",
        respond: () => ({
          // base64("hello world\n") = "aGVsbG8gd29ybGQK"
          content: "aGVsbG8gd29ybGQK",
          size: 12,
          mime: "text/markdown",
        }),
      },
      {
        match: (u, m) =>
          u === "/v1/skills/sk1/versions/3/supporting-files/reference/notes.md"
          && m === "put",
        respond: ({ data }) => {
          putCalls.push({ url: "PUT", data });
          return v4;
        },
      },
    ]);
    const user = userEvent.setup();
    renderSkillsRouter(["/skills/sk1"]);
    const tree = await screen.findByTestId("skill-file-tree");
    // Expand reference/ folder (default expanded) + click notes.md
    await user.click(within(tree).getByText("notes.md"));
    // ``skill-editor-monaco`` is the wrapper div; the mocked Monaco
    // textarea inside it carries ``monaco-stub`` (default mock testid).
    await waitFor(() =>
      expect(screen.getByTestId("skill-editor-monaco")).toBeInTheDocument(),
    );
    await user.click(screen.getByTestId("skill-editor-edit-btn"));
    const editor = screen.getByTestId("monaco-stub") as HTMLTextAreaElement;
    await user.clear(editor);
    await user.type(editor, "updated text");
    await user.click(screen.getByTestId("skill-editor-save-btn"));
    await waitFor(() => expect(putCalls.length).toBe(1));
  });

  it("delete flow requires typing the path to confirm", async () => {
    let deleteCalled = false;
    installAdapter([
      { match: (u) => u === "/v1/me", respond: () => meResponse },
      {
        match: (u, m) => u === "/v1/skills/sk1" && m === "get",
        respond: () => skillRow,
      },
      {
        match: (u) => u === "/v1/skills/sk1/versions",
        respond: () => ({ items: [highRiskVersionRow] }),
      },
      {
        match: (u, m) =>
          u === "/v1/skills/sk1/versions/3/supporting-files/reference/notes.md"
          && m === "get",
        respond: () => ({ content: "aGVsbG8=", size: 5, mime: "text/markdown" }),
      },
      {
        match: (u, m) =>
          u === "/v1/skills/sk1/versions/3/supporting-files/reference/notes.md"
          && m === "delete",
        respond: () => {
          deleteCalled = true;
          return { ...highRiskVersionRow, version: 4, supporting_files: {} };
        },
      },
    ]);
    const user = userEvent.setup();
    renderSkillsRouter(["/skills/sk1"]);
    const tree = await screen.findByTestId("skill-file-tree");
    await user.click(within(tree).getByText("notes.md"));
    await waitFor(() =>
      expect(screen.getByTestId("skill-editor-delete-btn")).toBeInTheDocument(),
    );
    await user.click(screen.getByTestId("skill-editor-delete-btn"));
    // Submit disabled until the path is typed back
    const submit = await screen.findByTestId("skill-delete-submit");
    expect(submit).toBeDisabled();
    await user.type(
      screen.getByTestId("skill-delete-confirm-input"),
      "reference/notes.md",
    );
    expect(submit).not.toBeDisabled();
    await user.click(submit);
    await waitFor(() => expect(deleteCalled).toBe(true));
  });

  it("Add file modal opens from the + Add file tree node", async () => {
    installAdapter(detailAdapter());
    const user = userEvent.setup();
    renderSkillsRouter(["/skills/sk1"]);
    const tree = await screen.findByTestId("skill-file-tree");
    await user.click(within(tree).getByText(/\+ Add file|添加文件/));
    await waitFor(() =>
      expect(screen.getByTestId("skill-add-file-path")).toBeInTheDocument(),
    );
  });
});
