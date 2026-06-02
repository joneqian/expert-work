# Stream S PR D — ModelSelect Widget + Capability-Adaptive Default Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the manifest editor's default model fields with a linked provider→model picker that lists only configured providers, auto-sets `supports_vision` from the model catalog, and seeds new agents with a capability-adaptive default — so an admin builds a runnable agent by picking from dropdowns, never hand-writing model YAML.

**Architecture:** A `/v1/model-catalog` SDK + cache feeds a custom RJSF **field** (`ModelSelectField`) applied to `spec.model` and its direct `fallback[]`. The field renders linked Antd `Select`s (provider → model), writes `provider`/`name`/`supports_vision` together, delegates the remaining ModelSpec fields to RJSF's defaults (nothing lost), and shows an inline note when the chosen provider has no embedding model (long-term memory won't work). The Create drawer loads the catalog and seeds the editor with a capability-adaptive default manifest.

**Tech Stack:** React 19, Antd 5 (`Select`), RJSF v5.24 (custom `field` via `fields` registry + `formContext`), `js-yaml`, Vitest + Playwright/axe. Package manager **pnpm**; run JS commands from `apps/admin-ui/`.

**Scope (locked with the user):** the linked widget covers **`spec.model` + its direct `fallback[]` only**. Other ModelSpec locations (`spec.routing.*.model`, `spec.vision.model`/`fallbacks`, `spec.aux_model`, deep nested fallback) keep RJSF default fields — still editable, plus the YAML tab. Do NOT build a custom widget for those.

**Builds on (already merged, PR B + PR C):**
- Backend `GET /v1/model-catalog` → `{ success, data: { providers: [{ provider, models: [{name, vision, embeddings, context_window, deprecated}] }] }, error }`. Only configured+enabled providers with ≥1 non-deprecated model appear; deprecated models already filtered out.
- `src/api/client.ts` `getJson<T>` unwraps the envelope.
- `src/components/manifest-editor/`: `schema.ts` (cache pattern to mirror), `yaml.ts` (`parseYaml`/`dumpYaml`), `FormView.tsx` (RJSF wrapper + `UI_SCHEMA` — the injection point), `ManifestEditor.tsx`, `index.ts`.
- `src/components/CreateAgentDrawer.tsx` exports `DEFAULT_AGENT_YAML` and renders `<ManifestEditor mode="create" initialYaml={DEFAULT_AGENT_YAML} onChange={setBuffer} />`.

---

## ModelSpec facts (from `packages/helix-protocol/.../agent_spec.py:74`)

Fields: `provider` (Literal: anthropic/openai/azure/self-hosted/kimi/glm/deepseek/qwen/doubao), `name` (string), `temperature`, `max_tokens`, `rate_limit_rpm`, `api_key_ref` (str|null), `base_url` (str|null), `azure_deployment`, `azure_api_version`, `fallback` (list[ModelSpec], recursive), `supports_vision` (bool), + cache opt-out. In JSON Schema this is `#/$defs/ModelSpec` (self-referential via `fallback`). The custom field handles `provider`/`name`/`supports_vision`; everything else delegates to RJSF defaults.

`spec.memory.long_term` is `LongTermMemorySpec | null`, default `null` (off). The embedder gate (`runtime.py:343`) rejects providers without an embedding model only when long-term memory is enabled — so the default (off) is always safe; the picker just *warns* when the chosen provider lacks embeddings.

---

## File Structure

**New**
- `apps/admin-ui/src/api/model_catalog.ts` — SDK + types: `fetchModelCatalog()`, `CatalogModel`, `ProviderModels`, `ModelCatalog`.
- `apps/admin-ui/src/components/manifest-editor/catalog.ts` — cache (mirror `schema.ts`) + pure lookups: `loadModelCatalog`, `__resetCatalogCacheForTest`, `providerNames`, `modelsFor`, `lookupModel`, `providerHasEmbeddings`.
- `apps/admin-ui/src/components/manifest-editor/defaults.ts` — `BASE_MANIFEST_YAML` (owns the template) + `buildDefaultManifest(catalog)` (capability-adaptive).
- `apps/admin-ui/src/components/manifest-editor/widgets/ModelSelectField.tsx` — the custom RJSF field.
- Tests under `__tests__/`: `catalog.test.tsx`, `defaults.test.tsx`, `ModelSelectField.test.tsx`; FormView integration cases added to a new `FormView.modelselect.test.tsx`.
- `apps/admin-ui/e2e/manifest-model-select.spec.ts`.

**Modified**
- `apps/admin-ui/src/components/manifest-editor/FormView.tsx` — register `fields={{ ModelSelect: ModelSelectField }}`, extend `UI_SCHEMA` (`ui:field` on `spec.model` + `spec.model.fallback.items`), load catalog and pass `formContext={{ modelCatalog }}`.
- `apps/admin-ui/src/components/CreateAgentDrawer.tsx` — on open, load catalog → `buildDefaultManifest` → `dumpYaml` → `initialYaml`; import `BASE_MANIFEST_YAML` from `defaults.ts` (keep `DEFAULT_AGENT_YAML` export as a thin re-export for back-compat, or migrate callers — see Task 5).
- `apps/admin-ui/src/i18n/locales/en.ts` + `zh-CN.ts` — new `model_select` namespace.

---

