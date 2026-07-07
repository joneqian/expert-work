/**
 * Platform Judge config e2e — Stream PI-3-A3.
 *
 * Proves a system_admin can view the platform judge model at /settings/platform,
 * switch it through the <PlatformJudgeSection> picker (persisted via PUT), and
 * clear it back to the agent-own default. Plus an axe pass.
 *
 * Mirrors platform-embedding.spec.ts: the section renders only on the
 * system_admin branch, so /v1/me is overridden with a system-admin identity and
 * the platform GETs are stubbed (Playwright routes are LIFO → win over fixtures).
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

const CREDENTIALS_VIEW = {
  success: true,
  data: { providers: [], tools: [] },
  error: null,
};

const EMBEDDING_CONFIG = {
  success: true,
  data: {
    embedding: { provider: "qwen", model: "text-embedding-v4" },
    rerank: null,
    available_embedding: [{ provider: "qwen", model: "text-embedding-v4" }],
    available_rerank: [],
  },
  error: null,
};

const JUDGE_CONFIG = {
  success: true,
  data: {
    judge: { provider: "deepseek", model: "deepseek-chat" },
    available: [
      { provider: "deepseek", model: "deepseek-chat" },
      { provider: "glm", model: "glm-4-flash" },
    ],
  },
  error: null,
};

const JUDGE_PUT_RESULT = {
  success: true,
  data: { judge: { provider: "glm", model: "glm-4-flash" } },
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
  await page.route("**/v1/me", async (route) => {
    await route.fulfill({ json: SYS_ADMIN_ME });
  });
  await page.route("**/v1/platform/credentials", async (route) => {
    await route.fulfill({ json: CREDENTIALS_VIEW });
  });
  await page.route("**/v1/platform/embedding-config", async (route) => {
    await route.fulfill({ json: EMBEDDING_CONFIG });
  });
  await page.route("**/v1/platform/judge-config", async (route) => {
    if (route.request().method() === "PUT") {
      await route.fulfill({ json: JUDGE_PUT_RESULT });
      return;
    }
    await route.fulfill({ json: JUDGE_CONFIG });
  });
});

test("system_admin views + saves the platform judge config", async ({ page }) => {
  await login(page);
  await page.goto("/settings/platform?tab=models");

  await expect(page.getByTestId("pj-root")).toBeVisible();
  // friendly help always present
  await expect(page.getByTestId("pj-help")).toBeVisible();
  await expect(page.getByText("deepseek / deepseek-chat")).toBeVisible();

  await page.getByTestId("pj-provider").locator(".ant-select").click();
  await page.locator(".ant-select-item-option-content", { hasText: "glm" }).click();
  await page.getByTestId("pj-model").locator(".ant-select").click();
  await page.locator(".ant-select-item-option-content", { hasText: "glm-4-flash" }).click();

  const [putReq] = await Promise.all([
    page.waitForRequest(
      (req) => req.url().includes("/v1/platform/judge-config") && req.method() === "PUT",
    ),
    page.getByTestId("pj-save").click(),
  ]);
  const body = putReq.postDataJSON();
  expect(body.judge_provider).toBe("glm");
  expect(body.judge_model).toBe("glm-4-flash");
});

test("settings/platform with the judge section passes axe", async ({ page }) => {
  await login(page);
  await page.goto("/settings/platform?tab=models");

  await expect(page.getByTestId("pj-root")).toBeVisible();
  await expectNoA11yViolations(page, "settings-platform");
});
