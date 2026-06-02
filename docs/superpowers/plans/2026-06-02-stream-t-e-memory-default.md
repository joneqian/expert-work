# Stream T PR E — Memory-on Default + 回炉 + Create-Agent Embedding Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make long-term memory on-by-default for newly-created agents, remove the conceptually-wrong "main-model provider has no embeddings" hint from PR D, and block+guide agent creation when the platform has no embedding configured — for every persona (tenant admin included).

**Architecture:** Long-term memory becomes part of the visual editor's default seed manifest (schema default stays `None`, so existing manifests/tests are untouched). A new **unauthenticated-by-role** `GET /v1/platform/embedding-config/status` returns only `{configured: bool}` so any logged-in agent-creator (tenant admin) can pre-check — the full config GET stays system_admin-only. `CreateAgentDrawer` fetches this status on open and, when unconfigured, replaces the editor with a block+guide panel pointing to `/settings/platform`. The build-time embedder gate (control-plane) remains the defensive backstop.

**Tech Stack:** FastAPI (control-plane), React 19 + Antd 5 + react-i18next + react-router-dom (admin-ui), Vitest + Playwright/axe.

---

## File Structure

- **Backend (Python):**
  - Modify: `services/control-plane/src/control_plane/api/platform_embedding_config.py` — add `GET .../status` (no system_admin gate).
  - Test: `services/control-plane/tests/test_platform_embedding_config_api.py` (existing — extend).
- **Frontend SDK (TS):**
  - Modify: `apps/admin-ui/src/api/platform_embedding_config.ts` — add `getPlatformEmbeddingStatus()`.
  - Test: `apps/admin-ui/src/api/__tests__/sdks.test.ts` (existing — extend).
- **回炉 (TS):**
  - Modify: `apps/admin-ui/src/components/manifest-editor/widgets/ModelSelectField.tsx`, `apps/admin-ui/src/components/manifest-editor/catalog.ts`, both i18n locale files.
  - Test: `apps/admin-ui/src/components/manifest-editor/__tests__/ModelSelectField.test.tsx`, `.../catalog.test.tsx`.
- **Default template (TS):**
  - Modify: `apps/admin-ui/src/components/manifest-editor/defaults.ts`.
  - Test: `apps/admin-ui/src/components/manifest-editor/__tests__/defaults.test.tsx`.
- **Create-agent gate (TS):**
  - Modify: `apps/admin-ui/src/components/CreateAgentDrawer.tsx`, both i18n locale files.
  - Test: `apps/admin-ui/src/components/__tests__/CreateAgentDrawer.test.tsx` (create if absent).
- **Docs + E2E:**
  - Modify: `docs/runbooks/getting-started.md`.
  - Create/modify: `apps/admin-ui/e2e/create-agent-embedding-gate.spec.ts`; stub the new status route in any existing spec that opens the drawer (`apps/admin-ui/e2e/manifest-editor.spec.ts`, `agents` specs).

---

## Task 1: Backend — `GET /v1/platform/embedding-config/status` (role-agnostic)

**Files:**
- Modify: `services/control-plane/src/control_plane/api/platform_embedding_config.py`
- Test: `services/control-plane/tests/test_platform_embedding_config_api.py`

Rationale: the full GET/PUT stay system_admin-only. The status endpoint exposes only a boolean (no provider/model names, no available lists) so a tenant admin can pre-check before building a memory-on agent. Gate on `_principal` only (any authenticated user).

- [ ] **Step 1: Write the failing test**

Add to `services/control-plane/tests/test_platform_embedding_config_api.py` (follow the existing test setup/fixtures in that file — reuse the same app/client builder and the same way other tests construct a non-system-admin principal vs a system-admin principal):

