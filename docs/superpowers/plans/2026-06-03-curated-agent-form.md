# Curated Agent Form (replace RJSF schema-dump) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** Replace the RJSF auto-generated `FormView` (which dumps the raw AgentSpec JSON schema — class names, Pydantic docstrings, every internal field, English-only, broken enums) with a curated, hand-built, i18n'd (zh-CN/en) create/edit form showing ONLY user-relevant fields with proper inputs/dropdowns. The YAML tab stays as the full-control escape hatch.

**Scope of the form (approved):** 基本 (name*, description) · 模型 (provider→model dropdowns + vision tag + temperature slider + advanced max_tokens/rate_limit_rpm) · 系统提示词 (system_prompt.template textarea) · 长期记忆 (memory.long_term toggle, default on, + advanced top_k) · 工具 (web_search/http/mcp checkboxes + MCP allow_tools). NOT shown (kept via defaults + YAML): apiVersion, kind, metadata.tenant, spec.tenant_config, extends, model.api_key_ref/base_url/azure_*, knowledge, subagents, reasoning, sandbox.

**Key behavior:** the curated form MERGES — it reads/writes only its known paths and preserves every other field in the manifest object (unlike RJSF which dropped unknown fields). `onChange` always emits the FULL manifest object. Form↔YAML round-trip unchanged (ManifestEditor still validates YAML→Form against the schema).

**Tech Stack:** React/Antd/Vitest/Playwright. Remove `@rjsf/*` usage from FormView (deps can stay installed; no longer imported by FormView).

---

## Task 1: `form_model.ts` — typed manifest accessors (pure, immutable)

**Files:** Create `apps/admin-ui/src/components/manifest-editor/form_model.ts`, `apps/admin-ui/src/components/manifest-editor/__tests__/form_model.test.ts`

- [ ] **Step 1: Write the module**

Pure helpers over an opaque manifest object (`unknown` → typed reads + immutable writes that deep-merge only the touched path, preserving all other keys). Use small immutable spread helpers.

