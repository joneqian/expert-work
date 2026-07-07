# Stream S PR E — Edit Integration (agent-detail manifest tab) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the visual `<ManifestEditor>` to the agent-detail "配置清单" tab — keeping the read-only-by-default browse, but swapping the writable raw-Monaco *edit* surface for the form/YAML editor — and prove the create + edit flows end-to-end, finishing Stream S.

**Architecture:** `ManifestTab` keeps its `view`/`edit` state machine. **View** mode stays a read-only Monaco render of the server snapshot (browse-safe, no fat-finger). **Edit** mode renders `<ManifestEditor mode="edit" initialYaml={snapshotYaml} onChange={setBuffer}>`; Save posts `buffer` to `PUT /v1/agents/{name}/{version}` (backend `ManifestLoader` stays authoritative); Cancel discards and returns to view. The record's `spec` field is the full manifest (apiVersion/kind/metadata/spec), so it feeds the editor directly.

**Tech Stack:** React 19, Antd 5, the existing `<ManifestEditor>` (RJSF form + YAML tabs), `js-yaml`, Vitest + Playwright/axe. pnpm; run from `apps/admin-ui/`.

**Scope:** edit-surface swap + tests + edit e2e. No new i18n (reuse `manifest_tab.*`). No backend changes. The read-only-default UX is preserved (user-locked decision).

**Builds on (merged):** PR C `<ManifestEditor>` (`src/components/manifest-editor`, exported via barrel; testids `manifest-editor-edit` root, `manifest-form-view`, `manifest-tab-form`/`manifest-tab-yaml`, `manifest-yaml-view`). PR D model picker (FormView loads the catalog, so edit mode also fetches `/v1/model-catalog`). `updateAgent(name, version, { manifest_yaml })` exists in `src/api/agents.ts`. `record.spec: Record<string, unknown>` is the full manifest.

---

## File Structure

**Modified**
- `apps/admin-ui/src/pages/agent_detail/ManifestTab.tsx` — edit mode → `<ManifestEditor>`; seed `buffer` from the snapshot; keep view-mode read-only Monaco + the Edit/Save/Cancel buttons.
- `apps/admin-ui/src/pages/__tests__/ManifestTab.test.tsx` — update edit-mode assertions; add schema + catalog mocks.

**New**
- `apps/admin-ui/e2e/manifest-edit.spec.ts` — navigate to an agent's manifest tab, Edit → form, save → `PUT`; + axe.

---

## Conventions
- ManifestEditor seeds from `initialYaml` only at mount; entering edit mode mounts it fresh (mode toggle), so no `key`-remount needed here. Seed `buffer` = `snapshotYaml` so a Save with no edits posts the unchanged manifest.
- ManifestEditor (edit mode) loads the schema AND the model catalog → unit tests that enter edit mode must stub `fetchAgentSchema` + `fetchModelCatalog` and reset both caches (`__resetSchemaCacheForTest`, `__resetCatalogCacheForTest`).
- Monaco is mocked to a `<textarea data-testid="monaco-stub">` in unit tests (and the view-mode `<Editor>` keeps its own `data-testid="manifest-editor"` which the mock forwards).
- Authoritative checks: `pnpm run typecheck` (`tsc -b`) exit 0; `npx vitest run <file>`. Ignore spurious LSP diagnostics on fresh/.test files. Leave the repo-root `.gitignore` change unstaged.

---

### Task 1: `ManifestTab` edit mode uses `<ManifestEditor>`

**Files:**
- Modify: `apps/admin-ui/src/pages/agent_detail/ManifestTab.tsx`
- Test: `apps/admin-ui/src/pages/__tests__/ManifestTab.test.tsx`

