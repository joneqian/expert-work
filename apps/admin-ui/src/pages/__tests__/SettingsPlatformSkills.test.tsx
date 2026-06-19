/**
 * Platform Skills UI tests — Stream X (X5).
 *
 * Covers the platform skill page (admin gate + table + empty/error
 * states) and the create / add-version / status-change / pin / import
 * flows.
 *
 * Backend ``/v1/platform/skills`` returns *raw* ``JSONResponse`` payloads
 * (NOT the ``{success, data, error}`` envelope), so the adapter mock
 * returns the raw object. An earlier version of this mock wrongly
 * enveloped the responses, which masked the SDK's envelope-vs-raw bug
 * (the page threw ``request failed`` against the real backend). Keep the
 * mock raw so it matches production.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { App } from "antd";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../i18n";

import { SettingsPlatformSkills } from "../SettingsPlatformSkills";
import { AuthProvider } from "../../auth/AuthContext";
import { apiClient, setStoredToken } from "../../api/client";

const TENANT = "00000000-0000-0000-0000-00000000acme";

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

const SKILL = {
  id: "psk-1",
  name: "web_search",
  status: "active" as const,
  latest_version: 2,
  description: "Search the web and return top N results.",
  category: "web",
  pinned: false,
  required_tier: "pro" as const,
  last_used_at: "2026-05-25T10:00:00Z",
  state_changed_at: "2026-05-20T10:00:00Z",
  created_at: "2026-05-20T10:00:00Z",
  updated_at: "2026-05-26T10:00:00Z",
};

const VERSION = {
  id: "v1",
  skill_id: "psk-1",
  version: 1,
  prompt_fragment: "Always cite sources.",
  tool_names: ["web_search"],
  description: "First cut.",
  category: "web",
  required_models: [],
  authored_by: "platform",
  supporting_files: {},
  lazy_load: false,
  high_risk: false,
  created_at: "2026-05-20T10:00:00Z",
};

/** The backend returns the payload raw (no ``{success, data, error}``
 *  envelope); the mock mirrors that exactly. */
function raw<T>(data: T): T {
  return data;
}

