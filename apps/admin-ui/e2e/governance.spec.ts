/**
 * Governance happy-path smoke — Stream H.4 PR 9 (closeout).
 *
 * One test per H.4 sub-face. Each test:
 *   1. logs in via paste-token
 *   2. navigates to the sub-face route
 *   3. asserts the page-specific entry chrome rendered (button /
 *      filter / table empty state, etc — proof "the route is wired
 *      and the SDK didn't 500 on first load")
 *   4. runs axe against the page (serious + critical only)
 *
 * The empty-state assertions intentionally lean on i18n-stable English
 * strings from the locale file so the test doesn't break on minor
 * copy tweaks. Stub responses come from ``installControlPlaneStub`` in
 * ``fixtures.ts``.
 */
import { test, expect, expectNoA11yViolations, SAMPLE_JWT } from "./fixtures";

async function login(page: import("@playwright/test").Page): Promise<void> {
  await page.goto("/login");
  await page.getByTestId("login-token").fill(SAMPLE_JWT);
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/agents$/);
}

test("/memory page renders + passes axe", async ({ page }) => {
  await login(page);
  await page.goto("/memory");
  await expect(page.getByPlaceholder(/Filter by content/i)).toBeVisible();
  await expectNoA11yViolations(page, "/memory");
});

test("/curation page renders both tabs + passes axe", async ({ page }) => {
  await login(page);
  await page.goto("/curation");
  await expect(page.getByRole("tab", { name: /Candidates/i })).toBeVisible();
  await expect(page.getByRole("tab", { name: /Eval Datasets/i })).toBeVisible();
  await expectNoA11yViolations(page, "/curation");
});

test("/skills page renders Import (creation is import-only) + passes axe", async ({ page }) => {
  await login(page);
  await page.goto("/skills");
  await expect(page.getByTestId("skills-import-btn")).toBeVisible();
  // Phase D: hand-build create is removed; import is the only creation path.
  await expect(page.getByTestId("skills-create-btn")).toHaveCount(0);
  await expectNoA11yViolations(page, "/skills");
});

test("/triggers page renders cron/webhook Tabs + passes axe", async ({ page }) => {
  await login(page);
  await page.goto("/triggers");
  await expect(page.getByRole("tab", { name: /Cron/i })).toBeVisible();
  await expect(page.getByRole("tab", { name: /Webhook/i })).toBeVisible();
  await expectNoA11yViolations(page, "/triggers");
});

test("/settings/audit page renders filter row + passes axe", async ({ page }) => {
  await login(page);
  await page.goto("/settings/audit");
  await expect(page.getByTestId("audit-filters")).toBeVisible();
  await expectNoA11yViolations(page, "/settings/audit");
});

test("/settings/service-accounts page renders Create button + passes axe", async ({ page }) => {
  await login(page);
  await page.goto("/settings/service-accounts");
  await expect(page.getByTestId("sa-create-btn")).toBeVisible();
  await expectNoA11yViolations(page, "/settings/service-accounts");
});

test("/settings/tenant-quotas page renders Create button + passes axe", async ({ page }) => {
  await login(page);
  await page.goto("/settings/tenant-quotas");
  await expect(page.getByTestId("quota-create-btn")).toBeVisible();
  await expectNoA11yViolations(page, "/settings/tenant-quotas");
});