```ts
export interface AgentManifest {
  apiVersion?: string;
  kind?: string;
  metadata?: { name?: string; version?: string; tenant?: string; [k: string]: unknown };
  spec?: {
    description?: string;
    model?: ModelFields;
    system_prompt?: { template?: string; [k: string]: unknown };
    memory?: { long_term?: LongTermFields | null; [k: string]: unknown } | null;
    tools?: ToolEntry[];
    [k: string]: unknown;
  };
  [k: string]: unknown;
}
export interface ModelFields { provider?: string; name?: string; supports_vision?: boolean; temperature?: number; max_tokens?: number; rate_limit_rpm?: number; [k: string]: unknown; }
export interface LongTermFields { retrieve_top_k?: number; write_back?: boolean; recall_mode?: string; }
export type ToolEntry = { type: string; name?: string; allow_tools?: string[]; config?: Record<string, unknown>; [k: string]: unknown };

const asObj = (v: unknown): AgentManifest => (v && typeof v === "object" && !Array.isArray(v) ? (v as AgentManifest) : {});

// readers
export const readName = (m: unknown): string => asObj(m).metadata?.name ?? "";
export const readDescription = (m: unknown): string => asObj(m).spec?.description ?? "";
export const readModel = (m: unknown): ModelFields => asObj(m).spec?.model ?? {};
export const readSystemPrompt = (m: unknown): string => asObj(m).spec?.system_prompt?.template ?? "";
export const readMemoryOn = (m: unknown): boolean => (asObj(m).spec?.memory?.long_term ?? null) !== null;
export const readTopK = (m: unknown): number | undefined => asObj(m).spec?.memory?.long_term?.retrieve_top_k;
export interface ToolFlags { webSearch: boolean; http: boolean; mcp: boolean; mcpAllowTools: string[]; }
export function readTools(m: unknown): ToolFlags {
  const tools = asObj(m).spec?.tools ?? [];
  const mcp = tools.find((t) => t.type === "mcp");
  return {
    webSearch: tools.some((t) => t.type === "builtin" && t.name === "web_search"),
    http: tools.some((t) => t.type === "http"),
    mcp: mcp !== undefined,
    mcpAllowTools: mcp?.allow_tools ?? [],
  };
}

// writers (immutable; merge metadata/spec, preserve siblings)
function patchSpec(m: unknown, spec: Record<string, unknown>): AgentManifest {
  const base = asObj(m);
  return { ...base, spec: { ...(base.spec ?? {}), ...spec } };
}
export function setName(m: unknown, name: string): AgentManifest {
  const base = asObj(m);
  return { ...base, metadata: { ...(base.metadata ?? {}), name } };
}
export const setDescription = (m: unknown, description: string): AgentManifest => patchSpec(m, { description });
export function setModel(m: unknown, model: ModelFields): AgentManifest {
  return patchSpec(m, { model: { ...readModel(m), ...model } });
}
// provider change resets name+vision; name change should set vision via caller (catalog lookup) then setModel.
export function setSystemPrompt(m: unknown, template: string): AgentManifest {
  const sp = asObj(m).spec?.system_prompt ?? {};
  return patchSpec(m, { system_prompt: { ...sp, template } });
}
export function setMemoryOn(m: unknown, on: boolean): AgentManifest {
  if (!on) return patchSpec(m, { memory: { ...(asObj(m).spec?.memory ?? {}), long_term: null } });
  const existing = asObj(m).spec?.memory?.long_term ?? null;
  const lt: LongTermFields = existing ?? { retrieve_top_k: 5, write_back: true, recall_mode: "per_session" };
  return patchSpec(m, { memory: { ...(asObj(m).spec?.memory ?? {}), long_term: lt } });
}
export function setTopK(m: unknown, k: number): AgentManifest {
  const lt = asObj(m).spec?.memory?.long_term ?? { write_back: true, recall_mode: "per_session" };
  return patchSpec(m, { memory: { ...(asObj(m).spec?.memory ?? {}), long_term: { ...lt, retrieve_top_k: k } } });
}
export function setTool(m: unknown, kind: "webSearch" | "http" | "mcp", on: boolean): AgentManifest {
  const tools = [...(asObj(m).spec?.tools ?? [])];
  const without = (pred: (t: ToolEntry) => boolean) => tools.filter((t) => !pred(t));
  if (kind === "webSearch") {
    const next = on ? [...without((t) => t.type === "builtin" && t.name === "web_search"), { type: "builtin", name: "web_search", config: {} }] : without((t) => t.type === "builtin" && t.name === "web_search");
    return patchSpec(m, { tools: next });
  }
  if (kind === "http") {
    const next = on ? [...without((t) => t.type === "http"), { type: "http" }] : without((t) => t.type === "http");
    return patchSpec(m, { tools: next });
  }
  const next = on ? [...without((t) => t.type === "mcp"), { type: "mcp", allow_tools: [] }] : without((t) => t.type === "mcp");
  return patchSpec(m, { tools: next });
}
export function setMcpAllowTools(m: unknown, allow: string[]): AgentManifest {
  const tools = (asObj(m).spec?.tools ?? []).map((t) => (t.type === "mcp" ? { ...t, allow_tools: allow } : t));
  return patchSpec(m, { tools });
}
```

- [ ] **Step 2: Tests (TDD)**

`form_model.test.ts`: for a seed manifest (use BASE_MANIFEST_YAML parsed, or a literal with apiVersion/kind/metadata/spec.sandbox/spec.model/spec.memory):
- readers return seed values; readMemoryOn true for seed (has long_term).
- `setName` changes metadata.name, PRESERVES apiVersion/kind/spec.sandbox.
- `setModel({provider:"x"})` merges into spec.model, preserves spec.system_prompt/sandbox.
- `setMemoryOn(false)` → long_term null; `setMemoryOn(true)` restores defaults; round-trip preserves spec.sandbox.
- `setTool("webSearch", true)` adds builtin web_search; `(…, false)` removes only it (leaves other tools); `setTool("mcp", true)` then `setMcpAllowTools(["a"])`; readTools reflects flags.
- **Critical preserve test:** after a chain of setters, `spec.sandbox` + `apiVersion` + `kind` from the seed are still intact.
Run: `cd apps/admin-ui && pnpm vitest run src/components/manifest-editor/__tests__/form_model.test.ts`.

- [ ] **Step 3: pre-commit + commit**

`feat(admin-ui): manifest form_model accessors (immutable, merge-preserving)`

---

## Task 2: `ModelSelect` plain controlled component (refactor from ModelSelectField)

**Files:** Create `apps/admin-ui/src/components/manifest-editor/widgets/ModelSelect.tsx` + `__tests__/ModelSelect.test.tsx`; DELETE `widgets/ModelSelectField.tsx`, `__tests__/ModelSelectField.test.tsx`, `__tests__/FormView.modelselect.test.tsx`; extend i18n.

- [ ] **Step 1: Component**

