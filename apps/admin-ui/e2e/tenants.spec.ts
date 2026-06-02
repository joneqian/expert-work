/**
 * Settings — Tenants page e2e (Stream U PR D).
 *
 * Proves a system_admin can list every tenant on the platform at
 * /settings/tenants and jump into a tenant's per-tenant config via the row's
 * "Manage" action. Plus an axe pass on the rendered page.
 *
 * The table is rendered only on the system_admin branch of the page, which
 * gates on /v1/me's ``is_system_admin``. The default fixture user is NOT a
 * system admin, so (mirroring platform-embedding.spec.ts) we override /v1/me
 * with a system-admin identity and stub the ``GET /v1/tenants`` list the page
 * issues. Playwright routes are LIFO, so these spec-level routes win over the
 * fixture stub.
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

const TENANTS_LIST = {
  success: true,
  data: [
    {
      tenant_id: "11111111-1111-1111-1111-111111111111",
      display_name: "乐毅大公司",
      plan: "free",
      created_at: "2026-06-02T00:00:00Z",
      status: "active",
    },
    {
      tenant_id: "33333333-3333-3333-3333-333333333333",
      display_name: "停用公司",
      plan: "free",
      created_at: "2026-06-02T00:00:00Z",
      status: "suspended",
    },
  ],
  error: null,
};

async function login(page: import("@playwright/test").Page): Promise<void> {
  await page.goto("/login");
  await expect(page.getByTestId("login-card")).toBeVisible();
  // Paste-token form sits behind the "Developer login" disclosure when OIDC is
  // configured; reveal it if collapsed (CI opens it by default).
  const tokenField = page.getByTestId("login-token");
  if (!(await tokenField.isVisible())) {
    await page.getByTestId("login-dev-toggle").click();
  }
  await tokenField.fill(SAMPLE_JWT);
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/agents$/);
}

test.beforeEach(async ({ page }) => {
  await page.route("**/v1/me", async (route) => {
    await route.fulfill({ json: SYS_ADMIN_ME });
  });
  await page.route("**/v1/tenants*", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: TENANTS_LIST });
      return;
    }
    await route.continue();
  });
});

test("lists tenants", async ({ page }) => {
  await login(page);
  await page.goto("/settings/tenants");

  await expect(page.getByTestId("st-table")).toBeVisible();
  await expect(page.getByText("乐毅大公司")).toBeVisible();
});

test("shows tenant status badges", async ({ page }) => {
  await login(page);
  await page.goto("/settings/tenants");

  await expect(page.getByTestId("st-table")).toBeVisible();
  await expect(
    page.getByTestId("st-status-11111111-1111-1111-1111-111111111111"),
  ).toHaveText("Active");
  await expect(
    page.getByTestId("st-status-33333333-3333-3333-3333-333333333333"),
  ).toHaveText("Suspended");
});

test("manage navigates to tenant-config", async ({ page }) => {
  await login(page);
  await page.goto("/settings/tenants");

  await expect(page.getByTestId("st-table")).toBeVisible();
  await page
    .getByTestId("st-manage-11111111-1111-1111-1111-111111111111")
    .click();
  await expect(page).toHaveURL(/\/settings\/tenant-config/);
});

test("settings/tenants passes axe", async ({ page }) => {
  await login(page);
  await page.goto("/settings/tenants");

  await expect(page.getByTestId("st-table")).toBeVisible();
  await expectNoA11yViolations(page, "settings-tenants");
});