## Conventions (verified; copy these)
- `getJson<T>(path)` returns unwrapped data. Catalog: `getJson<ModelCatalog>("/v1/model-catalog")`.
- Cache pattern: copy `schema.ts` exactly (module-level promise + `__reset…ForTest`).
- RJSF custom field: register via `<Form fields={{ ModelSelect: ModelSelectField }} />` and select it with `"ui:field": "ModelSelect"` in uiSchema. The field receives RJSF `FieldProps`: at minimum `formData`, `onChange(value)`, `schema`, `uiSchema`, `idSchema`, `registry`, `formContext`, `required`, `disabled`, `readonly`. **Verify the exact FieldProps shape against installed `@rjsf/utils` v5.24** in Task 3 (like PR C verified `validateFormData`).
- Thread the catalog to the field via `formContext` (RJSF passes `formContext` to every field/widget).
- i18n: keys in `TranslationKeys` (en.ts) + matching `zh-CN.ts`. Glossary: 提供方=provider, 模型=model, 视觉=vision, 长期记忆=long-term memory, 配置清单=manifest.
- Testids: `model-select-provider`, `model-select-name`, `model-select-vision`, `model-select-no-embeddings`. Antd `Select` doesn't forward `data-testid` to the searchable input cleanly — put the testid on a **wrapper element** and select options by visible text in tests (see Task 3 test).
- Authoritative checks: `pnpm run typecheck` (`tsc -b`) exit 0; `npx vitest run <file>`. IDE/LSP shows spurious React-UMD / toBeInTheDocument / Cannot-find-module diagnostics on fresh & .test files — ignore them.
- Leave the unrelated repo-root `.gitignore` change unstaged in every commit.

---

### Task 1: model-catalog SDK + cache + lookups

**Files:**
- Create: `apps/admin-ui/src/api/model_catalog.ts`
- Create: `apps/admin-ui/src/components/manifest-editor/catalog.ts`
- Test: `apps/admin-ui/src/components/manifest-editor/__tests__/catalog.test.tsx`

- [ ] **Step 1: Write the failing test**

`__tests__/catalog.test.tsx`:
```tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import * as sdk from "../../../api/model_catalog";
import {
  loadModelCatalog,
  __resetCatalogCacheForTest,
  providerNames,
  modelsFor,
  lookupModel,
  providerHasEmbeddings,
} from "../catalog";

const CATALOG = {
  providers: [
    {
      provider: "deepseek",
      models: [
        { name: "deepseek-v4-pro", vision: false, embeddings: false, context_window: 1000000, deprecated: false },
      ],
    },
    {
      provider: "openai",
      models: [
        { name: "gpt-5.5", vision: true, embeddings: false, context_window: 128000, deprecated: false },
        { name: "text-embedding-3-large", vision: false, embeddings: true, context_window: null, deprecated: false },
      ],
    },
  ],
};

afterEach(() => {
  __resetCatalogCacheForTest();
  vi.restoreAllMocks();
});

describe("model catalog", () => {
  it("fetches once and caches", async () => {
    const spy = vi.spyOn(sdk, "fetchModelCatalog").mockResolvedValue(CATALOG);
    const a = await loadModelCatalog();
    const b = await loadModelCatalog();
    expect(a).toBe(b);
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("lookups work", () => {
    expect(providerNames(CATALOG)).toEqual(["deepseek", "openai"]);
    expect(modelsFor(CATALOG, "openai").map((m) => m.name)).toEqual(["gpt-5.5", "text-embedding-3-large"]);
    expect(lookupModel(CATALOG, "openai", "gpt-5.5")?.vision).toBe(true);
    expect(lookupModel(CATALOG, "openai", "nope")).toBeUndefined();
    expect(providerHasEmbeddings(CATALOG, "openai")).toBe(true);
    expect(providerHasEmbeddings(CATALOG, "deepseek")).toBe(false);
  });
});
```

- [ ] **Step 2: Run it — confirm FAIL.** `npx vitest run src/components/manifest-editor/__tests__/catalog.test.tsx`

- [ ] **Step 3: Implement the SDK** — `src/api/model_catalog.ts`:
```ts
/**
 * Model catalog SDK — Stream S PR D (Mini-ADR S-4 client side).
 *
 * ``GET /v1/model-catalog`` returns the selectable models per *configured*
 * provider (the backend already intersects the catalog with platform
 * credentials and drops deprecated models), inside the standard envelope.
 */
import { getJson } from "./client";

export interface CatalogModel {
  name: string;
  vision: boolean;
  embeddings: boolean;
  context_window: number | null;
  deprecated: boolean;
}

export interface ProviderModels {
  provider: string;
  models: CatalogModel[];
}

export interface ModelCatalog {
  providers: ProviderModels[];
}

export async function fetchModelCatalog(): Promise<ModelCatalog> {
  return getJson<ModelCatalog>("/v1/model-catalog");
}
```

