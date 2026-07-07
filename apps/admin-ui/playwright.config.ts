/**
 * Playwright config — Stream H.1b PR 4.
 *
 * Auto-starts ``pnpm dev`` against an env-configured control-plane
 * (the dev server's vite proxy forwards /v1/* to the backend). For CI
 * the workflow injects a stubbed control-plane via E2E_CONTROL_PLANE
 * pointing at a wiremock-style fixture; locally you can run against
 * a real expert_work.control_plane.main on localhost:8000.
 */
import { defineConfig, devices } from "@playwright/test";

const PORT = 5173;
const BASE_URL = `http://localhost:${PORT}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [["line"], ["html", { open: "never" }]] : "list",
  timeout: 30_000,
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "pnpm dev",
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