```python
@pytest.mark.asyncio
async def test_status_reports_configured_true_when_embedding_set(client_factory):
    # Arrange: a service whose effective_embedding_config() returns a pair.
    client = client_factory(embedding=("qwen", "text-embedding-v4"))
    resp = await client.get("/v1/platform/embedding-config/status")
    assert resp.status_code == 200
    assert resp.json()["data"] == {"configured": True}


@pytest.mark.asyncio
async def test_status_reports_configured_false_when_unset(client_factory):
    client = client_factory(embedding=None)
    resp = await client.get("/v1/platform/embedding-config/status")
    assert resp.status_code == 200
    assert resp.json()["data"] == {"configured": False}


@pytest.mark.asyncio
async def test_status_allows_non_system_admin(client_factory):
    # A tenant admin (is_system_admin=False) may read status (full GET is 403).
    client = client_factory(embedding=("qwen", "text-embedding-v4"), system_admin=False)
    resp = await client.get("/v1/platform/embedding-config/status")
    assert resp.status_code == 200
    assert resp.json()["data"] == {"configured": True}
```

> NOTE to implementer: adapt the helper names (`client_factory`, `embedding=`, `system_admin=`) to whatever the existing test module already uses. If the existing tests build principals a specific way, mirror that exactly — do not invent a new harness. The three behaviors under test are fixed: configured→True, unconfigured→False, non-system-admin→200.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/control-plane && uv run python -m pytest tests/test_platform_embedding_config_api.py -k status -v`
Expected: FAIL (404 — route does not exist yet).

- [ ] **Step 3: Add the route**

In `build_platform_embedding_config_router()` in `platform_embedding_config.py`, add a handler (place it right after the `@router.get("")` handler). It must NOT call `_require_system_admin`:

```python
    @router.get("/status")
    async def get_platform_embedding_config_status(
        principal: Annotated[Principal, Depends(_principal)],
        embedding_config_service: Annotated[
            PlatformEmbeddingConfigService, Depends(_get_embedding_config_service)
        ],
    ) -> dict[str, object]:
        """Whether the platform has an effective embedding config.

        Role-agnostic (any authenticated principal): exposes only a boolean
        so an agent-creator — typically a tenant admin, who cannot read the
        full system_admin-only config — can block+guide before building a
        long-term-memory agent. No provider/model names are returned."""
        embedding = await embedding_config_service.effective_embedding_config()
        return {
            "success": True,
            "data": {"configured": embedding is not None},
            "error": None,
        }
```

> `principal` is unused beyond forcing authentication via the `_principal` dependency. If the repo's ruff config flags the unused param, keep it (the dependency must run) and prefix-free is fine — FastAPI needs the parameter; document with the existing pattern used elsewhere for auth-only deps. If ruff complains, add it to the function and reference it in a `# noqa`-free way by leaving it (Depends params are not flagged as unused by ruff's standard rules).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/control-plane && uv run python -m pytest tests/test_platform_embedding_config_api.py -k status -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Pre-commit + full module test**

Run: `uv run pre-commit run --files services/control-plane/src/control_plane/api/platform_embedding_config.py services/control-plane/tests/test_platform_embedding_config_api.py`
Run: `cd services/control-plane && uv run python -m pytest tests/test_platform_embedding_config_api.py -v`
Expected: all green; pre-commit clean (ruff + ruff-format + mypy hooks).

- [ ] **Step 6: Commit**

```bash
git add services/control-plane/src/control_plane/api/platform_embedding_config.py services/control-plane/tests/test_platform_embedding_config_api.py
git commit -m "feat(stream-t): PR E — role-agnostic embedding-config status endpoint"
```

---

## Task 2: Frontend SDK — `getPlatformEmbeddingStatus()`

**Files:**
- Modify: `apps/admin-ui/src/api/platform_embedding_config.ts`
- Test: `apps/admin-ui/src/api/__tests__/sdks.test.ts`

- [ ] **Step 1: Write the failing test**

Add to `apps/admin-ui/src/api/__tests__/sdks.test.ts` (mirror the existing `getPlatformEmbeddingConfig` test in that file — reuse its `getJson` mock/fetch-stub pattern):

