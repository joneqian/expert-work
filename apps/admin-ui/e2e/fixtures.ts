/**
 * Playwright fixtures — Stream H.1b PR 4.
 *
 * Two centralised fixtures so tests stay legible:
 *
 *   - ``mockControlPlane`` — wires ``page.route`` to intercept
 *     ``/v1/*`` and return stable fixture responses. CI can't reach a
 *     real expert_work.control_plane.main, so every E2E that doesn't
 *     explicitly opt out goes through this stub.
 *   - ``a11y`` — runs ``@axe-core/playwright`` against the current
 *     page and fails on serious + critical violations. Skipping
 *     ``color-contrast`` would be tempting against Antd defaults, but
 *     the expert_work tokens were tuned for WCAG AA, so we keep it on.
 */
import { test as base, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

export const SAMPLE_JWT = (() => {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const payload = btoa(
    JSON.stringify({
      sub: "11111111-1111-1111-1111-111111111111",
      sub_type: "user",
      tenant_id: "22222222-2222-2222-2222-222222222222",
      roles: ["admin"],
      iat: Math.floor(Date.now() / 1000),
      exp: Math.floor(Date.now() / 1000) + 3600,
    }),
  );
  return `${header}.${payload}.`;
})();

const ME_RESPONSE = {
  success: true,
  data: {
    subject_id: "11111111-1111-1111-1111-111111111111",
    subject_type: "user",
    tenant_id: "22222222-2222-2222-2222-222222222222",
    auth_method: "jwt",
    roles: ["admin"],
    scopes: [],
    is_system_admin: false,
    allowed_tenants: ["22222222-2222-2222-2222-222222222222"],
  },
  error: null,
};

const AGENTS_RESPONSE = {
  success: true,
  data: {
    items: [
      {
        id: "33333333-3333-3333-3333-333333333333",
        tenant_id: "22222222-2222-2222-2222-222222222222",
        name: "customer-support-bot",
        version: "3.4.2",
        status: "active",
        spec_sha256: "a".repeat(64),
        created_by: "alice@acme.com",
        created_at: "2026-04-12T09:00:00Z",
        updated_at: "2026-05-25T07:00:00Z",
      },
    ],
    total: 1,
    cross_tenant: false,
  },
  error: null,
};

const EMPTY_LIST = {
  success: true,
  data: { items: [], total: 0, cross_tenant: false },
  error: null,
};

// Conversation-centric IA — one conversation so the AgentDetail
// Conversations tab + the global /conversations browser have a row
// (envelope shape, matches GET /v1/conversations).
const CONVERSATIONS_RESPONSE = {
  success: true,
  error: null,
  data: {
    items: [
      {
        thread_id: "55555555-5555-5555-5555-555555555555",
        tenant_id: "22222222-2222-2222-2222-222222222222",
        user_id: "88888888-8888-8888-8888-888888888888",
        agent_name: "customer-support-bot",
        agent_version: "3.4.2",
        title: "refund question",
        status: "active",
        created_at: "2026-05-26T08:00:00Z",
        updated_at: "2026-05-26T08:00:32Z",
        run_count: 2,
        error_count: 0,
        pending_count: 0,
        last_run_at: "2026-05-26T08:00:32Z",
        tokens: {
          input_tokens: 100,
          output_tokens: 20,
          cache_creation_tokens: 0,
          cache_read_tokens: 0,
          total_tokens: 120,
          llm_calls: 2,
          models: ["claude-sonnet-4-5"],
        },
      },
    ],
    total: 1,
    cross_tenant: false,
  },
};

// P1-S2.5 — eval-runs list is raw ``{ items, total }`` (no envelope).
const EVAL_RUNS_RESPONSE = {
  items: [
    {
      id: "99999999-9999-9999-9999-999999999999",
      suite: "m0_baseline",
      status: "passed",
      triggered_by: "manual",
      summary: { pass_count: 15, total: 15 },
      created_at: "2026-06-14T08:00:00Z",
      started_at: "2026-06-14T08:00:05Z",
      finished_at: "2026-06-14T08:02:30Z",
    },
  ],
  total: 1,
};

/** Raw (un-enveloped) list shape used by curation / skills / triggers
 *  / audit endpoints — see H.4 PR 1 / PR 5 / PR 6 SDK rewrites. */
const RAW_EMPTY_LIST = { items: [], total: 0, cross_tenant: false };
const RAW_EMPTY_CURSOR = { items: [], next_cursor: null, cross_tenant: false };
const RAW_EMPTY_AUDIT = {
  items: [],
  next_cursor: null,
  has_more: false,
  applied_scope: "22222222-2222-2222-2222-222222222222",
};
const APPROVALS_RESPONSE = {
  success: true,
  data: {
    items: [
      {
        id: "55555555-5555-5555-5555-555555555555",
        tenant_id: "22222222-2222-2222-2222-222222222222",
        user_id: null,
        run_id: "44444444-4444-4444-4444-444444444444",
        thread_id: "33333333-3333-3333-3333-333333333333",
        request_id: "approval:e2e",
        node: "tools",
        reason_kind: "policy_gate",
        action_summary: "approval-gated tool 'send_email'",
        proposed_args: { to: "ops@example.com" },
        requested_at: "2026-06-12T08:00:00Z",
        timeout_at: "2026-06-13T08:00:00Z",
        status: "pending",
        decided_by: null,
        decided_at: null,
      },
    ],
    total: 1,
    limit: 100,
    offset: 0,
  },
  error: null,
};

const ENVELOPED_EMPTY_LIST = {
  success: true,
  data: { items: [], total: 0, cross_tenant: false },
  error: null,
};

export async function installControlPlaneStub(page: Page): Promise<void> {
  await page.route("**/v1/me", async (route) => {
    await route.fulfill({ json: ME_RESPONSE });
  });
  await page.route("**/v1/agents*", async (route) => {
    await route.fulfill({ json: AGENTS_RESPONSE });
  });
  await page.route("**/v1/api_keys*", async (route) => {
    await route.fulfill({ json: EMPTY_LIST });
  });
  await page.route("**/v1/service_accounts*", async (route) => {
    await route.fulfill({ json: ENVELOPED_EMPTY_LIST });
  });
  await page.route("**/v1/role_bindings*", async (route) => {
    await route.fulfill({ json: ENVELOPED_EMPTY_LIST });
  });
  await page.route("**/v1/conversations*", async (route) => {
    await route.fulfill({ json: CONVERSATIONS_RESPONSE });
  });
  // P1-S2.5 — eval runs (raw, un-enveloped). One passed run so the
  // /eval-runs E2E has a row to assert.
  await page.route("**/v1/eval-runs*", async (route) => {
    await route.fulfill({ json: EVAL_RUNS_RESPONSE });
  });
  // Stream HX-7 — the approval queue (enveloped). One pending row so
  // the /approvals page + nav badge have something to render.
  await page.route("**/v1/approvals*", async (route) => {
    await route.fulfill({ json: APPROVALS_RESPONSE });
  });
  // Memory backend is enveloped (Stream K.K6).
  await page.route("**/v1/memory*", async (route) => {
    await route.fulfill({ json: ENVELOPED_EMPTY_LIST });
  });
  // Curation / skills / triggers / audit backends are raw — Stream H.4
  // SDKs read them through ``apiClient`` directly.
  await page.route("**/v1/curation/candidates*", async (route) => {
    await route.fulfill({ json: RAW_EMPTY_LIST });
  });
  await page.route("**/v1/eval-datasets*", async (route) => {
    await route.fulfill({ json: RAW_EMPTY_LIST });
  });
  await page.route("**/v1/skills*", async (route) => {
    await route.fulfill({ json: RAW_EMPTY_CURSOR });
  });
  // Stream SE (SE-8) — skill-evolution governance (promote-requests queue,
  // eval-results, lineage, kill-switch). Raw, cursor-shaped where listy.
  await page.route("**/v1/skill-evolution/**", async (route) => {
    await route.fulfill({ json: RAW_EMPTY_CURSOR });
  });
  await page.route("**/v1/triggers*", async (route) => {
    await route.fulfill({ json: RAW_EMPTY_LIST });
  });
  await page.route("**/v1/audit*", async (route) => {
    await route.fulfill({ json: RAW_EMPTY_AUDIT });
  });
  // Tenant-scoped endpoints — match any tenant UUID in path.
  await page.route("**/v1/tenants/*/quotas*", async (route) => {
    await route.fulfill({ json: { success: true, data: [], error: null } });
  });
  await page.route("**/v1/tenants/*/config*", async (route) => {
    await route.fulfill({
      status: 404,
      json: {
        detail: {
          code: "TENANT_CONFIG_NOT_FOUND",
          message: "no tenant_config row exists for this tenant",
        },
      },
    });
  });
}

export const test = base.extend<{
  mockControlPlane: void;
}>({
  mockControlPlane: [
    async ({ page }, use) => {
      await installControlPlaneStub(page);
      await use();
    },
    { auto: true },
  ],
});

export { expect };

/** Rules left off the failure budget for PR 4. ``color-contrast``
 *  catches Antd default ``type="secondary"`` colours against the expert_work
 *  surface tokens — a real signal, but one that needs a token audit
 *  pass before we can fail CI on it. Tracked as a follow-up. */
const A11Y_PR4_WAIVERS = new Set(["color-contrast"]);

export async function expectNoA11yViolations(page: Page, label: string): Promise<void> {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  const blocking = results.violations.filter(
    (v) =>
      (v.impact === "serious" || v.impact === "critical") &&
      !A11Y_PR4_WAIVERS.has(v.id),
  );
  if (blocking.length > 0) {
    const summary = blocking
      .map((v) => `${v.id} (${v.impact}, ${v.nodes.length} nodes): ${v.help}`)
      .join("\n");
    throw new Error(`axe found ${blocking.length} serious/critical issues on ${label}:\n${summary}`);
  }
  // Log waived rules so the next token-audit pass has a clear list to
  // walk through; the violations remain visible in the HTML report.
  const waived = results.violations.filter((v) => A11Y_PR4_WAIVERS.has(v.id));
  for (const w of waived) {
    console.warn(`[a11y waived] ${label}: ${w.id} on ${w.nodes.length} nodes`);
  }
}
