# Stream T PR D — Platform Embedding/Rerank Config UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended). Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add an **Embedding & Rerank** section to `/settings/platform` so a system admin selects the platform embedding provider+model (and optional rerank) from dropdowns of configured-key + capability-matched models, and saves it — making long-term memory available platform-wide.

**Architecture:** A new SDK (`api/platform_embedding_config.ts`) over the PR C endpoints + a self-contained section component (`PlatformEmbeddingSection.tsx`) rendered inside `SettingsPlatformConfig.tsx`. The GET returns current selection + `available_embedding`/`available_rerank` (configured-key × capability-matched), so the dropdowns need no client-side filtering. Save → PUT; 422 validation codes map to friendly messages.

**Tech Stack:** React 19, Antd 5 (`Select`, `Card`, `Button`, `Alert`), Vitest + Testing Library, Playwright + axe. pnpm; run from `apps/admin-ui/`.

**Builds on:** PR C `GET/PUT /v1/platform/embedding-config`. GET `data`: `{ embedding: {provider,model}|null, rerank: {provider,model}|null, available_embedding: [{provider,model}], available_rerank: [{provider,model}] }`. PUT body: `{ embedding_provider, embedding_model, rerank_provider?, rerank_model? }`; 422 codes: `EMBEDDING_PROVIDER_KEY_MISSING`, `INVALID_EMBEDDING_MODEL`, `INVALID_RERANK_PAIR`, `RERANK_PROVIDER_KEY_MISSING`, `INVALID_RERANK_MODEL`. `getJson`/`putJson` unwrap the envelope; `ApiError` carries `code`+`message`.

---