```ts
it("getPlatformEmbeddingStatus calls the status endpoint", async () => {
  const spy = mockGetJson({ configured: true });
  const out = await getPlatformEmbeddingStatus();
  expect(spy).toHaveBeenCalledWith("/v1/platform/embedding-config/status");
  expect(out).toEqual({ configured: true });
});
```

> Adapt `mockGetJson` to the file's existing mock helper for `getJson`. Add `getPlatformEmbeddingStatus` to the import at the top.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/admin-ui && pnpm vitest run src/api/__tests__/sdks.test.ts -t "getPlatformEmbeddingStatus"`
Expected: FAIL (not exported).

- [ ] **Step 3: Add the SDK function + type**

In `apps/admin-ui/src/api/platform_embedding_config.ts` add:

```ts
export interface PlatformEmbeddingStatus {
  configured: boolean;
}

export async function getPlatformEmbeddingStatus(): Promise<PlatformEmbeddingStatus> {
  return getJson<PlatformEmbeddingStatus>("/v1/platform/embedding-config/status");
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/admin-ui && pnpm vitest run src/api/__tests__/sdks.test.ts -t "getPlatformEmbeddingStatus"`
Expected: PASS.

- [ ] **Step 5: Typecheck + pre-commit + commit**

Run: `cd apps/admin-ui && pnpm run typecheck`
Run: `uv run pre-commit run --files apps/admin-ui/src/api/platform_embedding_config.ts apps/admin-ui/src/api/__tests__/sdks.test.ts`

```bash
git add apps/admin-ui/src/api/platform_embedding_config.ts apps/admin-ui/src/api/__tests__/sdks.test.ts
git commit -m "feat(stream-t): PR E — getPlatformEmbeddingStatus SDK"
```

---

## Task 3: 回炉 — remove the "no embeddings on main-model provider" hint

**Files:**
- Modify: `apps/admin-ui/src/components/manifest-editor/widgets/ModelSelectField.tsx`
- Modify: `apps/admin-ui/src/components/manifest-editor/catalog.ts`
- Modify: `apps/admin-ui/src/i18n/locales/en.ts`, `apps/admin-ui/src/i18n/locales/zh-CN.ts`
- Test: `apps/admin-ui/src/components/manifest-editor/__tests__/ModelSelectField.test.tsx`, `.../catalog.test.tsx`

Rationale (Mini-ADR T-6): embedding uses the **platform** embedding provider, decoupled from the agent's main-model provider. The hint "this provider has no embedding model → long-term memory won't work" is conceptually wrong and must go. `providerHasEmbeddings` becomes dead after removal.

- [ ] **Step 1: Update tests first (RED via removal/assertion flip)**

In `__tests__/ModelSelectField.test.tsx`, DELETE the test "shows the no-embeddings note for a provider without an embedding model" (the one asserting `getByTestId("model-select-no-embeddings")`, ~lines 94-97). Add an assertion to an existing "renders for a provider" test (or a new small test) that the hint is gone:

```ts
it("does not show any embedding-availability note (embedding is platform-level)", async () => {
  // render with the deepseek provider selected (no embedding models in catalog)
  // ... existing render setup for a selected provider ...
  expect(screen.queryByTestId("model-select-no-embeddings")).toBeNull();
});
```

In `__tests__/catalog.test.tsx`, DELETE the two assertions referencing `providerHasEmbeddings` (~lines 49-50) and remove `providerHasEmbeddings` from that file's import from `../catalog`.

- [ ] **Step 2: Run tests to verify the deletion target fails / new assertion fails**

Run: `cd apps/admin-ui && pnpm vitest run src/components/manifest-editor/__tests__/ModelSelectField.test.tsx src/components/manifest-editor/__tests__/catalog.test.tsx`
Expected: the new `queryByTestId(...).toBeNull()` FAILS (hint still rendered); catalog test compile error gone after import removal.

- [ ] **Step 3: Remove the hint from ModelSelectField.tsx**

