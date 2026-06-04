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

const SKILLS = {
  success: true,
  data: {
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
    next_cursor: null,
  },
  error: null,
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

  // Open the create drawer to surface the form.
  await page.getByTestId("ps-add").click();
  await expect(page.getByTestId("psc-form")).toBeVisible();

  await expectNoA11yViolations(page, "/settings/platform-skills");
});

test("non-admin sees system-admin-only notice + passes axe", async ({ page }) => {
  await login(page);
  await page.goto("/settings/platform-skills");

  await expect(page.getByTestId("ps-not-admin")).toBeVisible();
  await expect(page.getByTestId("ps-table")).toHaveCount(0);
  await expectNoA11yViolations(page, "/settings/platform-skills");
});
