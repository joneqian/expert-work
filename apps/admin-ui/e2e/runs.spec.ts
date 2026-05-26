/**
 * Runs page E2E — Stream H.3 PR 1.
 *
 * Drives a login, navigates to /runs, and verifies the stubbed run row
 * renders + the page passes axe. Doesn't exercise the SSE event stream
 * (that's PR 7d) or approval flow (PR 7e); the value here is "the route
 * is wired and the table renders".
 */
import { test, expect, expectNoA11yViolations, SAMPLE_JWT } from "./fixtures";

test("/runs shows the stubbed run row after login", async ({ page }) => {
  await page.goto("/login");
  await page.getByTestId("login-token").fill(SAMPLE_JWT);
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/agents$/);
  await page.goto("/runs");
  await expect(page.getByText("customer-support-bot")).toBeVisible();
  await expect(page.getByText("v3.4.2")).toBeVisible();
});

test("/runs passes axe (serious + critical)", async ({ page }) => {
  await page.goto("/login");
  await page.getByTestId("login-token").fill(SAMPLE_JWT);
  await page.getByTestId("login-submit").click();
  await page.goto("/runs");
  await expect(page.getByText("customer-support-bot")).toBeVisible();
  await expectNoA11yViolations(page, "/runs");
});
