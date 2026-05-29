/**
 * E2E — Settings · Credentials panel (Stream O PR 2b).
 *
 * Stubs the composite credentials view with a more specific route than the
 * fixture's catch-all config 404, so Playwright's LIFO route priority serves
 * it, then asserts the three surfaces render + the page passes axe.
 */
import { test, expect, expectNoA11yViolations, SAMPLE_JWT } from "./fixtures";
import type { Page } from "@playwright/test";

const CREDENTIALS_VIEW = {
  success: true,
  data: {
    mode: "platform",
    providers: [
      { provider: "anthropic", platform_configured: true, tenant_secret_ref: null, used_by_agents: 2 },
      {
        provider: "openai",
        platform_configured: true,
        tenant_secret_ref: "kms://acme/openai",
        used_by_agents: 1,
      },
    ],
    tools: [
      { tool: "web_search", platform_configured: true, tenant_secret_ref: null, used_by_agents: 1 },
    ],
  },
  error: null,
};

async function stubAndLogin(page: Page): Promise<void> {
  await page.route("**/v1/tenants/*/config/credentials", async (route) => {
    await route.fulfill({ json: CREDENTIALS_VIEW });
  });
  await page.goto("/login");
  await page.getByTestId("login-token").fill(SAMPLE_JWT);
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/agents$/);
  await page.goto("/settings/credentials");
}

test("credentials panel renders the mode card + provider/tool tables", async ({ page }) => {
  await stubAndLogin(page);
  await expect(page.getByTestId("credentials-mode-card")).toBeVisible();
  // Default fallback language is zh-CN; CI navigator may be en — match both.
  await expect(page.getByTestId("credentials-mode-current")).toHaveText(/Platform|平台/);
  await expect(page.getByTestId("provider-creds-table")).toBeVisible();
  await expect(page.getByTestId("tool-creds-table")).toBeVisible();
  await expect(page.getByTestId("credentials-mode-switch-btn")).toBeVisible();
});

test("/settings/credentials passes axe (serious + critical)", async ({ page }) => {
  await stubAndLogin(page);
  await expect(page.getByTestId("credentials-mode-card")).toBeVisible();
  await expectNoA11yViolations(page, "/settings/credentials");
});
