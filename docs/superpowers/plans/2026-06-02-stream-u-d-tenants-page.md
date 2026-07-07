# Stream U PR D — Tenant Management Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** A `/settings/tenants` page (system_admin only) listing all tenants with a "Manage" action that switches the tenant scope into that tenant and navigates to its config page — turning the dead-ended "create tenant" flow into a manageable one.

**Architecture:** New `SettingsTenants` page fetches `listTenants()` (already in main) → Antd table. Mirror `SettingsCreateTenant` for the system_admin content gate (non-admin sees an Alert) and `SettingsMembers` for table conventions. "Manage" calls `useTenantScope().setScope(tenant_id)` + `useNavigate()("/settings/tenant-config")`. Wire route + sidebar nav + i18n + storybook + e2e/axe. (Tenant `status` column arrives in PR E.)

**Tech Stack:** React 19 + Antd 5 + react-i18next + react-router-dom; Vitest + Storybook + Playwright/axe.

---

## File Structure
- Create: `apps/admin-ui/src/pages/SettingsTenants.tsx`
- Test: `apps/admin-ui/src/pages/__tests__/SettingsTenants.test.tsx`
- Create: `apps/admin-ui/src/pages/SettingsTenants.stories.tsx`
- Modify: `apps/admin-ui/src/router.tsx` (import + route)
- Modify: `apps/admin-ui/src/components/Sidebar.tsx` (SETTINGS_ITEMS nav entry)
- Modify: `apps/admin-ui/src/i18n/locales/en.ts`, `zh-CN.ts` (nav.tenants + settings_tenants.*)
- Create: `apps/admin-ui/e2e/tenants.spec.ts`

---

## Task 1: SettingsTenants page + unit test

**Files:** Create `apps/admin-ui/src/pages/SettingsTenants.tsx`; Test `apps/admin-ui/src/pages/__tests__/SettingsTenants.test.tsx`

- [ ] **Step 1: Add i18n keys (both locales — typecheck enforces parity)**

`en.ts` interface — add to the `nav` block: `tenants: string;`. Add a new top-level interface block (place near `settings_create_tenant`):
```ts
  settings_tenants: {
    page_title: string;
    subtitle: string;
    not_admin_title: string;
    not_admin_body: string;
    col_display_name: string;
    col_plan: string;
    col_tenant_id: string;
    col_created: string;
    col_actions: string;
    manage: string;
    failed_to_load: string;
    empty: string;
  };
```
`en.ts` values — `nav.tenants: "Tenants",` and:
```ts
  settings_tenants: {
    page_title: "Tenants",
    subtitle: "All tenants on the platform. Click Manage to switch into a tenant and edit its config, quotas, and credentials.",
    not_admin_title: "System admin only",
    not_admin_body: "Listing all tenants is a platform-level action available to system admins.",
    col_display_name: "Display name",
    col_plan: "Plan",
    col_tenant_id: "Tenant id",
    col_created: "Created",
    col_actions: "Actions",
    manage: "Manage",
    failed_to_load: "Failed to load tenants",
    empty: "No tenants yet — create one from Create Tenant.",
  },
```
`zh-CN.ts` values — `nav.tenants: "租户",` and:
```ts
  settings_tenants: {
    page_title: "租户",
    subtitle: "平台上的所有租户。点「管理」切进某个租户，编辑其配置、配额与凭证。",
    not_admin_title: "仅系统管理员",
    not_admin_body: "列出所有租户是平台级操作，仅系统管理员可用。",
    col_display_name: "显示名",
    col_plan: "套餐",
    col_tenant_id: "租户 id",
    col_created: "创建时间",
    col_actions: "操作",
    manage: "管理",
    failed_to_load: "租户列表加载失败",
    empty: "还没有租户——去「创建租户」新建一个。",
  },
```

- [ ] **Step 2: Write the failing unit test**

