# Admin-UI PageHeader Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** Replace the copy-pasted breadcrumb + oversized page-header (used on 19 pages) with a single shared `PageHeader` component: compact single row (small icon + title + right-aligned actions), muted subtitle, NO fake "首页 › self" breadcrumb. Detail pages get a small `backTo` link instead of a breadcrumb. Matches the Linear/Console design baseline ([[project_admin_ui_design_baseline]]).

**Architecture:** New `apps/admin-ui/src/components/PageHeader.tsx` + CSS in `theme/global.css`. Sweep all 19 pages to use it, dropping `<Breadcrumb>` + the inline `ew-page-header` markup. Detail pages (RunDetail, SkillDetail) pass `backTo` (a "‹ parent" nav link) replacing their multi-level breadcrumb.

**Tech Stack:** React/Antd/Vitest/Storybook.

**Design tokens:** title `--ew-font-size-lg` (20px) + `--ew-font-weight-semibold`; icon 18px `--ew-text-secondary`; subtitle `--ew-font-size-sm` (13px) `--ew-text-secondary`; backTo `--ew-font-size-sm` `--ew-text-tertiary`; header bottom border `--ew-border-subtle`.

---

## Task 1: `PageHeader` component + CSS + test + story

**Files:** Create `apps/admin-ui/src/components/PageHeader.tsx`, `apps/admin-ui/src/components/__tests__/PageHeader.test.tsx`, `apps/admin-ui/src/components/PageHeader.stories.tsx`; Modify `apps/admin-ui/src/theme/global.css`

- [ ] **Step 1: Component**

```tsx
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ChevronLeft } from "lucide-react";

interface PageHeaderProps {
  title: string;
  icon?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  /** Detail pages: a small "‹ label" link to the parent list (replaces the breadcrumb). */
  backTo?: { label: string; to: string };
}

export function PageHeader({ title, icon, subtitle, actions, backTo }: PageHeaderProps) {
  return (
    <div className="ew-page-header" data-testid="page-header">
      {backTo && (
        <Link to={backTo.to} className="ew-page-header-back" data-testid="page-header-back">
          <ChevronLeft size={14} strokeWidth={1.75} />
          <span>{backTo.label}</span>
        </Link>
      )}
      <div className="ew-page-header-row">
        <div className="ew-page-header-title">
          {icon}
          <h1>{title}</h1>
        </div>
        {actions && <div className="ew-page-header-actions">{actions}</div>}
      </div>
      {subtitle && <p className="ew-page-header-subtitle">{subtitle}</p>}
    </div>
  );
}
```

- [ ] **Step 2: CSS — replace the `.ew-page-header` block in `theme/global.css`**

Replace the existing `.ew-page-header` + `.ew-page-header h1` rules with:
```css
.ew-page-header {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 20px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--ew-border-subtle);
}
.ew-page-header-back {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-size: var(--ew-font-size-sm);
  color: var(--ew-text-tertiary);
  width: fit-content;
}
.ew-page-header-back:hover { color: var(--ew-text-secondary); }
.ew-page-header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.ew-page-header-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.ew-page-header-title h1 {
  font-size: var(--ew-font-size-lg);
  font-weight: var(--ew-font-weight-semibold);
  line-height: var(--ew-line-height-tight);
  margin: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ew-page-header-title svg { color: var(--ew-text-secondary); flex-shrink: 0; }
.ew-page-header-actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.ew-page-header-subtitle {
  font-size: var(--ew-font-size-sm);
  color: var(--ew-text-secondary);
  margin: 0;
}
```

- [ ] **Step 3: Unit test**

`PageHeader.test.tsx` (wrap in `MemoryRouter` for the `Link`): renders title; renders subtitle when given; renders actions; `backTo` renders a link with the right `href` + label, omitted when absent; `page-header-back` absent without backTo.

- [ ] **Step 4: Story**

`PageHeader.stories.tsx`: stories for Default (title+icon+subtitle), WithActions (a primary Button), WithBackTo (detail variant). Wrap in router decorator if needed.

- [ ] **Step 5: Verify + commit**

Run: `cd apps/admin-ui && pnpm run typecheck && pnpm vitest run src/components/__tests__/PageHeader.test.tsx && pnpm run build-storybook 2>&1 | tail -3`.
Pre-commit + commit: `feat(admin-ui): add shared PageHeader (compact, no breadcrumb)`.

---