- [ ] **Step 1: Rewrite the test** `src/pages/__tests__/ManifestTab.test.tsx` to the following (adds schema+catalog mocks; view stays read-only Monaco; edit now renders ManifestEditor):
```tsx
/**
 * ManifestTab tests — Stream S PR E.
 *
 * View mode = read-only Monaco snapshot. Edit mode = the visual
 * <ManifestEditor> (form/YAML). Monaco is mocked to a textarea; the schema
 * and model-catalog SDKs are stubbed because edit mode mounts ManifestEditor.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../i18n";

vi.mock("@monaco-editor/react", () => {
  const Editor = ({
    value,
    onChange,
    options,
    ["data-testid"]: testId,
  }: {
    value?: string;
    onChange?: (v: string | undefined) => void;
    options?: { readOnly?: boolean };
    "data-testid"?: string;
  }) => (
    <textarea
      data-testid={testId ?? "monaco-stub"}
      readOnly={options?.readOnly}
      value={value}
      onChange={(e) => onChange?.(e.target.value)}
    />
  );
  return { default: Editor };
});

import { ApiError } from "../../api/client";
import * as agentsSdk from "../../api/agents";
import * as schemaSdk from "../../api/manifest_schema";
import * as catalogSdk from "../../api/model_catalog";
import { __resetSchemaCacheForTest } from "../../components/manifest-editor/schema";
import { __resetCatalogCacheForTest } from "../../components/manifest-editor/catalog";
import { ManifestTab } from "../agent_detail/ManifestTab";
import type { AgentDetailResponse } from "../../api/agents";

const sampleDetail: AgentDetailResponse = {
  record: {
    id: "11111111-1111-1111-1111-111111111111",
    tenant_id: "22222222-2222-2222-2222-222222222222",
    name: "demo-agent",
    version: "1.0.0",
    status: "active",
    spec_sha256: "abc123def456abc123def456abc123def456abc123def456abc123def456abcd",
    created_by: "user-1",
    created_at: "2026-05-25T00:00:00Z",
    updated_at: "2026-05-25T00:00:00Z",
    spec: {
      apiVersion: "expert_work.io/v1",
      kind: "Agent",
      metadata: { name: "demo-agent", version: "1.0.0" },
      spec: { model: { provider: "anthropic", name: "claude-sonnet-4-6" } },
    },
  },
} as AgentDetailResponse;

const onSaved = vi.fn();
const updateAgentMock = vi.spyOn(agentsSdk, "updateAgent");

beforeEach(() => {
  onSaved.mockClear();
  updateAgentMock.mockReset();
  __resetSchemaCacheForTest();
  __resetCatalogCacheForTest();
  vi.spyOn(schemaSdk, "fetchAgentSchema").mockResolvedValue({
    type: "object",
    properties: {
      metadata: { type: "object", properties: { name: { type: "string" } } },
      spec: { type: "object", properties: { model: { type: "object", properties: { provider: { type: "string" }, name: { type: "string" } } } } },
    },
  });
  vi.spyOn(catalogSdk, "fetchModelCatalog").mockResolvedValue({
    providers: [
      { provider: "anthropic", models: [{ name: "claude-sonnet-4-6", vision: true, embeddings: false, context_window: 200000, deprecated: false }] },
    ],
  });
});

afterEach(() => vi.restoreAllMocks());

describe("ManifestTab", () => {
  it("starts in view mode with a read-only editor and an Edit button", () => {
    render(<ManifestTab detail={sampleDetail} onSaved={onSaved} />);
    const editor = screen.getByTestId("manifest-editor") as HTMLTextAreaElement;
    expect(editor.readOnly).toBe(true);
    expect(editor.value).toContain("demo-agent");
    expect(editor.value).toContain("claude-sonnet-4-6");
    expect(screen.getByTestId("manifest-edit-btn")).toBeInTheDocument();
    expect(screen.queryByTestId("manifest-save-btn")).not.toBeInTheDocument();
  });

  it("clicking Edit reveals the visual ManifestEditor plus Save + Cancel", async () => {
    const user = userEvent.setup();
    render(<ManifestTab detail={sampleDetail} onSaved={onSaved} />);
    await user.click(screen.getByTestId("manifest-edit-btn"));
    await waitFor(() => expect(screen.getByTestId("manifest-editor-edit")).toBeInTheDocument());
    expect(screen.getByTestId("manifest-save-btn")).toBeInTheDocument();
    expect(screen.getByTestId("manifest-cancel-btn")).toBeInTheDocument();
  });

  it("saves edits via updateAgent and returns to view mode on success", async () => {
    const user = userEvent.setup();
    updateAgentMock.mockResolvedValue(sampleDetail);
    render(<ManifestTab detail={sampleDetail} onSaved={onSaved} />);
    await user.click(screen.getByTestId("manifest-edit-btn"));
    await screen.findByTestId("manifest-editor-edit");
    // edit via the YAML tab for a deterministic buffer
    await user.click(screen.getByTestId("manifest-tab-yaml"));
    const ta = screen.getByTestId("monaco-stub") as HTMLTextAreaElement;
    await user.clear(ta);
    await user.type(ta, "edited: yaml");
    await user.click(screen.getByTestId("manifest-save-btn"));
    await waitFor(() =>
      expect(updateAgentMock).toHaveBeenCalledWith("demo-agent", "1.0.0", { manifest_yaml: "edited: yaml" }),
    );
    expect(onSaved).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("manifest-edit-btn")).toBeInTheDocument();
  });

  it("surfaces an error alert when updateAgent rejects, stays in edit mode", async () => {
    const user = userEvent.setup();
    updateAgentMock.mockRejectedValue(new ApiError("name mismatch", "MANIFEST_PATH_MISMATCH", 422));
    render(<ManifestTab detail={sampleDetail} onSaved={onSaved} />);
    await user.click(screen.getByTestId("manifest-edit-btn"));
    await screen.findByTestId("manifest-editor-edit");
    await user.click(screen.getByTestId("manifest-save-btn"));
    const alert = await screen.findByTestId("manifest-error");
    expect(alert).toHaveTextContent("MANIFEST_PATH_MISMATCH");
    expect(onSaved).not.toHaveBeenCalled();
    expect(screen.getByTestId("manifest-save-btn")).toBeInTheDocument();
  });

  it("Cancel returns to view mode without calling updateAgent", async () => {
    const user = userEvent.setup();
    render(<ManifestTab detail={sampleDetail} onSaved={onSaved} />);
    await user.click(screen.getByTestId("manifest-edit-btn"));
    await screen.findByTestId("manifest-editor-edit");
    await user.click(screen.getByTestId("manifest-cancel-btn"));
    expect(updateAgentMock).not.toHaveBeenCalled();
    expect(screen.getByTestId("manifest-edit-btn")).toBeInTheDocument();
    expect((screen.getByTestId("manifest-editor") as HTMLTextAreaElement).value).toContain("demo-agent");
  });
});
```