- Remove `providerHasEmbeddings` from the import on line 22 → `import { lookupModel, modelsFor, providerNames } from "../catalog";`
- Delete the `noEmbeddings` const (line 69).
- Delete the `{noEmbeddings && (<Alert ... data-testid="model-select-no-embeddings" .../>)}` block (lines 105-113).
- If `Alert` is no longer used anywhere else in the file, remove it from the `antd` import on line 17 → `import { Collapse, Input, InputNumber, Select, Tag } from "antd";` (verify with grep first; `Alert` appears only in the removed block).

- [ ] **Step 4: Remove dead `providerHasEmbeddings` from catalog.ts**

Delete the function (lines 40-42):
```ts
export function providerHasEmbeddings(catalog: ModelCatalog, provider: string): boolean {
  return modelsFor(catalog, provider).some((m) => m.embeddings);
}
```
Grep the repo for any other `providerHasEmbeddings` usage first (`rg providerHasEmbeddings apps/admin-ui/src`) — there should be none after Step 3 + test edits.

- [ ] **Step 5: Remove the i18n key**

- `en.ts`: remove `no_embeddings: string;` from the `model_select` interface block (line 108) AND the value (lines 894-895, the multiline `no_embeddings: "..."`).
- `zh-CN.ts`: remove `no_embeddings: "该提供方没有 embedding 模型，长期记忆无法在其上使用。"` (line 111).

- [ ] **Step 6: Run tests + typecheck**

Run: `cd apps/admin-ui && pnpm vitest run src/components/manifest-editor && pnpm run typecheck`
Expected: PASS (typecheck enforces en/zh parity — confirms the key removed from both).

- [ ] **Step 7: Pre-commit + commit**

Run: `uv run pre-commit run --files apps/admin-ui/src/components/manifest-editor/widgets/ModelSelectField.tsx apps/admin-ui/src/components/manifest-editor/catalog.ts apps/admin-ui/src/i18n/locales/en.ts apps/admin-ui/src/i18n/locales/zh-CN.ts apps/admin-ui/src/components/manifest-editor/__tests__/ModelSelectField.test.tsx apps/admin-ui/src/components/manifest-editor/__tests__/catalog.test.tsx`

```bash
git add apps/admin-ui/src/components/manifest-editor apps/admin-ui/src/i18n/locales/en.ts apps/admin-ui/src/i18n/locales/zh-CN.ts
git commit -m "feat(stream-t): PR E — drop misleading main-model embedding hint (embedding is platform-level)"
```

---

## Task 4: Default template — long-term memory on by default

**Files:**
- Modify: `apps/admin-ui/src/components/manifest-editor/defaults.ts`
- Test: `apps/admin-ui/src/components/manifest-editor/__tests__/defaults.test.tsx`

Rationale (Mini-ADR T-5 ③): newly-built agents seed with `spec.memory.long_term`. Schema default stays `None` (no protocol change), so existing manifests/tests are unaffected; only the editor's seed changes.

- [ ] **Step 1: Flip the test (RED)**

In `__tests__/defaults.test.tsx` change line 28 from:
```ts
expect(m).not.toHaveProperty("spec.memory.long_term");
```
to:
```ts
expect(m).toHaveProperty("spec.memory.long_term");
expect((m as any).spec.memory.long_term).toMatchObject({
  retrieve_top_k: 5,
  write_back: true,
  recall_mode: "per_session",
});
```
> Use the test's existing parsing helper for `m` (the parsed BASE_MANIFEST_YAML / buildDefaultManifest output) — match the pattern already in the file rather than `as any` if a typed accessor exists.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/admin-ui && pnpm vitest run src/components/manifest-editor/__tests__/defaults.test.tsx`
Expected: FAIL (no memory property).

- [ ] **Step 3: Add memory.long_term to BASE_MANIFEST_YAML + fix the header comment**

In `defaults.ts`, insert after the `system_prompt` block (after line 25, before `sandbox:`), with 2-space spec indentation:

```yaml
  memory:
    long_term:
      retrieve_top_k: 5
      write_back: true
      recall_mode: per_session
```

