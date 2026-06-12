/**
 * Knowledge page E2E — Stream H.7 PR 1.
 *
 * Smoke: /knowledge is routed and renders the stubbed base; selecting
 * it loads the documents with their ingest-status tags. Knowledge
 * routes are registered here (no fixture default); most-recently-added
 * handlers win.
 */
import { test, expect, SAMPLE_JWT } from "./fixtures";

const BASES_RESPONSE = {
  bases: [
    {
      id: "11111111-1111-1111-1111-111111111111",
      name: "support-docs",
      chunk_max_tokens: 512,
      chunk_overlap_tokens: 64,
      created_at: "2026-06-12T00:00:00Z",
    },
  ],
};

const DOCUMENTS_RESPONSE = {
  documents: [
    {
      id: "22222222-2222-2222-2222-222222222221",
      filename: "faq.pdf",
      status: "ready",
      error: null,
      chunk_count: 12,
      created_at: "2026-06-12T00:00:00Z",
      updated_at: "2026-06-12T00:05:00Z",
    },
  ],
};

test("/knowledge renders bases and the selected base's documents", async ({ page }) => {
  await page.route("**/v1/knowledge/bases", async (route) => {
    await route.fulfill({ json: BASES_RESPONSE });
  });
  await page.route("**/v1/knowledge/bases/*/documents", async (route) => {
    await route.fulfill({ json: DOCUMENTS_RESPONSE });
  });

  await page.goto("/login");
  await page.getByTestId("login-token").fill(SAMPLE_JWT);
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/agents$/);

  await page.goto("/knowledge");
  await expect(page.getByTestId("kb-table")).toBeVisible();
  await expect(page.getByText("support-docs")).toBeVisible();

  await page.getByText("support-docs").click();
  await expect(page.getByText("faq.pdf")).toBeVisible();
  await expect(page.getByText("ready")).toBeVisible();
});