- [ ] **Step 2: Run it — confirm FAIL** (edit mode still renders a writable Monaco, no `manifest-editor-edit`). `npx vitest run src/pages/__tests__/ManifestTab.test.tsx`

- [ ] **Step 3: Edit `ManifestTab.tsx`.** Add `import { ManifestEditor } from "../../components/manifest-editor";`. Seed `buffer` from `snapshotYaml` (already the case). Replace the single `<Editor>` at the bottom with a mode switch: view → read-only `<Editor>` (unchanged options, `value={snapshotYaml}`, `data-testid="manifest-editor"`); edit → `<ManifestEditor mode="edit" initialYaml={snapshotYaml} onChange={setBuffer} />`. Keep the header hint, the Edit/Save/Cancel buttons, the error Alert, and the `handleEdit`/`handleCancel`/`handleSave` callbacks exactly as they are (they already seed/reset `buffer` from `snapshotYaml` and post `{ manifest_yaml: buffer }`). Concretely, replace the JSX from `<Editor ... data-testid="manifest-editor" />` with:
```tsx
      {mode === "view" ? (
        <Editor
          language="yaml"
          value={snapshotYaml}
          theme="vs-dark"
          height="calc(100vh - 360px)"
          options={{
            readOnly: true,
            minimap: { enabled: false },
            fontFamily: "var(--ew-font-mono)",
            fontSize: 12,
            tabSize: 2,
            scrollBeyondLastLine: false,
            renderWhitespace: "boundary",
            wordWrap: "on",
          }}
          data-testid="manifest-editor"
        />
      ) : (
        <ManifestEditor mode="edit" initialYaml={snapshotYaml} onChange={setBuffer} />
      )}
```
The view-mode `<Editor>` no longer needs `onChange` (it's read-only). Keep `setBuffer` — it's now fed by `ManifestEditor`. Leave `handleSave` posting `buffer`.

- [ ] **Step 4: Run the test — confirm PASS (5).** `npx vitest run src/pages/__tests__/ManifestTab.test.tsx`. If the "saves edits" case's buffer is empty because the YAML-tab edit didn't propagate, confirm `ManifestEditor`'s YAML `onChange` calls back (it does in PR C); the seeded `buffer` covers the no-edit path anyway.

- [ ] **Step 5: Typecheck + commit**
```bash
pnpm run typecheck
git add src/pages/agent_detail/ManifestTab.tsx src/pages/__tests__/ManifestTab.test.tsx
git commit -m "feat(admin-ui): agent-detail manifest tab edits via ManifestEditor (Stream S PR E)"
```

---

### Task 2: Playwright e2e — edit an existing manifest via the form

**Files:**
- Create: `apps/admin-ui/e2e/manifest-edit.spec.ts`
- Reference: `e2e/manifest-editor.spec.ts`, `e2e/manifest-model-select.spec.ts`, `e2e/fixtures.ts`.

- [ ] **Step 1: Study the fixtures + an existing detail route.** Read `e2e/fixtures.ts` to learn the control-plane stub helper and whether `GET /v1/agents/:name/:version` (agent detail) is already stubbed (the agents-list stub exists; check for a detail stub). Note the login pattern and `expectNoA11yViolations(page, label)` signature from the PR C/D specs.

- [ ] **Step 2: Write `e2e/manifest-edit.spec.ts`.** It must:
  1. Log in (reuse the PR C/D login helper verbatim, incl. dev-toggle disclosure).
  2. Stub `**/v1/agents/schema` and `**/v1/model-catalog` (enveloped) as in PR D's spec (schema must include `spec.model` as an object).
  3. Stub `GET **/v1/agents/<name>/<version>` to return an enveloped `AgentDetailResponse` whose `record.spec` is a full manifest (apiVersion/kind/metadata/spec with a `model` object). If the fixture already provides a detail stub for a known agent, navigate to that agent instead and only add schema/catalog/PUT stubs.
  4. Stub `PUT **/v1/agents/<name>/<version>` to return success (enveloped) and let the test assert it was called.
  5. Navigate to the agent's manifest tab: `page.goto("/agents/<name>/<version>/manifest")` (after login), OR click through the agents list → agent row → "配置清单"/Manifest tab. Use whichever the existing agent-detail e2e (if any) uses; route nav is simplest.
  6. **Test A — "edit via form":** assert view mode shows the read-only manifest + `manifest-edit-btn`; click Edit; assert `manifest-editor-edit` (the visual editor) is visible and `manifest-form-view` shows; switch to the YAML tab (`manifest-tab-yaml`) → `manifest-yaml-view` visible; click Save (`manifest-save-btn`); assert the `PUT` was hit (e.g. await a request matcher or assert it returns to view with `manifest-edit-btn`).
  7. **Test B — axe:** in edit mode, `await expectNoA11yViolations(page, "manifest-tab")` — fix any real serious/critical violation in the component (not the filter); report any fix.

- [ ] **Step 3: Run** `pnpm run e2e -- manifest-edit` → both pass. (Kill a stale 5173 server if present.) Keep it light (no heavy Monaco typing — switching tabs is enough to prove the editor mounts).

- [ ] **Step 4: typecheck + commit**
```bash
pnpm run typecheck
git add e2e/manifest-edit.spec.ts <any component file fixed for axe>
git commit -m "test(admin-ui): e2e edit-manifest-via-form + axe (Stream S PR E)"
```

---

## Final verification (before opening the PR)

From `apps/admin-ui/`:
- [ ] `pnpm run test` — all vitest pass (ManifestTab + everything).
- [ ] `pnpm run typecheck` — exit 0.
- [ ] `pnpm run build` + `pnpm run build-storybook` — succeed.
- [ ] `pnpm run e2e` — manifest-editor + manifest-model-select + manifest-edit + smoke pass.
- [ ] repo root: `uv run pre-commit run --files <changed files>` — clean.

PR title: `feat(stream-s): PR E — edit integration (agent-detail manifest tab) + Stream S finale`. Body: link design + this plan; note edit mode now uses `<ManifestEditor>` while preserving read-only-by-default browse (user-locked); create + edit both run e2e; mark Stream S complete and update `docs/ITERATION-PLAN.md` Stream S backlog (A–E all shipped) in the same PR per the iteration-plan-sync rule.

---

## Self-Review

**Spec coverage (STREAM-S-DESIGN.md §5 PR E = "接 智能体详情→配置清单 标签; create+edit 两条 e2e + axe; 收尾"):**
- agent-detail manifest tab uses `<ManifestEditor>` for editing (S-7 edit half) → Task 1. ✅ (read-only-default browse preserved per user decision.)
- create e2e → already exists (PR C/D). edit e2e + axe → Task 2. ✅
- 收尾 / Stream S complete → ITERATION-PLAN update noted in the final PR step. ✅
- No new i18n (reuse `manifest_tab.*`) — correct, no namespace churn.

**Placeholder scan:** no TBD/"handle appropriately"; full code for ManifestTab + its test; the e2e step gives concrete stub shapes + two acceptable navigation approaches (route vs click-through) with a decision rule. No placeholders.

**Type consistency:** `ManifestEditor` props `{ mode: "create"|"edit"; initialYaml; onChange }` match the Task 1 call `<ManifestEditor mode="edit" initialYaml={snapshotYaml} onChange={setBuffer} />`. `updateAgent(name, version, { manifest_yaml })` matches the existing SDK + the test assertion. `__resetSchemaCacheForTest`/`__resetCatalogCacheForTest`/`fetchAgentSchema`/`fetchModelCatalog` names match PR C/D exports. `record.spec` (full manifest) feeds `snapshotYaml` → `initialYaml` consistently.