## Task 2: Sweep settings pages (10)

**Files (each):** `SettingsApiKeys, SettingsAudit, SettingsMembers, SettingsPlatformConfig, SettingsRoleBindings, SettingsServiceAccounts, SettingsTenants, SettingsTenantConfig, SettingsTenantCredentials, SettingsTenantQuotas` (under `apps/admin-ui/src/pages/`)

**Recipe per page:**
- [ ] Replace the `<Breadcrumb ... />` element AND the `<div className="ew-page-header"> ...icon... <h1>...</h1> ...subtitle <p>... </div>` block with a single:
  ```tsx
  <PageHeader
    icon={<TheSameIcon size={18} strokeWidth={1.5} />}
    title={t("...page_title")}
    subtitle={t("...subtitle")}
    actions={/* any existing primary action button(s) that were in the header, e.g. SettingsTenants' tenants-create button; otherwise omit */}
  />
  ```
- [ ] Import `PageHeader` from `../components/PageHeader`.
- [ ] Remove now-unused imports: `Breadcrumb` (from antd) and `ChevronRight` (lucide) IF no longer used elsewhere in the file (grep within the file). Keep the page's own title icon import (now passed to PageHeader).
- [ ] These are all top-level pages → NO `backTo`.
- [ ] Preserve everything else (the page body, gates, etc.). For pages whose action button currently sits elsewhere (e.g. a "Create" button below the header), move it into `actions` only if it's clearly the page-level primary action; otherwise leave it and omit `actions`. Report any judgment calls.

**Verify:** `cd apps/admin-ui && pnpm run typecheck && pnpm vitest run` → typecheck 0, all tests pass. Fix any test that asserted on the old breadcrumb/`common.home` (update to the new structure). Pre-commit + commit: `refactor(admin-ui): adopt PageHeader on settings pages`.

---

## Task 3: Sweep main-nav pages (7) + detail pages (2)

**Top-level (no backTo):** `AgentsList, RunsList, Curation, MemoryAdmin, SkillsList, TriggersList, ComingSoon` — same recipe as Task 2. Move `AgentsList`'s `agents-create` button into `actions`.

**Detail pages (use backTo):** `RunDetail, SkillDetail`:
- [ ] READ each page's current `<Breadcrumb items={[...]} />` to find the parent (e.g. RunDetail: 首页 › Runs › {id} → parent is Runs at `/runs`; SkillDetail: → Skills at `/skills`). Replace with:
  ```tsx
  <PageHeader
    title={/* the dynamic title the page already shows, e.g. run id / skill name */}
    backTo={{ label: t("nav.runs"|"nav.skills"), to: "/runs"|"/skills" }}
    actions={/* existing header actions if any */}
  />
  ```
  Drop the fake "首页" crumb. Keep the dynamic title. If the detail page shows metadata under the title, keep it (as `subtitle` or below).

**Verify:** `cd apps/admin-ui && pnpm run typecheck && pnpm vitest run && pnpm run build` → all green. Update any detail-page test asserting old breadcrumb. Pre-commit + commit: `refactor(admin-ui): adopt PageHeader on list + detail pages`.

---

## Task 4: cleanup + whole-PR gate

- [ ] grep `ew-page-header` across `src` (non-CSS): should appear ONLY inside `PageHeader.tsx` now. grep `Breadcrumb` under `src/pages`: should be ZERO (all replaced). Report any stragglers + fix.
- [ ] `common.home` i18n key ("首页"/"Home"): grep usage — if now unused everywhere, remove from `en.ts` (interface + value) + `zh-CN.ts`. If still referenced, leave it. Report.
- [ ] Whole-PR preflight: `cd apps/admin-ui && pnpm run typecheck && pnpm vitest run && pnpm run build && pnpm run build-storybook && pnpm exec playwright test` ; root `uv run pre-commit run --all-files`. Fix any e2e that asserted the old breadcrumb/header.
- [ ] Commit + open PR `feat/page-header-redesign`.

## Self-Review (controller)
- **One shared component, 19 pages** — DRY; design changes in one place. ✅
- **No fake "首页" breadcrumb**; detail pages keep a real `backTo` parent link. ✅
- **Compact** (20px title, 18px icon, 13px muted subtitle, single row with actions). ✅
- **Actions preserved** (AgentsList create, SettingsTenants create moved into `actions`). ✅
- **e2e/tests** that asserted old breadcrumb updated. ✅
