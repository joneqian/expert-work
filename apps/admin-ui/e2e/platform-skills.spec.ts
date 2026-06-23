/**
 * Platform Skills page e2e — Stream X (X5, system_admin).
 *
 * system_admin sees the platform skill table; a non-admin sees the
 * "system admin only" notice. Both run axe. Default mock has
 * ``is_system_admin: false`` so the admin test overrides ``/v1/me`` and stubs
 * the platform-skills GET (Playwright routes are LIFO).
 */
import { test, expect, expectNoA11yViolations, SAMPLE_JWT } from "./fixtures";

const SYS_ADMIN_ME = {
  success: true,
  data: {
    subject_id: "11111111-1111-1111-1111-111111111111",
    subject_type: "user",
    tenant_id: "22222222-2222-2222-2222-222222222222",
    auth_method: "jwt",
    roles: ["admin"],
    scopes: [],
    is_system_admin: true,
    allowed_tenants: "*",
  },
  error: null,
};

// Raw, NOT enveloped: the platform-skills backend returns bare
// ``JSONResponse(content={...})`` (no ``{success,data,error}``). The mock
// must mirror that or the SDK's raw read yields no rows. See
// ``api/platform-skills.ts`` header + the matching vitest mock.
const SKILLS = {
  items: [
    {
      id: "psk-1",
      name: "web_search",
      status: "active",
      latest_version: 2,
      description: "Search the web and return top N results.",
      category: "web",
      pinned: false,
      required_tier: "pro",
      last_used_at: "2026-05-25T10:00:00Z",
      state_changed_at: "2026-05-20T10:00:00Z",
      created_at: "2026-05-20T10:00:00Z",
      updated_at: "2026-05-26T10:00:00Z",
    },
  ],
  total: 1,
};

async function login(page: import("@playwright/test").Page): Promise<void> {
  await page.goto("/login");
  await expect(page.getByTestId("login-card")).toBeVisible();
  const tokenField = page.getByTestId("login-token");
  if (!(await tokenField.isVisible())) {
    await page.getByTestId("login-dev-toggle").click();
  }
  await tokenField.fill(SAMPLE_JWT);
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/agents$/);
}

test("system_admin sees the platform skill table + passes axe", async ({ page }) => {
  await page.route("**/v1/me", async (route) => {
    await route.fulfill({ json: SYS_ADMIN_ME });
  });
  await page.route("**/v1/platform/skills*", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: SKILLS });
      return;
    }
    await route.fallback();
  });
  await login(page);
  await page.goto("/settings/platform-skills");

  await expect(page.getByTestId("ps-table")).toBeVisible();
  await expect(page.getByText("web_search", { exact: true })).toBeVisible();

  // Phase D: creation is import-only — the Import .skill action is primary,
  // the hand-build "New skill" drawer is gone.
  await expect(page.getByTestId("ps-import-btn")).toBeVisible();
  await expect(page.getByTestId("ps-add")).toHaveCount(0);

  await expectNoA11yViolations(page, "/settings/platform-skills");
});

test("non-admin sees system-admin-only notice + passes axe", async ({ page }) => {
  await login(page);
  await page.goto("/settings/platform-skills");

  await expect(page.getByTestId("ps-not-admin")).toBeVisible();
  await expect(page.getByTestId("ps-table")).toHaveCount(0);
  await expectNoA11yViolations(page, "/settings/platform-skills");
});

