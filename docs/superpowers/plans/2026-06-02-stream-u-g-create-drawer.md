# Stream U PR G — Fold Create-Tenant into a Drawer (remove standalone menu) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** Remove the standalone "Create Tenant" nav item + page; surface tenant creation as a "Create Tenant" button on the `/settings/tenants` management page that opens a `CreateTenantDrawer` (mirrors `CreateAgentDrawer`). Single-page IA. **Must preserve the tenant_id UUID client-validation from PR #370.**

**Architecture:** Extract the create form (display_name/plan/tenant_id-with-UUID-rule/first_admin_*) from `SettingsCreateTenant.tsx` into `components/CreateTenantDrawer.tsx` (props `open/onClose/onCreated`). On success it shows the existing success Alert (new tenant_id copyable + first-admin summary) in-drawer AND fires `onCreated` so the list refreshes. `SettingsTenants` adds a header "Create Tenant" button + the drawer. Delete the standalone page/route/nav/stories/test.

**Tech Stack:** React/Antd/Vitest/Playwright.

---

## File Structure
- Create: `apps/admin-ui/src/components/CreateTenantDrawer.tsx`, `apps/admin-ui/src/components/__tests__/CreateTenantDrawer.test.tsx`
- Modify: `apps/admin-ui/src/pages/SettingsTenants.tsx`, `apps/admin-ui/src/pages/__tests__/SettingsTenants.test.tsx`
- Modify: `apps/admin-ui/src/router.tsx` (remove route+import), `apps/admin-ui/src/components/Sidebar.tsx` (remove nav item)
- Modify: `apps/admin-ui/src/i18n/locales/{en,zh-CN}.ts` (remove `nav.create_tenant`; add `settings_tenants.create`)
- Delete: `apps/admin-ui/src/pages/SettingsCreateTenant.tsx`, `SettingsCreateTenant.stories.tsx`, `apps/admin-ui/src/pages/__tests__/SettingsCreateTenant.test.tsx`
- Modify: `apps/admin-ui/e2e/tenants.spec.ts` (create via drawer); check/remove any create-tenant e2e

---

## Task 1: CreateTenantDrawer component + test

**Files:** Create `apps/admin-ui/src/components/CreateTenantDrawer.tsx`, `apps/admin-ui/src/components/__tests__/CreateTenantDrawer.test.tsx`

- [ ] **Step 1: i18n — add the list-page button label**

`en.ts` interface `settings_tenants`: add `create: string;`. `en.ts` values: `create: "Create Tenant",`. `zh-CN.ts` values: `create: "创建租户",`.

- [ ] **Step 2: Build the drawer (port from SettingsCreateTenant)**

READ `apps/admin-ui/src/pages/SettingsCreateTenant.tsx` (the form, the `UUID_RE` validator, `onCreate` with the try/catch validateFields + body build + `createTenant` + success/error) and `apps/admin-ui/src/components/CreateAgentDrawer.tsx` (Drawer shape: `open`/`onClose`/`onCreated` props, footer Cancel/submit buttons, reset on close).

