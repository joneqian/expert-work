/**
 * Run detail E2E — Stream CM-8 PR4.
 *
 * Closes the PR 7e debt (approval flow was never driven end-to-end) and
 * covers the new PlanPanel. Spec-level ``page.route`` registrations
 * stack on top of ``mockControlPlane`` (later routes win), so each test
 * shapes the run/plan payloads it needs and captures the writes.
 */
import { test, expect, expectNoA11yViolations, SAMPLE_JWT } from "./fixtures";
import type { Page, Route } from "@playwright/test";

const THREAD = "55555555-5555-5555-5555-555555555555";
const RUN = "44444444-4444-4444-4444-444444444444";

const PLAN = {
  goal: "ship the feature",
  steps: [
    { id: "1", description: "write tests", status: "completed" },
    { id: "2", description: "implement", status: "in_progress" },
  ],
};

const APPROVAL = {
  request_id: "req-1",
  node: "deploy",
  reason_kind: "irreversible",
  action_summary: "Deploy build 42 to production",
  proposed_args: { target: "prod", build: 42 },
  requested_at: "2026-06-10T08:00:00Z",
  timeout_at: "2026-06-11T08:00:00Z",
};

function runDetail(status: string, withApproval: boolean) {
  return {
    run_id: RUN,
    thread_id: THREAD,
    status,
    trace_id: null,
    pending_approval: withApproval ? APPROVAL : null,
  };
}

async function openRunDetail(
  page: Page,
  { status, withApproval, plan }: { status: string; withApproval: boolean; plan: object | null },
): Promise<{ resumes: unknown[]; planPuts: unknown[] }> {
  const resumes: unknown[] = [];
  const planPuts: unknown[] = [];
  await page.route(`**/v1/sessions/${THREAD}/runs/${RUN}`, async (route: Route) => {
    await route.fulfill({ json: runDetail(status, withApproval) });
  });
  await page.route(`**/v1/sessions/${THREAD}/runs/${RUN}/resume`, async (route: Route) => {
    resumes.push(route.request().postDataJSON());
    await route.fulfill({ json: runDetail("running", false) });
  });
  await page.route(`**/v1/sessions/${THREAD}/plan`, async (route: Route) => {
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON();
      planPuts.push(body);
      await route.fulfill({ json: body });
      return;
    }
    if (plan === null) {
      await route.fulfill({ status: 204, body: "" });
      return;
    }
    await route.fulfill({ json: plan });
  });

  await page.goto("/login");
  // Local dev servers may have VITE_OIDC_* set — the token field then
  // hides behind the dev-login toggle (CI shows it directly).
  const tokenField = page.getByTestId("login-token");
  if (!(await tokenField.isVisible())) {
    await page.getByTestId("login-dev-toggle").click();
  }
  await tokenField.fill(SAMPLE_JWT);
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/agents$/);
  await page.goto(`/runs/${THREAD}/${RUN}`);
  return { resumes, planPuts };
}

test("approval card renders and approve posts the verdict (PR 7e debt)", async ({ page }) => {
  const { resumes } = await openRunDetail(page, {
    status: "paused",
    withApproval: true,
    plan: PLAN,
  });
  await expect(page.getByTestId("approval-card")).toBeVisible();
  await expect(page.getByText("Deploy build 42 to production")).toBeVisible();
  await page.getByTestId("approval-approve").click();
  await expect.poll(() => resumes.length).toBe(1);
  expect(resumes[0]).toEqual({ decision: "approve" });
});

test("approval reject posts the verdict", async ({ page }) => {
  const { resumes } = await openRunDetail(page, {
    status: "paused",
    withApproval: true,
    plan: null,
  });
  await page.getByTestId("approval-reject").click();
  await expect.poll(() => resumes.length).toBe(1);
  expect(resumes[0]).toEqual({ decision: "reject" });
});

test("plan panel shows the goal and steps, and edits flow through PUT", async ({ page }) => {
  const { planPuts } = await openRunDetail(page, {
    status: "success",
    withApproval: false,
    plan: PLAN,
  });
  await expect(page.getByTestId("plan-panel")).toBeVisible();
  await expect(page.getByText("ship the feature")).toBeVisible();
  await expect(page.getByText("write tests")).toBeVisible();

  await page.getByTestId("plan-edit").click();
  await page.getByTestId("plan-step-input-1").fill("implement + review");
  await page.getByTestId("plan-save").click();
  await expect.poll(() => planPuts.length).toBe(1);
  const put = planPuts[0] as { steps: Array<{ description: string }> };
  expect(put.steps[1].description).toBe("implement + review");
});

test("plan edit is locked while the run is live", async ({ page }) => {
  await openRunDetail(page, { status: "running", withApproval: false, plan: PLAN });
  await expect(page.getByTestId("plan-panel")).toBeVisible();
  await expect(page.getByTestId("plan-edit")).toBeDisabled();
});

test("run detail with approval + plan passes axe (serious + critical)", async ({ page }) => {
  await openRunDetail(page, { status: "paused", withApproval: true, plan: PLAN });
  await expect(page.getByTestId("approval-card")).toBeVisible();
  await expect(page.getByTestId("plan-panel")).toBeVisible();
  await expectNoA11yViolations(page, `/runs/${THREAD}/${RUN}`);
});