So the `spec:` block becomes model → system_prompt → memory → sandbox.

Update the file header comment (lines 7-8): replace
```
 * model the platform can actually build. Long-term memory stays off (default),
 * so the embedder gate can't trip at runtime.
```
with
```
 * model the platform can actually build. Long-term memory is ON by default
 * (Stream T): a memory-less agent has little product value, so new agents seed
 * with ``spec.memory.long_term``. This requires a platform embedding config —
 * CreateAgentDrawer blocks+guides when none is set (the build-time embedder
 * gate is the backstop).
```

> Verify `buildDefaultManifest` preserves `memory`: it spreads `...spec` and only overrides `model`, so `memory` carries through. No code change needed there.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/admin-ui && pnpm vitest run src/components/manifest-editor/__tests__/defaults.test.tsx`
Expected: PASS.

- [ ] **Step 5: Typecheck + pre-commit + commit**

Run: `cd apps/admin-ui && pnpm run typecheck`
Run: `uv run pre-commit run --files apps/admin-ui/src/components/manifest-editor/defaults.ts apps/admin-ui/src/components/manifest-editor/__tests__/defaults.test.tsx`

```bash
git add apps/admin-ui/src/components/manifest-editor/defaults.ts apps/admin-ui/src/components/manifest-editor/__tests__/defaults.test.tsx
git commit -m "feat(stream-t): PR E — seed new agents with long-term memory on by default"
```

---

## Task 5: Create-agent embedding gate (block + guide)

**Files:**
- Modify: `apps/admin-ui/src/components/CreateAgentDrawer.tsx`
- Modify: `apps/admin-ui/src/i18n/locales/en.ts`, `apps/admin-ui/src/i18n/locales/zh-CN.ts`
- Test: `apps/admin-ui/src/components/__tests__/CreateAgentDrawer.test.tsx`

Rationale (Mini-ADR T-5 ①): when the platform has no embedding config, building a memory-on agent yields one that fails at first run. Block at the drawer and guide to `/settings/platform`.

- [ ] **Step 1: Add i18n keys**

`en.ts` — add to the `create_agent` interface block (after `create_failed: string;`, line 744):
```ts
    embedding_required_title: string;
    embedding_required_desc: string;
    embedding_required_cta: string;
```
`en.ts` values block (after `create_failed: "Failed to create agent",`, line 1571):
```ts
    embedding_required_title: "Configure platform embedding first",
    embedding_required_desc:
      "New agents use long-term memory, which needs a platform embedding model. No embedding is configured yet — set one in Platform Settings, then create your agent.",
    embedding_required_cta: "Go to Platform Settings",
```
`zh-CN.ts` values block (after `create_failed: "智能体创建失败",`, line 770):
```ts
    embedding_required_title: "请先配置平台 Embedding",
    embedding_required_desc:
      "新建智能体默认开启长期记忆，需要平台的 Embedding 模型。平台尚未配置 Embedding——请先在「平台设置」里配置，再创建智能体。",
    embedding_required_cta: "前往平台设置",
```

- [ ] **Step 2: Write the failing tests**

Create `apps/admin-ui/src/components/__tests__/CreateAgentDrawer.test.tsx` (mirror existing component-test setup in this repo: i18n provider wrapper, antd, `vi.mock` for the api modules). Mock `../../api/platform_embedding_config` and `../manifest-editor/catalog` (`loadModelCatalog`). Key cases:

```ts
import { render, screen, waitFor } from "@testing-library/react";
// ... i18n + router wrappers per existing test conventions ...
import { CreateAgentDrawer } from "../CreateAgentDrawer";

vi.mock("../../api/platform_embedding_config", () => ({
  getPlatformEmbeddingStatus: vi.fn(),
}));
// ... mock loadModelCatalog to resolve a minimal catalog ...