- [ ] **Step 4: Implement the cache + lookups** — `src/components/manifest-editor/catalog.ts`:
```ts
/**
 * Process-lifetime cache + pure lookups over the model catalog. Mirrors
 * ``schema.ts``. Lookups are plain functions so they're trivially testable.
 */
import {
  fetchModelCatalog,
  type CatalogModel,
  type ModelCatalog,
} from "../../api/model_catalog";

let cached: Promise<ModelCatalog> | null = null;

export function loadModelCatalog(): Promise<ModelCatalog> {
  if (cached === null) {
    cached = fetchModelCatalog();
  }
  return cached;
}

export function __resetCatalogCacheForTest(): void {
  cached = null;
}

export function providerNames(catalog: ModelCatalog): string[] {
  return catalog.providers.map((p) => p.provider);
}

export function modelsFor(catalog: ModelCatalog, provider: string): CatalogModel[] {
  return catalog.providers.find((p) => p.provider === provider)?.models ?? [];
}

export function lookupModel(
  catalog: ModelCatalog,
  provider: string,
  name: string,
): CatalogModel | undefined {
  return modelsFor(catalog, provider).find((m) => m.name === name);
}

export function providerHasEmbeddings(catalog: ModelCatalog, provider: string): boolean {
  return modelsFor(catalog, provider).some((m) => m.embeddings);
}
```

- [ ] **Step 5: Run the test — confirm PASS (2 tests).**

- [ ] **Step 6: Typecheck + commit**
```bash
pnpm run typecheck
git add src/api/model_catalog.ts src/components/manifest-editor/catalog.ts src/components/manifest-editor/__tests__/catalog.test.tsx
git commit -m "feat(admin-ui): model-catalog SDK + cache + lookups (Stream S PR D)"
```

---

### Task 2: Capability-adaptive default manifest

**Files:**
- Create: `apps/admin-ui/src/components/manifest-editor/defaults.ts`
- Test: `apps/admin-ui/src/components/manifest-editor/__tests__/defaults.test.tsx`

**Behaviour:** `buildDefaultManifest(catalog)` parses `BASE_MANIFEST_YAML`, then if the catalog has ≥1 provider with a non-embedding ("chat") model, overrides `spec.model` to `{ provider, name, supports_vision }` of the first such provider/model (first provider in catalog order, first non-embedding model). If the catalog is empty (no configured provider), returns the base unchanged (static fallback so the form still renders). It must NOT enable `memory.long_term`.

- [ ] **Step 1: Write the failing test**

`__tests__/defaults.test.tsx`:
```tsx
import { describe, expect, it } from "vitest";
import { buildDefaultManifest } from "../defaults";

type Manifest = { spec: { model: { provider: string; name: string; supports_vision: boolean } } };

describe("buildDefaultManifest", () => {
  it("picks the first configured provider's first chat model and its vision flag", () => {
    const catalog = {
      providers: [
        {
          provider: "openai",
          models: [
            { name: "text-embedding-3-large", vision: false, embeddings: true, context_window: null, deprecated: false },
            { name: "gpt-5.5", vision: true, embeddings: false, context_window: 128000, deprecated: false },
          ],
        },
      ],
    };
    const m = buildDefaultManifest(catalog) as Manifest;
    // skips the embedding-only model, picks the chat model, copies its vision flag
    expect(m.spec.model.provider).toBe("openai");
    expect(m.spec.model.name).toBe("gpt-5.5");
    expect(m.spec.model.supports_vision).toBe(true);
  });

  it("falls back to the base template when no provider is configured", () => {
    const m = buildDefaultManifest({ providers: [] }) as Manifest;
    expect(m.spec.model.provider).toBeTruthy(); // base template's default provider
    expect(m).not.toHaveProperty("spec.memory.long_term");
  });
});
```

- [ ] **Step 2: Run it — confirm FAIL.**

- [ ] **Step 3: Implement** — `src/components/manifest-editor/defaults.ts`:
```ts
/**
 * Default manifest template + capability-adaptive seeding (Mini-ADR S-5).
 *
 * ``BASE_MANIFEST_YAML`` is the blank-canvas manifest. ``buildDefaultManifest``
 * pre-selects the first *configured* provider's first chat (non-embedding)
 * model and copies its vision capability, so a new agent starts on a model the
 * platform can actually build. Long-term memory stays off (default), so the
 * embedder gate can't trip at runtime.
 */
import { parseYaml } from "./yaml";
import type { CatalogModel, ModelCatalog } from "../../api/model_catalog";

export const BASE_MANIFEST_YAML = `apiVersion: helix.io/v1
kind: Agent
metadata:
  name: my-agent
  version: "1.0.0"
  tenant: my-tenant
spec:
  tenant_config: {}
  model:
    provider: anthropic
    name: claude-sonnet-4-6
  system_prompt:
    template: "You are a helpful assistant."
  sandbox:
    resources: { cpu: "1.0", memory: "1Gi" }
    network:
      egress: proxy
      allowlist: []
    filesystem:
      readonly_root: true
      writable: ["/workspace"]
`;

interface FirstChat {
  provider: string;
  model: CatalogModel;
}

function firstChatModel(catalog: ModelCatalog): FirstChat | null {
  for (const p of catalog.providers) {
    const chat = p.models.find((m) => !m.embeddings && !m.deprecated);
    if (chat) return { provider: p.provider, model: chat };
  }
  return null;
}

export function buildDefaultManifest(catalog: ModelCatalog): unknown {
  const base = parseYaml(BASE_MANIFEST_YAML) as Record<string, unknown>;
  const pick = firstChatModel(catalog);
  if (!pick) return base;
  const spec = base.spec as Record<string, unknown>;
  return {
    ...base,
    spec: {
      ...spec,
      model: {
        provider: pick.provider,
        name: pick.model.name,
        supports_vision: pick.model.vision,
      },
    },
  };
}
```