Create `CreateTenantDrawer.tsx`:
```tsx
interface CreateTenantDrawerProps {
  open: boolean;
  onClose: () => void;
  onCreated: (record: CreatedTenant) => void;
}
```
- Use an Antd `Drawer` (title `t("settings_create_tenant.page_title")`, width ~520, `destroyOnHidden`). Footer: Cancel (`ct-cancel`) + Create primary (`ct-submit`, loading while submitting).
- Body = the SAME `Form` as the page (KEEP every `Form.Item` incl. the `tenant_id` `UUID_RE` validator rule verbatim — copy `UUID_RE` const + the validator). Keep the success `Alert` block (created_id `ct-created-id` copyable + first-admin `ct-first-admin`) rendered above the form when `createdId !== null`.
- `onCreate`: identical to the page's (try/catch `form.validateFields()`; build `CreateTenantBody`; `await createTenant(body)`; on success `setCreatedId(record.tenant_id)`, `setFirstAdmin(record.first_admin ?? null)`, `message.success`, `form.resetFields()`, **and `onCreated(record)`** so the parent reloads; on error `message.error`).
- Drop the page-level breadcrumb/`ew-page-header` and the `not-admin` gate (the drawer is only opened from the already-system-admin-gated tenants page).
- Reset state on close (mirror CreateAgentDrawer's reset): clear createdId/firstAdmin/error + `form.resetFields()`.
- Keep `data-testid="create-tenant-drawer"` on the Drawer. Reuse the existing `ct-*` testids where present (`ct-display-name`, `ct-plan`, `ct-tenant-id`, `ct-first-admin-email`, `ct-first-admin-name`, `ct-created-id`, `ct-first-admin`). Submit button testid: `ct-submit`.

- [ ] **Step 3: Port tests (RED→GREEN)**

Create `components/__tests__/CreateTenantDrawer.test.tsx` by porting `pages/__tests__/SettingsCreateTenant.test.tsx`'s assertions (the PR #370 UUID-validation cases are the critical ones to preserve):
- render the drawer `open` (wrap as the old test did + provide `onClose`/`onCreated` vi.fns; the drawer needs no AuthProvider gate now, but keep i18n + App + adapter mock).
- non-UUID tenant_id → submit → shows the `tenant_id_invalid` error + `createTenant` POST not called (assert via the adapter capture, as the ported test did).
- blank tenant_id → POST omits tenant_id.
- valid UUID → POST includes tenant_id.
- (new) on success → `onCreated` called with the created record.
Run red then green: `cd apps/admin-ui && pnpm vitest run src/components/__tests__/CreateTenantDrawer.test.tsx`.

- [ ] **Step 4: typecheck + pre-commit + commit**

Run: `cd apps/admin-ui && pnpm run typecheck`.
Run: `uv run pre-commit run --files apps/admin-ui/src/components/CreateTenantDrawer.tsx apps/admin-ui/src/components/__tests__/CreateTenantDrawer.test.tsx apps/admin-ui/src/i18n/locales/en.ts apps/admin-ui/src/i18n/locales/zh-CN.ts`
```bash
git add apps/admin-ui/src/components/CreateTenantDrawer.tsx apps/admin-ui/src/components/__tests__/CreateTenantDrawer.test.tsx apps/admin-ui/src/i18n/locales/en.ts apps/admin-ui/src/i18n/locales/zh-CN.ts
git commit -m "feat(stream-u): PR G — CreateTenantDrawer (port form + UUID validation)"
```

---

## Task 2: Wire into tenants page; remove standalone page/route/nav

**Files:** `SettingsTenants.tsx` (+ test), `router.tsx`, `Sidebar.tsx`, i18n; delete `SettingsCreateTenant.{tsx,stories.tsx}` + its test; update `e2e/tenants.spec.ts`

- [ ] **Step 1: SettingsTenants — Create button + drawer**

In `SettingsTenants.tsx`:
- import `CreateTenantDrawer` from `../components/CreateTenantDrawer`; add `const [createOpen, setCreateOpen] = useState(false);`.
- Ensure the list fetch is a reusable `reload` callback (PR E already extracted it — reuse it).
- In the page header (top-right), add `<Button type="primary" data-testid="tenants-create" onClick={() => setCreateOpen(true)}>{t("settings_tenants.create")}</Button>`. (Place it in the header row; mirror how other pages put a primary action top-right — flex space-between with the title, or after the subtitle.)
- Render `<CreateTenantDrawer open={createOpen} onClose={() => setCreateOpen(false)} onCreated={() => { setCreateOpen(false); reload(); }} />` (reload refreshes the list to show the new tenant). Only render the button/drawer in the system_admin branch.

- [ ] **Step 2: Remove standalone page/route/nav**

- `router.tsx`: delete the `SettingsCreateTenant` import + the `<Route path="/settings/create-tenant" .../>`.
- `Sidebar.tsx`: delete the `settings-create-tenant` entry from `SETTINGS_ITEMS`. If `Building2` becomes unused after this (the tenants list uses `Building`), remove `Building2` from the lucide import (grep to confirm no other use in the file).
- Delete files: `apps/admin-ui/src/pages/SettingsCreateTenant.tsx`, `apps/admin-ui/src/pages/SettingsCreateTenant.stories.tsx`, `apps/admin-ui/src/pages/__tests__/SettingsCreateTenant.test.tsx`.
- i18n: remove `nav.create_tenant` from `en.ts` (interface line + value) and `zh-CN.ts` (value). FIRST grep `nav.create_tenant` / `"nav.create_tenant"` across `src` to ensure no remaining reference (CommandPalette, etc.) — if any consumer exists, update it (e.g. CommandPalette command that navigated to create-tenant → point to `/settings/tenants` or drop). Report what you found. KEEP all `settings_create_tenant.*` keys (the drawer's form still uses them).

- [ ] **Step 3: Update SettingsTenants test + e2e**

- `pages/__tests__/SettingsTenants.test.tsx`: add a case — clicking `tenants-create` opens `create-tenant-drawer` (mock `../../components/CreateTenantDrawer` to a stub that asserts `open`, OR mock `createTenant` and drive the real drawer; simplest: assert the drawer testid appears when the button is clicked). Keep existing tests green (the new button must not break them).
- `e2e/tenants.spec.ts`: the create flow now lives here — add/adjust so a system_admin clicks `tenants-create`, the drawer opens, and (optionally, with `POST /v1/tenants` stubbed) a create succeeds. If there was a separate `e2e/create-tenant*.spec.ts`, fold its essential assertions here and delete it (grep `e2e/` for create-tenant). Keep axe green.

- [ ] **Step 4: Full verify**

Run: `cd apps/admin-ui && pnpm run typecheck && pnpm vitest run && pnpm run build && pnpm run build-storybook 2>&1 | tail -3`. Then `pnpm exec playwright test tenants`.
Expected: typecheck 0; vitest all pass (the deleted SettingsCreateTenant test is gone; CreateTenantDrawer test covers it); build + storybook clean (no dangling import to the deleted stories); e2e pass + axe.
Stale LSP diagnostics: typecheck exit 0 authoritative.

- [ ] **Step 5: pre-commit + commit**

Run pre-commit on all changed + deleted files (`uv run pre-commit run --files ...` — for deletions, just include the still-existing changed files).
```bash
git add -A apps/admin-ui
git commit -m "feat(stream-u): PR G — surface create on tenants page; remove standalone create-tenant page/route/nav"
```

---

## Task 3: backlog note + PR

- [ ] Add a one-line note under the Stream U backlog in `docs/ITERATION-PLAN.md`: `- [x] **U-G IA 收口**：创建租户折进 /settings/tenants 抽屉（CreateTenantDrawer，保留 tenant_id UUID 校验）+ 删独立页/路由/菜单（PR G）`. Commit the plan doc + backlog.
- [ ] Whole-PR preflight already covered by Task 2 Step 4; open PR `stream-u/g-create-drawer`.

## Self-Review (controller)
- **Preserve PR #370 UUID validation** — ported verbatim into the drawer + tests. ✅
- **No dangling refs** — grep `create-tenant`/`nav.create_tenant`/`SettingsCreateTenant` returns nothing after removal (except the kept `settings_create_tenant.*` form keys). ✅
- **Success UX preserved** — drawer shows created tenant_id (copyable) + first-admin summary in-place, and refreshes the list via `onCreated`. ✅
- **Storybook** — deleted `SettingsCreateTenant.stories.tsx`; no orphan import. (Optionally add a `CreateTenantDrawer.stories.tsx` — not required.) ✅