it("shows the embedding-required gate when platform embedding is unconfigured", async () => {
  (getPlatformEmbeddingStatus as Mock).mockResolvedValue({ configured: false });
  renderDrawer({ open: true });
  expect(await screen.findByTestId("create-agent-embedding-gate")).toBeInTheDocument();
  // editor is not rendered; submit is disabled
  expect(screen.queryByTestId("create-agent-submit")).toHaveAttribute("disabled");
});

it("renders the editor when platform embedding is configured", async () => {
  (getPlatformEmbeddingStatus as Mock).mockResolvedValue({ configured: true });
  renderDrawer({ open: true });
  await waitFor(() =>
    expect(screen.queryByTestId("create-agent-embedding-gate")).toBeNull(),
  );
  expect(screen.getByTestId("create-agent-submit")).not.toHaveAttribute("disabled");
});

it("the gate CTA navigates to /settings/platform and closes the drawer", async () => {
  (getPlatformEmbeddingStatus as Mock).mockResolvedValue({ configured: false });
  const onClose = vi.fn();
  renderDrawer({ open: true, onClose });
  const cta = await screen.findByTestId("create-agent-embedding-cta");
  cta.click();
  expect(mockNavigate).toHaveBeenCalledWith("/settings/platform");
  expect(onClose).toHaveBeenCalled();
});
```
> Use the repo's existing router-mock convention for `useNavigate` (a `mockNavigate` via `vi.mock("react-router-dom", ...)`), matching how other component tests that navigate are written. If none exists, mock `react-router-dom`'s `useNavigate` to return `mockNavigate`.

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd apps/admin-ui && pnpm vitest run src/components/__tests__/CreateAgentDrawer.test.tsx`
Expected: FAIL (gate not implemented).

- [ ] **Step 4: Implement the gate in CreateAgentDrawer.tsx**

- Add imports:
  ```ts
  import { useNavigate } from "react-router-dom";
  import { getPlatformEmbeddingStatus } from "../api/platform_embedding_config";
  ```
- Add state + navigate inside the component:
  ```ts
  const navigate = useNavigate();
  // null = loading/unknown; true/false once fetched
  const [embeddingConfigured, setEmbeddingConfigured] = useState<boolean | null>(null);
  ```
- In the existing `useEffect(..., [open])`, alongside `loadModelCatalog()`, fetch status (reset to `null` on open so a reopened drawer re-checks):
  ```ts
  setEmbeddingConfigured(null);
  getPlatformEmbeddingStatus().then(
    (s) => { if (alive) setEmbeddingConfigured(s.configured); },
    () => { if (alive) setEmbeddingConfigured(true); }, // fail-open: don't block on a status fetch error; build-time gate backstops
  );
  ```
  > Fail-open rationale: a transient status-fetch failure must not block legitimate creation; the control-plane build-time embedder gate remains the hard backstop. Keep this comment in the code.
- Compute `const blocked = embeddingConfigured === false;`
- Disable submit when blocked: on the submit `<Button>` add `disabled={submitting || blocked}` (keep `loading={submitting}`).
- In the drawer body, when `blocked`, render the gate INSTEAD of `<ManifestEditor>`:
  ```tsx
  {blocked ? (
    <div data-testid="create-agent-embedding-gate">
      <Alert
        type="warning"
        showIcon
        message={t("create_agent.embedding_required_title")}
        description={t("create_agent.embedding_required_desc")}
        style={{ marginBottom: 12 }}
      />
      <Button
        type="primary"
        data-testid="create-agent-embedding-cta"
        onClick={() => {
          onClose();
          navigate("/settings/platform");
        }}
      >
        {t("create_agent.embedding_required_cta")}
      </Button>
    </div>
  ) : (
    <ManifestEditor key={initialYaml} mode="create" initialYaml={initialYaml} onChange={setBuffer} />
  )}
  ```
  Keep the existing hint `<Text>` and error `<Alert>` above this block as-is.

- [ ] **Step 5: Run tests + typecheck**

Run: `cd apps/admin-ui && pnpm vitest run src/components/__tests__/CreateAgentDrawer.test.tsx && pnpm run typecheck`
Expected: PASS.

- [ ] **Step 6: Pre-commit + commit**

