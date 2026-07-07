/**
 * Platform Admins page + cross-tenant Members smoke (Stream N self-service).
 *
 * Two faces:
 *   1. ``/settings/platform-users`` gates on system_admin. The default
 *      stub identity is a plain admin, so the page shows the notice; a
 *      per-test ``/v1/me`` override flips ``is_system_admin`` to prove
 *      the grant form + list render for an admin.
 *   2. ``/settings/members`` switches to a read-only aggregate when the
 *      tenant scope is "*"; we seed sessionStorage + a cross-tenant
 *      members stub and assert the write surfaces are gone.
 *
 * Each test runs axe (serious + critical only).
 */
import { test, expect, expectNoA11yViolations, SAMPLE_JWT } from "./fixtures";
import type { Page } from "@playwright/test";

const SELF = "11111111-1111-1111-1111-111111111111";

async function login(page: Page): Promise<void> {
  await page.goto("/login");
  await page.getByTestId("login-token").fill(SAMPLE_JWT);
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/agents$/);
}

/** Override ``/v1/me`` to a system_admin identity (the default stub is a
 *  plain admin). Must be installed before the page navigates. */
async function asSystemAdmin(page: Page): Promise<void> {
  await page.route("**/v1/me", async (route) => {
    await route.fulfill({
      json: {
        success: true,
        data: {
          subject_id: SELF,
          subject_type: "user",
          tenant_id: "22222222-2222-2222-2222-222222222222",
          auth_method: "jwt",
          roles: ["system_admin"],
          scopes: [],
          is_system_admin: true,
          allowed_tenants: "*",
        },
        error: null,
      },
    });
  });
}

test("/settings/platform-users shows the notice for a non-admin + passes axe", async ({
  page,
}) => {
  await login(page);
  await page.goto("/settings/platform-users");
  await expect(page.getByTestId("pu-not-admin")).toBeVisible();
  await expectNoA11yViolations(page, "/settings/platform-users");
});

test("/settings/platform-users renders the grant form + list for a system_admin", async ({
  page,
}) => {
  await login(page);
  await asSystemAdmin(page);
  await page.route("**/v1/role_bindings*", async (route) => {
    await route.fulfill({
      json: {
        success: true,
        data: {
          items: [
            {
              id: "b1",
              tenant_id: null,
              subject_type: "user",
              subject_id: SELF,
              role: "system_admin",
              platform_scope: true,
              granted_by: "bootstrap",
              granted_at: "2026-06-10T08:00:00Z",
            },
          ],
          total: 1,
          cross_tenant: false,
        },
        error: null,
      },
    });
  });
  await page.goto("/settings/platform-users");
  await expect(page.getByTestId("pu-grant-submit")).toBeVisible();
  await expect(page.getByTestId("pu-table")).toContainText(SELF);
  await expectNoA11yViolations(page, "/settings/platform-users");
});

test("/settings/members cross-tenant view is read-only + passes axe", async ({
  page,
}) => {
  await login(page);
  await asSystemAdmin(page);
  await page.route("**/v1/members*", async (route) => {
    await route.fulfill({
      json: {
        success: true,
        data: {
          items: [
            {
              id: "m-1",
              tenant_id: "22222222-2222-2222-2222-222222222222",
              email: "alice@acme.com",
              display_name: "Alice",
              role: "admin",
              status: "active",
              keycloak_user_id: "kc-1",
              subject_id: "s-1",
              invited_by: "u1",
              invited_at: "2026-05-26T10:00:00Z",
              activated_at: "2026-05-27T10:00:00Z",
              updated_at: "2026-05-27T10:00:00Z",
            },
          ],
          total: 1,
        },
        error: null,
      },
    });
  });
  // Pre-set the tenant scope to the cross-tenant aggregate (string init
  // script avoids needing the DOM lib in the e2e tsconfig).
  await page.addInitScript(
    "window.sessionStorage.setItem('expert_work.admin.tenantScope', '*')",
  );
  await page.goto("/settings/members");
  await expect(page.getByTestId("members-cross-banner")).toBeVisible();
  await expect(page.getByTestId("members-invite-btn")).toHaveCount(0);
  await expectNoA11yViolations(page, "/settings/members");
});