test("system_admin imports a skill from GitHub", async ({ page }) => {
  const IMPORTED = {
    skill: {
      id: "psk-gh",
      name: "find-skills",
      status: "active",
      latest_version: 1,
      description: "Find skills.",
      category: "meta",
      pinned: false,
      required_tier: "free",
      last_used_at: null,
      state_changed_at: "2026-06-20T10:00:00Z",
      created_at: "2026-06-20T10:00:00Z",
      updated_at: "2026-06-20T10:00:00Z",
    },
    version: { version: 1, tool_names: [] },
    created: true,
  };
  await page.route("**/v1/me", async (route) => {
    await route.fulfill({ json: SYS_ADMIN_ME });
  });
  // Listing fails (e.g. unreachable source) → the dialog falls back to the
  // manual skill-path input, and a single import still works.
  await page.route("**/v1/platform/skills/list-github-skills", async (route) => {
    await route.fulfill({
      status: 400,
      json: { detail: { code: "GITHUB_IMPORT_ERROR", message: "cannot reach source" } },
    });
  });
  await page.route("**/v1/platform/skills/import-from-github", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({ status: 201, json: IMPORTED });
      return;
    }
    await route.fallback();
  });
  await page.route("**/v1/platform/skills*", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: SKILLS });
      return;
    }
    await route.fallback();
  });
  await login(page);
  await page.goto("/settings/platform-skills");

  await expect(page.getByTestId("ps-table")).toBeVisible();
  await page.getByTestId("ps-import-github-btn").click();
  await expect(page.getByTestId("ps-github-source")).toBeVisible();
  await page.getByTestId("ps-github-source").fill("vercel-labs/skills");
  // Listing failed → manual skill-path input is shown.
  await expect(page.getByTestId("ps-github-skill")).toBeVisible();
  await page.getByTestId("ps-github-skill").fill("find-skills");

  const [req] = await Promise.all([
    page.waitForRequest(
      (r) =>
        r.url().includes("/v1/platform/skills/import-from-github") &&
        r.method() === "POST",
    ),
    page.getByTestId("ps-github-submit").click(),
  ]);
  const body = req.postDataJSON();
  expect(body.source).toBe("vercel-labs/skills");
  expect(body.skill).toBe("find-skills");
  // Full success → the dialog auto-closes.
  await expect(page.getByTestId("ps-github-modal")).toBeHidden();
});

test("GitHub multi-skill repo → auto-listed picker + batch import with results", async ({
  page,
}) => {
  await page.route("**/v1/me", async (route) => {
    await route.fulfill({ json: SYS_ADMIN_ME });
  });
  // Entering a source auto-lists its skills (no discover-click).
  await page.route("**/v1/platform/skills/list-github-skills", async (route) => {
    await route.fulfill({
      status: 200,
      json: { candidates: ["skills/find-skills", "skills/other"] },
    });
  });
  await page.route("**/v1/platform/skills/import-from-github/batch", async (route) => {
    await route.fulfill({
      status: 200,
      json: {
        results: [
          { skill: "skills/find-skills", status: "created", name: "find-skills", version: 1 },
          { skill: "skills/other", status: "failed", reason: "invalid skill content" },
        ],
      },
    });
  });
  await page.route("**/v1/platform/skills*", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: SKILLS });
      return;
    }
    await route.fallback();
  });
  await login(page);
  await page.goto("/settings/platform-skills");

  await page.getByTestId("ps-import-github-btn").click();
  await page.getByTestId("ps-github-source").fill("vercel-labs/skills");
  // Candidates auto-appear from the listing — no "click import to discover".
  await expect(page.getByTestId("ps-github-candidates-hint")).toBeVisible();
  await expect(page.getByTestId("ps-github-skill-select")).toBeVisible();

  // Select all candidates (avoids antd dropdown-portal click flake), then batch.
  await page.getByTestId("ps-github-select-all").click();
  const [req] = await Promise.all([
    page.waitForRequest(
      (r) =>
        r.url().includes("/v1/platform/skills/import-from-github/batch") &&
        r.method() === "POST",
    ),
    page.getByTestId("ps-github-submit").click(),
  ]);
  const body = req.postDataJSON() as { skills: string[] };
  expect(body.skills.sort()).toEqual(["skills/find-skills", "skills/other"]);

  // Partial failure → per-skill results render and the dialog stays open.
  const results = page.getByTestId("ps-github-results");
  await expect(results).toBeVisible();
  await expect(results.getByText("skills/find-skills")).toBeVisible();
  await expect(results.getByText("invalid skill content")).toBeVisible();
});

