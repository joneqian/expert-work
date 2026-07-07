# Stream S PR C — `<ManifestEditor>` Frontend Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a schema-driven `<ManifestEditor>` (visual form ⇄ raw-YAML tabs with switch-time sync + validation) and wire it into the Create-Agent drawer, so an admin can author a manifest without hand-writing YAML.

**Architecture:** A single in-memory `manifestObject` is the source of truth. The **Form** tab renders [RJSF](https://rjsf-team.github.io/react-jsonschema-form/) straight from the backend's `GET /v1/agents/schema` (zero drift). The **YAML** tab is a Monaco escape hatch. Switching tabs serialises (`dump`) or parses+validates (`parse → ajv`); an invalid YAML→Form switch is blocked with an inline error. The editor emits the current manifest as a YAML string via `onChange` so the parent (drawer) submits exactly what's shown.

**Tech Stack:** React 19, Antd 5, `@rjsf/antd` + `@rjsf/validator-ajv8` (RJSF v5), `js-yaml`, `@monaco-editor/react`, Vitest + Testing Library, Playwright + axe.

**Scope boundary (do NOT build here — later PRs):**
- ❌ `ModelSelect` provider/model linked widget + `/v1/model-catalog` SDK → **PR D**
- ❌ `defaults.ts` capability-adaptive default template + warning banner → **PR D**
- ❌ Wiring into the agent-detail `配置清单` tab → **PR E**

In PR C the Form tab uses RJSF's **default** widgets for every field (including `model`); the only custom polish is a minimal `uiSchema` (field ordering + a few `textarea`/`collapsed` hints). PR D replaces the model fields with the custom widget.

---

## File Structure

**New files**
- `apps/admin-ui/src/api/manifest_schema.ts` — SDK: `fetchAgentSchema()` → `GET /v1/agents/schema`.
- `apps/admin-ui/src/components/manifest-editor/schema.ts` — process-lifetime cache around `fetchAgentSchema()`.
- `apps/admin-ui/src/components/manifest-editor/yaml.ts` — `parseYaml` / `dumpYaml` wrappers around `js-yaml`.
- `apps/admin-ui/src/components/manifest-editor/YamlView.tsx` — Monaco wrapper (YAML), testid on a wrapper div.
- `apps/admin-ui/src/components/manifest-editor/FormView.tsx` — RJSF wrapper + baseline `uiSchema`.
- `apps/admin-ui/src/components/manifest-editor/ManifestEditor.tsx` — tabs + dual state + switch-time sync/validation.
- `apps/admin-ui/src/components/manifest-editor/index.ts` — re-export `ManifestEditor`.
- Tests: `apps/admin-ui/src/components/manifest-editor/__tests__/{schema,yaml,YamlView,FormView,ManifestEditor}.test.tsx`.
- `apps/admin-ui/e2e/manifest-editor.spec.ts` — Playwright create-via-form flow + axe.

**Modified files**
- `apps/admin-ui/package.json` — add RJSF deps.
- `apps/admin-ui/src/components/CreateAgentDrawer.tsx` — swap Monaco `<Editor>` for `<ManifestEditor>`.
- `apps/admin-ui/src/components/__tests__/CreateAgentDrawer.test.tsx` — update for the new internal editor.
- `apps/admin-ui/src/i18n/locales/en.ts` + `zh-CN.ts` — new `manifest_editor` namespace.

---

## Conventions (copy these — verified against the codebase)

- **Package manager:** detect the lockfile in `apps/admin-ui/` (`package-lock.json` → `npm`, `pnpm-lock.yaml` → `pnpm`, `yarn.lock` → `yarn`). Use that tool for installs. Run all JS commands from `apps/admin-ui/`.
- **API SDK:** `getJson<T>(path)` (from `src/api/client.ts`) already unwraps the `{success,data,error}` envelope. The schema endpoint IS enveloped (`agent_schema.py:24`), so `getJson<JsonSchema>("/v1/agents/schema")` returns the schema object.
- **i18n:** `const { t } = useTranslation()`; keys live in `TranslationKeys` (interface in `en.ts`) and must be added to BOTH `en.ts` and `zh-CN.ts`. Glossary: 配置清单=manifest contexts, 表单=form, 提供方=provider, 智能体=agent. Keep `YAML`/`JSON`/`Monaco` as tokens.
- **Monaco testid gotcha:** `@monaco-editor/react`'s `<Editor>` does NOT forward `data-testid` to a queryable DOM node in real browsers. Put the testid on a **wrapper `div`**, not on `<Editor>`. Unit tests mock Monaco to a `<textarea>` (see existing mock below).
- **Props:** named `interface XxxProps`, no `React.FC`. Explicit types on exports. No `any` — use `unknown` + narrowing. No `console.log`.
- **Styling:** inline `style={{ … }}` with `var(--ew-*)` tokens, and `.ew-*` classes from `theme/global.css`. Antd components inherit the ConfigProvider theme.
- **Vitest:** test files under a `__tests__/` dir, `*.test.tsx`; import `"../../../i18n"` to init translations; mock SDK with `vi.spyOn`; the global `src/test/setup.ts` stubs axios so no network happens.
- **Run commands** (from `apps/admin-ui/`):
  - Unit tests: `npm run test -- <path>` (vitest; adjust verb to the PM). One file: `npx vitest run src/components/manifest-editor/__tests__/yaml.test.tsx`.
  - Typecheck: `npm run typecheck` (`tsc --noEmit`).
  - Build: `npm run build`. Storybook build: `npm run build-storybook`.
  - E2E: `npm run e2e` (Playwright). Lint/format for the whole repo still goes through root `pre-commit`.

**Monaco mock (reuse verbatim in any unit test that mounts a YAML view):**
```typescript
vi.mock("@monaco-editor/react", () => {
  const Editor = ({
    value,
    onChange,
  }: {
    value?: string;
    onChange?: (v: string | undefined) => void;
  }) => (
    <textarea
      data-testid="monaco-stub"
      value={value}
      onChange={(e) => onChange?.(e.target.value)}
    />
  );
  return { default: Editor };
});
```

---

### Task 1: Add RJSF dependencies and confirm React 19 / Antd 5 compatibility

**Files:**
- Modify: `apps/admin-ui/package.json`
- Test: `apps/admin-ui/src/components/manifest-editor/__tests__/rjsf-smoke.test.tsx` (temporary smoke; deleted in Task 5's commit)

- [ ] **Step 1: Install the four RJSF packages (pinned to the same v5 minor)**

From `apps/admin-ui/`, using the detected package manager (npm shown):
```bash
npm install @rjsf/core@^5.24.0 @rjsf/antd@^5.24.0 @rjsf/utils@^5.24.0 @rjsf/validator-ajv8@^5.24.0
```
If the install fails on a React 19 peer-dependency conflict, retry with the PM's legacy-peer flag (`npm install … --legacy-peer-deps`). Record in the commit message which flag (if any) was needed.

Expected: `package.json` `dependencies` now lists all four `@rjsf/*` packages; the lockfile updates. `@rjsf/validator-ajv8` brings `ajv` v8 transitively — do **not** add `ajv` separately.

- [ ] **Step 2: Write a smoke test that mounts an RJSF form**

`apps/admin-ui/src/components/manifest-editor/__tests__/rjsf-smoke.test.tsx`:
```typescript
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import Form from "@rjsf/antd";
import validator from "@rjsf/validator-ajv8";

describe("rjsf compat smoke", () => {
  it("renders a string field from a schema", () => {
    const schema = {
      type: "object",
      properties: { greeting: { type: "string", title: "Greeting" } },
    } as const;
    render(<Form schema={schema} validator={validator} />);
    expect(screen.getByText("Greeting")).toBeInTheDocument();
  });

  it("validateFormData reports a missing required field", () => {
    const schema = {
      type: "object",
      required: ["greeting"],
      properties: { greeting: { type: "string" } },
    } as const;
    const result = validator.validateFormData({}, schema);
    expect(result.errors.length).toBeGreaterThan(0);
  });
});
```

- [ ] **Step 3: Run the smoke test**

Run: `npx vitest run src/components/manifest-editor/__tests__/rjsf-smoke.test.tsx`
Expected: PASS (2 tests). If `validator.validateFormData` has a different signature in the installed version, note the actual API in a comment — later tasks depend on it. If RJSF's antd theme throws under React 19, STOP and report BLOCKED with the error (this is the known risk; do not paper over it).

- [ ] **Step 4: Confirm typecheck + build still pass**

Run: `npm run typecheck && npm run build`
Expected: both succeed. (The smoke test file stays for now; Task 5 removes it once `FormView` exists.)

- [ ] **Step 5: Commit**

```bash
git add apps/admin-ui/package.json apps/admin-ui/package-lock.json \
  apps/admin-ui/src/components/manifest-editor/__tests__/rjsf-smoke.test.tsx
git commit -m "build(admin-ui): add RJSF deps for the visual manifest editor (Stream S PR C)"
```

---

### Task 2: Schema SDK + process-lifetime cache

**Files:**
- Create: `apps/admin-ui/src/api/manifest_schema.ts`
- Create: `apps/admin-ui/src/components/manifest-editor/schema.ts`
- Test: `apps/admin-ui/src/components/manifest-editor/__tests__/schema.test.tsx`

- [ ] **Step 1: Write the failing test**

`__tests__/schema.test.tsx`:
```typescript
import { afterEach, describe, expect, it, vi } from "vitest";
import * as sdk from "../../../api/manifest_schema";
import { loadAgentSchema, __resetSchemaCacheForTest } from "../schema";

afterEach(() => {
  __resetSchemaCacheForTest();
  vi.restoreAllMocks();
});

describe("loadAgentSchema", () => {
  it("fetches the schema and caches it across calls", async () => {
    const fake = { type: "object", properties: {} };
    const spy = vi.spyOn(sdk, "fetchAgentSchema").mockResolvedValue(fake);

    const first = await loadAgentSchema();
    const second = await loadAgentSchema();

    expect(first).toBe(fake);
    expect(second).toBe(fake);
    expect(spy).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `npx vitest run src/components/manifest-editor/__tests__/schema.test.tsx`
Expected: FAIL — `../schema` / `fetchAgentSchema` not found.

- [ ] **Step 3: Implement the SDK**

`apps/admin-ui/src/api/manifest_schema.ts`:
```typescript
/**
 * Manifest JSON Schema SDK — Stream S PR C (Mini-ADR S-1).
 *
 * Wraps ``GET /v1/agents/schema`` which returns ``AgentSpec.model_json_schema()``
 * inside the standard ``{ success, data, error }`` envelope. The visual editor
 * renders its form straight from this, so the form never drifts from the
 * backend contract.
 */
import { getJson } from "./client";

/** A JSON Schema document. Kept loose — RJSF consumes it structurally. */
export type JsonSchema = Record<string, unknown>;

export async function fetchAgentSchema(): Promise<JsonSchema> {
  return getJson<JsonSchema>("/v1/agents/schema");
}
```

- [ ] **Step 4: Implement the cache**

`apps/admin-ui/src/components/manifest-editor/schema.ts`:
```typescript
/**
 * Process-lifetime cache for the AgentSpec JSON Schema. The schema only
 * changes on backend deploy, so one fetch per page load is plenty. The
 * cache stores the in-flight promise to dedupe concurrent callers.
 */
import { fetchAgentSchema, type JsonSchema } from "../../api/manifest_schema";

let cached: Promise<JsonSchema> | null = null;

export function loadAgentSchema(): Promise<JsonSchema> {
  if (cached === null) {
    cached = fetchAgentSchema();
  }
  return cached;
}

/** Test-only: clear the cache between cases. */
export function __resetSchemaCacheForTest(): void {
  cached = null;
}
```

- [ ] **Step 5: Run the test**

Run: `npx vitest run src/components/manifest-editor/__tests__/schema.test.tsx`
Expected: PASS (1 test).

- [ ] **Step 6: Commit**

```bash
git add apps/admin-ui/src/api/manifest_schema.ts \
  apps/admin-ui/src/components/manifest-editor/schema.ts \
  apps/admin-ui/src/components/manifest-editor/__tests__/schema.test.tsx
git commit -m "feat(admin-ui): agent-schema SDK + cache for manifest editor (Stream S PR C)"
```

---

### Task 3: YAML parse/dump helpers

**Files:**
- Create: `apps/admin-ui/src/components/manifest-editor/yaml.ts`
- Test: `apps/admin-ui/src/components/manifest-editor/__tests__/yaml.test.tsx`

- [ ] **Step 1: Write the failing test**

`__tests__/yaml.test.tsx`:
```typescript
import { describe, expect, it } from "vitest";
import { dumpYaml, parseYaml } from "../yaml";

describe("yaml helpers", () => {
  it("round-trips an object without losing fields", () => {
    const obj = { a: 1, b: { c: ["x", "y"], d: true }, e: "hello" };
    const restored = parseYaml(dumpYaml(obj));
    expect(restored).toEqual(obj);
  });

  it("parseYaml throws on malformed YAML", () => {
    expect(() => parseYaml("a:\n  - b\n - c")).toThrow();
  });

  it("dumpYaml emits block style, not inline JSON", () => {
    const text = dumpYaml({ model: { provider: "deepseek" } });
    expect(text).toContain("model:");
    expect(text).toContain("provider: deepseek");
  });
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `npx vitest run src/components/manifest-editor/__tests__/yaml.test.tsx`
Expected: FAIL — `../yaml` not found.

- [ ] **Step 3: Implement**

`apps/admin-ui/src/components/manifest-editor/yaml.ts`:
```typescript
/**
 * YAML serialise/parse for the manifest editor. Single js-yaml instance so
 * the Form and YAML views can't disagree on formatting. ``dumpYaml`` mirrors
 * the options used elsewhere in the UI (``lineWidth: 120``).
 */
import { dump, load } from "js-yaml";

export function parseYaml(text: string): unknown {
  return load(text);
}

export function dumpYaml(value: unknown): string {
  return dump(value, { lineWidth: 120, noRefs: true });
}
```

- [ ] **Step 4: Run the test**

Run: `npx vitest run src/components/manifest-editor/__tests__/yaml.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/admin-ui/src/components/manifest-editor/yaml.ts \
  apps/admin-ui/src/components/manifest-editor/__tests__/yaml.test.tsx
git commit -m "feat(admin-ui): YAML parse/dump helpers for manifest editor (Stream S PR C)"
```

---

### Task 4: `YamlView` (Monaco wrapper with a queryable testid)

**Files:**
- Create: `apps/admin-ui/src/components/manifest-editor/YamlView.tsx`
- Test: `apps/admin-ui/src/components/manifest-editor/__tests__/YamlView.test.tsx`

- [ ] **Step 1: Write the failing test**

`__tests__/YamlView.test.tsx`:
```typescript
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("@monaco-editor/react", () => {
  const Editor = ({
    value,
    onChange,
  }: {
    value?: string;
    onChange?: (v: string | undefined) => void;
  }) => (
    <textarea
      data-testid="monaco-stub"
      value={value}
      onChange={(e) => onChange?.(e.target.value)}
    />
  );
  return { default: Editor };
});

import { YamlView } from "../YamlView";

describe("YamlView", () => {
  it("shows the value and reports edits", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<YamlView value="model: {}" onChange={onChange} />);

    const root = screen.getByTestId("manifest-yaml-view");
    expect(root).toBeInTheDocument();

    const ta = screen.getByTestId("monaco-stub") as HTMLTextAreaElement;
    expect(ta.value).toBe("model: {}");
    await user.type(ta, "!");
    expect(onChange).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `npx vitest run src/components/manifest-editor/__tests__/YamlView.test.tsx`
Expected: FAIL — `../YamlView` not found.

- [ ] **Step 3: Implement**

`apps/admin-ui/src/components/manifest-editor/YamlView.tsx`:
```typescript
/**
 * Raw-YAML escape hatch for the manifest editor — a thin Monaco wrapper.
 * The testid lives on the wrapper div because ``@monaco-editor/react`` does
 * not forward ``data-testid`` to a queryable node in a real browser.
 */
import Editor from "@monaco-editor/react";

interface YamlViewProps {
  value: string;
  onChange: (value: string) => void;
}

export function YamlView({ value, onChange }: YamlViewProps) {
  return (
    <div data-testid="manifest-yaml-view">
      <Editor
        language="yaml"
        value={value}
        onChange={(v) => onChange(v ?? "")}
        theme="vs-dark"
        height="calc(100vh - 300px)"
        options={{
          minimap: { enabled: false },
          fontFamily: "var(--ew-font-mono)",
          fontSize: 12,
          tabSize: 2,
          scrollBeyondLastLine: false,
          renderWhitespace: "boundary",
          wordWrap: "on",
        }}
      />
    </div>
  );
}
```

- [ ] **Step 4: Run the test**

Run: `npx vitest run src/components/manifest-editor/__tests__/YamlView.test.tsx`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add apps/admin-ui/src/components/manifest-editor/YamlView.tsx \
  apps/admin-ui/src/components/manifest-editor/__tests__/YamlView.test.tsx
git commit -m "feat(admin-ui): YamlView Monaco wrapper for manifest editor (Stream S PR C)"
```

---

### Task 5: `FormView` (RJSF wrapper + baseline uiSchema)

**Files:**
- Create: `apps/admin-ui/src/components/manifest-editor/FormView.tsx`
- Delete: `apps/admin-ui/src/components/manifest-editor/__tests__/rjsf-smoke.test.tsx` (superseded)
- Test: `apps/admin-ui/src/components/manifest-editor/__tests__/FormView.test.tsx`

- [ ] **Step 1: Write the failing test**

`__tests__/FormView.test.tsx`:
```typescript
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FormView } from "../FormView";

const schema = {
  type: "object",
  properties: {
    metadata: {
      type: "object",
      properties: { name: { type: "string", title: "Name" } },
    },
  },
} as const;

describe("FormView", () => {
  it("renders fields from the schema", () => {
    render(<FormView schema={schema} formData={{}} onChange={vi.fn()} />);
    expect(screen.getByText("Name")).toBeInTheDocument();
  });

  it("emits changed formData", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<FormView schema={schema} formData={{}} onChange={onChange} />);
    const input = screen.getByLabelText("Name");
    await user.type(input, "bot");
    expect(onChange).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `npx vitest run src/components/manifest-editor/__tests__/FormView.test.tsx`
Expected: FAIL — `../FormView` not found.

- [ ] **Step 3: Implement**

`apps/admin-ui/src/components/manifest-editor/FormView.tsx`:
```typescript
/**
 * Schema-driven form view — RJSF (antd theme) rendering the AgentSpec JSON
 * Schema. The whole manifest is editable here without custom code; ``uiSchema``
 * only nudges layout (collapse rarely-touched blocks, multiline the prompt).
 * The model picker stays on RJSF's default widgets in PR C — PR D swaps in a
 * provider/model linked widget.
 */
import Form from "@rjsf/antd";
import validator from "@rjsf/validator-ajv8";
import type { IChangeEvent } from "@rjsf/core";
import type { UiSchema } from "@rjsf/utils";

import type { JsonSchema } from "../../api/manifest_schema";

interface FormViewProps {
  schema: JsonSchema;
  formData: unknown;
  onChange: (data: unknown) => void;
}

/** Baseline layout polish. Keep minimal — PR D extends it for the model. */
const UI_SCHEMA: UiSchema = {
  "ui:submitButtonOptions": { norender: true },
  spec: {
    system_prompt: {
      template: { "ui:widget": "textarea", "ui:options": { rows: 6 } },
    },
  },
};

export function FormView({ schema, formData, onChange }: FormViewProps) {
  return (
    <div data-testid="manifest-form-view">
      <Form
        schema={schema}
        uiSchema={UI_SCHEMA}
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

- [ ] **Step 4: Delete the temporary smoke test and run FormView's test**

```bash
git rm apps/admin-ui/src/components/manifest-editor/__tests__/rjsf-smoke.test.tsx
```
Run: `npx vitest run src/components/manifest-editor/__tests__/FormView.test.tsx`
Expected: PASS (2 tests). If `getByLabelText("Name")` doesn't match (RJSF antd label wiring), fall back to `screen.getByRole("textbox")` — note the working query in a comment.

- [ ] **Step 5: Commit**

```bash
git add apps/admin-ui/src/components/manifest-editor/FormView.tsx \
  apps/admin-ui/src/components/manifest-editor/__tests__/FormView.test.tsx \
  apps/admin-ui/src/components/manifest-editor/__tests__/rjsf-smoke.test.tsx
git commit -m "feat(admin-ui): FormView RJSF wrapper for manifest editor (Stream S PR C)"
```

---

### Task 6: i18n namespace for the editor

**Files:**
- Modify: `apps/admin-ui/src/i18n/locales/en.ts`
- Modify: `apps/admin-ui/src/i18n/locales/zh-CN.ts`

- [ ] **Step 1: Add the `manifest_editor` keys to the `TranslationKeys` interface and `en` object**

In `en.ts`, add to the `TranslationKeys` interface (place after `manifest_tab`):
```typescript
  manifest_editor: {
    tab_form: string;
    tab_yaml: string;
    loading_schema: string;
    schema_load_failed: string;
    invalid_yaml_title: string;
    invalid_yaml_hint: string;
  };
```
And to the `en` object (matching location):
```typescript
  manifest_editor: {
    tab_form: "Form",
    tab_yaml: "YAML",
    loading_schema: "Loading schema…",
    schema_load_failed: "Failed to load the manifest schema",
    invalid_yaml_title: "Can't switch to Form",
    invalid_yaml_hint:
      "The YAML is invalid or doesn't match the manifest schema. Fix it here first.",
  };
```

- [ ] **Step 2: Add the matching `zh-CN` strings**

In `zh-CN.ts`, add to the `zhCN` object:
```typescript
  manifest_editor: {
    tab_form: "表单",
    tab_yaml: "YAML",
    loading_schema: "正在加载配置清单结构…",
    schema_load_failed: "加载配置清单结构失败",
    invalid_yaml_title: "无法切换到表单",
    invalid_yaml_hint: "当前 YAML 不合法或不符合配置清单结构，请先在此修正。",
  };
```

- [ ] **Step 3: Typecheck (the interface enforces both locales stay in sync)**

Run: `npm run typecheck`
Expected: PASS. If `zh-CN.ts` is missing the namespace, `tsc` fails with a missing-property error — add it.

- [ ] **Step 4: Commit**

```bash
git add apps/admin-ui/src/i18n/locales/en.ts apps/admin-ui/src/i18n/locales/zh-CN.ts
git commit -m "i18n(admin-ui): manifest_editor namespace (Stream S PR C)"
```

---

### Task 7: `ManifestEditor` — tabs, dual state, switch-time sync + validation

**Files:**
- Create: `apps/admin-ui/src/components/manifest-editor/ManifestEditor.tsx`
- Create: `apps/admin-ui/src/components/manifest-editor/index.ts`
- Test: `apps/admin-ui/src/components/manifest-editor/__tests__/ManifestEditor.test.tsx`

**Component contract:**
```typescript
interface ManifestEditorProps {
  mode: "create" | "edit";          // reserved for PR D/E behaviour; PR C ignores it beyond a testid
  initialYaml: string;              // seed; parsed once on mount
  onChange: (yaml: string) => void; // fires with the latest manifest as YAML (form edits + raw edits)
}
```
**State model (the heart of S-2):**
- `manifestObject: unknown` — Form's source of truth, seeded `parseYaml(initialYaml)` (fallback `{}` if seed is malformed).
- `yamlText: string` — YAML view buffer, seeded `initialYaml`.
- `tab: "form" | "yaml"`.
- `switchError: string | null` — set when a YAML→Form switch is blocked.
- `schema / schemaError / loading` — from `loadAgentSchema()`.

**Transitions:**
- Form `onChange(data)` → `setManifestObject(data)`; `const y = dumpYaml(data)`; `setYamlText(y)`; `onChange(y)`.
- YAML `onChange(text)` → `setYamlText(text)`; `onChange(text)`. (`manifestObject` untouched until switch.)
- Switch **Form→YAML**: `const y = dumpYaml(manifestObject)`; `setYamlText(y)`; `onChange(y)`; `setTab("yaml")`; clear `switchError`.
- Switch **YAML→Form**: `try { parsed = parseYaml(yamlText) }` then `validator.validateFormData(parsed, schema).errors`. If parse throws OR errors non-empty → `setSwitchError(...)`, **stay** on YAML. Else `setManifestObject(parsed)`; `setTab("form")`; clear error.

- [ ] **Step 1: Write the failing test**

`__tests__/ManifestEditor.test.tsx`:
```typescript
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../../i18n";

vi.mock("@monaco-editor/react", () => {
  const Editor = ({
    value,
    onChange,
  }: {
    value?: string;
    onChange?: (v: string | undefined) => void;
  }) => (
    <textarea
      data-testid="monaco-stub"
      value={value}
      onChange={(e) => onChange?.(e.target.value)}
    />
  );
  return { default: Editor };
});

import * as schemaSdk from "../../../api/manifest_schema";
import { __resetSchemaCacheForTest } from "../schema";
import { ManifestEditor } from "../ManifestEditor";

const SCHEMA = {
  type: "object",
  required: ["metadata"],
  properties: {
    metadata: {
      type: "object",
      required: ["name"],
      properties: { name: { type: "string", title: "Name" } },
    },
  },
};

const SEED = 'metadata:\n  name: bot\n';

beforeEach(() => {
  __resetSchemaCacheForTest();
  vi.spyOn(schemaSdk, "fetchAgentSchema").mockResolvedValue(SCHEMA);
});
afterEach(() => vi.restoreAllMocks());

describe("ManifestEditor", () => {
  it("loads the schema and shows the Form tab by default", async () => {
    render(<ManifestEditor mode="create" initialYaml={SEED} onChange={vi.fn()} />);
    await waitFor(() => expect(screen.getByTestId("manifest-form-view")).toBeInTheDocument());
  });

  it("switching to YAML shows the dumped manifest", async () => {
    const user = userEvent.setup();
    render(<ManifestEditor mode="create" initialYaml={SEED} onChange={vi.fn()} />);
    await screen.findByTestId("manifest-form-view");
    await user.click(screen.getByTestId("manifest-tab-yaml"));
    const ta = screen.getByTestId("monaco-stub") as HTMLTextAreaElement;
    expect(ta.value).toContain("name: bot");
  });

  it("blocks the YAML→Form switch when YAML is invalid against the schema", async () => {
    const user = userEvent.setup();
    render(<ManifestEditor mode="create" initialYaml={SEED} onChange={vi.fn()} />);
    await screen.findByTestId("manifest-form-view");
    await user.click(screen.getByTestId("manifest-tab-yaml"));

    const ta = screen.getByTestId("monaco-stub") as HTMLTextAreaElement;
    await user.clear(ta);
    await user.type(ta, "metadata:\n  notname: x");

    await user.click(screen.getByTestId("manifest-tab-form"));
    expect(screen.getByTestId("manifest-switch-error")).toBeInTheDocument();
    expect(screen.queryByTestId("manifest-form-view")).not.toBeInTheDocument();
  });

  it("emits the latest YAML through onChange on raw edits", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<ManifestEditor mode="create" initialYaml={SEED} onChange={onChange} />);
    await screen.findByTestId("manifest-form-view");
    await user.click(screen.getByTestId("manifest-tab-yaml"));
    const ta = screen.getByTestId("monaco-stub") as HTMLTextAreaElement;
    await user.clear(ta);
    await user.type(ta, "metadata:\n  name: edited");
    expect(onChange).toHaveBeenLastCalledWith(expect.stringContaining("edited"));
  });
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `npx vitest run src/components/manifest-editor/__tests__/ManifestEditor.test.tsx`
Expected: FAIL — `../ManifestEditor` not found.

- [ ] **Step 3: Implement `ManifestEditor`**

`apps/admin-ui/src/components/manifest-editor/ManifestEditor.tsx`:
```typescript
/**
 * Visual manifest editor — Stream S PR C (Mini-ADRs S-1/S-2/S-6).
 *
 * VS-Code-Settings style: a schema-driven Form tab and a raw YAML escape
 * hatch over a single in-memory ``manifestObject``. Switching tabs serialises
 * (Form→YAML) or parses+validates (YAML→Form); an invalid YAML→Form switch is
 * blocked with an inline error. ``onChange`` always carries the latest manifest
 * as a YAML string so the parent submits exactly what's shown.
 */
import { useEffect, useMemo, useState } from "react";
import { Alert, Segmented, Spin } from "antd";
import validator from "@rjsf/validator-ajv8";
import { useTranslation } from "react-i18next";

import type { JsonSchema } from "../../api/manifest_schema";
import { loadAgentSchema } from "./schema";
import { dumpYaml, parseYaml } from "./yaml";
import { FormView } from "./FormView";
import { YamlView } from "./YamlView";

type Tab = "form" | "yaml";

interface ManifestEditorProps {
  mode: "create" | "edit";
  initialYaml: string;
  onChange: (yaml: string) => void;
}

function safeSeed(initialYaml: string): unknown {
  try {
    return parseYaml(initialYaml) ?? {};
  } catch {
    return {};
  }
}

export function ManifestEditor({ mode, initialYaml, onChange }: ManifestEditorProps) {
  const { t } = useTranslation();
  const seed = useMemo(() => safeSeed(initialYaml), [initialYaml]);

  const [schema, setSchema] = useState<JsonSchema | null>(null);
  const [schemaError, setSchemaError] = useState(false);
  const [tab, setTab] = useState<Tab>("form");
  const [manifestObject, setManifestObject] = useState<unknown>(seed);
  const [yamlText, setYamlText] = useState<string>(initialYaml);
  const [switchError, setSwitchError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    loadAgentSchema().then(
      (s) => alive && setSchema(s),
      () => alive && setSchemaError(true),
    );
    return () => {
      alive = false;
    };
  }, []);

  function handleFormChange(data: unknown): void {
    setManifestObject(data);
    const y = dumpYaml(data);
    setYamlText(y);
    onChange(y);
  }

  function handleYamlChange(text: string): void {
    setYamlText(text);
    onChange(text);
  }

  function switchTo(next: Tab): void {
    if (next === tab) return;
    if (next === "yaml") {
      const y = dumpYaml(manifestObject);
      setYamlText(y);
      onChange(y);
      setSwitchError(null);
      setTab("yaml");
      return;
    }
    // yaml -> form: parse + validate; block on failure.
    let parsed: unknown;
    try {
      parsed = parseYaml(yamlText);
    } catch {
      setSwitchError(t("manifest_editor.invalid_yaml_hint"));
      return;
    }
    if (schema && validator.validateFormData(parsed, schema).errors.length > 0) {
      setSwitchError(t("manifest_editor.invalid_yaml_hint"));
      return;
    }
    setManifestObject(parsed);
    setSwitchError(null);
    setTab("form");
  }

  if (schemaError) {
    return (
      <Alert
        type="error"
        showIcon
        message={t("manifest_editor.schema_load_failed")}
        data-testid="manifest-schema-error"
      />
    );
  }
  if (schema === null) {
    return (
      <div data-testid="manifest-schema-loading" style={{ padding: 24, textAlign: "center" }}>
        <Spin /> <span style={{ marginLeft: 8 }}>{t("manifest_editor.loading_schema")}</span>
      </div>
    );
  }

  return (
    <div data-testid={`manifest-editor-${mode}`}>
      <Segmented
        value={tab}
        onChange={(v) => switchTo(v as Tab)}
        options={[
          { label: t("manifest_editor.tab_form"), value: "form", "data-testid": "manifest-tab-form" } as never,
          { label: t("manifest_editor.tab_yaml"), value: "yaml", "data-testid": "manifest-tab-yaml" } as never,
        ]}
        style={{ marginBottom: 12 }}
      />

      {switchError !== null && (
        <Alert
          type="warning"
          showIcon
          message={t("manifest_editor.invalid_yaml_title")}
          description={switchError}
          style={{ marginBottom: 12 }}
          data-testid="manifest-switch-error"
        />
      )}

      {tab === "form" ? (
        <FormView schema={schema} formData={manifestObject} onChange={handleFormChange} />
      ) : (
        <YamlView value={yamlText} onChange={handleYamlChange} />
      )}
    </div>
  );
}
```

> **Note on Segmented testids:** Antd's `Segmented` may not forward `data-testid` onto each option's DOM node. If the test can't find `manifest-tab-yaml`/`manifest-tab-form`, replace `Segmented` with two Antd `Button`s (a small segmented-style group) that DO take `data-testid`, keeping the same `switchTo` calls. Make the tabs queryable by testid — adjust the component, not the test.

- [ ] **Step 4: Add the barrel export**

`apps/admin-ui/src/components/manifest-editor/index.ts`:
```typescript
export { ManifestEditor } from "./ManifestEditor";
```

- [ ] **Step 5: Run the test**

Run: `npx vitest run src/components/manifest-editor/__tests__/ManifestEditor.test.tsx`
Expected: PASS (4 tests). If a tab click can't find its testid, apply the Segmented→Buttons note above and re-run.

- [ ] **Step 6: Commit**

```bash
git add apps/admin-ui/src/components/manifest-editor/ManifestEditor.tsx \
  apps/admin-ui/src/components/manifest-editor/index.ts \
  apps/admin-ui/src/components/manifest-editor/__tests__/ManifestEditor.test.tsx
git commit -m "feat(admin-ui): ManifestEditor tabs + switch-time sync/validation (Stream S PR C)"
```

---

### Task 8: Wire `ManifestEditor` into `CreateAgentDrawer`

**Files:**
- Modify: `apps/admin-ui/src/components/CreateAgentDrawer.tsx`
- Modify: `apps/admin-ui/src/components/__tests__/CreateAgentDrawer.test.tsx`

- [ ] **Step 1: Update the drawer's existing test for the new internal editor**

The drawer keeps `buffer`/submit/error. The visible editor is now `ManifestEditor` (Form tab by default). Replace the Monaco-stub assertions. New `CreateAgentDrawer.test.tsx` body (keep imports; add the schema mock):
```typescript
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../i18n";

vi.mock("@monaco-editor/react", () => {
  const Editor = ({
    value,
    onChange,
  }: {
    value?: string;
    onChange?: (v: string | undefined) => void;
  }) => (
    <textarea
      data-testid="monaco-stub"
      value={value}
      onChange={(e) => onChange?.(e.target.value)}
    />
  );
  return { default: Editor };
});

import * as agentsSdk from "../../api/agents";
import * as schemaSdk from "../../api/manifest_schema";
import { __resetSchemaCacheForTest } from "../manifest-editor/schema";
import { ApiError } from "../../api/client";
import { CreateAgentDrawer, DEFAULT_AGENT_YAML } from "../CreateAgentDrawer";

const sampleCreated = {
  record: { name: "my-agent", version: "1.0.0" },
} as unknown as agentsSdk.AgentDetailResponse;

const onClose = vi.fn();
const onCreated = vi.fn();

beforeEach(() => {
  __resetSchemaCacheForTest();
  vi.spyOn(schemaSdk, "fetchAgentSchema").mockResolvedValue({
    type: "object",
    properties: { metadata: { type: "object", properties: { name: { type: "string" } } } },
  });
  onClose.mockClear();
  onCreated.mockClear();
});
afterEach(() => vi.restoreAllMocks());

describe("CreateAgentDrawer", () => {
  it("renders the manifest editor on the Form tab", async () => {
    render(<CreateAgentDrawer open onClose={onClose} onCreated={onCreated} />);
    await waitFor(() => expect(screen.getByTestId("manifest-editor-create")).toBeInTheDocument());
    expect(screen.getByTestId("manifest-form-view")).toBeInTheDocument();
  });

  it("submits the default manifest YAML via createAgent", async () => {
    const user = userEvent.setup();
    const createMock = vi.spyOn(agentsSdk, "createAgent").mockResolvedValue(sampleCreated);
    render(<CreateAgentDrawer open onClose={onClose} onCreated={onCreated} />);
    await screen.findByTestId("manifest-editor-create");

    await user.click(screen.getByTestId("create-agent-submit"));

    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));
    const payload = createMock.mock.calls[0][0] as { manifest_yaml: string };
    expect(payload.manifest_yaml).toContain("kind: Agent");
    expect(onCreated).toHaveBeenCalledWith(sampleCreated);
  });

  it("surfaces server errors and keeps the drawer open", async () => {
    const user = userEvent.setup();
    vi.spyOn(agentsSdk, "createAgent").mockRejectedValue(
      new ApiError("name + version already exists", "MANIFEST_DUPLICATE", 409),
    );
    render(<CreateAgentDrawer open onClose={onClose} onCreated={onCreated} />);
    await screen.findByTestId("manifest-editor-create");
    await user.click(screen.getByTestId("create-agent-submit"));
    const alert = await screen.findByTestId("create-agent-error");
    expect(alert).toHaveTextContent("MANIFEST_DUPLICATE");
  });
});

void DEFAULT_AGENT_YAML;
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `npx vitest run src/components/__tests__/CreateAgentDrawer.test.tsx`
Expected: FAIL — drawer still renders `<Editor>`, no `manifest-editor-create` testid.

- [ ] **Step 3: Swap the editor in `CreateAgentDrawer.tsx`**

Remove the Monaco import and the `<Editor … data-testid="create-agent-editor" />` block. Add the import:
```typescript
import { ManifestEditor } from "./manifest-editor";
```
Replace the `<Editor … />` JSX (lines ~146-162) with:
```tsx
      <ManifestEditor mode="create" initialYaml={DEFAULT_AGENT_YAML} onChange={setBuffer} />
```
Keep everything else (`buffer` state seeded `DEFAULT_AGENT_YAML`, `handleSubmit` posting `{ manifest_yaml: buffer }`, error Alert, footer). `destroyOnHidden` remounts `ManifestEditor` fresh on each open, so `reset()` + remount reseed the editor.

- [ ] **Step 4: Run the drawer test + typecheck**

Run: `npx vitest run src/components/__tests__/CreateAgentDrawer.test.tsx && npm run typecheck`
Expected: PASS (3 tests) and clean typecheck. Remove the now-unused `Editor` import if `tsc` flags it.

- [ ] **Step 5: Commit**

```bash
git add apps/admin-ui/src/components/CreateAgentDrawer.tsx \
  apps/admin-ui/src/components/__tests__/CreateAgentDrawer.test.tsx
git commit -m "feat(admin-ui): use ManifestEditor in CreateAgentDrawer (Stream S PR C)"
```

---

### Task 9: Playwright e2e — create-via-form flow + axe

**Files:**
- Create: `apps/admin-ui/e2e/manifest-editor.spec.ts`
- Reference (read, don't edit): `apps/admin-ui/e2e/fixtures.ts`, `apps/admin-ui/e2e/smoke.spec.ts`

- [ ] **Step 1: Read the fixtures to learn the stubbing helpers**

Open `apps/admin-ui/e2e/fixtures.ts` and note: `SAMPLE_JWT`, the `mockControlPlane`/route-stub helper, and `expectNoA11yViolations(page, label)`. The new spec must stub `GET /v1/agents/schema` (enveloped) and `POST /v1/agents`. Follow the existing route-stub style exactly — if fixtures expose a `mockControlPlane({...})` map, add the two routes there; otherwise use `page.route(...)` in the test.

- [ ] **Step 2: Write the e2e spec**

`apps/admin-ui/e2e/manifest-editor.spec.ts`:
```typescript
import { test, expect, expectNoA11yViolations, SAMPLE_JWT } from "./fixtures";

const SCHEMA_ENVELOPE = {
  success: true,
  error: null,
  data: {
    type: "object",
    properties: {
      metadata: {
        type: "object",
        properties: { name: { type: "string", title: "Name" } },
      },
    },
  },
};

async function login(page) {
  await page.goto("/login");
  await page.getByTestId("login-token").fill(SAMPLE_JWT);
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/agents$/);
}

test("create drawer opens the manifest editor on the Form tab", async ({ page }) => {
  await page.route("**/v1/agents/schema", (route) =>
    route.fulfill({ json: SCHEMA_ENVELOPE }),
  );
  await login(page);

  await page.getByTestId("agents-create").click(); // open the drawer (verify this testid in AgentsList)
  await expect(page.getByTestId("manifest-editor-create")).toBeVisible();
  await expect(page.getByTestId("manifest-form-view")).toBeVisible();

  // Switch to YAML and back proves the tab wiring renders in a real browser.
  await page.getByTestId("manifest-tab-yaml").click();
  await expect(page.getByTestId("manifest-yaml-view")).toBeVisible();
});

test("create drawer passes axe (serious + critical)", async ({ page }) => {
  await page.route("**/v1/agents/schema", (route) =>
    route.fulfill({ json: SCHEMA_ENVELOPE }),
  );
  await login(page);
  await page.getByTestId("agents-create").click();
  await expect(page.getByTestId("manifest-editor-create")).toBeVisible();
  await expectNoA11yViolations(page, "create-agent-drawer");
});
```

- [ ] **Step 3: Confirm the drawer-open testid**

Grep `AgentsList.tsx` for the button that opens `CreateAgentDrawer` and use its real `data-testid` in the spec (replace `agents-create` if different). Run: `grep -n "create" apps/admin-ui/src/pages/AgentsList.tsx`.

- [ ] **Step 4: Run the e2e suite**

Run: `npm run e2e -- manifest-editor`
Expected: both tests PASS. If the dev server needs the backend stubbed for `/v1/agents` list too, mirror what `smoke.spec.ts` does. If Monaco is heavy/slow in CI, the first test only needs the YAML *view* visible (not typed) — keep it light.

- [ ] **Step 5: Commit**

```bash
git add apps/admin-ui/e2e/manifest-editor.spec.ts
git commit -m "test(admin-ui): e2e create-via-form + axe for ManifestEditor (Stream S PR C)"
```

---

## Final verification (run before opening the PR)

From `apps/admin-ui/`:
- [ ] `npm run test` — all vitest suites pass (new + existing CreateAgentDrawer).
- [ ] `npm run typecheck` — clean.
- [ ] `npm run build` — succeeds.
- [ ] `npm run build-storybook` — succeeds (no new stories required, but the build must not break).
- [ ] `npm run e2e` — passes (or at least the manifest-editor + smoke specs).
- [ ] From repo root: `pre-commit run --files <changed files>` — clean (prettier/eslint).

Then open the PR: `feat(stream-s): PR C — visual ManifestEditor (form ⇄ YAML) in Create drawer`. Body: link STREAM-S-DESIGN.md, state PR C scope (Mini-ADRs S-1/S-2/S-6/S-7 partial), and note that ModelSelect (S-3/S-4 UI) + capability-adaptive defaults (S-5) land in PR D and the agent-detail tab integration in PR E.

---

## Self-Review

**Spec coverage (STREAM-S-DESIGN.md §5 PR C = "RJSF + 表单/YAML 双标签 + 切换同步 + ajv 校验; 替换 Create 抽屉; vitest + Playwright"):**
- RJSF form → Task 1 (deps) + Task 5 (FormView). ✅
- 表单/YAML 双标签 + 切换同步 → Task 7 (ManifestEditor `switchTo`). ✅
- ajv 校验 on switch → Task 7 (`validator.validateFormData`). ✅ (S-6 layer ② "YAML→form parse+ajv".)
- Single source of truth object (S-2) → Task 7 state model. ✅
- 替换 Create 抽屉 (S-7 create half) → Task 8. ✅
- vitest → Tasks 2-8; Playwright + axe → Task 9. ✅
- i18n (S-8) → Task 6. ✅
- Schema endpoint SDK (S-1 client side) → Task 2. ✅
- **Deferred, correctly out of scope:** ModelSelect/model-catalog UI (S-3/S-4) = PR D; defaults capability-adaptation (S-5) = PR D; agent-detail tab (S-7 edit half) = PR E. Layer ① (RJSF liveValidate) is intentionally `liveValidate={false}` here to match S-2 (validate on switch, not per-keystroke); layer ③ (backend ManifestLoader) already exists and runs on submit.

**Placeholder scan:** no TBD/TODO/"handle errors appropriately" — every code step has full code. Two explicit fallbacks (Segmented→Buttons testid; `getByLabelText`→`getByRole`) are conditional adjustments with concrete instructions, not placeholders.

**Type consistency:** `JsonSchema` (from `manifest_schema.ts`) used uniformly in `schema.ts`, `FormView`, `ManifestEditor`. `ManifestEditorProps` (`mode`/`initialYaml`/`onChange`) matches the Task 8 call site `<ManifestEditor mode="create" initialYaml={DEFAULT_AGENT_YAML} onChange={setBuffer} />`. `fetchAgentSchema`/`loadAgentSchema`/`__resetSchemaCacheForTest` names consistent across Tasks 2, 7, 8. `dumpYaml`/`parseYaml` consistent across Tasks 3, 7. `onChange(yaml: string)` contract consistent between ManifestEditor and the drawer's `setBuffer`.