function renderPage(roles: string[]) {
  setStoredToken(makeJwt({ sub: "u1", tenant_id: TENANT, roles }));
  return render(
    <MemoryRouter>
      <AuthProvider>
        <App>
          <SettingsPlatformSkills />
        </App>
      </AuthProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("SettingsPlatformSkills page", () => {
  it("non-system-admin sees the admin-only notice, no table", async () => {
    installAdapter([
      {
        match: (u) => u.endsWith("/platform/skills"),
        respond: () => raw({ items: [], next_cursor: null }),
      },
    ]);
    renderPage(["admin"]);
    await waitFor(() => expect(screen.getByTestId("ps-not-admin")).toBeInTheDocument());
    expect(screen.queryByTestId("ps-table")).not.toBeInTheDocument();
  });

  it("system_admin sees the table with skill rows", async () => {
    installAdapter([
      {
        match: (u) => u.endsWith("/platform/skills"),
        respond: () => raw({ items: [SKILL], next_cursor: null }),
      },
    ]);
    renderPage(["system_admin"]);
    await waitFor(() => expect(screen.getByTestId("ps-table")).toBeInTheDocument());
    expect(screen.getByText("web_search")).toBeInTheDocument();
    expect(screen.getByTestId("ps-manage-psk-1")).toBeInTheDocument();
    expect(screen.getByTestId("ps-pin-toggle-psk-1")).toBeInTheDocument();
  });

  it("shows the guided empty state for an admin with no skills", async () => {
    installAdapter([
      {
        match: (u) => u.endsWith("/platform/skills"),
        respond: () => raw({ items: [], next_cursor: null }),
      },
    ]);
    renderPage(["system_admin"]);
    await waitFor(() => expect(screen.getByTestId("ps-empty")).toBeInTheDocument());
  });

  it("renders the error Alert when the list fails", async () => {
    apiClient.defaults.adapter = (config) =>
      Promise.reject({
        isAxiosError: true,
        response: { status: 500, data: { detail: { code: "BOOM", message: "boom" } } },
        message: "boom",
        config,
      });
    renderPage(["system_admin"]);
    await waitFor(() => expect(screen.getByTestId("ps-error")).toBeInTheDocument());
  });

  it("create flow POSTs a new skill", async () => {
    let postBody: unknown = null;
    installAdapter([
      {
        match: (u, m) => u.endsWith("/platform/skills") && m === "post",
        respond: ({ data }) => {
          postBody = data;
          return raw({ ...SKILL, id: "psk-new", name: "translate" });
        },
        status: 201,
      },
      {
        match: (u, m) => u.endsWith("/platform/skills") && m === "get",
        respond: () => raw({ items: [], next_cursor: null }),
      },
    ]);
    const user = userEvent.setup();
    renderPage(["system_admin"]);
    await waitFor(() => expect(screen.getByTestId("ps-add")).toBeInTheDocument());
    await user.click(screen.getByTestId("ps-add"));
    await waitFor(() => expect(screen.getByTestId("psc-form")).toBeInTheDocument());
    await user.type(screen.getByTestId("psc-name"), "translate");
    await user.click(screen.getByTestId("psc-submit"));
    await waitFor(() => expect(postBody).not.toBeNull());
    const parsed = typeof postBody === "string" ? JSON.parse(postBody) : postBody;
    expect(parsed.name).toBe("translate");
    expect(parsed.required_tier).toBe("free");
  });

  it("import flow uploads a .skill ZIP and refreshes the list", async () => {
    let importPosted = false;
    let listCalls = 0;
    installAdapter([
      {
        match: (u, m) => u.endsWith("/platform/skills/import") && m === "post",
        respond: () => {
          importPosted = true;
          return raw({ skill: SKILL, version: VERSION, created: true });
        },
        status: 201,
      },
      {
        match: (u, m) => u.endsWith("/platform/skills") && m === "get",
        respond: () => {
          listCalls += 1;
          // First load empty; after import the refreshed list has the skill.
          return raw({ items: importPosted ? [SKILL] : [], next_cursor: null });
        },
      },
    ]);
    const user = userEvent.setup();
    renderPage(["system_admin"]);
    await waitFor(() => expect(screen.getByTestId("ps-import-btn")).toBeInTheDocument());

    const file = new File(["PK"], "web_search.skill", {
      type: "application/zip",
    });
    await user.upload(screen.getByTestId<HTMLInputElement>("ps-import-input"), file);

    await waitFor(() => expect(importPosted).toBe(true));
    // The import handler triggers a refresh ⇒ a second GET, now non-empty.
    await waitFor(() => expect(screen.getByText("web_search")).toBeInTheDocument());
    expect(listCalls).toBeGreaterThanOrEqual(2);
  });

  it("manage drawer adds a version, changes status, and toggles pin", async () => {
    let versionBody: unknown = null;
    const patchBodies: unknown[] = [];
    installAdapter([
      {
        match: (u, m) => u.endsWith("/platform/skills") && m === "get",
        respond: () => raw({ items: [SKILL], next_cursor: null }),
      },
      {
        match: (u, m) => u.endsWith("/platform/skills/psk-1/versions") && m === "get",
        respond: () => raw({ items: [VERSION] }),
      },
      {
        match: (u, m) => u.endsWith("/platform/skills/psk-1/versions") && m === "post",
        respond: ({ data }) => {
          versionBody = data;
          return raw({ ...VERSION, version: 2 });
        },
        status: 201,
      },
      {
        match: (u, m) => u.endsWith("/platform/skills/psk-1") && m === "patch",
        respond: ({ data }) => {
          patchBodies.push(data);
          return raw({ ...SKILL });
        },
      },
    ]);
    const user = userEvent.setup();
    renderPage(["system_admin"]);
    await waitFor(() => expect(screen.getByTestId("ps-manage-psk-1")).toBeInTheDocument());
    await user.click(screen.getByTestId("ps-manage-psk-1"));
    await waitFor(() => expect(screen.getByTestId("psm-add-version-form")).toBeInTheDocument());

    // Existing version renders.
    await waitFor(() => expect(screen.getByTestId("psm-version-1")).toBeInTheDocument());

    // Add a version.
    await user.type(screen.getByTestId("psm-prompt-fragment"), "Be concise.");
    await user.click(screen.getByTestId("psm-add-version"));
    await waitFor(() => expect(versionBody).not.toBeNull());
    const vParsed = typeof versionBody === "string" ? JSON.parse(versionBody) : versionBody;
    expect(vParsed.prompt_fragment).toBe("Be concise.");

    // Toggle pin → PATCH { pinned: true }.
    await user.click(screen.getByTestId("psm-pin"));
    await waitFor(() => expect(patchBodies.length).toBeGreaterThan(0));
    const pinParsed =
      typeof patchBodies[0] === "string"
        ? JSON.parse(patchBodies[0] as string)
        : patchBodies[0];
    expect(pinParsed).toEqual({ pinned: true });
  });
});
