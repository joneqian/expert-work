# Stream V-F — MCP Discovery Endpoints + Management UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** Tenant admins manage their remote MCP servers from `/settings/mcp-servers` — a table with live connection health, an expandable tool list, per-row re-test, add/edit (rotate-token) drawer with a "test connection" button, and delete. Plus the discovery endpoints the V-G agent-form picker will consume.

**Architecture:** Three new endpoints on the existing `/v1/mcp-servers` router (probe-only `test`, `available`, live `{name}/tools` — all reuse the V-C probe / `_build_mcp_client`). A new admin-UI page `SettingsMcpServers` + `CreateMcpServerDrawer`, built by **mirroring the accepted `SettingsTenants` page + `CreateTenantDrawer`** and adapting to MCP realities (health is live + central, tools are the payload, token is write-only). New SDK client `api/mcp-servers.ts`, i18n `mcp_servers.*`/`create_mcp_server.*`, nav + route, Storybook + Playwright + axe.

**UX rules (MCP-specific, user-confirmed):**
- Table loads instantly — do NOT probe all servers on load (a hung server must not stall the page). 状态 column shows enabled/disabled statically.
- **测试** (per row) and **row-expand** trigger a live probe via `GET /{name}/tools` → show 「已连接·N 个工具 / 连不上」 + the tool list (expand shows the names).
- Token is write-only: create takes a token; edit offers "轮换 token" (never displays the current value).
- Guided empty state: explain what MCP servers are for + the add button.
- Layout = table + antd `expandable` rows. Design baseline: antd + lucide-react + dark-first + testid `ms-*` / `cms-*`, mirroring existing settings pages.

**Scope (V-F):** the 3 endpoints + SDK + the management page + drawer + nav/route/i18n + stories/e2e. NOT: the agent-form server picker (V-G consumes `/available` + `/{name}/tools`). Platform-server live tool-listing via `/{name}/tools` is out (tenant-registered servers only; platform servers appear in `/available` by name) — noted as follow-up.

**Branch:** `stream-v/f-discovery-ui` (off `main`, after V-E merged).

**CodeQL guardrails:** no `Protocol`/ABC `...` bodies; no tenant-derived values in `logger.*`.

**Key facts (verified 2026-06-03):**
- Router + DI + `_public`: `services/control-plane/src/control_plane/api/mcp_servers.py` (router `build_mcp_servers_router()`, `_get_store`/`_get_secret_store`, `_public` strips `token_secret_ref`, `require("mcp_server", "read"|"write")`, `probe_remote_mcp`/`McpProbeError` imported).
- Probe-only: `probe_remote_mcp(*, name, transport, url, bearer_token, timeout_s)` → `Sequence[MCPToolDef]` or raises `McpProbeError(code,message)`.
- Token resolve: `from helix_agent.runtime.secret_store import parse_secret_ref` (the path mcp_servers.py already uses) + `secret_store.get(parse_secret_ref(ref))`.
- Tenant allowlist: `TenantConfigService.get(tenant_id=...).mcp_allowlist`. The service is on `app.state` (grep `tenant_config` in app.py for the exact attribute — likely `app.state.tenant_config_service`); if not exposed, read via the existing `_get_*` pattern or the tenant_config store.
- Frontend mirrors: `apps/admin-ui/src/pages/SettingsTenants.tsx`, `apps/admin-ui/src/components/CreateTenantDrawer.tsx`, `apps/admin-ui/src/api/tenants.ts` + `client.ts` (`getJson`/`postJson`/`apiClient`/`ApiError`), `apps/admin-ui/src/router.tsx`, `apps/admin-ui/src/components/Sidebar.tsx`, `apps/admin-ui/src/i18n/locales/{en,zh-CN}.ts` (`TranslationKeys`), `apps/admin-ui/src/pages/SettingsOps.stories.tsx`, `apps/admin-ui/e2e/tenants.spec.ts` (+ `e2e/fixtures`).
- admin-ui CI jobs: typecheck+test+build, storybook build, Playwright+axe — all must pass.

---