- [ ] **Step 4: Run the test — confirm PASS (2 tests).**

- [ ] **Step 5: Typecheck + commit**
```bash
pnpm run typecheck
git add src/components/manifest-editor/defaults.ts src/components/manifest-editor/__tests__/defaults.test.tsx
git commit -m "feat(admin-ui): capability-adaptive default manifest (Stream S PR D)"
```

---

### Task 3: `ModelSelectField` custom RJSF field

**Files:**
- Create: `apps/admin-ui/src/components/manifest-editor/widgets/ModelSelectField.tsx`
- Test: `apps/admin-ui/src/components/manifest-editor/__tests__/ModelSelectField.test.tsx`

**Contract.** A custom RJSF **field** for a `ModelSpec` object. RJSF `FieldProps`:
- reads `formData` (the ModelSpec object, may be partial), `formContext.modelCatalog` (a `ModelCatalog` or `undefined` while loading), `registry`, `schema`, `uiSchema`, `idSchema`, `disabled`/`readonly`.
- renders:
  1. **Provider** Antd `Select` — options = `providerNames(catalog)`; value = `formData.provider`. On change → `onChange({ ...formData, provider, name: undefined, supports_vision: false })` (reset model when provider changes). Wrapper testid `model-select-provider`.
  2. **Model** Antd `Select` — options = `modelsFor(catalog, formData.provider)` (label `name`, optionally show context window); value = `formData.name`; disabled until a provider is chosen. On change → look up the model and `onChange({ ...formData, name, supports_vision: entry?.vision ?? false })`. Wrapper testid `model-select-name`.
  3. **Vision** read-only indicator (Antd `Tag`/text) reflecting `formData.supports_vision`, testid `model-select-vision`. Auto-set; not directly editable here.
  4. **No-embeddings note** — when a provider is selected and `!providerHasEmbeddings(catalog, provider)`, an inline Antd `Alert type="info"` / muted text (testid `model-select-no-embeddings`) with i18n `model_select.no_embeddings`.
  5. **Remaining ModelSpec fields** (temperature, max_tokens, rate_limit_rpm, api_key_ref, base_url, azure_*, fallback, cache opt-out) — must stay editable. Delegate to RJSF: render the default object for the *remaining* properties so nothing is lost. (See implementation note.)
- While `catalog` is undefined (loading), render the provider Select disabled with a loading placeholder; do not crash.

**Implementation note — verify RJSF v5.24 FieldProps + delegation first.** Before writing the component, open `node_modules/@rjsf/utils/lib/types.d.ts` (or `.../index.d.ts`) and confirm the `FieldProps` member names actually installed: `formData`, `onChange`, `registry`, `formContext`, `idSchema`, `schema`, `uiSchema`, `disabled`, `readonly`. Confirm `registry.fields.SchemaField` exists. If a name differs, adapt and note it in your report (this is the PR-C-style "verify against installed API" step).

For requirement (5), use whichever of these binds cleanly against the installed RJSF — both are acceptable; the test in Step 1 only asserts that a remaining field (`temperature`) is still rendered and editable:
- **(a) Delegate (preferred):** render `const { SchemaField } = registry.fields;` with a reduced schema = the field's `schema` minus `provider`/`name`/`supports_vision` properties (and those names dropped from `required`), passing through `idSchema`, `formData`, `onChange` so edits merge back. Wrap in `<details>`/Antd `Collapse` labelled "Advanced".
- **(b) Hand-render:** Antd `InputNumber`/`Input` for the bounded advanced set (temperature, max_tokens, rate_limit_rpm, api_key_ref, base_url, azure_deployment, azure_api_version) inside an Antd `Collapse`, each writing back via `onChange({ ...formData, <field>: v })`. (Accept that a future new ModelSpec scalar would need a code change or the YAML tab — note this if you choose (b).)

- [ ] **Step 1: Write the failing test**