READ `apps/admin-ui/src/pages/SettingsCreateTenant.tsx` (system_admin gate pattern via `useAuth().identity.isSystemAdmin`) and an existing list-page test (e.g. `__tests__/SettingsIam.test.tsx` or `SettingsMembers` test) for the harness (AuthProvider + JWT via `makeJwt`/`setStoredToken`, MemoryRouter, App wrapper, adapter mock). Mock `../../api/tenants` `listTenants`. Mock `react-router-dom` `useNavigate` → `mockNavigate`. Cases:
```ts
it("lists tenants for a system admin", async () => {
  (listTenants as Mock).mockResolvedValue([
    { tenant_id: "11111111-1111-1111-1111-111111111111", display_name: "乐毅大公司", plan: "free", created_at: "2026-06-02T00:00:00Z" },
  ]);
  renderPage(/* system_admin */);
  expect(await screen.findByText("乐毅大公司")).toBeInTheDocument();
});

it("Manage sets tenant scope and navigates to tenant-config", async () => {
  (listTenants as Mock).mockResolvedValue([{ tenant_id: "11111111-1111-1111-1111-111111111111", display_name: "Acme", plan: "free", created_at: "2026-06-02T00:00:00Z" }]);
  renderPage(/* system_admin */);
  (await screen.findByTestId("st-manage-11111111-1111-1111-1111-111111111111")).click();
  expect(mockNavigate).toHaveBeenCalledWith("/settings/tenant-config");
  // scope persisted to the specific tenant
  expect(window.sessionStorage.getItem("expert_work.admin.tenantScope")).toBe("11111111-1111-1111-1111-111111111111");
});

it("non-admin sees the not-admin alert and does not fetch", async () => {
  renderPage(/* non-admin */);
  expect(await screen.findByTestId("st-not-admin")).toBeInTheDocument();
  expect(listTenants).not.toHaveBeenCalled();
});
```
> Wrap render in `TenantScopeProvider` so `useTenantScope().setScope` persists to sessionStorage (the Manage assertion relies on it). Mirror the `renderForm` helper from `SettingsCreateTenant.test.tsx` (now in repo) but add `TenantScopeProvider`.

- [ ] **Step 3: Run red**

Run: `cd apps/admin-ui && pnpm vitest run src/pages/__tests__/SettingsTenants.test.tsx` → FAIL (module missing).

- [ ] **Step 4: Implement `SettingsTenants.tsx`**

Mirror `SettingsCreateTenant.tsx`'s shell (breadcrumb/header/`useTranslation`/`useAuth` system_admin gate). Use Antd `Table`. On mount (system_admin only) fetch `listTenants()`.
```tsx
import { useCallback, useEffect, useState } from "react";
import { Alert, Breadcrumb, Button, Table, Typography } from "antd";
import { Building, ChevronRight } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { listTenants, type TenantSummary } from "../api/tenants";
import { useAuth } from "../auth/AuthContext";
import { useTenantScope } from "../tenant/TenantScopeContext";

const { Text } = Typography;

export function SettingsTenants() {
  const { t } = useTranslation();
  const auth = useAuth();
  const navigate = useNavigate();
  const { setScope } = useTenantScope();
  const isSystemAdmin = auth.identity?.isSystemAdmin ?? false;
  const [rows, setRows] = useState<TenantSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!isSystemAdmin) { setLoading(false); return; }
    let alive = true;
    listTenants().then(
      (data) => { if (alive) { setRows(data); setLoading(false); } },
      (e: unknown) => { if (alive) { setError(e instanceof Error ? e.message : String(e)); setLoading(false); } },
    );
    return () => { alive = false; };
  }, [isSystemAdmin]);

  const manage = useCallback((tenantId: string) => {
    setScope(tenantId);
    navigate("/settings/tenant-config");
  }, [setScope, navigate]);

  // ...header (mirror SettingsCreateTenant)...
  // non-admin → <Alert data-testid="st-not-admin" .../>
  // else → <Table data-testid="st-table" rowKey="tenant_id" loading={loading}
  //   locale={{ emptyText: t("settings_tenants.empty") }}
  //   columns: display_name, plan, tenant_id (<Text code copyable>), created_at (new Date(...).toLocaleString()),
  //   actions: <Button data-testid={`st-manage-${r.tenant_id}`} onClick={() => manage(r.tenant_id)}>{t("settings_tenants.manage")}</Button>
  //   dataSource={rows} />
  // error → <Alert type="error" data-testid="st-error" message={t("settings_tenants.failed_to_load")} description={error}/>
}
```
Root `<div data-testid="st-root">`. Keep it focused (<150 lines). Use the repo's existing page-header classes (`ew-page-header`) as in SettingsCreateTenant.

- [ ] **Step 5: Run green + typecheck**

Run: `cd apps/admin-ui && pnpm vitest run src/pages/__tests__/SettingsTenants.test.tsx && pnpm run typecheck` → PASS, typecheck 0.

- [ ] **Step 6: pre-commit + commit**