Run: `uv run pre-commit run --files apps/admin-ui/src/components/CreateAgentDrawer.tsx apps/admin-ui/src/components/__tests__/CreateAgentDrawer.test.tsx apps/admin-ui/src/i18n/locales/en.ts apps/admin-ui/src/i18n/locales/zh-CN.ts`

```bash
git add apps/admin-ui/src/components/CreateAgentDrawer.tsx apps/admin-ui/src/components/__tests__/CreateAgentDrawer.test.tsx apps/admin-ui/src/i18n/locales/en.ts apps/admin-ui/src/i18n/locales/zh-CN.ts
git commit -m "feat(stream-t): PR E — block+guide agent creation when platform embedding unconfigured"
```

---

## Task 6: getting-started onboarding step + E2E gate spec

**Files:**
- Modify: `docs/runbooks/getting-started.md`
- Create: `apps/admin-ui/e2e/create-agent-embedding-gate.spec.ts`
- Modify: any existing e2e spec that opens `CreateAgentDrawer` (must stub the new status route, else the fetch 404s in the test backend).

- [ ] **Step 1: Add the onboarding step (doc)**

In `docs/runbooks/getting-started.md`, insert a new subsection `### 5.1 配平台 Embedding & Rerank(Stream T)` immediately after §5 (the LLM-key section, before `## 6`). Content:

```markdown
### 5.1 配平台 Embedding & Rerank(Stream T)

新建智能体默认开启**长期记忆**,需要平台级 Embedding 模型(rerank 可选)。**建任何 agent 前必须先配**,否则建 agent 入口会挡住并引导回这里。

浏览器:`/settings/platform`(system_admin)→ **Embedding & Rerank** 区:
1. **Embedding provider** 选一个已配 key 的 provider(只列有 embedding 模型的)→ **Embedding model** 选一个(如通义 `text-embedding-v4`、智谱 `embedding-3`)。
2. (可选)打开 **Rerank**,选 provider/model(如通义 `qwen3-vl-rerank`)。
3. 保存。没配该 provider 的 key 会被拦,先回 §5 配 key。

> 立即生效:embedder 运行期读当前平台配置,改完无需重启。
```

- [ ] **Step 2: Verify doc step (manual read)**

Run: `rg -n "5.1 配平台 Embedding" docs/runbooks/getting-started.md`
Expected: matches; section sits between §5 and §6.

- [ ] **Step 3: Stub the status route in existing drawer-opening e2e specs**

Find specs that open the create-agent drawer: `rg -l "create-agent|agents-create|CreateAgentDrawer" apps/admin-ui/e2e`. In each (notably `manifest-editor.spec.ts` and any agents spec that clicks `agents-create`), add a route stub in `beforeEach` (or before opening the drawer) so the drawer's status fetch resolves configured:

```ts
await page.route("**/v1/platform/embedding-config/status", async (route) => {
  await route.fulfill({ json: { success: true, data: { configured: true }, error: null } });
});
```
> Without this, the drawer fetch fail-opens (renders editor) per Task 5 — but rely on an explicit stub so the tests assert the configured path deterministically.

- [ ] **Step 4: Write the gate E2E spec**

Create `apps/admin-ui/e2e/create-agent-embedding-gate.spec.ts`. Reuse the login + stub conventions from `apps/admin-ui/e2e/manifest-editor.spec.ts` (token paste login, `/v1/agents/schema` stub, `/v1/me`). Two tests:

