/**
 * Members page e2e — Stream R W2.
 *
 * Covers: page loads + table renders one member; open the invite drawer,
 * fill email + role, submit → assert the POST payload carries the
 * invitation; an axe accessibility pass. The ``mockControlPlane``
 * fixture stubs ``/v1/me`` + the rest of the platform; this spec adds
 * ``/v1/members`` (list) + ``/v1/members/invite`` (create) routes.
 */
import { test, expect, expectNoA11yViolations, SAMPLE_JWT } from "./fixtures";

const MEMBERS_LIST = {
  success: true,
  data: {
    items: [
      {
        id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        tenant_id: "22222222-2222-2222-2222-222222222222",
        email: "alice@acme.com",
        display_name: "Alice",
        role: "admin",
        status: "active",
        keycloak_user_id: "kc-alice",
        subject_id: "11111111-1111-1111-1111-111111111111",
        invited_by: "11111111-1111-1111-1111-111111111111",
        invited_at: "2026-05-01T09:00:00Z",
        activated_at: "2026-05-02T09:00:00Z",
        updated_at: "2026-05-02T09:00:00Z",
      },
    ],
    total: 1,
  },
  error: null,
};

const INVITE_RESULT = {
  success: true,
  data: {
    results: [
      {
        email: "bob@acme.com",
        member_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        status: "invited",
        error_code: null,
      },
    ],
  },
  error: null,
};

async function login(page: import("@playwright/test").Page): Promise<void> {
  await page.goto("/login");
  await page.getByTestId("login-token").fill(SAMPLE_JWT);
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/agents$/);
}

test("members page loads + renders the table", async ({ page }) => {
  await page.route("**/v1/members*", async (route) => {
    await route.fulfill({ json: MEMBERS_LIST });
  });
  await login(page);
  await page.goto("/settings/members");

  await expect(page.getByTestId("members-root")).toBeVisible();
  await expect(page.getByTestId("members-table")).toBeVisible();
  await expect(page.getByText("alice@acme.com")).toBeVisible();

  await expectNoA11yViolations(page, "/settings/members");
});

test("invite drawer submits an invitation with email + role", async ({ page }) => {
  let invitePayload: unknown = null;
  // Invite route is registered before the list route so it wins for the
  // /invite path (Playwright routes are LIFO — last registered wins),
  // and the broader list glob handles the GET + post-submit refresh.
  await page.route("**/v1/members*", async (route) => {
    await route.fulfill({ json: MEMBERS_LIST });
  });
  await page.route("**/v1/members/invite", async (route) => {
    invitePayload = route.request().postDataJSON();
    await route.fulfill({ json: INVITE_RESULT });
  });

  await login(page);
  await page.goto("/settings/members");

  await page.getByTestId("members-invite-btn").click();
  await expect(page.getByTestId("members-invite-drawer")).toBeVisible();

  await page.getByTestId("members-invite-email").fill("bob@acme.com");
  await page.getByTestId("members-invite-role").click();
  await page.getByTitle("operator").click();
  await page.getByTestId("members-invite-submit").click();

  await expect.poll(() => invitePayload).not.toBeNull();
  expect(invitePayload).toMatchObject({
    invitations: [{ email: "bob@acme.com", role: "operator" }],
  });
});
