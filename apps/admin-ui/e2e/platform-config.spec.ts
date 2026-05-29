/**
 * Platform Credentials page e2e — Stream P (PR I).
 *
 * system_admin sees the provider/tool tables; a non-admin sees the
 * "system admin only" notice. Both run axe. The default mock has
 * ``is_system_admin: false``, so the admin test overrides ``/v1/me`` and
 * stubs the platform-credentials GET (Playwright routes are LIFO).
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

const VIEW = {
  success: true,
  data: {
    providers: [
      { provider: "anthropic", source: "db", secret_ref: "kms://platform/anthropic", enabled: true, used_by_agents: 3 },
      { provider: "qwen", source: "unset", secret_ref: null, enabled: false, used_by_agents: 0 },
    ],
    tools: [{ tool: "web_search", source: "env", secret_ref: "secret://tavily", enabled: true, used_by_agents: 1 }],
  },
  error: null,
};

async function login(page: import("@playwright/test").Page): Promise<void> {
  await page.goto("/login");
  await page.getByTestId("login-token").fill(SAMPLE_JWT);
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/agents$/);
}

test("system_admin sees platform credential tables + passes axe", async ({ page }) => {
  await page.route("**/v1/me", async (route) => {
    await route.fulfill({ json: SYS_ADMIN_ME });
  });
  await page.route("**/v1/platform/credentials", async (route) => {
    await route.fulfill({ json: VIEW });
  });
  await login(page);
  await page.goto("/settings/platform");

  await expect(page.getByTestId("pc-providers-table")).toBeVisible();
  await expect(page.getByText("anthropic", { exact: true })).toBeVisible();
  await expect(page.getByTestId("pc-tools-table")).toBeVisible();
  await expectNoA11yViolations(page, "/settings/platform");
});

test("non-admin sees system-admin-only notice + passes axe", async ({ page }) => {
  await login(page);
  await page.goto("/settings/platform");

  await expect(page.getByTestId("pc-not-admin")).toBeVisible();
  await expect(page.getByTestId("pc-providers-table")).toHaveCount(0);
  await expectNoA11yViolations(page, "/settings/platform");
});