test("GitHub batch import auto-closes the dialog on full success", async ({ page }) => {
  await page.route("**/v1/me", async (route) => {
    await route.fulfill({ json: SYS_ADMIN_ME });
  });
  await page.route("**/v1/platform/skills/list-github-skills", async (route) => {
    await route.fulfill({
      status: 200,
      json: { candidates: ["skills/a", "skills/b"] },
    });
  });
  await page.route("**/v1/platform/skills/import-from-github/batch", async (route) => {
    await route.fulfill({
      status: 200,
      json: {
        results: [
          { skill: "skills/a", status: "created", name: "a", version: 1 },
          { skill: "skills/b", status: "created", name: "b", version: 1 },
        ],
      },
    });
  });
  await page.route("**/v1/platform/skills*", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: SKILLS });
      return;
    }
    await route.fallback();
  });
  await login(page);
  await page.goto("/settings/platform-skills");

  await page.getByTestId("ps-import-github-btn").click();
  await page.getByTestId("ps-github-source").fill("owner/repo");
  await expect(page.getByTestId("ps-github-skill-select")).toBeVisible();
  await page.getByTestId("ps-github-select-all").click();
  await Promise.all([
    page.waitForRequest(
      (r) =>
        r.url().includes("/v1/platform/skills/import-from-github/batch") &&
        r.method() === "POST",
    ),
    page.getByTestId("ps-github-submit").click(),
  ]);

  // Full success → the dialog auto-closes (no leftover results panel).
  await expect(page.getByTestId("ps-github-modal")).toBeHidden();
});

test("system_admin batch-locks selected skills via the server-side batch endpoint", async ({
  page,
}) => {
  const TWO = {
    items: [SKILLS.items[0], { ...SKILLS.items[0], id: "psk-2", name: "code_search" }],
    total: 2,
  };
  await page.route("**/v1/me", async (route) => {
    await route.fulfill({ json: SYS_ADMIN_ME });
  });
  // Routes are LIFO — register the list (broad) first, then the batch route so
  // the more specific ``/batch`` handler wins for the POST.
  await page.route("**/v1/platform/skills*", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: TWO });
      return;
    }
    await route.fallback();
  });
  // One atomic batch call instead of a PATCH per row.
  await page.route("**/v1/platform/skills/batch", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({ status: 200, json: { updated: 2 } });
      return;
    }
    await route.fallback();
  });
  await login(page);
  await page.goto("/settings/platform-skills");
  await expect(page.getByTestId("ps-table")).toBeVisible();

  // Header checkbox selects all rows → the batch toolbar appears.
  await page.getByRole("checkbox").first().check();
  await expect(page.getByTestId("ps-batch-toolbar")).toBeVisible();
  // Guard: labels must resolve, not show raw i18n keys (regression #769).
  await expect(page.getByTestId("ps-batch-lock")).not.toContainText("platform_skills");

  const [req] = await Promise.all([
    page.waitForRequest(
      (r) =>
        r.url().includes("/v1/platform/skills/batch") && r.method() === "POST",
    ),
    page.getByTestId("ps-batch-lock").click(),
  ]);
  const body = req.postDataJSON() as { set_pinned?: boolean; ids?: string[] };
  // Page selection → ``ids`` scope; lock sets pinned=true.
  expect(body.set_pinned).toBe(true);
  expect(body.ids?.sort()).toEqual(["psk-1", "psk-2"]);
  // Batch completes → selection clears → toolbar hides.
  await expect(page.getByTestId("ps-batch-toolbar")).toBeHidden();
});

test("search narrows the platform skill list (server-side q)", async ({ page }) => {
  const TWO = {
    items: [SKILLS.items[0], { ...SKILLS.items[0], id: "psk-2", name: "code_search" }],
    total: 2,
  };
  const FILTERED = { items: [SKILLS.items[0]], total: 1 };
  await page.route("**/v1/me", async (route) => {
    await route.fulfill({ json: SYS_ADMIN_ME });
  });
  await page.route("**/v1/platform/skills*", async (route) => {
    if (route.request().method() === "GET") {
      const url = new URL(route.request().url());
      const q = url.searchParams.get("q");
      await route.fulfill({ json: q ? FILTERED : TWO });
      return;
    }
    await route.fallback();
  });
  await login(page);
  await page.goto("/settings/platform-skills");
  await expect(page.getByTestId("ps-table")).toBeVisible();
  await expect(page.getByText("code_search", { exact: true })).toBeVisible();

  // Typing a query refetches with ``q`` → the backend returns the narrowed set.
  const [req] = await Promise.all([
    page.waitForRequest(
      (r) =>
        r.url().includes("/v1/platform/skills") &&
        r.method() === "GET" &&
        new URL(r.url()).searchParams.get("q") === "web",
    ),
    page.getByTestId("ps-search").fill("web"),
  ]);
  expect(new URL(req.url()).searchParams.get("q")).toBe("web");
  await expect(page.getByText("code_search", { exact: true })).toBeHidden();
  await expect(page.getByText("web_search", { exact: true })).toBeVisible();
});