`__tests__/ModelSelectField.test.tsx`:
```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Form from "@rjsf/antd";
import validator from "@rjsf/validator-ajv8";
import { ModelSelectField } from "../widgets/ModelSelectField";

const MODELSPEC_SCHEMA = {
  type: "object",
  properties: {
    provider: { type: "string" },
    name: { type: "string" },
    supports_vision: { type: "boolean" },
    temperature: { type: "number", default: 0.2 },
  },
} as const;

const CATALOG = {
  providers: [
    {
      provider: "deepseek",
      models: [
        { name: "deepseek-v4-pro", vision: false, embeddings: false, context_window: 1000000, deprecated: false },
      ],
    },
    {
      provider: "openai",
      models: [
        { name: "gpt-5.5", vision: true, embeddings: false, context_window: 128000, deprecated: false },
        { name: "text-embedding-3-large", vision: false, embeddings: true, context_window: null, deprecated: false },
      ],
    },
  ],
};

function renderField(formData: unknown, onChange = vi.fn()) {
  return render(
    <Form
      schema={MODELSPEC_SCHEMA as object}
      validator={validator}
      fields={{ ModelSelect: ModelSelectField }}
      uiSchema={{ "ui:field": "ModelSelect", "ui:submitButtonOptions": { norender: true } }}
      formData={formData}
      formContext={{ modelCatalog: CATALOG }}
      onChange={(e) => onChange(e.formData)}
    />,
  );
}

describe("ModelSelectField", () => {
  it("lists configured providers", async () => {
    const user = userEvent.setup();
    renderField({});
    const provider = within(screen.getByTestId("model-select-provider")).getByRole("combobox");
    await user.click(provider);
    expect(await screen.findByText("deepseek")).toBeInTheDocument();
    expect(screen.getByText("openai")).toBeInTheDocument();
  });

  it("selecting a vision model auto-sets supports_vision", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderField({ provider: "openai" }, onChange);
    const nameSel = within(screen.getByTestId("model-select-name")).getByRole("combobox");
    await user.click(nameSel);
    await user.click(await screen.findByText("gpt-5.5"));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ provider: "openai", name: "gpt-5.5", supports_vision: true }),
    );
  });

  it("shows the no-embeddings note for a provider without an embedding model", () => {
    renderField({ provider: "deepseek" });
    expect(screen.getByTestId("model-select-no-embeddings")).toBeInTheDocument();
  });

  it("keeps a remaining ModelSpec field (temperature) editable", () => {
    renderField({ provider: "openai", name: "gpt-5.5" });
    // delegated/hand-rendered advanced field must be present somewhere in the form
    expect(screen.getByText(/temperature/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run it — confirm FAIL** (`../widgets/ModelSelectField` missing). `npx vitest run src/components/manifest-editor/__tests__/ModelSelectField.test.tsx`

- [ ] **Step 3: Implement `ModelSelectField.tsx`.** Build the component to satisfy the contract + the 4 tests. Skeleton for the core (fill the delegation per the note; bind FieldProps to the verified installed shape):
```tsx
/**
 * Custom RJSF field for a ModelSpec — Stream S PR D (Mini-ADR S-3).
 *
 * Linked provider→model dropdowns (configured providers only, from the model
 * catalog in formContext); selecting a model copies its vision capability into
 * supports_vision. Remaining ModelSpec fields delegate to RJSF defaults so
 * nothing is lost. Applied to spec.model and its direct fallback[] items.
 */
import { Alert, Select, Tag } from "antd";
import type { FieldProps } from "@rjsf/utils";
import { useTranslation } from "react-i18next";

import type { ModelCatalog } from "../../../api/model_catalog";
import { lookupModel, modelsFor, providerHasEmbeddings, providerNames } from "../catalog";

type ModelSpecData = {
  provider?: string;
  name?: string;
  supports_vision?: boolean;
  [k: string]: unknown;
};

export function ModelSelectField(props: FieldProps): JSX.Element {
  const { formData, onChange, formContext } = props;
  const { t } = useTranslation();
  const data = (formData ?? {}) as ModelSpecData;
  const catalog = (formContext as { modelCatalog?: ModelCatalog } | undefined)?.modelCatalog;

  const providers = catalog ? providerNames(catalog) : [];
  const models = catalog && data.provider ? modelsFor(catalog, data.provider) : [];

  function onProvider(provider: string): void {
    onChange({ ...data, provider, name: undefined, supports_vision: false });
  }
  function onModel(name: string): void {
    const entry = catalog && data.provider ? lookupModel(catalog, data.provider, name) : undefined;
    onChange({ ...data, name, supports_vision: entry?.vision ?? false });
  }

  const noEmbeddings =
    catalog && data.provider ? !providerHasEmbeddings(catalog, data.provider) : false;

  return (
    <div data-testid="model-select-field">
      <div data-testid="model-select-provider" style={{ marginBottom: 8 }}>
        <Select
          aria-label={t("model_select.provider_label")}
          placeholder={t("model_select.provider_placeholder")}
          loading={!catalog}
          disabled={!catalog}
          value={data.provider}
          onChange={onProvider}
          options={providers.map((p) => ({ label: p, value: p }))}
          style={{ width: "100%" }}
        />
      </div>
      <div data-testid="model-select-name" style={{ marginBottom: 8 }}>
        <Select
          aria-label={t("model_select.model_label")}
          placeholder={t("model_select.model_placeholder")}
          disabled={!data.provider}
          value={data.name}
          onChange={onModel}
          options={models.map((m) => ({ label: m.name, value: m.name }))}
          style={{ width: "100%" }}
        />
      </div>
      <div data-testid="model-select-vision" style={{ marginBottom: 8 }}>
        <Tag color={data.supports_vision ? "cyan" : "default"}>
          {data.supports_vision ? t("model_select.vision_on") : t("model_select.vision_off")}
        </Tag>
      </div>
      {noEmbeddings && (
        <Alert
          type="info"
          showIcon
          message={t("model_select.no_embeddings")}
          style={{ marginBottom: 8 }}
          data-testid="model-select-no-embeddings"
        />
      )}
      {/* Remaining ModelSpec fields — delegate to RJSF defaults (see plan note). */}
      {/* render reduced SchemaField OR an Antd Collapse of advanced inputs here */}
    </div>
  );
}
```

- [ ] **Step 4: Run the test — confirm PASS (4 tests).** If a `getByRole("combobox")` query doesn't match Antd's Select internals in jsdom, query the option text after clicking the wrapper, or use `screen.getByTestId("model-select-provider")` + `userEvent.click` then option text. Adjust the test queries (not the intent) to whatever reliably opens the Antd Select and selects an option in jsdom; document the working approach in a comment. If the `temperature` delegation test fails, finish requirement (5) until it passes.

- [ ] **Step 5: Typecheck + commit**
```bash
pnpm run typecheck
git add src/components/manifest-editor/widgets/ModelSelectField.tsx src/components/manifest-editor/__tests__/ModelSelectField.test.tsx
git commit -m "feat(admin-ui): ModelSelect custom RJSF field (Stream S PR D)"
```

---

### Task 4: Wire `ModelSelect` into `FormView`

**Files:**
- Modify: `apps/admin-ui/src/components/manifest-editor/FormView.tsx`
- Test: `apps/admin-ui/src/components/manifest-editor/__tests__/FormView.modelselect.test.tsx`

FormView must: load the catalog (`loadModelCatalog`) into state, pass it as `formContext={{ modelCatalog }}`, register `fields={{ ModelSelect: ModelSelectField }}`, and extend `UI_SCHEMA` so `spec.model` and `spec.model.fallback.items` use `"ui:field": "ModelSelect"`. Keep the existing `system_prompt` textarea uiSchema.

- [ ] **Step 1: Write the failing test**

`__tests__/FormView.modelselect.test.tsx`:
```tsx
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import "../../../i18n";
import * as catalogSdk from "../../../api/model_catalog";
import { __resetCatalogCacheForTest } from "../catalog";
import { FormView } from "../FormView";