```ts
import { test, expect } from "@playwright/test";
// ... import SAMPLE_JWT + login helper per manifest-editor.spec.ts ...

test("blocks agent creation when platform embedding is unconfigured", async ({ page }) => {
  await page.route("**/v1/platform/embedding-config/status", (route) =>
    route.fulfill({ json: { success: true, data: { configured: false }, error: null } }),
  );
  // ... stub /v1/me, /v1/agents/schema, /v1/agents list; login; go to /agents ...
  await page.getByTestId("agents-create").click();
  await expect(page.getByTestId("create-agent-embedding-gate")).toBeVisible();
  await expect(page.getByTestId("create-agent-submit")).toBeDisabled();
  await page.getByTestId("create-agent-embedding-cta").click();
  await expect(page).toHaveURL(/\/settings\/platform/);
});

test("allows agent creation when platform embedding is configured", async ({ page }) => {
  await page.route("**/v1/platform/embedding-config/status", (route) =>
    route.fulfill({ json: { success: true, data: { configured: true }, error: null } }),
  );
  // ... same login/stubs; go to /agents ...
  await page.getByTestId("agents-create").click();
  await expect(page.getByTestId("create-agent-embedding-gate")).toHaveCount(0);
  await expect(page.getByTestId("create-agent-submit")).toBeEnabled();
});
```
> Match the exact stub/login helpers already used in the repo's e2e specs (the model-catalog `/v1/models` or `/v1/agents/schema` stubs the drawer needs). Run the existing manifest-editor spec too, to confirm the added status stub didn't break it.

- [ ] **Step 5: Run e2e + axe**

Run: `cd apps/admin-ui && pnpm exec playwright test create-agent-embedding-gate manifest-editor`
Expected: PASS (new spec + unbroken existing spec).

- [ ] **Step 6: Pre-commit + commit**

Run: `uv run pre-commit run --files docs/runbooks/getting-started.md apps/admin-ui/e2e/create-agent-embedding-gate.spec.ts <each modified existing spec>`

```bash
git add docs/runbooks/getting-started.md apps/admin-ui/e2e
git commit -m "docs+test(stream-t): PR E — onboarding embedding step + create-agent gate e2e"
```

---

## Task 7: Backlog sync + final whole-PR gate

**Files:**
- Modify: `docs/ITERATION-PLAN.md`

- [ ] **Step 1: Tick T-E in the backlog**

In `docs/ITERATION-PLAN.md`, change the `- [ ] **T-E ...` line (~line 856) to `- [x]` and append the PR number once merged (leave a placeholder `(PR #___)` to fill at merge, per the iteration-plan-sync rule).

- [ ] **Step 2: Full preflight (whole PR)**

Run (from repo root):
```bash
uv run pre-commit run --all-files
cd services/control-plane && uv run python -m pytest -m "not integration" -q
cd ../../apps/admin-ui && pnpm run typecheck && pnpm vitest run && pnpm run build && pnpm run build-storybook
pnpm exec playwright test
```
Expected: all green. Fix any drift before opening the PR.

- [ ] **Step 3: Commit backlog + open PR**

```bash
git add docs/ITERATION-PLAN.md
git commit -m "docs(stream-t): PR E — tick T-E in backlog"
```
Open PR `stream-t/e-memory-default` → main; body summarizes: memory-on default, 回炉 hint removal, role-agnostic status endpoint, create-agent block+guide, onboarding step. Footer: 🤖 Generated with Claude Code.

---

## Self-Review (controller, before dispatch)

- **Spec coverage:** T-5 ①(create-agent gate = Task 5 + status endpoint Task 1/2), ②(build-time gate retained — no change needed, defensive backstop already in control-plane), ③(default template memory-on = Task 4); T-6(回炉 = Task 3); onboarding(Task 6). ✅
- **Design deviation resolved:** status endpoint (Task 1) is the user-approved fix for "GET is system_admin-only but tenant admins create agents." ✅
- **No protocol change:** `MemorySpec.long_term` schema default stays `None`; only the editor seed changes — existing manifests/tests unaffected. ✅
- **Type consistency:** `getPlatformEmbeddingStatus(): Promise<{configured: boolean}>` used identically in SDK (Task 2) and drawer (Task 5); testid `create-agent-embedding-gate`/`-cta` consistent across Task 5 unit test and Task 6 e2e. ✅
- **Pre-commit:** every task runs `uv run pre-commit run --files` (ruff-format included) before commit — the recurring CI Lint/Pre-commit trap. ✅