READ `widgets/ModelSelectField.tsx` (its provider/model linked dropdowns + vision tag + advanced panel logic) and `catalog.ts` (`providerNames`, `modelsFor`, `lookupModel`). Build a PLAIN controlled component (no RJSF):
```tsx
interface ModelSelectProps {
  value: ModelFields;            // from form_model
  catalog?: ModelCatalog;
  onChange: (next: ModelFields) => void;
}
```
- Provider `Select` (`data-testid="model-select-provider"`) from `providerNames(catalog)`; on change → `onChange({ ...value, provider, name: undefined, supports_vision: false })`.
- Model `Select` (`model-select-name`) from `modelsFor(catalog, value.provider)`; on change → look up vision via `lookupModel` → `onChange({ ...value, name, supports_vision: entry?.vision ?? false })`.
- Vision `Tag` (`model-select-vision`) — t("model_select.vision_on"/"vision_off").
- **Temperature** (`model-select-temperature`): an antd `Slider` 0–2 step 0.1 (with the numeric value shown) OR `InputNumber` 0–2 — label `t("model_select.temperature")`; onChange → `onChange({ ...value, temperature })`.
- Advanced `Collapse` (`model-select-advanced`): `max_tokens` + `rate_limit_rpm` `InputNumber`s only. (DROP api_key_ref / base_url / azure_* — power-user, YAML only.)
- Keep `data-testid="model-select-field"` wrapper for continuity.
Reuse the same labels; ADD `model_select.temperature` to en.ts (interface + value) + zh-CN.ts (value: "温度").

- [ ] **Step 2: Test**

`ModelSelect.test.tsx`: with a catalog stub — selecting a provider resets model+vision; selecting a model sets vision from catalog; temperature change propagates; advanced shows max_tokens/rate_limit. (Mirror the old ModelSelectField test's antd-Select interaction: open via the testid wrapper then click the visible `.ant-select-item-option-content`.)

- [ ] **Step 3: Delete old + verify**

Delete `ModelSelectField.tsx`, `ModelSelectField.test.tsx`, `FormView.modelselect.test.tsx` (its RJSF-field coverage is replaced by ModelSelect.test + FormView test). grep `ModelSelectField` across src → zero. `pnpm run typecheck`.

- [ ] **Step 4: pre-commit + commit**

`refactor(admin-ui): ModelSelect plain controlled component (drop RJSF field)`

---

## Task 3: Rewrite `FormView` as the curated form + ManifestEditor wiring + i18n

**Files:** Rewrite `FormView.tsx`; modify `ManifestEditor.tsx`; `__tests__/FormView.test.tsx` (rewrite); `i18n/locales/{en,zh-CN}.ts` (new `agent_form` namespace).

- [ ] **Step 1: i18n `agent_form` (both locales)**

Add an `agent_form` block. Keys (en → zh):
- section_basic "Basics"→"基本", field_name "Name"→"名称", field_name_required "Name is required"→"名称必填", field_name_placeholder "my-agent"→"my-agent", field_description "Description"→"描述",
- section_model "Model"→"模型",
- section_prompt "System prompt"→"系统提示词", field_prompt_placeholder "You are a helpful assistant."→"你是一个有帮助的助手。",
- section_memory "Long-term memory"→"长期记忆", memory_on "On"→"开启", memory_off "Off"→"关闭", memory_hint "Remembers across sessions; needs a platform embedding."→"跨会话记忆,需要平台已配 Embedding。", memory_topk "Memories recalled per run"→"每轮召回条数",
- section_tools "Tools"→"工具", tool_web_search "Web search"→"联网搜索", tool_http "HTTP tool"→"HTTP 工具", tool_mcp "MCP tools"→"MCP 工具", tool_mcp_allow "Allowed MCP tools (optional, comma-separated)"→"允许的 MCP 工具(可选,逗号分隔)",
- advanced "Advanced"→"高级".

- [ ] **Step 2: Rewrite FormView.tsx**

Props become `{ formData: unknown; onChange: (data: unknown) => void }` (DROP `schema` — no longer used). Load catalog (keep the existing `loadModelCatalog` effect). Render sections using `form_model` helpers + `ModelSelect`. Each control's onChange = `onChange(setX(formData, ...))` (emits the full merged manifest). Use antd `Input`, `Input.TextArea`, `Switch`, `Checkbox`, `InputNumber`, `Collapse`, `Typography`. Use the `hx-page-header`-free section styling (simple `<h3>`/labels with spacing; match the app's form aesthetics — look at SettingsTenantConfig or PlatformEmbeddingSection for the in-app form style). Keep `data-testid="manifest-form-view"` wrapper. Section testids: `af-basic`, `af-model`, `af-prompt`, `af-memory`, `af-tools`; field testids: `af-name`, `af-description`, `af-prompt-input`, `af-memory-toggle`, `af-topk`, `af-tool-web_search`, `af-tool-http`, `af-tool-mcp`, `af-mcp-allow`.
- Name: `Input` (required indicator); Description: `Input`.
- Model: `<ModelSelect value={readModel(formData)} catalog={catalog} onChange={(mdl) => onChange(setModel(formData, mdl))} />`.
- Prompt: `Input.TextArea` rows 6, value `readSystemPrompt`.
- Memory: `Switch` (checked `readMemoryOn`) + hint; when on, an Advanced collapse with `InputNumber` top_k.
- Tools: three `Checkbox`es (web_search/http/mcp); when mcp checked, show an `Input` for comma-separated allow_tools (parse to array on change via setMcpAllowTools).

