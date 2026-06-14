/**
 * Eval Runs page E2E — P1-S2.5-FE.
 *
 * Logs in, navigates to /eval-runs, asserts the stubbed run row renders
 * and the route + page pass axe. The control-plane stub (fixtures) serves
 * the raw eval-runs list.
 */
import { test, expect, expectNoA11yViolations, SAMPLE_JWT } from "./fixtures";

async function login(page: import("@playwright/test").Page): Promise<void> {
  await page.goto("/login");
  await page.getByTestId("login-token").fill(SAMPLE_JWT);
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/agents$/);
}

test("/eval-runs shows the stubbed eval run after login", async ({ page }) => {
  await login(page);
  await page.goto("/eval-runs");
  await expect(page.getByTestId("eval-table")).toBeVisible();
  await expect(page.getByText("passed")).toBeVisible();
  await expect(page.getByText("15/15")).toBeVisible();
});

test("/eval-runs passes axe (serious + critical)", async ({ page }) => {
  await login(page);
  await page.goto("/eval-runs");
  await expect(page.getByTestId("eval-table")).toBeVisible();
  await expectNoA11yViolations(page, "/eval-runs");
});
