/**
 * Create-agent embedding gate E2E — Stream T PR E.
 *
 * New agents default to long-term memory on, which requires a platform-level
 * embedding model. The Create-Agent drawer fetches
 * ``GET /v1/platform/embedding-config/status`` on open. When the platform has
 * no embedding configured it renders ``create-agent-embedding-gate`` (an Alert
 * + a CTA that navigates to ``/settings/platform`` and closes the drawer) and
 * disables ``create-agent-submit``; otherwise the editor renders normally.
 *
 * Login + the schema/model-catalog stubs mirror ``manifest-editor.spec.ts`` /
 * ``manifest-model-select.spec.ts``. The shared ``installControlPlaneStub``
 * fixture registers ``**​/v1/agents*`` (which also matches ``/agents/schema``);
 * we register the more specific schema route *after* it so it wins (Playwright
 * runs the most-recently-added handler first). The status route is registered
 * per-test because the two tests need opposite ``configured`` values.
 */
import { test, expect, SAMPLE_JWT } from "./fixtures";

const SCHEMA_ENVELOPE = {
  success: true,
  error: null,
  data: {
    type: "object",
    properties: {
      metadata: {
        type: "object",
        properties: {
          name: { type: "string", title: "Name" },
        },
      },
    },
  },
};

const CATALOG_ENVELOPE = {
  success: true,
  error: null,
  data: {
    providers: [
      {
        provider: "openai",
        models: [
          {
            name: "gpt-5.5",
            vision: true,
            embeddings: false,
            context_window: 128000,
            deprecated: false,
          },
        ],
      },
    ],
  },
};

/** Stub the per-test status route, then run the shared login flow. */
async function loginWithStatus(
  page: import("@playwright/test").Page,
  configured: boolean,
): Promise<void> {
  // More specific than the fixture's ``**/v1/agents*`` stub; registered after
  // it so it takes precedence for the schema fetch.
  await page.route("**/v1/agents/schema", async (route) => {
    await route.fulfill({ json: SCHEMA_ENVELOPE });
  });
  await page.route("**/v1/model-catalog", async (route) => {
    await route.fulfill({ json: CATALOG_ENVELOPE });
  });
  await page.route("**/v1/platform/embedding-config/status", (route) =>
    route.fulfill({ json: { success: true, data: { configured }, error: null } }),
  );
  await page.goto("/login");
  await expect(page.getByTestId("login-card")).toBeVisible();
  // The paste-token form sits behind the "Developer login" disclosure whenever
  // OIDC is configured. In CI it is open by default; locally it may be
  // collapsed — reveal it if needed.
  const tokenField = page.getByTestId("login-token");
  if (!(await tokenField.isVisible())) {
    await page.getByTestId("login-dev-toggle").click();
  }
  await tokenField.fill(SAMPLE_JWT);
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/agents$/);
  await expect(page.getByText("customer-support-bot")).toBeVisible();
}

test("blocks agent creation when platform embedding is unconfigured", async ({
  page,
}) => {
  await loginWithStatus(page, false);

  await page.getByTestId("agents-create").click();
  await expect(page.getByTestId("create-agent-drawer")).toBeVisible();
  await expect(page.getByTestId("create-agent-embedding-gate")).toBeVisible();
  await expect(page.getByTestId("create-agent-submit")).toBeDisabled();

  await page.getByTestId("create-agent-embedding-cta").click();
  await expect(page).toHaveURL(/\/settings\/platform/);
});

test("allows agent creation when platform embedding is configured", async ({
  page,
}) => {
  await loginWithStatus(page, true);

  await page.getByTestId("agents-create").click();
  await expect(page.getByTestId("create-agent-drawer")).toBeVisible();
  await expect(page.getByTestId("create-agent-embedding-gate")).toHaveCount(0);
  await expect(page.getByTestId("create-agent-submit")).toBeEnabled();
});