- [ ] **Step 3: ManifestEditor wiring**

In `ManifestEditor.tsx`: the `<FormView .../>` call — drop the `schema={schema}` prop (FormView no longer takes it). Keep `schema` state + its use in `switchTo` (YAML→Form validation stays). Update the `handleFormChange` comment: the curated form now MERGES (preserves non-curated fields), it is no longer "schema-authoritative / drops unknown keys".

- [ ] **Step 4: Rewrite FormView.test.tsx**

Cover: renders sections; editing name calls onChange with merged manifest (metadata.name set, apiVersion/sandbox preserved); memory toggle off → long_term null; tool checkbox adds entry; model select integration (provider→model). Mock `loadModelCatalog`. Mirror existing test harness.

- [ ] **Step 5: Verify**

`cd apps/admin-ui && pnpm run typecheck && pnpm vitest run src/components/manifest-editor && pnpm run build`. Fix ManifestEditor.test.tsx if it asserted RJSF fields.

- [ ] **Step 6: pre-commit + commit**

`feat(admin-ui): curated agent form (basics/model/prompt/memory/tools) replacing RJSF schema dump`

---

## Task 4: e2e + storybook + whole-PR gate + PR

**Files:** e2e specs that drive the form tab; storybook; whole-PR run.

- [ ] **Step 1: Update e2e**

Specs: `manifest-editor.spec.ts`, `manifest-edit.spec.ts`, `manifest-model-select.spec.ts`, `create-agent-embedding-gate.spec.ts`. READ each; any that interacted with the old RJSF fields (e.g. typed into schema-named inputs, the model select via old testids) → update to the curated form's testids (`af-name`, `af-prompt-input`, `model-select-provider`, etc.). The create/edit happy paths should now drive the curated form. Keep axe green.

- [ ] **Step 2: Storybook**

If a FormView/ModelSelectField story exists, update/replace it for the curated FormView + ModelSelect. `pnpm run build-storybook`.

- [ ] **Step 3: Whole-PR gate**

`cd apps/admin-ui && pnpm run typecheck && pnpm vitest run && pnpm run build && pnpm run build-storybook 2>&1 | tail -3 && pnpm exec playwright test 2>&1 | tail -25` ; root `uv run pre-commit run --all-files`. All green. grep `@rjsf` under `src/components/manifest-editor` → should be gone from FormView (ManifestEditor still imports validator for YAML→Form validation — that's fine to keep). grep `ModelSelectField` → zero.
> The local `.env.development.local` (VITE_OIDC_ISSUER set) hides the dev-login token field → playwright login fills time out. If smoke specs fail on `login-token`, it's this pre-existing env issue (proven earlier), not the change — note it; CI env is clean.

- [ ] **Step 4: backlog note + PR**

Add to `docs/ITERATION-PLAN.md` (under Stream S or a small "UI polish" note): `- [x] 重做建/改 agent 表单：RJSF schema-dump → 手工 curated 表单(名称/描述/模型+温度/系统提示词/记忆开关/工具),中文+下拉+开关;YAML 页保留`. Commit plan doc + backlog. Open PR `feat/curated-agent-form`.

## Self-Review (controller)
- **Root cause fixed:** no more RJSF schema dump — curated fields only, i18n, dropdowns/toggles. ✅
- **Merge-preserving:** form_model writers keep apiVersion/kind/tenant/sandbox/tenant_config intact; YAML tab = full control. ✅
- **Memory headline visible** (toggle, default on). **Tools = web_search/http/mcp** (matches the schema union; MCP allow_tools). **Temperature** surfaced. ✅
- **Old RJSF artifacts removed** (ModelSelectField + RJSF-field tests). ✅
- **e2e/tests updated** to curated testids. ✅
