# Stream V-G — Agent-Form MCP Server Picker (the finale) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** Replace the agent form's free-text MCP "allow_tools" box with a real picker — select servers (from `/available`), expand a server to check specific tools (from `/{name}/tools`), writing `MCPToolSpec.servers` + `allow_tools`. Closes the Stream V loop: register a remote MCP → build an agent that picks it → agent calls its tools.

**Architecture:** A new `McpToolPicker` component (fetches `/available` + per-server `/{name}/tools`, manages selection, calls the form's `onChange`) replaces the `af-mcp-allow` free-text `Input` in `FormView`. `form_model.ts` gains a `servers` accessor (`setMcpServers`) + reads it in `readTools`. The SDK already has `listAvailableMcpServers` + `listMcpServerTools` (V-F). Writes the manifest `tools: [{type:"mcp", servers:[...], allow_tools:[...]}]`.

**UX:** Enable MCP toggle (exists) → server checkbox list (from `/available`, each with a platform/tenant source tag); checking a server adds it to `servers` (empty `servers` = all available). Each checked server can be expanded → its live tools as checkboxes; checking specific tools adds their names to `allow_tools` (flat list across servers; empty = all tools of the selected servers). Loading/error/empty states. Token never involved (form only references servers by name).

**Scope (V-G):** `form_model` `servers` accessor + `McpToolPicker` + FormView wiring + i18n + tests. Backend already done (V-E schema, V-F endpoints).

**Branch:** `stream-v/g-agent-form` (off `main`, after V-F merged).

**CodeQL guardrails:** no `Protocol` `...` (N/A — TS); no tenant-derived values in any backend log (N/A — frontend-only PR).

**Key facts (verified 2026-06-03):**
- `form_model.ts`: `ToolEntry = { type; name?; allow_tools?: string[]; config?; [k]: unknown }`; `ToolFlags { webSearch; http; mcp; mcpAllowTools }`; `readTools` finds the mcp entry; `setMcpAllowTools(m, allow)` maps mcp entry → `{...t, allow_tools}` via `patchSpec`. `setTool(m,"mcp",on)` adds/removes `{type:"mcp", allow_tools:[]}`.
- `FormView.tsx` MCP section (~line 159-185): `af-tool-mcp` checkbox toggles mcp; when on, an `af-mcp-allow` free-text `Input` writes `setMcpAllowTools` (comma-split). **This is what V-G replaces.**
- SDK (V-F): `listAvailableMcpServers(): Promise<{name, source:"platform"|"tenant", enabled?}[]>` and `listMcpServerTools(name): Promise<{name, description}[]>` in `apps/admin-ui/src/api/mcp-servers.ts`.
- i18n: `apps/admin-ui/src/i18n/locales/{en,zh-CN}.ts`, `agent_form.*` namespace (has `tool_mcp`, `tool_mcp_allow`).
- Form-model tests: grep `form_model` under `apps/admin-ui/src` for the existing unit test file.
- Manifest editor tests / FormView tests: grep `FormView` / `ManifestEditor` in `apps/admin-ui/src` + `e2e/`.

---

## File Structure
**Create:** `apps/admin-ui/src/components/manifest-editor/widgets/McpToolPicker.tsx`; (if a form_model test file exists, extend it, else create `apps/admin-ui/src/components/manifest-editor/form_model.test.ts`).
**Modify:** `apps/admin-ui/src/components/manifest-editor/form_model.ts`, `apps/admin-ui/src/components/manifest-editor/FormView.tsx`, `apps/admin-ui/src/i18n/locales/{en,zh-CN}.ts`, the manifest-editor stories/e2e.

---

## Task 1: `form_model` — `servers` accessor

**Files:** Modify `form_model.ts` + its unit test.

- [ ] **Step 1: Write failing tests** (grep the existing form_model test file; add):

```typescript
import { readTools, setMcpServers, setMcpAllowTools, setTool } from "./form_model";

const withMcp = () => setTool({ apiVersion: "v1", kind: "Agent", spec: {} }, "mcp", true);

test("readTools defaults mcpServers to empty", () => {
  expect(readTools(withMcp()).mcpServers).toEqual([]);
});

test("setMcpServers sets the servers list on the mcp tool entry", () => {
  const m = setMcpServers(withMcp(), ["github", "linear"]);
  expect(readTools(m).mcpServers).toEqual(["github", "linear"]);
});

test("setMcpServers preserves allow_tools (merge-preserving)", () => {
  let m = setMcpAllowTools(withMcp(), ["create_issue"]);
  m = setMcpServers(m, ["github"]);
  expect(readTools(m).mcpAllowTools).toEqual(["create_issue"]);
  expect(readTools(m).mcpServers).toEqual(["github"]);
});

test("setMcpServers no-ops when there is no mcp tool", () => {
  const m = setMcpServers({ apiVersion: "v1", kind: "Agent", spec: {} }, ["github"]);
  expect(readTools(m).mcp).toBe(false);
});
```

- [ ] **Step 2: Run → fail** (`setMcpServers` / `mcpServers` don't exist). `cd apps/admin-ui && pnpm run test -- form_model` (adapt to the repo's test runner/invocation).

- [ ] **Step 3: Implement** in `form_model.ts`:
  - Add `servers?: string[];` to the `ToolEntry` type (explicit, before `config`).
  - Add `mcpServers: string[];` to `ToolFlags`.
  - In `readTools`, add `mcpServers: mcp?.servers ?? [],`.
  - Add `setMcpServers` (mirror `setMcpAllowTools`):
    ```typescript
    export function setMcpServers(m: unknown, servers: string[]): AgentManifest {
      const tools = (specOf(m).tools ?? []).map((t) =>
        t.type === "mcp" ? { ...t, servers } : t,
      );
      return patchSpec(m, { tools });
    }
    ```

- [ ] **Step 4: Run → pass.** `cd apps/admin-ui && pnpm run test -- form_model` + `pnpm run typecheck`.

- [ ] **Step 5: Commit** `feat(stream-v): form_model setMcpServers accessor (V-G)`.

---

## Task 2: i18n keys for the picker

**Files:** Modify `apps/admin-ui/src/i18n/locales/{en,zh-CN}.ts`.

- [ ] **Step 1:** Add to `agent_form` namespace (en source-of-truth + zh-CN parity, proper 简体中文):
```
agent_form.mcp_servers_label: "MCP servers this agent can use"  (此 agent 可用的 MCP 服务器)
agent_form.mcp_servers_hint: "Leave all unchecked to allow every available server"  (全不选 = 允许所有可用 server)
agent_form.mcp_no_servers: "No MCP servers available. Register one under Settings → MCP Servers."  (暂无可用 MCP 服务器，请到 设置 → MCP 服务器 注册)
agent_form.mcp_source_platform: "platform"  (平台)
agent_form.mcp_source_tenant: "tenant"  (租户)
agent_form.mcp_tools_label: "Tools"  (工具)
agent_form.mcp_tools_hint: "Leave all unchecked to allow every tool from the selected servers"  (全不选 = 允许选中 server 的所有工具)
agent_form.mcp_tools_loading: "Loading tools…"  (加载工具中…)
agent_form.mcp_tools_unreachable: "Could not load tools"  (工具加载失败)
agent_form.mcp_servers_loading: "Loading servers…"  (加载服务器中…)
agent_form.mcp_servers_load_failed: "Could not load servers"  (服务器加载失败)
```
(Keep the existing `tool_mcp` toggle label; `tool_mcp_allow` becomes unused — remove it from both files + the interface IF nothing else references it; grep first.)

- [ ] **Step 2:** `pnpm run typecheck` (parity). Commit `feat(stream-v): i18n keys for agent-form MCP picker (V-G)`.

---

## Task 3: `McpToolPicker` component

**Files:** Create `apps/admin-ui/src/components/manifest-editor/widgets/McpToolPicker.tsx`.

- [ ] **Step 1: Read** an existing widget in `apps/admin-ui/src/components/manifest-editor/widgets/` (e.g. `ModelSelect.tsx`) for the controlled-component + styling idiom, and `SettingsMcpServers.tsx` for the `listAvailableMcpServers`/`listMcpServerTools` call + loading/error handling pattern.

- [ ] **Step 2: Implement** a controlled component:
  ```typescript
  interface McpToolPickerProps {
    servers: string[];        // selected server names (MCPToolSpec.servers)
    allowTools: string[];     // selected tool names (MCPToolSpec.allow_tools)
    onServersChange: (next: string[]) => void;
    onAllowToolsChange: (next: string[]) => void;
  }
  ```
  Behavior:
  - On mount: `listAvailableMcpServers()` → state `available` (+ loading/error states: `mcp_servers_loading`, `mcp_servers_load_failed`). If empty → `mcp_no_servers` hint.
  - Render each available server as a Checkbox (testid `af-mcp-server-{name}`), checked when `name ∈ servers` (or when `servers` is empty? — NO: an explicit selection model; empty `servers` means "all" semantically, but in the UI show checkboxes unchecked = none-selected-yet. Treat the displayed `servers` list as the explicit selection; if the user checks none, `servers=[]` which the backend reads as "all available". Add the `mcp_servers_hint` explaining empty = all.). Show the source tag (`mcp_source_platform`/`mcp_source_tenant`).
  - Checking/unchecking a server → `onServersChange(next)`.
  - Each CHECKED server is expandable (e.g. an antd Collapse/Panel or a nested block, testid `af-mcp-tools-{name}`): on first expand, `listMcpServerTools(name)` → tool checkboxes (testid `af-mcp-tool-{name}` per tool — use the bare tool name; note tool names are namespaced by server visually but `allow_tools` is a FLAT list). Loading → `mcp_tools_loading`; error → `mcp_tools_unreachable`.
  - Checking a tool adds its bare name to `allowTools`; unchecking removes it → `onAllowToolsChange(next)`. (allow_tools is shared across servers; empty = all tools.)
  - Cache fetched tools per server name in component state.
  - Use antd (Checkbox, Collapse, Spin, Tag, Alert) + the design baseline. i18n `agent_form.mcp_*`.

- [ ] **Step 3:** `pnpm run typecheck`. Commit `feat(stream-v): McpToolPicker widget (V-G)`.

---

## Task 4: Wire `McpToolPicker` into `FormView`

**Files:** Modify `apps/admin-ui/src/components/manifest-editor/FormView.tsx`.

- [ ] **Step 1:** Replace the `{tools.mcp && (<div data-testid="af-mcp-allow">...free-text Input...</div>)}` block with:
  ```tsx
  {tools.mcp && (
    <McpToolPicker
      servers={tools.mcpServers}
      allowTools={tools.mcpAllowTools}
      onServersChange={(next) => onChange(setMcpServers(formData, next))}
      onAllowToolsChange={(next) => onChange(setMcpAllowTools(formData, next))}
    />
  )}
  ```
  Import `McpToolPicker` + `setMcpServers`. Keep the `af-tool-mcp` toggle. Remove the now-unused `Input` import if nothing else in FormView uses it (grep), and the `setMcpAllowTools` comma-split logic.

- [ ] **Step 2:** `pnpm run typecheck && pnpm run build`. Commit `feat(stream-v): agent form uses McpToolPicker (replace free-text allow_tools) (V-G)`.

---

## Task 5: Stories + Playwright + axe

**Files:** Update the manifest-editor/FormView stories + e2e (grep for the existing FormView/agent-create story + e2e spec).

- [ ] **Step 1: Stories** — add a story (or extend the FormView/agent-form story) showing the MCP picker with `listAvailableMcpServers` mocked (2 servers) + `listMcpServerTools` mocked. Mirror the existing fixture decorator (apiClient adapter). If FormView is only rendered inside ManifestEditor/the create-agent page, add the story there.

- [ ] **Step 2: e2e** — in the agent-create/edit e2e spec (grep `af-tool-mcp` / agent form e2e), add/extend a test: enable MCP (`af-tool-mcp`) → mock `/v1/mcp-servers/available` → check a server (`af-mcp-server-github`) → expand → mock `/v1/mcp-servers/github/tools` → check a tool (`af-mcp-tool-create_issue`) → submit → assert the saved manifest has `tools:[{type:mcp, servers:["github"], allow_tools:["create_issue"]}]` (or assert the create POST body via route interception). Add axe on the form. Mirror the existing agent-form e2e + mocking idiom.

- [ ] **Step 3:** `pnpm run typecheck && pnpm run build-storybook`; compile the e2e (`npx playwright test --list`). Commit `test(stream-v): agent-form MCP picker stories + e2e (V-G)`.

---

## Task 6: Preflight + push + PR

- [ ] **Step 1:** `cd apps/admin-ui && pnpm run typecheck && pnpm run test && pnpm run build && pnpm run build-storybook` — all green.
- [ ] **Step 2:** Backend untouched, but run `uv run ruff check . && uv run mypy packages services/orchestrator/src` for safety (should be unaffected). No lockfile drift.
- [ ] **Step 3:** Push + PR (`stream-v/g-agent-form` → main; title `feat(stream-v): PR G — agent-form MCP server picker (finale)`; body: replaces free-text allow_tools with server-select + per-server tool checkboxes writing `servers`+`allow_tools`; closes the Stream V loop; tests).
- [ ] **Step 4:** Poll CI green (admin-ui jobs); resolve any CodeQL threads (frontend-only — none expected).

---

## Self-Review (plan author)
**Spec coverage (V-8):** `servers` accessor (Task 1) ✓; server multi-select + per-server tool checkboxes from `/available`+`/{name}/tools` (Task 3) ✓; FormView replaces free-text (Task 4) ✓; i18n (Task 2), stories/e2e (Task 5) ✓.
**Semantics:** empty `servers` = all available servers; empty `allow_tools` = all tools of selected servers — matches the V-E backend filter (`server_select = set(servers) or None`, `allow = set(allow_tools) or None`). The hint text states "empty = all" for both, so the UX matches the runtime.
**allow_tools is flat** (not per-server) — the picker's per-server tool checkboxes all contribute to one `allow_tools` list (bare tool names). This matches the schema; documented in Task 3.
**Placeholder note:** Frontend tasks point at the live widgets/stories/e2e to mirror (the accepted patterns) + specify the exact props/testids/i18n/semantics. form_model (Task 1) has full code.
**Risk:** tool-name collisions across servers in the flat `allow_tools` (two servers both expose `search`) — checking one checks the name for both. Acceptable for V-G (the schema is flat); note it. The picker shows tools grouped by server so the user sees the namespacing even though the filter is by bare name.
