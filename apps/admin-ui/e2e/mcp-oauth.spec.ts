/**
 * Settings — My MCP connections e2e (Stream MCP-OAUTH).
 *
 * Proves a tenant member can:
 *   (a) see their OAuth connections list with status;
 *   (b) disconnect a connection (DELETE + reload);
 *   (c) pass the axe accessibility audit;
 *   (d) see the OAuth badge + Authorize affordance for an oauth2 catalog
 *       connector in the add-server drawer.
 *
 * Network fully mocked. The OAuth connections endpoint returns RAW ``{items}``
 * (no envelope); the tenant catalog returns the standard envelope — the mocks
 * reflect both shapes. Spec routes use ``route.fallback()`` (NOT continue) so
 * unmatched requests fall through to the fixture's global stub.
 */
import { test, expect, expectNoA11yViolations, SAMPLE_JWT } from "./fixtures";

const CONNECTIONS_FIRST = {
  items: [
    {
      id: "conn-1",
      tenant_id: "t1",
      user_id: "kc-user",
      catalog_id: "cat-linear",
      name: "linear",
      status: "connected",
      resolved_url: "https://mcp.linear.app/sse",
      scopes: "read write",
      token_expires_at: "2026-12-01T00:00:00Z",
      last_refresh_at: "2026-06-01T00:00:00Z",
      last_error: null,
      created_at: "2026-05-20T08:00:00Z",
      updated_at: "2026-06-01T00:00:00Z",
    },
  ],
};

const CATALOG = {
  success: true,
  data: [
    {
      id: "cat-linear",
      name: "linear",
      display_name: "Linear",
      description: "Your Linear issues.",
      category: "dev-tools",
      icon: "",
      transport: "sse",
      url_template: "https://mcp.linear.app/sse",
      auth_type: "oauth2",
      auth_schema: { fields: [] },
      required_tier: "free",
      enabled: true,
      created_at: "2026-05-01T10:00:00Z",
      updated_at: "2026-05-01T10:00:00Z",
      updated_by: "u1",
      entitled: true,
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

test.beforeEach(async ({ page }) => {
  // Tenant catalog (envelope) — register before the broader mcp-servers route.
  await page.route("**/v1/mcp-servers/catalog", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: CATALOG });
      return;
    }
    await route.fallback();
  });
  await page.route("**/v1/mcp-servers", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { success: true, data: [], error: null } });
      return;
    }
    await route.fallback();
  });
});

test("(a) lists the member's OAuth connections", async ({ page }) => {
  await page.route("**/v1/mcp-oauth/connections", async (route) => {
    await route.fulfill({ json: CONNECTIONS_FIRST });
  });
  await login(page);
  await page.goto("/settings/mcp-oauth");

  await expect(page.getByTestId("mo-table")).toBeVisible();
  await expect(page.getByTestId("mo-name-linear")).toHaveText("Linear");
  await expect(page.getByTestId("mo-status-linear")).toBeVisible();
});

test("(b) disconnect removes the connection", async ({ page }) => {
  let disconnected = false;
  await page.route("**/v1/mcp-oauth/connections", async (route) => {
    await route.fulfill({
      json: disconnected ? { items: [] } : CONNECTIONS_FIRST,
    });
  });
  await page.route("**/v1/mcp-oauth/connections/*", async (route) => {
    if (route.request().method() === "DELETE") {
      disconnected = true;
      await route.fulfill({ status: 204, body: "" });
      return;
    }
    await route.fallback();
  });

  await login(page);
  await page.goto("/settings/mcp-oauth");
  await expect(page.getByTestId("mo-name-linear")).toBeVisible();

  await page.getByTestId("mo-disconnect-linear").click();
  await page.getByRole("button", { name: /确认|Confirm/ }).click();

  await expect(page.getByTestId("mo-name-linear")).toHaveCount(0);
});

test("(c) settings/mcp-oauth passes axe", async ({ page }) => {
  await page.route("**/v1/mcp-oauth/connections", async (route) => {
    await route.fulfill({ json: CONNECTIONS_FIRST });
  });
  await login(page);
  await page.goto("/settings/mcp-oauth");
  await expect(page.getByTestId("mo-table")).toBeVisible();
  await expectNoA11yViolations(page, "settings-mcp-oauth");
});

test("(d) oauth2 connector shows OAuth badge + Authorize affordance", async ({
  page,
}) => {
  await page.route("**/v1/mcp-oauth/connections", async (route) => {
    await route.fulfill({ json: { items: [] } });
  });
  await login(page);
  await page.goto("/settings/mcp-servers");
  await expect(page.getByTestId("ms-table")).toBeVisible();

  await page.getByTestId("ms-add").click();
  // The catalog browser shows the oauth2 connector tagged "OAuth".
  await expect(page.getByTestId("cb-oauth-linear")).toBeVisible();
  // Selecting it opens the authorize panel (not the secret-fields form).
  await page.getByTestId("cb-select-linear").click();
  await expect(page.getByTestId("ocf-authorize")).toBeVisible();
});
