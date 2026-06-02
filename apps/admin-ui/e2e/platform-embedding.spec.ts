/**
 * Platform Embedding config e2e — Stream T PR D.
 *
 * Proves a system_admin can view the current platform embedding selection at
 * /settings/platform and switch the embedding provider/model through the
 * <PlatformEmbeddingSection> picker, with the change persisted via PUT. Plus an
 * axe pass on the rendered page.
 *
 * The section is rendered only on the system_admin branch of the page, which
 * gates on /v1/me's ``is_system_admin``. The default fixture user is NOT a
 * system admin, so (mirroring platform-config.spec.ts) we override /v1/me with
 * a system-admin identity and stub the platform GETs the page issues. Playwright
 * routes are LIFO, so these spec-level routes win over the fixture stub.
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

// Platform credentials view — the page's own GET. Kept minimal; the embedding
// section is the subject under test.
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
    available_embedding: [
      { provider: "qwen", model: "text-embedding-v4" },
      { provider: "glm", model: "embedding-3" },
    ],
    available_rerank: [{ provider: "qwen", model: "qwen3-vl-rerank" }],
  },
  error: null,
};

// putJson unwraps the envelope's ``data``; the section reads ``embedding`` /
// ``rerank`` off it.
const EMBEDDING_PUT_RESULT = {
  success: true,
  data: { embedding: { provider: "glm", model: "embedding-3" }, rerank: null },
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
  await page.route("**/v1/platform/credentials", async (route) => {
    await route.fulfill({ json: CREDENTIALS_VIEW });
  });
  await page.route("**/v1/platform/embedding-config", async (route) => {
    if (route.request().method() === "PUT") {
      await route.fulfill({ json: EMBEDDING_PUT_RESULT });
      return;
    }
    await route.fulfill({ json: EMBEDDING_CONFIG });
  });
});

test("system_admin views + saves the platform embedding config", async ({ page }) => {
  await login(page);
  await page.goto("/settings/platform");

  // Section loads (pe-loading first, then pe-root) and shows the current model.
  await expect(page.getByTestId("pe-root")).toBeVisible();
  await expect(page.getByText("qwen / text-embedding-v4")).toBeVisible();

  // Switch provider → glm, then model → embedding-3 (Antd Select via wrapper).
  await page.getByTestId("pe-embedding-provider").locator(".ant-select").click();
  await page
    .locator(".ant-select-item-option-content", { hasText: "glm" })
    .click();
  await page.getByTestId("pe-embedding-model").locator(".ant-select").click();
  await page
    .locator(".ant-select-item-option-content", { hasText: "embedding-3" })
    .click();

  // Save fires the PUT.
  const [putReq] = await Promise.all([
    page.waitForRequest(
      (req) =>
        req.url().includes("/v1/platform/embedding-config") &&
        req.method() === "PUT",
    ),
    page.getByTestId("pe-save").click(),
  ]);
  const body = putReq.postDataJSON();
  expect(body.embedding_provider).toBe("glm");
  expect(body.embedding_model).toBe("embedding-3");
});

test("settings/platform with the embedding section passes axe", async ({ page }) => {
  await login(page);
  await page.goto("/settings/platform");

  await expect(page.getByTestId("pe-root")).toBeVisible();
  await expectNoA11yViolations(page, "settings-platform");
});