Run: `uv run pre-commit run --files apps/admin-ui/src/pages/SettingsTenants.tsx apps/admin-ui/src/pages/__tests__/SettingsTenants.test.tsx apps/admin-ui/src/i18n/locales/en.ts apps/admin-ui/src/i18n/locales/zh-CN.ts`
```bash
git add apps/admin-ui/src/pages/SettingsTenants.tsx apps/admin-ui/src/pages/__tests__/SettingsTenants.test.tsx apps/admin-ui/src/i18n/locales/en.ts apps/admin-ui/src/i18n/locales/zh-CN.ts
git commit -m "feat(stream-u): PR D — tenants management page + i18n"
```

---

## Task 2: Wire route + sidebar nav

**Files:** Modify `apps/admin-ui/src/router.tsx`, `apps/admin-ui/src/components/Sidebar.tsx`

- [ ] **Step 1: Route**

In `router.tsx`: add `import { SettingsTenants } from "./pages/SettingsTenants";` (keep alpha order near other Settings imports) and a route (place near the other tenant routes):
```tsx
      <Route path="/settings/tenants" element={<SettingsTenants />} />
```

- [ ] **Step 2: Sidebar nav entry**

In `Sidebar.tsx` `SETTINGS_ITEMS`, add as the FIRST settings item (before create-tenant) so "list then create" reads naturally:
```tsx
  { key: "settings-tenants", labelKey: "nav.tenants", icon: <Building size={16} strokeWidth={1.5} />, path: "/settings/tenants" },
```
Add `Building` to the `lucide-react` import in Sidebar.tsx (note: `Building2` is already imported for create-tenant; `Building` is the distinct list icon).

- [ ] **Step 3: Verify build + typecheck**

Run: `cd apps/admin-ui && pnpm run typecheck && pnpm vitest run src/components` → 0 errors, components tests pass (Sidebar test, if any, still green — if a Sidebar test asserts the settings item count, update it minimally and report).

- [ ] **Step 4: pre-commit + commit**

Run: `uv run pre-commit run --files apps/admin-ui/src/router.tsx apps/admin-ui/src/components/Sidebar.tsx`
```bash
git add apps/admin-ui/src/router.tsx apps/admin-ui/src/components/Sidebar.tsx
git commit -m "feat(stream-u): PR D — route + sidebar nav for tenants page"
```

---

## Task 3: Storybook + E2E/axe

**Files:** Create `apps/admin-ui/src/pages/SettingsTenants.stories.tsx`, `apps/admin-ui/e2e/tenants.spec.ts`

- [ ] **Step 1: Storybook**

Mirror `apps/admin-ui/src/pages/SettingsCreateTenant.stories.tsx`. A default story rendering `SettingsTenants` (mock/stub `listTenants` via the story's existing API-mock approach if that file uses one; otherwise a simple render is fine). Keep it minimal but buildable.

- [ ] **Step 2: E2E + axe**

Create `e2e/tenants.spec.ts` mirroring `e2e/platform-embedding.spec.ts` (system-admin `/v1/me` override + token login). Stub `**/v1/tenants*` GET → `{success:true, data:[{tenant_id, display_name:"乐毅大公司", plan:"free", created_at:"2026-06-02T00:00:00Z"}], error:null}`. Test:
- log in, go to `/settings/tenants`; assert `st-table` visible and "乐毅大公司" present.
- click `st-manage-<id>`; assert URL becomes `/settings/tenant-config`.
- run axe (mirror the axe call in platform-embedding.spec.ts) → no violations.

- [ ] **Step 3: Run**

Run: `cd apps/admin-ui && pnpm run build-storybook 2>&1 | tail -5` (builds clean) and `pnpm exec playwright test tenants` → pass + axe clean.

- [ ] **Step 4: pre-commit + commit**

Run: `uv run pre-commit run --files apps/admin-ui/src/pages/SettingsTenants.stories.tsx apps/admin-ui/e2e/tenants.spec.ts`
```bash
git add apps/admin-ui/src/pages/SettingsTenants.stories.tsx apps/admin-ui/e2e/tenants.spec.ts
git commit -m "test(stream-u): PR D — tenants page storybook + e2e/axe"
```

---

## Self-Review (controller)
- **Coverage:** U-3 (list/management page + nav). Manage = switch scope + jump to config (no duplicate edit UI — display_name/plan stays in tenant-config per design). Status column deferred to PR E. ✅
- **Type consistency:** `TenantSummary` from SDK reused; testids `st-root/st-table/st-manage-{id}/st-not-admin/st-error` consistent across unit + e2e. ✅
- **i18n parity:** nav.tenants + settings_tenants.* in both locales; typecheck enforces. ✅
- **Pre-commit before each commit.** ✅