const SCHEMA = {
  type: "object",
  properties: {
    spec: {
      type: "object",
      properties: {
        model: {
          type: "object",
          properties: {
            provider: { type: "string" },
            name: { type: "string" },
            supports_vision: { type: "boolean" },
          },
        },
      },
    },
  },
} as const;

beforeEach(() => {
  __resetCatalogCacheForTest();
  vi.spyOn(catalogSdk, "fetchModelCatalog").mockResolvedValue({
    providers: [
      { provider: "deepseek", models: [{ name: "deepseek-v4-pro", vision: false, embeddings: false, context_window: 1000000, deprecated: false }] },
    ],
  });
});
afterEach(() => vi.restoreAllMocks());

describe("FormView model picker", () => {
  it("renders the ModelSelect field for spec.model once the catalog loads", async () => {
    render(<FormView schema={SCHEMA as object} formData={{ spec: { model: { provider: "deepseek" } } }} onChange={vi.fn()} />);
    await waitFor(() => expect(screen.getByTestId("model-select-field")).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: Run it — confirm FAIL** (FormView doesn't load catalog / register the field yet).

- [ ] **Step 3: Modify `FormView.tsx`.** Add catalog loading + field registration + uiSchema:
```tsx
import { useEffect, useState } from "react";
import Form from "@rjsf/antd";
import validator from "@rjsf/validator-ajv8";
import type { IChangeEvent } from "@rjsf/core";
import type { UiSchema } from "@rjsf/utils";

import type { JsonSchema } from "../../api/manifest_schema";
import type { ModelCatalog } from "../../api/model_catalog";
import { loadModelCatalog } from "./catalog";
import { ModelSelectField } from "./widgets/ModelSelectField";

interface FormViewProps {
  schema: JsonSchema;
  formData: unknown;
  onChange: (data: unknown) => void;
}

const UI_SCHEMA: UiSchema = {
  "ui:submitButtonOptions": { norender: true },
  spec: {
    system_prompt: {
      template: { "ui:widget": "textarea", "ui:options": { rows: 6 } },
    },
    model: {
      "ui:field": "ModelSelect",
      fallback: { items: { "ui:field": "ModelSelect" } },
    },
  },
};

export function FormView({ schema, formData, onChange }: FormViewProps) {
  const [modelCatalog, setModelCatalog] = useState<ModelCatalog | undefined>(undefined);

  useEffect(() => {
    let alive = true;
    loadModelCatalog().then(
      (c) => alive && setModelCatalog(c),
      () => {
        /* catalog optional — the field degrades to a disabled/loading select */
      },
    );
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div data-testid="manifest-form-view">
      <Form
        schema={schema}
        uiSchema={UI_SCHEMA}
        fields={{ ModelSelect: ModelSelectField }}
        formContext={{ modelCatalog }}
        formData={formData}
        validator={validator}
        liveValidate={false}
        showErrorList={false}
        onChange={(e: IChangeEvent) => onChange(e.formData)}
      />
    </div>
  );
}
```

- [ ] **Step 4: Run the new test + the existing FormView test**
`npx vitest run src/components/manifest-editor/__tests__/FormView.modelselect.test.tsx src/components/manifest-editor/__tests__/FormView.test.tsx`
Expected: all pass. The existing FormView test uses a schema with `metadata.name` only (no `spec.model`), so it still renders default fields — confirm it's unaffected.

- [ ] **Step 5: Typecheck + commit**
```bash
pnpm run typecheck
git add src/components/manifest-editor/FormView.tsx src/components/manifest-editor/__tests__/FormView.modelselect.test.tsx
git commit -m "feat(admin-ui): wire ModelSelect into FormView for spec.model + fallback (Stream S PR D)"
```

---

### Task 5: Capability-adaptive default seed in `CreateAgentDrawer`

**Files:**
- Modify: `apps/admin-ui/src/components/CreateAgentDrawer.tsx`
- Test: `apps/admin-ui/src/components/__tests__/CreateAgentDrawer.test.tsx`

On open, the drawer loads the catalog and seeds the editor with `dumpYaml(buildDefaultManifest(catalog))`; until the catalog resolves (or if it fails), it uses `BASE_MANIFEST_YAML`. Keep submit logic. `DEFAULT_AGENT_YAML` export becomes a re-export of `BASE_MANIFEST_YAML` (back-compat for any importer).

- [ ] **Step 1: Update the drawer test.** Add a catalog mock to the existing test setup and a case asserting the seeded default reflects a configured provider. Add to `beforeEach` the catalog spy, and add this case:
```tsx
  it("seeds the editor with the first configured provider's model", async () => {
    render(<CreateAgentDrawer open onClose={onClose} onCreated={onCreated} />);
    await screen.findByTestId("manifest-editor-create");
    // switch to YAML to read the seeded manifest deterministically
    await userEvent.click(screen.getByTestId("manifest-tab-yaml"));
    const ta = screen.getByTestId("monaco-stub") as HTMLTextAreaElement;
    await waitFor(() => expect(ta.value).toContain("provider: deepseek"));
  });
```
Add to `beforeEach` (alongside the existing schema mock):
```tsx
  __resetCatalogCacheForTest();
  vi.spyOn(catalogSdk, "fetchModelCatalog").mockResolvedValue({
    providers: [
      { provider: "deepseek", models: [{ name: "deepseek-v4-pro", vision: false, embeddings: false, context_window: 1000000, deprecated: false }] },
    ],
  });
```
with imports `import * as catalogSdk from "../../api/model_catalog";` and `import { __resetCatalogCacheForTest } from "../manifest-editor/catalog";`. Keep the existing 3 tests; they should still pass (the default seed contains `kind: Agent`).

- [ ] **Step 2: Run it — confirm the new case FAILS** (drawer still seeds the static `DEFAULT_AGENT_YAML` = anthropic).

- [ ] **Step 3: Modify `CreateAgentDrawer.tsx`.**
- Replace the inline `DEFAULT_AGENT_YAML` constant with: `import { BASE_MANIFEST_YAML, buildDefaultManifest } from "./manifest-editor/defaults";` and `export const DEFAULT_AGENT_YAML = BASE_MANIFEST_YAML;` (back-compat).
- Add state `const [initialYaml, setInitialYaml] = useState(BASE_MANIFEST_YAML);` and seed `buffer` from it.
- On open, load the catalog and compute the adaptive default:
```tsx
import { dumpYaml } from "./manifest-editor/yaml";
import { loadModelCatalog } from "./manifest-editor/catalog";
// ...
useEffect(() => {
  if (!open) return;
  let alive = true;
  loadModelCatalog().then(
    (catalog) => {
      if (!alive) return;
      const seeded = dumpYaml(buildDefaultManifest(catalog));
      setInitialYaml(seeded);
      setBuffer(seeded);
    },
    () => {
      /* keep BASE_MANIFEST_YAML seed on failure */
    },
  );
  return () => {
    alive = false;
  };
}, [open]);
```
- Pass `initialYaml` to the editor: `<ManifestEditor mode="create" initialYaml={initialYaml} onChange={setBuffer} />`. Because the drawer uses `destroyOnHidden`, the editor remounts when `initialYaml` changes after the catalog resolves — acceptable (it reseeds with the adaptive default). Keep `reset()` setting `buffer`/`initialYaml` back to `BASE_MANIFEST_YAML`.

- [ ] **Step 4: Run the drawer test + typecheck** — all 4 cases pass; `pnpm run typecheck` exit 0.

- [ ] **Step 5: Commit**
```bash
git add src/components/CreateAgentDrawer.tsx src/components/__tests__/CreateAgentDrawer.test.tsx
git commit -m "feat(admin-ui): seed Create drawer with capability-adaptive default (Stream S PR D)"
```

---

### Task 6: i18n `model_select` namespace

**Files:**
- Modify: `apps/admin-ui/src/i18n/locales/en.ts` + `zh-CN.ts`

- [ ] **Step 1: Add to `TranslationKeys` + `en` (after `manifest_editor`):**
```ts
  model_select: {
    provider_label: string;
    provider_placeholder: string;
    model_label: string;
    model_placeholder: string;
    vision_on: string;
    vision_off: string;
    no_embeddings: string;
  };
```
en values:
```ts
  model_select: {
    provider_label: "Provider",
    provider_placeholder: "Select a configured provider",
    model_label: "Model",
    model_placeholder: "Select a model",
    vision_on: "Vision: supported",
    vision_off: "Vision: not supported",
    no_embeddings:
      "This provider has no embedding model — long-term memory won't work with it.",
  };
```

- [ ] **Step 2: Add the `zh-CN` block:**
```ts
  model_select: {
    provider_label: "提供方",
    provider_placeholder: "选择已配置密钥的提供方",
    model_label: "模型",
    model_placeholder: "选择模型",
    vision_on: "视觉：支持",
    vision_off: "视觉：不支持",
    no_embeddings: "该提供方没有 embedding 模型，长期记忆无法在其上使用。",
  };
```

- [ ] **Step 3: `pnpm run typecheck`** (interface enforces both locales) → exit 0.

- [ ] **Step 4: Commit**
```bash
git add src/i18n/locales/en.ts src/i18n/locales/zh-CN.ts
git commit -m "i18n(admin-ui): model_select namespace (Stream S PR D)"
```

---

### Task 7: Playwright e2e — pick a model via the form

**Files:**
- Create: `apps/admin-ui/e2e/manifest-model-select.spec.ts`
- Reference: `e2e/fixtures.ts`, `e2e/manifest-editor.spec.ts` (PR C — copy its login + schema-stub pattern).

- [ ] **Step 1: Read `e2e/manifest-editor.spec.ts`** for the exact login + `**/v1/agents/schema` stub + create-button (`agents-create`) pattern. The new spec additionally stubs `**/v1/model-catalog` with the enveloped catalog:
```ts
{ success: true, error: null, data: { providers: [
  { provider: "deepseek", models: [{ name: "deepseek-v4-pro", vision: false, embeddings: false, context_window: 1000000, deprecated: false }] },
  { provider: "openai", models: [{ name: "gpt-5.5", vision: true, embeddings: false, context_window: 128000, deprecated: false }] },
] } }
```
The schema stub must include `spec.model` as an object with `provider`/`name`/`supports_vision` so the ModelSelect field renders.

- [ ] **Step 2: Write `e2e/manifest-model-select.spec.ts`** — log in, stub both routes, open the create drawer, assert `model-select-field` is visible, pick provider `openai` then model `gpt-5.5`, and assert `model-select-vision` shows the supported state. Mirror the PR C spec's helpers/structure exactly (same login disclosure handling). Keep it light (no Monaco typing). Add an axe check (`expectNoA11yViolations(page, "create-agent-drawer")`) — fix any real serious/critical violation in the component (e.g. add `aria-label`s), do not weaken the filter.

- [ ] **Step 3: Run** `pnpm run e2e -- manifest-model-select` → tests pass.

- [ ] **Step 4: Commit**
```bash
git add apps/admin-ui/e2e/manifest-model-select.spec.ts <any component file fixed for axe>
git commit -m "test(admin-ui): e2e pick-model-via-form + axe (Stream S PR D)"
```

---

## Final verification (before opening the PR)

From `apps/admin-ui/`:
- [ ] `pnpm run test` — all vitest suites pass (new + existing).
- [ ] `pnpm run typecheck` — exit 0.
- [ ] `pnpm run build` and `pnpm run build-storybook` — succeed.
- [ ] `pnpm run e2e` — manifest-editor + manifest-model-select + smoke pass.
- [ ] From repo root: `uv run pre-commit run --files <changed files>` — clean.

PR title: `feat(stream-s): PR D — ModelSelect linked widget + capability-adaptive default`. Body: link the design + this plan; state scope (main `spec.model` + direct `fallback[]`; other ModelSpec locations use default fields per the locked decision); note S-3/S-4(client)/S-5 covered; defer agent-detail tab integration + create/edit e2e to PR E.

---

## Self-Review

**Spec coverage (STREAM-S-DESIGN.md §5 PR D = "ModelSelect 联动控件 + defaults.ts 能力自适应 + 黄条提示 + i18n; 测试"):**
- ModelSelect linked control (S-3) → Task 3 + Task 4 (wiring). Provider dropdown = configured-only (catalog), model dropdown = provider's models, auto-`supports_vision`. ✅
- model-catalog client (S-4 client side) → Task 1. ✅
- Capability-adaptive default (S-5) → Task 2 (`buildDefaultManifest`) + Task 5 (drawer seed). ✅
- "黄条提示" → realised as the inline `model-select-no-embeddings` note in Task 3 (per grounding, long-term memory defaults off, so the warning is contextual to provider choice, not a global banner). ✅
- fallback reuse → Task 4 uiSchema `spec.model.fallback.items["ui:field"]`. ✅
- i18n (S-8) → Task 6. ✅
- Tests → Tasks 1-5 vitest, Task 7 e2e + axe. ✅
- **Deferred (correct):** other ModelSpec locations keep default fields (locked scope); agent-detail tab integration + create/edit e2e = PR E.

**Placeholder scan:** no TBD/"handle appropriately". Task 3's delegation requirement (5) gives two concrete acceptable approaches (a/b) + a driving test, plus an explicit "verify FieldProps against installed RJSF" step — these are bounded decisions with criteria, not placeholders, matching the PR-C pattern that worked.

**Type consistency:** `ModelCatalog`/`ProviderModels`/`CatalogModel` defined once in `api/model_catalog.ts`, imported by `catalog.ts`, `defaults.ts`, `ModelSelectField`, `FormView`, `CreateAgentDrawer`. `loadModelCatalog`/`__resetCatalogCacheForTest`/`providerNames`/`modelsFor`/`lookupModel`/`providerHasEmbeddings` consistent across Tasks 1/3/4. `buildDefaultManifest(catalog)`/`BASE_MANIFEST_YAML` consistent across Tasks 2/5. `formContext={{ modelCatalog }}` key matches the field's `formContext.modelCatalog` read (Tasks 3/4). uiSchema `ui:field: "ModelSelect"` matches `fields={{ ModelSelect: ModelSelectField }}` registration (Tasks 3/4).