## File Structure
**Create:** `apps/admin-ui/src/api/mcp-servers.ts`, `apps/admin-ui/src/pages/SettingsMcpServers.tsx`, `apps/admin-ui/src/components/CreateMcpServerDrawer.tsx`, `apps/admin-ui/src/pages/SettingsMcpServers.stories.tsx`, `apps/admin-ui/e2e/mcp-servers.spec.ts`.
**Modify:** `services/control-plane/src/control_plane/api/mcp_servers.py` (+ test), `apps/admin-ui/src/router.tsx`, `apps/admin-ui/src/components/Sidebar.tsx`, `apps/admin-ui/src/i18n/locales/en.ts` + `zh-CN.ts`.

---

## Task 1: Backend — `test`, `available`, `{name}/tools` endpoints

**Files:**
- Modify: `services/control-plane/src/control_plane/api/mcp_servers.py`
- Modify: `services/control-plane/tests/test_mcp_servers_api.py`

- [ ] **Step 1: Read the router** to confirm imports + the `TenantConfigService` app.state accessor (grep `tenant_config` in `app.py`; if no service accessor exists for this router, add `_get_tenant_config_service(request)` returning `getattr(request.app.state, "tenant_config_service", None)`).

- [ ] **Step 2: Write the failing API tests**

Add to `services/control-plane/tests/test_mcp_servers_api.py` (mirror the existing fixtures + `_fake_probe_ok`/`_fake_probe_fail` + monkeypatch of `control_plane.api.mcp_servers.probe_remote_mcp`):

```python
@pytest.mark.asyncio
async def test_test_connection_probes_without_persisting(monkeypatch) -> None:
    app, admin_headers, _ = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        r = await client.post(
            "/v1/mcp-servers/test",
            json={"transport": "streamable_http", "url": "https://mcp.example.com/mcp", "auth_type": "none"},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["data"]["tool_count"] == 1
        # nothing persisted
        lst = await client.get("/v1/mcp-servers", headers=admin_headers)
        assert lst.json()["data"] == []


@pytest.mark.asyncio
async def test_test_connection_failure_returns_422(monkeypatch) -> None:
    app, admin_headers, _ = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_fail)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        r = await client.post(
            "/v1/mcp-servers/test",
            json={"transport": "sse", "url": "https://down.example.com/sse", "auth_type": "none"},
            headers=admin_headers,
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "MCP_SERVER_PROBE_FAILED"


@pytest.mark.asyncio
async def test_available_lists_platform_allowlist_and_tenant_servers(monkeypatch) -> None:
    app, admin_headers, tenant_id = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    # seed a tenant server + a platform allowlist entry (set tenant_config.mcp_allowlist).
    # (Use the same seeding the other tests use; if the in-memory tenant_config
    #  defaults to empty allowlist, set it via the tenant_config store/service.)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        await client.post(
            "/v1/mcp-servers",
            json={"name": "github", "transport": "sse", "url": "https://x.example.com/sse", "auth_type": "none"},
            headers=admin_headers,
        )
        r = await client.get("/v1/mcp-servers/available", headers=admin_headers)
        assert r.status_code == 200
        names = {item["name"] for item in r.json()["data"]}
        assert "github" in names  # tenant server present
        sources = {item["name"]: item["source"] for item in r.json()["data"]}
        assert sources["github"] == "tenant"


@pytest.mark.asyncio
async def test_server_tools_lists_live_tools(monkeypatch) -> None:
    app, admin_headers, _ = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        await client.post(
            "/v1/mcp-servers",
            json={"name": "github", "transport": "sse", "url": "https://x.example.com/sse", "auth_type": "none"},
            headers=admin_headers,
        )
        r = await client.get("/v1/mcp-servers/github/tools", headers=admin_headers)
        assert r.status_code == 200
        assert r.json()["data"][0]["name"] == "create_issue"


@pytest.mark.asyncio
async def test_server_tools_unknown_404(monkeypatch) -> None:
    app, admin_headers, _ = await _make_app_with_admin()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        r = await client.get("/v1/mcp-servers/nope/tools", headers=admin_headers)
        assert r.status_code == 404
```

