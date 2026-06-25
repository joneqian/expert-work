/**
 * MCP Catalog platform page e2e — Stream W (system_admin).
 *
 * system_admin sees the connector catalog table; a non-admin sees the
 * "system admin only" notice. Both run axe. Default mock has
 * ``is_system_admin: false`` so the admin test overrides ``/v1/me`` and stubs
 * the catalog GET (Playwright routes are LIFO).
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

const CATALOG = {
  success: true,
  data: [
    {
      id: "cat-1",
      name: "github",
      display_name: "GitHub",
      description: "GitHub MCP connector",
      category: "dev-tools",
      icon: "",
      transport: "sse",
      url_template: "https://mcp.github.com/sse",
      auth_type: "bearer",
      auth_schema: {
        fields: [
          { key: "token", label: "Token", kind: "secret", required: true },
        ],
      },
      required_tier: "pro",
      enabled: true,
      created_at: "2026-05-01T10:00:00Z",
      updated_at: "2026-05-01T10:00:00Z",
      updated_by: "u1",
    },
  ],
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

test("system_admin sees the connector catalog table + passes axe", async ({
  page,
}) => {
  await page.route("**/v1/me", async (route) => {
    await route.fulfill({ json: SYS_ADMIN_ME });
  });
  await page.route("**/v1/platform/mcp-catalog", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: CATALOG });
      return;
    }
    await route.fallback();
  });
  await login(page);
  await page.goto("/settings/mcp-catalog");

  await expect(page.getByTestId("cat-table")).toBeVisible();
  await expect(page.getByText("GitHub", { exact: true })).toBeVisible();

  // Open the create Modal to surface the tabbed platform-server form.
  // The Basic tab is active by default — assert its fields (Auth tab fields
  // are force-rendered but hidden, so not toBeVisible).
  await page.getByTestId("cat-add").click();
  await expect(page.getByTestId("cce-form")).toBeVisible();
  await expect(page.getByTestId("cce-name")).toBeVisible();
  await expect(page.getByTestId("cce-url-template")).toBeVisible();

  await expectNoA11yViolations(page, "/settings/mcp-catalog");
});

test("non-admin sees system-admin-only notice + passes axe", async ({
  page,
}) => {
  await login(page);
  await page.goto("/settings/mcp-catalog");

  await expect(page.getByTestId("cat-not-admin")).toBeVisible();
  await expect(page.getByTestId("cat-table")).toHaveCount(0);
  await expectNoA11yViolations(page, "/settings/mcp-catalog");
});