## Conventions (verified, mirror `SettingsPlatformConfig.tsx` + `api/platform_config.ts`)
- SDK style: `api/platform_config.ts` — typed interfaces + `getJson<T>`/`putJson<T>` wrappers.
- Page: `SettingsPlatformConfig.tsx` — system-admin page, `pc-*` testids, i18n namespace `settings_platform`, Antd tables + edit modal, `message.success(...)` on save. The new section lives in this page (a new `<h2>` section after the tools table).
- i18n: keys in `TranslationKeys` (en.ts) + matching `zh-CN.ts`. Reuse `settings_platform.*`; add an `embedding_*` sub-group.
- Antd `Select` jsdom gotcha (from Stream S): in unit tests, open via the wrapper testid then click the visible `.ant-select-item-option-content`; in Playwright use `.locator(".ant-select-item-option-content", { hasText })`. Put testids on wrapper divs.
- **Before every commit run `uv run pre-commit run --files <changed>` from repo root** (ruff-format etc. don't apply to TS, but the whitespace/eof hooks do) AND `pnpm run typecheck`. Unit: `npx vitest run <path>`; e2e: `pnpm run e2e -- <spec>`.

---

### Task 1: SDK `api/platform_embedding_config.ts`

**Files:**
- Create: `apps/admin-ui/src/api/platform_embedding_config.ts`
- Test: `apps/admin-ui/src/api/__tests__/platform_embedding_config.test.ts` (if the repo tests SDKs; else fold into the component test — check for existing `src/api/__tests__`)

- [ ] **Step 1: Implement the SDK:**
```typescript
/**
 * Platform Embedding/Rerank config SDK — backed by /v1/platform/embedding-config
 * (Stream T PR C). system_admin-only, platform-level.
 */
import { getJson, putJson } from "./client";

export interface ProviderModel {
  provider: string;
  model: string;
}

export interface PlatformEmbeddingConfigView {
  embedding: ProviderModel | null;
  rerank: ProviderModel | null;
  available_embedding: ProviderModel[];
  available_rerank: ProviderModel[];
}

export interface PlatformEmbeddingConfigWrite {
  embedding_provider: string;
  embedding_model: string;
  rerank_provider?: string;
  rerank_model?: string;
}

export async function getPlatformEmbeddingConfig(): Promise<PlatformEmbeddingConfigView> {
  return getJson<PlatformEmbeddingConfigView>("/v1/platform/embedding-config");
}

export async function putPlatformEmbeddingConfig(
  body: PlatformEmbeddingConfigWrite,
): Promise<{ embedding: ProviderModel | null; rerank: ProviderModel | null }> {
  return putJson("/v1/platform/embedding-config", body);
}
```

- [ ] **Step 2: Test** (if `src/api/__tests__` exists, add a small test stubbing axios like sibling SDK tests; otherwise skip a dedicated SDK test — the component test in Task 2 covers it via `vi.spyOn`). Check first: `ls apps/admin-ui/src/api/__tests__ 2>/dev/null`. If none exist, note that and rely on Task 2.

- [ ] **Step 3: typecheck + commit**
```bash
pnpm run typecheck
git add apps/admin-ui/src/api/platform_embedding_config.ts apps/admin-ui/src/api/__tests__/ 2>/dev/null
git commit -m "feat(admin-ui): platform embedding-config SDK (Stream T PR D)"
```

---

### Task 2: `PlatformEmbeddingSection` component (current + edit dropdowns + save)

**Files:**
- Create: `apps/admin-ui/src/pages/settings_platform/PlatformEmbeddingSection.tsx` (or `src/components/` if that's the page's convention — check where SettingsPlatformConfig keeps subcomponents; if none, put it next to the page)
- Test: `apps/admin-ui/src/pages/settings_platform/__tests__/PlatformEmbeddingSection.test.tsx`

**Behavior:**
- On mount, `getPlatformEmbeddingConfig()`; show a loading state, then render.
- **Current**: show the configured embedding `provider/model` (or a "not configured — long-term memory unavailable" warning Alert, testid `pe-unconfigured`).
- **Edit form**:
  - Embedding **provider** Select (distinct `provider`s from `available_embedding`); on change reset model. testid wrapper `pe-embedding-provider`.
  - Embedding **model** Select (models from `available_embedding` matching the chosen provider); disabled until provider chosen. testid `pe-embedding-model`.
  - **Enable rerank** toggle (Switch/Checkbox, testid `pe-rerank-toggle`); when on, rerank **provider** + **model** Selects from `available_rerank` (testids `pe-rerank-provider`/`pe-rerank-model`).
  - **Save** button (testid `pe-save`) → `putPlatformEmbeddingConfig({...})` (omit rerank fields when toggle off). On success: `message.success`, refresh from the PUT response / re-GET. On `ApiError`: show an Alert (testid `pe-error`) mapping the 422 `code` to an i18n message (fall back to `err.message`).
- Props: `onSaved?: () => void` (optional; the page may want to refresh). Keep the component self-contained (does its own fetch).

- [ ] **Step 1: Write the failing test** `__tests__/PlatformEmbeddingSection.test.tsx`:
```tsx
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../../i18n";
import * as sdk from "../../../api/platform_embedding_config";
import { ApiError } from "../../../api/client";
import { PlatformEmbeddingSection } from "../PlatformEmbeddingSection";

const VIEW = {
  embedding: { provider: "qwen", model: "text-embedding-v4" },
  rerank: null,
  available_embedding: [
    { provider: "qwen", model: "text-embedding-v4" },
    { provider: "glm", model: "embedding-3" },
  ],
  available_rerank: [{ provider: "qwen", model: "qwen3-vl-rerank" }],
};

beforeEach(() => vi.spyOn(sdk, "getPlatformEmbeddingConfig").mockResolvedValue(VIEW));
afterEach(() => vi.restoreAllMocks());

describe("PlatformEmbeddingSection", () => {
  it("shows the current embedding selection", async () => {
    render(<PlatformEmbeddingSection />);
    await waitFor(() => expect(screen.getByTestId("pe-root")).toBeInTheDocument());
    expect(screen.getByTestId("pe-root")).toHaveTextContent("text-embedding-v4");
  });

  it("saves a new embedding selection via PUT", async () => {
    const user = userEvent.setup();
    const put = vi.spyOn(sdk, "putPlatformEmbeddingConfig").mockResolvedValue({
      embedding: { provider: "glm", model: "embedding-3" }, rerank: null,
    });
    render(<PlatformEmbeddingSection />);
    await screen.findByTestId("pe-root");
    // choose provider glm
    await user.click(within(screen.getByTestId("pe-embedding-provider")).getByRole("combobox"));
    await user.click(await screen.findByText("glm"));
    // choose model embedding-3
    await user.click(within(screen.getByTestId("pe-embedding-model")).getByRole("combobox"));
    await user.click(await screen.findByText("embedding-3"));
    await user.click(screen.getByTestId("pe-save"));
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith(expect.objectContaining({ embedding_provider: "glm", embedding_model: "embedding-3" })),
    );
  });

  it("surfaces a 422 error code as a friendly message", async () => {
    const user = userEvent.setup();
    vi.spyOn(sdk, "putPlatformEmbeddingConfig").mockRejectedValue(
      new ApiError("provider key missing", "EMBEDDING_PROVIDER_KEY_MISSING", 422),
    );
    render(<PlatformEmbeddingSection />);
    await screen.findByTestId("pe-root");
    await user.click(screen.getByTestId("pe-save"));
    expect(await screen.findByTestId("pe-error")).toBeInTheDocument();
  });
});
```
(Adjust the Antd Select interaction to the jsdom-robust approach if `getByRole("combobox")` doesn't match — see conventions. Keep the intent.)

- [ ] **Step 2: Run — confirm FAIL.** `npx vitest run src/pages/settings_platform/__tests__/PlatformEmbeddingSection.test.tsx`
- [ ] **Step 3: Implement** the component per the behavior. Use Antd `Card`/`Select`/`Switch`/`Button`/`Alert`, `useTranslation`, `message.success`. Map the 5 known 422 codes to `t("settings_platform.embedding_err_<code>")`, fallback `err.message`.
- [ ] **Step 4: Run — confirm PASS (3).**
- [ ] **Step 5: typecheck + commit** (`pnpm run typecheck`; `pre-commit run --files`):
```bash
git add apps/admin-ui/src/pages/settings_platform/
git commit -m "feat(admin-ui): PlatformEmbeddingSection (current + edit + save) (Stream T PR D)"
```

---

### Task 3: Wire the section into `/settings/platform` + i18n

**Files:**
- Modify: `apps/admin-ui/src/pages/SettingsPlatformConfig.tsx` (render `<PlatformEmbeddingSection />` after the tools table, under a new `<h2>`)
- Modify: `apps/admin-ui/src/i18n/locales/en.ts` + `zh-CN.ts` (add `settings_platform.embedding_*` keys)

- [ ] **Step 1: Add i18n keys** to `TranslationKeys`/`en` + `zh-CN` under `settings_platform` (the namespace already exists): `embedding_heading`, `embedding_current`, `embedding_unconfigured`, `embedding_provider_label`, `embedding_model_label`, `rerank_enable`, `rerank_provider_label`, `rerank_model_label`, `embedding_save`, `embedding_saved`, and the 5 error keys `embedding_err_EMBEDDING_PROVIDER_KEY_MISSING` / `_INVALID_EMBEDDING_MODEL` / `_INVALID_RERANK_PAIR` / `_RERANK_PROVIDER_KEY_MISSING` / `_INVALID_RERANK_MODEL` (en: human messages, e.g. "This provider has no configured key — add it under Providers above first."; zh: translations). Glossary: 提供方/模型/向量(embedding)/重排(rerank)/长期记忆.
- [ ] **Step 2: Render the section** in `SettingsPlatformConfig.tsx` after the tools `<Table>` (inside the admin-only branch): a `<h2>{t("settings_platform.embedding_heading")}</h2>` then `<PlatformEmbeddingSection />`. Import it.
- [ ] **Step 3: typecheck** (`pnpm run typecheck` — the `TranslationKeys` interface enforces zh parity) + run the existing SettingsPlatformConfig test if any (grep `SettingsPlatformConfig` under `__tests__`); confirm still green.
- [ ] **Step 4: pre-commit + commit**
```bash
git add apps/admin-ui/src/pages/SettingsPlatformConfig.tsx apps/admin-ui/src/i18n/locales/en.ts apps/admin-ui/src/i18n/locales/zh-CN.ts
git commit -m "feat(admin-ui): mount embedding section in /settings/platform + i18n (Stream T PR D)"
```

---

### Task 4: Playwright e2e + axe

**Files:**
- Create: `apps/admin-ui/e2e/platform-embedding.spec.ts`
- Reference: an existing settings/platform e2e if present (`ls apps/admin-ui/e2e | grep -i platform`), else the PR C/D Stream-S specs for the login + stub + axe pattern.

- [ ] **Step 1: Read** the e2e fixtures + an existing platform/settings spec (or `manifest-model-select.spec.ts`) for login + route-stub + `expectNoA11yViolations` signature. Confirm the system_admin login path reaches `/settings/platform`.
- [ ] **Step 2: Write `platform-embedding.spec.ts`:** log in as system_admin; stub `GET **/v1/platform/embedding-config` (enveloped, with `available_embedding`/`available_rerank`) and `PUT **/v1/platform/embedding-config` (enveloped success). Navigate to `/settings/platform`. Test A: the embedding section (`pe-root`) is visible and shows the current model; pick a provider+model and Save → assert the PUT fired. Test B: `expectNoA11yViolations(page, "settings-platform")` (or the page's label) — fix any real serious/critical violation in the component (aria-labels on the Selects), don't weaken the filter.
- [ ] **Step 3: Run** `pnpm run e2e -- platform-embedding` → pass.
- [ ] **Step 4: commit**
```bash
git add apps/admin-ui/e2e/platform-embedding.spec.ts
git commit -m "test(admin-ui): e2e platform embedding config + axe (Stream T PR D)"
```

---

## Final verification
- [ ] `pnpm run test` (all vitest), `pnpm run typecheck`, `pnpm run build`, `pnpm run build-storybook`, `pnpm run e2e` (platform-embedding + smoke) — green.
- [ ] `uv run pre-commit run --files <changed>` clean.

PR title: `feat(stream-t): PR D — platform embedding/rerank config UI`. Body: link design; the section lets a system admin configure platform embedding/rerank from capability-filtered dropdowns; consumes PR C's GET/PUT; memory-on default + PR D-hint revert + onboarding + E2E = PR E.

---

## Self-Review
**Spec coverage (T-7):** config section with provider dropdown (only configured-key providers — server-filtered via `available_*`), model dropdown (only capability-matched), rerank optional, key-configured enforced (server 422 → friendly message) → Tasks 2/3. SDK → Task 1. i18n (T-8) → Task 3. e2e+axe → Task 4. Deferred: memory-on default template + PR D-hint revert + create-agent guard + onboarding = PR E.
**Placeholders:** component behavior + tests fully specified; "check where the page keeps subcomponents / whether SDK tests exist" are read-and-match directives, not placeholders.
**Type consistency:** `ProviderModel`/`PlatformEmbeddingConfigView`/`PlatformEmbeddingConfigWrite` (Task 1) consumed by the component (Task 2). Testids `pe-*` consistent across component + tests + e2e. i18n keys referenced in Task 2 are added in Task 3 (same risk as Stream S — could add keys in Task 2; but Task 3 adds them before Task 4's axe run, and `react-i18next` returns the key if missing so Task 2 tests still pass on testids — acceptable; if a Task 2 test asserts a translated string, add that key in Task 2).