(Ensure `_fake_probe_ok` returns `[MCPToolDef(name="create_issue", ...)]`. The `available` test's platform-allowlist seeding may need the tenant_config store — adapt to how the in-memory app exposes it; if setting the allowlist is awkward in the test harness, assert only the tenant-server half and note the platform half is covered by a unit test.)

- [ ] **Step 3: Run → fail.** `cd /Users/mac/src/github/jone_qian/helix-agent && uv run python -m pytest services/control-plane/tests/test_mcp_servers_api.py -q -k "test_connection or available or server_tools"` → 404s / missing routes.

- [ ] **Step 4: Implement the three endpoints** in `build_mcp_servers_router()`:

```python
class TestConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transport: McpServerTransport
    url: str = Field(min_length=1)
    auth_type: McpServerAuthType = "none"
    token: SecretStr | None = None
    timeout_s: float = Field(default=_DEFAULT_TIMEOUT_S, gt=0, le=300)


@router.post("/test")
async def test_mcp_connection(
    payload: TestConnectionRequest,
    principal: Annotated[Principal, Depends(require("mcp_server", "write"))],
) -> dict[str, object]:
    if payload.auth_type == "bearer" and (
        payload.token is None or not payload.token.get_secret_value().strip()
    ):
        raise HTTPException(
            status_code=422,
            detail={"code": "MCP_SERVER_TOKEN_REQUIRED", "message": "bearer auth requires a non-empty token"},
        )
    raw = payload.token.get_secret_value() if payload.token is not None else None
    try:
        tools = await probe_remote_mcp(
            name="test", transport=payload.transport, url=payload.url,
            bearer_token=raw, timeout_s=payload.timeout_s,
        )
    except McpProbeError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": exc.message}) from exc
    return {"success": True, "data": {"tool_count": len(tools)}, "error": None}


@router.get("/available")
async def list_available_mcp_servers(
    principal: Annotated[Principal, Depends(require("mcp_server", "read"))],
    store: Annotated[TenantMcpServerStore, Depends(_get_store)],
    tenant_config_service: Annotated[object, Depends(_get_tenant_config_service)],
) -> dict[str, object]:
    tenant_id = principal.tenant_id
    available: list[dict[str, object]] = []
    # platform servers the tenant is allowed (names only — the platform pool
    # is operator-controlled; tools/health for these are out of V-F scope).
    if tenant_config_service is not None:
        try:
            cfg = await tenant_config_service.get(tenant_id=tenant_id)  # type: ignore[attr-defined]
            for name in cfg.mcp_allowlist:
                available.append({"name": name, "source": "platform"})
        except Exception:  # noqa: BLE001 — no tenant_config row → no platform servers
            logger.info("mcp_servers.available.no_tenant_config")
    for rec in await store.list_for_tenant(tenant_id=tenant_id):
        available.append({"name": rec.name, "source": "tenant", "enabled": rec.enabled})
    return {"success": True, "data": available, "error": None}


@router.get("/{name}/tools")
async def list_mcp_server_tools(
    name: Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")],
    principal: Annotated[Principal, Depends(require("mcp_server", "read"))],
    store: Annotated[TenantMcpServerStore, Depends(_get_store)],
    secret_store: Annotated[SecretStore, Depends(_get_secret_store)],
) -> dict[str, object]:
    record = await store.get(tenant_id=principal.tenant_id, name=name)
    if record is None:
        raise HTTPException(status_code=404, detail={"code": "MCP_SERVER_NOT_FOUND", "message": "not found"})
    raw: str | None = None
    if record.auth_type == "bearer" and record.token_secret_ref is not None:
        raw = await secret_store.get(parse_secret_ref(record.token_secret_ref))
    try:
        tools = await probe_remote_mcp(
            name=record.name, transport=record.transport, url=record.url,
            bearer_token=raw, timeout_s=record.timeout_s,
        )
    except McpProbeError as exc:
        raise HTTPException(status_code=502, detail={"code": exc.code, "message": exc.message}) from exc
    return {
        "success": True,
        "data": [{"name": t.name, "description": t.description or ""} for t in tools],
        "error": None,
    }
```

Add imports as needed (`Path` from fastapi, `parse_secret_ref` from `helix_agent.runtime.secret_store`, `SecretStr` already imported). **Route ordering**: register `/test` and `/available` BEFORE `/{name}` PATCH/DELETE? FastAPI matches `/test` and `/available` as literal paths fine even with `/{name}` present, but `/{name}/tools` is a distinct path so no conflict. Verify no path shadows the existing `PATCH/DELETE /{name}`.

- [ ] **Step 5: Run → pass + lint.** Tests green; `uv run ruff check services/control-plane && uv run ruff format --check services/control-plane`. Drop unused `# noqa` (BLE001 not enabled).

- [ ] **Step 6: Commit** `feat(stream-v): mcp-servers discovery endpoints (test/available/{name}/tools) (V-F)`.

---

## Task 2: SDK client `api/mcp-servers.ts`

**Files:** Create `apps/admin-ui/src/api/mcp-servers.ts`.

- [ ] **Step 1: Read `apps/admin-ui/src/api/tenants.ts` + `client.ts`** for the exact `getJson`/`postJson`/`apiClient.delete`/`ApiError` idiom + envelope unwrap.

- [ ] **Step 2: Write the client** (mirror tenants.ts):

```typescript
import { apiClient, getJson, postJson } from "./client";

export type McpTransport = "sse" | "streamable_http";
export type McpAuthType = "none" | "bearer";

export interface McpServer {
  id: string;
  name: string;
  transport: McpTransport;
  url: string;
  auth_type: McpAuthType;
  timeout_s: number;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}
export interface CreateMcpServerBody {
  name: string;
  transport: McpTransport;
  url: string;
  auth_type: McpAuthType;
  token?: string;
  timeout_s?: number;
}
export interface UpdateMcpServerBody {
  url?: string;
  token?: string;
  timeout_s?: number;
  enabled?: boolean;
}
export interface McpTool { name: string; description: string; }
export interface AvailableMcpServer { name: string; source: "platform" | "tenant"; enabled?: boolean; }
export interface TestConnectionBody {
  transport: McpTransport; url: string; auth_type: McpAuthType; token?: string; timeout_s?: number;
}

export const listMcpServers = (): Promise<McpServer[]> => getJson<McpServer[]>("/v1/mcp-servers");
export const createMcpServer = (b: CreateMcpServerBody): Promise<McpServer> =>
  postJson<McpServer>("/v1/mcp-servers", b);
export const updateMcpServer = (name: string, b: UpdateMcpServerBody): Promise<McpServer> =>
  apiClient.patch(`/v1/mcp-servers/${encodeURIComponent(name)}`, b).then((r) => r.data.data);
export const deleteMcpServer = (name: string): Promise<void> =>
  apiClient.delete(`/v1/mcp-servers/${encodeURIComponent(name)}`).then(() => undefined);
export const testMcpConnection = (b: TestConnectionBody): Promise<{ tool_count: number }> =>
  postJson<{ tool_count: number }>("/v1/mcp-servers/test", b);
export const listMcpServerTools = (name: string): Promise<McpTool[]> =>
  getJson<McpTool[]>(`/v1/mcp-servers/${encodeURIComponent(name)}/tools`);
export const listAvailableMcpServers = (): Promise<AvailableMcpServer[]> =>
  getJson<AvailableMcpServer[]>("/v1/mcp-servers/available");
```

Verify the PATCH idiom against how tenants.ts/members.ts call PATCH (the repo may have a `patchJson` helper — use it if present; else `apiClient.patch(...).then(r => r.data.data)` matching the envelope). Match the real client.

- [ ] **Step 3: typecheck + commit.** `cd apps/admin-ui && pnpm run typecheck` (or the repo's command). Commit `feat(stream-v): admin-ui mcp-servers API client (V-F)`.

---

## Task 3: i18n keys (en + zh-CN)

**Files:** Modify `apps/admin-ui/src/i18n/locales/en.ts` + `zh-CN.ts`.

- [ ] **Step 1: Read both files** + the `TranslationKeys` interface. Add a `nav.mcp_servers` key and two namespaces. Add to the `TranslationKeys` interface (en.ts is the source of truth) and the same keys to zh-CN.ts (tsc enforces parity).

EN values (zh-CN equivalents in parens — use proper 简体中文):
```
nav.mcp_servers: "MCP Servers"  (MCP 服务器)
mcp_servers: {
  page_title: "MCP Servers"  (MCP 服务器)
  subtitle: "Manage the remote MCP servers your agents can call tools from"  (管理本租户可接入的远程 MCP 服务器，agent 可调用其工具)
  add: "Add server"  (添加 server)
  col_name / col_transport / col_url / col_auth / col_status / col_tools / col_actions
  status_enabled: "Enabled"  (已启用)  status_disabled: "Disabled"  (已停用)
  test: "Test"  (测试)  edit: "Edit"  (编辑)  delete: "Delete"  (删除)
  testing: "Testing…"  (测试中…)
  connected: "Connected · {{count}} tools"  (已连接 · {{count}} 个工具)
  unreachable: "Unreachable"  (连不上)
  tools_loading: "Loading tools…"  (加载工具中…)
  no_tools: "No tools advertised"  (无工具)
  empty_title: "No MCP servers yet"  (还没有接入 MCP server)
  empty_hint: "MCP servers let your agents call external tools like GitHub or Linear."  (MCP server 让 agent 能调用 GitHub、Linear 等外部工具。)
  delete_confirm: "Delete server {{name}}?"  (删除 server {{name}}？)
  failed_to_load: "Failed to load MCP servers"  (加载 MCP server 失败)
}
create_mcp_server: {
  add_title: "Add MCP server"  (添加 MCP server)
  edit_title: "Edit MCP server"  (编辑 MCP server)
  field_name / field_transport / field_url / field_auth / field_token / field_timeout (labels)
  token_hint_create: "Pasted once, stored encrypted — never shown again"  (粘贴一次，加密存储，不再回显)
  token_hint_edit: "Leave blank to keep the current token; enter a new value to rotate"  (留空保持当前 token；填新值即轮换)
  test_connection: "Test connection"  (测试连接)
  test_ok: "Connected · {{count}} tools"  (连接成功 · {{count}} 个工具)
  test_failed: "Connection failed"  (连接失败)
  name_required / url_required / url_invalid / token_required (validation msgs)
  submit_add: "Add"  (添加)  submit_save: "Save"  (保存)
}
```
(Use i18next interpolation `{{count}}`/`{{name}}` consistent with how existing keys do counts — check an existing pluralized/interpolated key and match.)

- [ ] **Step 2: typecheck (parity) + commit.** `pnpm run typecheck` must pass (proves en/zh-CN parity). Commit `feat(stream-v): i18n keys for mcp-servers page (V-F)`.

---

## Task 4: `CreateMcpServerDrawer` (add/edit + test-connection)

**Files:** Create `apps/admin-ui/src/components/CreateMcpServerDrawer.tsx`.

- [ ] **Step 1: Read `apps/admin-ui/src/components/CreateTenantDrawer.tsx` in full** and mirror its structure (Drawer 520px, `Form.useForm`, vertical layout, footer Cancel/Submit, ApiError handling, reset-on-close, success handling). Adapt for MCP:
  - Props: `{ open, onClose, onSaved, editing?: McpServer | null }` (edit mode pre-fills name [disabled], transport, url, auth, timeout; token blank with the edit hint).
  - Fields: `name` (required, pattern `^[a-z0-9][a-z0-9_-]{0,63}$`, disabled when editing), `transport` (Select: sse / streamable_http), `url` (required, must start http/https), `auth_type` (Select: none / bearer), `token` (Input.Password, shown only when auth=bearer; required-when-bearer on create, optional on edit with the rotate hint), `timeout_s` (InputNumber 1–300, default 30).
  - **测试连接 button** (testid `cms-test`): calls `testMcpConnection({transport,url,auth_type,token,timeout_s})` using the current form values (validate those fields first); shows a success `Alert` (连接成功 · N tools) or error `Alert` (the ApiError message). Disabled while testing; spinner.
  - Submit: create → `createMcpServer(body)`; edit → `updateMcpServer(name, {url, token?, timeout_s, enabled})` (omit token if blank). On success → `onSaved()` + close.
  - testids: `cms-form`, `cms-name`, `cms-transport`, `cms-url`, `cms-auth`, `cms-token`, `cms-timeout`, `cms-test`, `cms-submit`, `cms-test-result`.
  - i18n: `create_mcp_server.*`. ApiError → `${err.code}: ${err.message}` alert (mirror CreateTenantDrawer).

- [ ] **Step 2: typecheck + commit** `feat(stream-v): CreateMcpServerDrawer with test-connection (V-F)`.

---

## Task 5: `SettingsMcpServers` page (table + expand + test + actions) + route + nav

**Files:** Create `apps/admin-ui/src/pages/SettingsMcpServers.tsx`; modify `router.tsx`, `Sidebar.tsx`.

- [ ] **Step 1: Read `apps/admin-ui/src/pages/SettingsTenants.tsx` in full** and mirror (PageHeader with a lucide icon + title + subtitle + primary "Add server" button; antd Table; loading/error/empty states; `reload()` after mutations; testids). Adapt for MCP:
  - Columns: 名称 (`name`) / 传输 (`transport` Tag) / URL (ellipsis) / 认证 (`auth_type` Tag) / 状态 / 工具 / 操作.
  - **状态 column**: static `enabled` badge (已启用 green / 已停用 default) by default. Per row, hold transient live-probe state: `idle | testing | connected(count) | unreachable`. When the user clicks **测试** (testid `ms-test-{name}`) OR expands the row, call `listMcpServerTools(name)` → on success set connected+count (and cache the tool list for the expansion); on error (502) set unreachable. Show a spinner while testing.
  - **工具 column**: shows the live count once probed (`—` until then).
  - **expandable rows** (antd Table `expandable`): expanding a row triggers the same `listMcpServerTools(name)` (if not already loaded) and renders the tool names (chips/list) + descriptions, with `tools_loading` / `no_tools` states. testid `ms-tools-{name}`.
  - **操作**: 测试 (`ms-test-{name}`), 编辑 (`ms-edit-{name}` → opens drawer in edit mode), 停用/启用 (toggle `enabled` via `updateMcpServer(name,{enabled})`, testid `ms-toggle-{name}`), 删除 (`ms-delete-{name}` → `Popconfirm` → `deleteMcpServer(name)`; on 409 MCP_SERVER_IN_USE show the message listing referencing agents).
  - **Empty state**: `empty_title` + `empty_hint` + the add button (testid `ms-empty`).
  - **Add server** button (testid `ms-add`) opens `CreateMcpServerDrawer` (create mode). `onSaved` → `reload()`.
  - Root testid `ms-root`, table `ms-table`, error `ms-error`.

- [ ] **Step 2: Route** — in `router.tsx` add `<Route path="/settings/mcp-servers" element={<SettingsMcpServers />} />` (mirror the tenants route; lazy-import if that's the pattern). **Nav** — in `Sidebar.tsx` `SETTINGS_ITEMS` add `{ key, labelKey: "nav.mcp_servers", icon: <Plug size={16} strokeWidth={1.5} /> (or another fitting lucide icon), path: "/settings/mcp-servers" }` near the other settings entries. Gate to the same role visibility as the other tenant-admin settings pages (check how SettingsTenants is gated).

- [ ] **Step 3: typecheck + build + commit.** `cd apps/admin-ui && pnpm run typecheck && pnpm run build`. Commit `feat(stream-v): /settings/mcp-servers management page (V-F)`.

---

## Task 6: Storybook + Playwright + axe

**Files:** Create `apps/admin-ui/src/pages/SettingsMcpServers.stories.tsx`, `apps/admin-ui/e2e/mcp-servers.spec.ts`.

- [ ] **Step 1: Stories** — mirror `apps/admin-ui/src/pages/SettingsOps.stories.tsx`: `Empty` (envelope `{success,true,data:[]}`) and `WithServers` (2-3 servers, mixed transport/auth/enabled). Use the existing fixture decorator (JWT + apiClient adapter).

- [ ] **Step 2: e2e** — mirror `apps/admin-ui/e2e/tenants.spec.ts` (+ `e2e/fixtures`). Mock `**/v1/mcp-servers*` GET → server list; mock `**/v1/mcp-servers/*/tools` → tool list; mock POST `/test`. Tests: (a) lists servers (`ms-table` visible, a server name visible); (b) expand a row shows its tools (`ms-tools-{name}`); (c) 测试 shows connected status; (d) open add drawer + 测试连接 shows success; (e) axe: `await expectNoA11yViolations(page, "settings-mcp-servers")`.

- [ ] **Step 3: Run locally if possible** (`pnpm run storybook:build`, `pnpm run test:e2e` — or rely on CI if the local Playwright browsers aren't installed; at minimum ensure stories typecheck + the spec compiles). Commit `test(stream-v): mcp-servers page stories + e2e + axe (V-F)`.

---

## Task 7: Preflight + push + PR

- [ ] **Step 1: Backend tests** — `uv run python -m pytest -m "not integration" services/control-plane -q` → pass.
- [ ] **Step 2: Backend lint/type** — `uv run ruff check . && uv run ruff format --check .`; `uv run mypy packages services/audit-backup-worker/src services/event-log-archive-job/src services/orchestrator/src services/retention-cleanup-job/src` (control-plane/src not in gate, but run it for the router).
- [ ] **Step 3: Frontend** — `cd apps/admin-ui && pnpm run typecheck && pnpm run test && pnpm run build && pnpm run storybook:build` (match the repo's actual scripts — check package.json). All green.
- [ ] **Step 4: uv.lock drift** — none expected (no Python deps; pnpm-lock only if a dep added — none expected).
- [ ] **Step 5: Push + PR** (`stream-v/f-discovery-ui` → main; title `feat(stream-v): PR F — MCP discovery endpoints + management UI`; body summarizing endpoints + the table-with-expandable-rows page + the MCP-specific UX + tests). Note the platform-server-tools follow-up.
- [ ] **Step 6: Poll CI green** (admin-ui Playwright+axe + storybook + typecheck/test/build, plus the backend jobs). Resolve any CodeQL threads.

---

## Self-Review (plan author)
**Spec coverage (V-6 discovery + V-9 management UI):** `/test` + `/available` + `/{name}/tools` (Task 1) ✓; management page table+expand+test+rotate+empty (Task 5) ✓; drawer with test-connection (Task 4) ✓; SDK (Task 2), i18n parity (Task 3), nav/route (Task 5), stories+e2e+axe (Task 6) ✓.
**MCP-specific (user-confirmed):** live health via on-demand probe (not on load), tool drill-down on expand, per-row re-test, write-only token with rotate, guided empty state, table+expandable layout — all in Tasks 4/5.
**Placeholder note:** Frontend tasks intentionally instruct "read + mirror the accepted SettingsTenants/CreateTenantDrawer and adapt for MCP" rather than transcribing full TSX — the live accepted pages are the source of truth for product-grade fidelity, and the MCP adaptations (columns, expand→tools, test, rotate, empty) + testids + i18n keys + endpoint contracts are all specified. Backend (Task 1) has full code.
**Type/contract consistency:** endpoint shapes (`/test`→{tool_count}, `/available`→[{name,source,enabled?}], `/{name}/tools`→[{name,description}]) match the SDK client types and the page/drawer consumers.
**Risk:** the `/available` platform-allowlist seeding in tests may be awkward on the in-memory harness — Task 1 Step 2 says assert the tenant half + note. The `/{name}/tools` returns 502 (not 422) for a reachable-but-failing live probe (the server WAS registered/probed OK at create; a later failure is a gateway error) — consumers (page) map 502→unreachable.
