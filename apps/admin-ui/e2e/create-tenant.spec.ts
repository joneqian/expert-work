/**
 * Create Tenant page e2e — Stream P (PR E).
 *
 * Two paths: a system_admin sees the form and can create a tenant; a
 * non-admin sees the "system admin only" notice. Both run axe. The
 * ``mockControlPlane`` fixture stubs ``/v1/me`` with ``is_system_admin:
 * false`` by default, so the admin test overrides that route first
 * (Playwright routes are LIFO — last registered wins).
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

const CREATED_TENANT = {
  success: true,
  data: {
    tenant_id: "33333333-3333-3333-3333-333333333333",
    display_name: "Acme Inc",
    plan: "free",
  },
  error: null,
};

const CREATED_WITH_ADMIN = {
  success: true,
  data: {
    tenant_id: "33333333-3333-3333-3333-333333333333",
    display_name: "Acme Inc",
    plan: "free",
    first_admin: {
      member_id: "44444444-4444-4444-4444-444444444444",
      email: "boss@acme.com",
      status: "invited",
      keycloak_user_id: "55555555-5555-5555-5555-555555555555",
    },
  },
  error: null,
};

async function login(page: import("@playwright/test").Page): Promise<void> {
  await page.goto("/login");
  await page.getByTestId("login-token").fill(SAMPLE_JWT);
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/agents$/);
}

test("system_admin creates a tenant + passes axe", async ({ page }) => {
  await page.route("**/v1/me", async (route) => {
    await route.fulfill({ json: SYS_ADMIN_ME });
  });
  await page.route("**/v1/tenants", async (route) => {
    await route.fulfill({ status: 201, json: CREATED_TENANT });
  });
  await login(page);
  await page.goto("/settings/create-tenant");

  await expect(page.getByTestId("ct-display-name")).toBeVisible();
  await page.getByTestId("ct-display-name").fill("Acme Inc");
  await page.getByTestId("ct-submit").click();

  await expect(page.getByTestId("ct-created")).toBeVisible();
  await expect(page.getByTestId("ct-created-id")).toContainText(
    "33333333-3333-3333-3333-333333333333",
  );
  await expectNoA11yViolations(page, "/settings/create-tenant");
});

test("system_admin creates a tenant with a first admin", async ({ page }) => {
  await page.route("**/v1/me", async (route) => {
    await route.fulfill({ json: SYS_ADMIN_ME });
  });
  await page.route("**/v1/tenants", async (route) => {
    await route.fulfill({ status: 201, json: CREATED_WITH_ADMIN });
  });
  await login(page);
  await page.goto("/settings/create-tenant");

  await page.getByTestId("ct-display-name").fill("Acme Inc");
  await page.getByTestId("ct-first-admin-email").fill("boss@acme.com");
  await page.getByTestId("ct-submit").click();

  await expect(page.getByTestId("ct-created")).toBeVisible();
  await expect(page.getByTestId("ct-first-admin")).toContainText("boss@acme.com");
  await expect(page.getByTestId("ct-first-admin")).toContainText("invited");
});

test("non-admin sees system-admin-only notice + passes axe", async ({ page }) => {
  // Default mockControlPlane /v1/me has is_system_admin: false.
  await login(page);
  await page.goto("/settings/create-tenant");

  await expect(page.getByTestId("ct-not-admin")).toBeVisible();
  await expect(page.getByTestId("ct-form")).toHaveCount(0);
  await expectNoA11yViolations(page, "/settings/create-tenant");
});
