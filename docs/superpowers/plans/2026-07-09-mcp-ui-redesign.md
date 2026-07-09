# MCP 界面与术语重设计 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 MCP 三个入口从"重叠混乱"整成一致 IA:统一 MCP 服务器列表(平台+自定义一张表)、三个"启用"改名(发布/启用/运行)、OAuth 独立成"我的 MCP 授权"页、目录页术语对齐;修复"启用平台服务器后列表看不到"的功能断层。

**Architecture:** 后端仅 `/available` 平台行加 3 字段(`display_name`/`auth_type`/`catalog_id`);前端统一列表两拉合并(`listMcpServers` 全明细 + `/available` 平台行),纯函数 `buildUnifiedRows` 合成判别联合行;添加抽屉 toggle 回调触发父页刷新;OAuth/目录页纯 i18n 文案。

**Tech Stack:** control-plane (FastAPI + pytest)、admin-ui (React + antd + react-i18next + vitest)。

## Global Constraints

- **术语字典(所有文案照此):** 主词 = **MCP 服务器**;来源 = **平台** / **自定义**;三动作互不撞车 —— 平台发布模板=**发布**(已发布/已下架)、租户纳入=**启用**(已启用/未启用)、自定义运行开关=**运行**(运行中/已停用)。
- **i18n 双语同改:** 每个新键改三处(`en.ts` 的 `TranslationKeys` 接口 + `en` 常量 + `zh-CN.ts` 常量);仅改值的重命名改两处(en 常量 + zh 常量)。`i18n` parity 测试(`zh-CN` 键集 === `en`)必须绿。
- **平台行 catalog 未命中(allowlist 残留)必须降级为 `{name, source}`,不 500。**
- **不碰:** 数据模型 / 迁移 / `tenant_mcp_server.catalog_id` 回填 / 运行时 OAuth 池 / RLS / 目录 CRUD 功能。
- **CI 闸:** 后端 `uv run ruff format`、`uv run pre-commit run ruff --files <改动文件>`、`uv run mypy packages services/control-plane/src`;前端 `pnpm --filter admin-ui typecheck`、`pnpm --filter admin-ui test`、`pnpm --filter admin-ui lint`。
- 每个自定义服务器行的"运行开关"按钮文案用 `act_run`/`act_stop`,**不得**复用 `status_*`(避免与"启用"混淆)。

---

### Task 1: 后端 `/available` 平台行增强

**Files:**
- Modify: `services/control-plane/src/control_plane/api/mcp_servers.py`(`list_available_mcp_servers`,当前 717–751)
- Test: `services/control-plane/tests/test_mcp_servers_api.py`

**Interfaces:**
- Produces: `GET /v1/mcp-servers/available` 的 `source:"platform"` 行,catalog 命中时携带 `display_name: str`、`auth_type: str`、`catalog_id: str`;未命中时仍为 `{"name": str, "source": "platform"}`。`source:"tenant"` 行形状不变。

- [ ] **Step 1: 写失败测试**

在 `test_mcp_servers_api.py` 末尾追加(复用已有 `_seed_catalog_entry` / `_enable_for_tenant` / `_make_app_with_admin`,已 import `McpConnectorCatalogUpsert`):

```python
@pytest.mark.asyncio
async def test_available_platform_row_enriched(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tenant-enabled platform server carries display_name/auth_type/catalog_id."""
    app, admin_headers, tenant_id = await _make_app_with_admin()
    await _seed_catalog_entry(
        app,
        McpConnectorCatalogUpsert(
            name="amap-maps",
            display_name="高德地图",
            transport="streamable_http",
            url_template="https://mcp.amap.test/mcp",
            auth_type="oauth2",
            oauth_client_id="cid",
        ),
    )
    await _enable_for_tenant(app, tenant_id, "amap-maps")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        r = await client.get("/v1/mcp-servers/available", headers=admin_headers)
    assert r.status_code == 200, r.text
    rows = {item["name"]: item for item in r.json()["data"]}
    row = rows["amap-maps"]
    assert row["source"] == "platform"
    assert row["display_name"] == "高德地图"
    assert row["auth_type"] == "oauth2"
    assert "catalog_id" in row and row["catalog_id"]


@pytest.mark.asyncio
async def test_available_platform_row_degrades_when_catalog_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale allowlist name (no catalog entry) degrades to {name, source} — no 500."""
    app, admin_headers, tenant_id = await _make_app_with_admin()
    await _enable_for_tenant(app, tenant_id, "ghost-server")  # not seeded in catalog
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        r = await client.get("/v1/mcp-servers/available", headers=admin_headers)
    assert r.status_code == 200, r.text
    rows = {item["name"]: item for item in r.json()["data"]}
    assert rows["ghost-server"] == {"name": "ghost-server", "source": "platform"}
```

> `McpConnectorCatalogUpsert` 的 `oauth2` 需 `oauth_client_id`(见 `_seed_catalog_entry` 用法与 zh 文案 `oauth_client_id_required`)。若字段名不符,以 `expert_work.protocol.McpConnectorCatalogUpsert` 定义为准。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd services/control-plane && uv run pytest tests/test_mcp_servers_api.py::test_available_platform_row_enriched tests/test_mcp_servers_api.py::test_available_platform_row_degrades_when_catalog_missing -v`
Expected: FAIL —— enriched 断言 `KeyError: 'display_name'`;degrade 断言目前也 pass(裸行)但 enriched 必失败。

- [ ] **Step 3: 实现增强**

把 `list_available_mcp_servers` 里 allowlist 那段(717–751)改为:

```python
    @router.get("/available")
    async def list_available_mcp_servers(
        principal: Annotated[Principal, Depends(require("mcp_server", "read"))],
        store: Annotated[TenantMcpServerStore, Depends(_get_store)],
        catalog_store: Annotated[McpConnectorCatalogStore, Depends(_get_catalog_store)],
        tenant_config_service: Annotated[object, Depends(_get_tenant_config_service)],
    ) -> dict[str, object]:
        tenant_id = principal.tenant_id
        available: list[dict[str, object]] = []
        allowlist: list[str] = []
        if tenant_config_service is not None:
            try:
                cfg = await tenant_config_service.get(tenant_id=tenant_id)  # type: ignore[attr-defined]
                allowlist = list(cfg.mcp_allowlist)
            except Exception:
                logger.info("mcp_servers.available.no_tenant_config")
        tenant_rows = await store.list_for_tenant(tenant_id=tenant_id)

        # Fetch the platform catalog once if either the allowlist (platform rows,
        # enriched below) or any catalog-bound tenant row needs it. Catalog is
        # NULL-tenant → bypass RLS (W-8).
        need_catalog = bool(allowlist) or any(
            getattr(r, "catalog_id", None) is not None for r in tenant_rows
        )
        by_name: dict[str, object] = {}
        catalog_names: dict[UUID, str] = {}
        if need_catalog:
            async with bypass_rls_session():
                entries = await catalog_store.list()
            by_name = {e.name: e for e in entries}
            catalog_names = {e.id: e.name for e in entries}

        for name in allowlist:
            entry = by_name.get(name)
            if entry is None:  # stale allowlist → catalog entry deleted; degrade
                available.append({"name": name, "source": "platform"})
                continue
            available.append(
                {
                    "name": name,
                    "source": "platform",
                    "display_name": entry.display_name,  # type: ignore[attr-defined]
                    "auth_type": entry.auth_type,  # type: ignore[attr-defined]
                    "catalog_id": str(entry.id),  # type: ignore[attr-defined]
                }
            )

        for rec in tenant_rows:
            row: dict[str, object] = {
                "name": rec.name,
                "source": "tenant",
                "enabled": rec.enabled,
            }
            catalog_id = getattr(rec, "catalog_id", None)
            if catalog_id is not None:
                row["catalog_id"] = str(catalog_id)
                row["catalog_name"] = catalog_names.get(catalog_id)
            available.append(row)
        return {"success": True, "data": available, "error": None}
```

> `by_name: dict[str, object]` 让 `entry` 为 `object`,故 `entry.display_name` 等加 `# type: ignore[attr-defined]`(与本文件既有风格一致)。若嫌 ignore,可 `from expert_work.persistence... import McpConnectorCatalogRecord` 精确标注 `by_name: dict[str, McpConnectorCatalogRecord]` 并去掉 ignore —— 二选一,以 mypy 绿为准。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd services/control-plane && uv run pytest tests/test_mcp_servers_api.py -v -k "available"`
Expected: PASS(含既有 `test_available_lists_tenant_servers` 不回归)。

- [ ] **Step 5: 闸 + 提交**

```bash
cd services/control-plane
uv run ruff format src/control_plane/api/mcp_servers.py tests/test_mcp_servers_api.py
uv run pre-commit run ruff --files src/control_plane/api/mcp_servers.py tests/test_mcp_servers_api.py
uv run mypy packages services/control-plane/src   # 从仓库根跑;若路径不符按 CI 配置
cd /Users/mac/src/github/jone_qian/expert-work
git add services/control-plane/src/control_plane/api/mcp_servers.py services/control-plane/tests/test_mcp_servers_api.py
git commit -m "feat(mcp): /available 平台行携带 display_name/auth_type/catalog_id"
```

---

### Task 2: i18n 重命名 + 新键(en + zh-CN)

**Files:**
- Modify: `apps/admin-ui/src/i18n/locales/en.ts`(`TranslationKeys` 接口 + `en` 常量)
- Modify: `apps/admin-ui/src/i18n/locales/zh-CN.ts`(`zhCN` 常量)
- Test: `apps/admin-ui/src/i18n/__tests__/i18n.test.tsx`

**Interfaces:**
- Produces 新键(`mcp_servers` 命名空间):`source_platform`、`source_custom`、`platform_hosted`、`status_enabled_platform`、`needs_authorize`、`remove`、`remove_confirm`、`act_run`、`act_stop`。供 Task 3/5 使用。

- [ ] **Step 1: 写失败测试**

在 `i18n.test.tsx` 的 `describe("locale modules", …)` 内追加(锁术语字典的代表值 + 新键存在):

```tsx
import zhCN from "../locales/zh-CN";  // 已 import,复用

it("canonical MCP terminology is applied (zh)", () => {
  expect(zhCN.nav.mcp_oauth).toBe("我的 MCP 授权");
  expect(zhCN.mcp_servers.add).toBe("添加 MCP 服务器");
  expect(zhCN.mcp_servers.status_enabled).toBe("运行中");
  expect(zhCN.mcp_catalog.col_enabled).toBe("发布状态");
  expect(zhCN.mcp_oauth.page_title).toBe("我的 MCP 授权");
  // 新键存在
  expect(zhCN.mcp_servers.source_platform).toBe("平台");
  expect(zhCN.mcp_servers.needs_authorize).toBe("需你授权");
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pnpm --filter admin-ui test -- i18n`
Expected: FAIL —— 值仍是旧文案 / 新键为 `undefined`;`tsc` 亦报新键不在 `TranslationKeys`。

- [ ] **Step 3: 改接口 + en 常量 + zh 常量**

**(a) `en.ts` 的 `TranslationKeys` 接口 `mcp_servers` 块(约 1039–1058)** 追加字段声明:

```typescript
    source_platform: string;
    source_custom: string;
    platform_hosted: string;
    status_enabled_platform: string;
    needs_authorize: string;
    remove: string;
    remove_confirm: string;
    act_run: string;
    act_stop: string;
```

**(b) `en.ts` 的 `en` 常量 —— 重命名值 + 新键:**

| 键路径 | 旧值 | 新值 |
|---|---|---|
| `nav.mcp_oauth`(2318) | `"My MCP Connections"` | `"My MCP Authorizations"` |
| `mcp_servers.add`(3409) | `"Add server"` | `"Add MCP server"` |
| `mcp_servers.status_enabled`(3417) | `"Enabled"` | `"Running"` |
| `mcp_servers.status_disabled`(3418) | `"Disabled"` | `"Disabled"`(不变) |
| `mcp_catalog.col_enabled`(3498) | `"Enabled"` | `"Publish status"` |
| `mcp_catalog.browser_empty`(3569) | `"No connectors are available for your plan yet."` | `"No servers are available for your plan yet."` |
| `mcp_oauth.page_title`(3599) | `"My MCP Connections"` | `"My MCP Authorizations"` |
| `mcp_oauth.page_subtitle`(3600) | `"OAuth connectors you've authorized with your own account."` | `"MCP servers you've authorized with your own account."` |
| `mcp_oauth.empty`(3602) | `"You haven't connected any OAuth MCP connectors yet."` | `"You haven't authorized any MCP servers yet."` |
| `mcp_oauth.col_connector`(3603) | `"Connector"` | `"MCP server"` |

在 `en` 常量的 `mcp_servers` 块内追加新键:

```typescript
    source_platform: "Platform",
    source_custom: "Custom",
    platform_hosted: "Platform-hosted",
    status_enabled_platform: "Enabled (platform)",
    needs_authorize: "Needs your authorization",
    remove: "Remove",
    remove_confirm: "Remove platform server {{name}}?",
    act_run: "Run",
    act_stop: "Stop",
```

**(c) `zh-CN.ts` 的 `zhCN` 常量 —— 重命名值 + 新键:**

| 键路径 | 旧值 | 新值 |
|---|---|---|
| `nav.mcp_oauth`(63) | `"我的 MCP 连接"` | `"我的 MCP 授权"` |
| `mcp_servers.add`(1108) | `"添加 server"` | `"添加 MCP 服务器"` |
| `mcp_servers.status_enabled`(1116) | `"已启用"` | `"运行中"` |
| `mcp_servers.status_disabled`(1117) | `"已停用"` | `"已停用"`(不变) |
| `mcp_catalog.col_enabled`(1191) | `"启用"` | `"发布状态"` |
| `mcp_catalog.browser_empty`(1261) | `"当前套餐下暂无可用连接器。"` | `"当前套餐下暂无可用服务器。"` |
| `mcp_catalog.advanced_hint`(1265) | `"目录里没有想要的连接器？"` | `"目录里没有想要的服务器？"` |
| `mcp_oauth.page_title`(1289) | `"我的 MCP 连接"` | `"我的 MCP 授权"` |
| `mcp_oauth.page_subtitle`(1290) | `"你用自己的账号授权过的 OAuth 连接器。"` | `"你用自己的账号授权过的 MCP 服务器。"` |
| `mcp_oauth.empty`(1292) | `"你还没有连接任何 OAuth MCP 连接器。"` | `"你还没有授权任何 MCP 服务器。"` |
| `mcp_oauth.col_connector`(1293) | `"连接器"` | `"MCP 服务器"` |

在 `zhCN` 常量的 `mcp_servers` 块内追加新键:

```typescript
    source_platform: "平台",
    source_custom: "自定义",
    platform_hosted: "平台托管",
    status_enabled_platform: "已启用（平台）",
    needs_authorize: "需你授权",
    remove: "移出",
    remove_confirm: "移出平台服务器 {{name}}？",
    act_run: "运行",
    act_stop: "停用",
```

> 说明:`mcp_catalog` 管理页(admin)内部沿用"连接器"为其领域名词,不做整体清洗;本任务只改租户可见面(服务器列表 / 添加抽屉 / OAuth 页 / 导航)与唯一的"启用"歧义(`col_enabled`→发布状态)。

- [ ] **Step 4: 跑测试确认通过**

Run: `pnpm --filter admin-ui test -- i18n && pnpm --filter admin-ui typecheck`
Expected: PASS —— parity(zh 键集===en)绿、新键断言绿、`tsc` 无错。

- [ ] **Step 5: 提交**

```bash
git add apps/admin-ui/src/i18n/locales/en.ts apps/admin-ui/src/i18n/locales/zh-CN.ts apps/admin-ui/src/i18n/__tests__/i18n.test.tsx
git commit -m "i18n(mcp): 术语字典对齐(发布/启用/运行)+ 统一列表平台行新键"
```

---

### Task 3: 前端合并助手 + `AvailableMcpServer` 加字段

**Files:**
- Modify: `apps/admin-ui/src/api/mcp-servers.ts`(`AvailableMcpServer` 接口,70–79)
- Create: `apps/admin-ui/src/pages/mcpServerRows.ts`
- Test: `apps/admin-ui/src/pages/__tests__/mcpServerRows.test.ts`

**Interfaces:**
- Consumes: `McpServer`、`McpAuthType`、`AvailableMcpServer`(from `../api/mcp-servers`)。
- Produces:
  - `AvailableMcpServer` 追加可选 `display_name?: string`、`auth_type?: McpAuthType`(`catalog_id?` 已存在)。
  - `type UnifiedRow`(判别联合,`source: "tenant" | "platform"`)。
  - `buildUnifiedRows(servers: readonly McpServer[], available: readonly AvailableMcpServer[]): UnifiedRow[]`。

- [ ] **Step 1: 写失败测试**

Create `apps/admin-ui/src/pages/__tests__/mcpServerRows.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import type { AvailableMcpServer, McpServer } from "../../api/mcp-servers";
import { buildUnifiedRows } from "../mcpServerRows";

const custom: McpServer = {
  id: "s1",
  name: "my-custom",
  transport: "sse",
  url: "https://x.example.com/sse",
  auth_type: "bearer",
  timeout_s: 30,
  enabled: true,
  created_at: "",
  updated_at: "",
};

describe("buildUnifiedRows", () => {
  it("maps enriched platform rows and puts them before custom rows", () => {
    const available: AvailableMcpServer[] = [
      {
        name: "amap-maps",
        source: "platform",
        display_name: "高德地图",
        auth_type: "oauth2",
        catalog_id: "c1",
      },
    ];
    const rows = buildUnifiedRows([custom], available);
    expect(rows).toHaveLength(2);
    const [p, c] = rows;
    expect(p.source).toBe("platform");
    if (p.source === "platform") {
      expect(p.displayName).toBe("高德地图");
      expect(p.authType).toBe("oauth2");
      expect(p.catalogId).toBe("c1");
      expect(p.key).toBe("platform:amap-maps");
    }
    expect(c.source).toBe("tenant");
    if (c.source === "tenant") {
      expect(c.server.name).toBe("my-custom");
      expect(c.key).toBe("tenant:my-custom");
    }
  });

  it("degrades a platform row missing enrichment (stale allowlist)", () => {
    const rows = buildUnifiedRows(
      [],
      [{ name: "ghost", source: "platform" }],
    );
    expect(rows).toHaveLength(1);
    const [p] = rows;
    if (p.source === "platform") {
      expect(p.displayName).toBe("ghost");
      expect(p.authType).toBe("none");
      expect(p.catalogId).toBeNull();
    }
  });

  it("ignores source==='tenant' rows from available (servers is authoritative)", () => {
    const rows = buildUnifiedRows(
      [custom],
      [{ name: "my-custom", source: "tenant", enabled: true }],
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].source).toBe("tenant");
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pnpm --filter admin-ui test -- mcpServerRows`
Expected: FAIL —— `buildUnifiedRows` 不存在(模块解析失败)。

- [ ] **Step 3: 实现类型 + 助手**

**(a)** `api/mcp-servers.ts` 的 `AvailableMcpServer`(70–79)追加两个可选字段:

```typescript
export interface AvailableMcpServer {
  name: string;
  source: "platform" | "tenant";
  enabled?: boolean;
  /** Catalog connector id this tenant server was instantiated from (Stream W).
   *  Absent for custom-registered / platform-allowlisted servers. */
  catalog_id?: string;
  /** Human-readable catalog connector name (Stream W). */
  catalog_name?: string;
  /** Platform (allowlist) rows: connector display name (enriched by /available). */
  display_name?: string;
  /** Platform (allowlist) rows: connector auth type (enriched by /available). */
  auth_type?: McpAuthType;
}
```

**(b)** Create `apps/admin-ui/src/pages/mcpServerRows.ts`:

```ts
/**
 * Unify the tenant's MCP servers into one row model for SettingsMcpServers.
 *
 * Two disjoint sources merge here:
 *   - custom servers (`tenant_mcp_server` rows) → full McpServer detail, editable.
 *   - platform servers the tenant opted into (allowlist, from `/available`
 *     with source==="platform") → read-only, platform-hosted config.
 *
 * `available`'s source==="tenant" entries are ignored: `servers` already carries
 * those with full columns (transport/url/auth), so they'd be duplicates.
 */
import type { AvailableMcpServer, McpAuthType, McpServer } from "../api/mcp-servers";

export type UnifiedRow =
  | { key: string; source: "tenant"; server: McpServer }
  | {
      key: string;
      source: "platform";
      name: string;
      displayName: string;
      authType: McpAuthType;
      catalogId: string | null;
    };

export function buildUnifiedRows(
  servers: readonly McpServer[],
  available: readonly AvailableMcpServer[],
): UnifiedRow[] {
  const platform: UnifiedRow[] = available
    .filter((a) => a.source === "platform")
    .map((a) => ({
      key: `platform:${a.name}`,
      source: "platform" as const,
      name: a.name,
      displayName: a.display_name ?? a.name,
      authType: a.auth_type ?? "none",
      catalogId: a.catalog_id ?? null,
    }));
  const custom: UnifiedRow[] = servers.map((s) => ({
    key: `tenant:${s.name}`,
    source: "tenant" as const,
    server: s,
  }));
  return [...platform, ...custom];
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pnpm --filter admin-ui test -- mcpServerRows && pnpm --filter admin-ui typecheck`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add apps/admin-ui/src/api/mcp-servers.ts apps/admin-ui/src/pages/mcpServerRows.ts apps/admin-ui/src/pages/__tests__/mcpServerRows.test.ts
git commit -m "feat(mcp): AvailableMcpServer 平台字段 + buildUnifiedRows 合并助手"
```

---

### Task 4: 添加抽屉 `onEnabledChange` 回调

**Files:**
- Modify: `apps/admin-ui/src/components/mcp_catalog/AddMcpServerDrawer.tsx`
- Test: Create `apps/admin-ui/src/components/mcp_catalog/AddMcpServerDrawer.test.tsx`

**Interfaces:**
- Consumes: `enablePlatformServer` / `disablePlatformServer` / `listTenantCatalog`(既有)。
- Produces: `AddMcpServerDrawerProps` 追加 `onEnabledChange?: () => void`;在 `handleToggleEnable` 启用/停用**成功后**调用,供父页刷新统一列表。

- [ ] **Step 1: 写失败测试**

Create `apps/admin-ui/src/components/mcp_catalog/AddMcpServerDrawer.test.tsx`:

```tsx
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "antd";
import "../../../i18n";

import { AddMcpServerDrawer } from "./AddMcpServerDrawer";
import * as catalogSdk from "../../api/mcp-catalog";

const listMock = vi.spyOn(catalogSdk, "listTenantCatalog");
const enableMock = vi.spyOn(catalogSdk, "enablePlatformServer");

beforeEach(() => {
  listMock.mockReset();
  enableMock.mockReset();
});

function renderDrawer(onEnabledChange: () => void) {
  return render(
    <App>
      <AddMcpServerDrawer
        open
        onClose={() => {}}
        onSaved={() => {}}
        onEnabledChange={onEnabledChange}
      />
    </App>,
  );
}

describe("AddMcpServerDrawer", () => {
  it("fires onEnabledChange after a successful enable toggle", async () => {
    listMock.mockResolvedValue([
      {
        id: "c1",
        name: "amap-maps",
        display_name: "高德地图",
        description: "",
        transport: "streamable_http",
        auth_type: "bearer",
        category: "location",
        required_tier: "free",
        entitled: true,
        tenant_enabled: false,
      },
    ] as never);
    enableMock.mockResolvedValue({} as never);
    const onEnabledChange = vi.fn();
    renderDrawer(onEnabledChange);

    const toggle = await screen.findByTestId("cb-toggle-amap-maps");
    await userEvent.click(toggle);

    await waitFor(() => expect(enableMock).toHaveBeenCalledWith("c1"));
    await waitFor(() => expect(onEnabledChange).toHaveBeenCalledTimes(1));
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pnpm --filter admin-ui test -- AddMcpServerDrawer`
Expected: FAIL —— `onEnabledChange` prop 不存在(类型报错)/ 未被调用。

- [ ] **Step 3: 加 prop 并调用**

`AddMcpServerDrawer.tsx` 改三处:

接口(29–35)加可选 prop:

```typescript
export interface AddMcpServerDrawerProps {
  open: boolean;
  onClose: () => void;
  /** Fires after a successful custom-server create so the parent can refresh +
   *  close. (Enable/disable toggles persist in place and keep the drawer open.) */
  onSaved: () => void;
  /** Fires after a successful platform enable/disable toggle so the parent can
   *  refresh its unified server list (the toggle keeps the drawer open). */
  onEnabledChange?: () => void;
}
```

解构(41–45):

```typescript
export function AddMcpServerDrawer({
  open,
  onClose,
  onSaved,
  onEnabledChange,
}: AddMcpServerDrawerProps) {
```

`handleToggleEnable` 成功分支(setEntries 之后,85–111)追加回调:

```typescript
        setEntries((prev) =>
          prev.map((e) =>
            e.id === entry.id ? { ...e, tenant_enabled: next } : e,
          ),
        );
        onEnabledChange?.();
```

并把 `onEnabledChange` 加进该 `useCallback` 依赖数组:`}, [message, onEnabledChange]);`

- [ ] **Step 4: 跑测试确认通过**

Run: `pnpm --filter admin-ui test -- AddMcpServerDrawer && pnpm --filter admin-ui typecheck`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add apps/admin-ui/src/components/mcp_catalog/AddMcpServerDrawer.tsx apps/admin-ui/src/components/mcp_catalog/AddMcpServerDrawer.test.tsx
git commit -m "feat(mcp): 添加抽屉 toggle 成功后回调 onEnabledChange"
```

---

### Task 5: `SettingsMcpServers` 统一列表重写

**Files:**
- Modify: `apps/admin-ui/src/pages/SettingsMcpServers.tsx`(整文件重写数据层 + 行渲染)
- Modify: `apps/admin-ui/src/pages/SettingsMcpServers.stories.tsx`(mock 两个接口)
- Test: Create `apps/admin-ui/src/pages/__tests__/SettingsMcpServers.test.tsx`

**Interfaces:**
- Consumes: `buildUnifiedRows`/`UnifiedRow`(T3)、`listAvailableMcpServers`(T3 增强)、`AddMcpServerDrawer.onEnabledChange`(T4)、`disablePlatformServer`(mcp-catalog)、i18n 新键(T2)。

- [ ] **Step 1: 写失败测试**

Create `apps/admin-ui/src/pages/__tests__/SettingsMcpServers.test.tsx`:

```tsx
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "antd";
import "../../i18n";

import { SettingsMcpServers } from "../SettingsMcpServers";
import * as serversSdk from "../../api/mcp-servers";
import type { McpServer } from "../../api/mcp-servers";

const listMock = vi.spyOn(serversSdk, "listMcpServers");
const availMock = vi.spyOn(serversSdk, "listAvailableMcpServers");

beforeEach(() => {
  listMock.mockReset();
  availMock.mockReset();
});

const custom: McpServer = {
  id: "s1",
  name: "my-custom",
  transport: "sse",
  url: "https://x.example.com/sse",
  auth_type: "bearer",
  timeout_s: 30,
  enabled: true,
  created_at: "",
  updated_at: "",
};

function renderPage() {
  return render(
    <MemoryRouter>
      <App>
        <SettingsMcpServers />
      </App>
    </MemoryRouter>,
  );
}

describe("SettingsMcpServers unified list", () => {
  it("renders platform and custom rows with source tags", async () => {
    listMock.mockResolvedValue([custom]);
    availMock.mockResolvedValue([
      { name: "amap", source: "platform", display_name: "高德地图", auth_type: "none", catalog_id: "c1" },
    ]);
    renderPage();
    expect(await screen.findByText("高德地图")).toBeInTheDocument();
    expect(screen.getByText("my-custom")).toBeInTheDocument();
    expect(screen.getAllByText("平台").length).toBeGreaterThan(0);
    expect(screen.getAllByText("自定义").length).toBeGreaterThan(0);
  });

  it("oauth2 platform row shows the authorize link and hides Test", async () => {
    listMock.mockResolvedValue([]);
    availMock.mockResolvedValue([
      { name: "gh", source: "platform", display_name: "GitHub", auth_type: "oauth2", catalog_id: "c2" },
    ]);
    renderPage();
    expect(await screen.findByTestId("ms-authorize-gh")).toBeInTheDocument();
    expect(screen.queryByTestId("ms-test-gh")).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pnpm --filter admin-ui test -- SettingsMcpServers`
Expected: FAIL —— 当前页只读 `listMcpServers`,无平台行、无 `ms-authorize-*`。

- [ ] **Step 3: 重写页面**

整体替换 `apps/admin-ui/src/pages/SettingsMcpServers.tsx`:

```tsx
/**
 * Settings — MCP 服务器统一列表。
 *
 * 一张表合并两个不相交来源:
 *   - 自定义服务器(`tenant_mcp_server`,`listMcpServers()` 全明细,可编辑)。
 *   - 平台服务器(租户已启用的 allowlist,`/available` 里 source="platform",
 *     经后端增强携带 display_name/auth_type/catalog_id;只读、平台托管)。
 *
 * 平台行:bearer/none 可"测试"+"移出";oauth2 挂"需你授权 →"跳授权页、不给测试
 * (后端探测返回 409)。自定义行:测试 / 编辑 / 运行开关(运行中↔已停用)/ 删除。
 */
import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  App,
  Button,
  Popconfirm,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { Plug } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import {
  deleteMcpServer,
  listAvailableMcpServers,
  listMcpServerTools,
  listMcpServers,
  updateMcpServer,
  type McpServer,
  type McpTool,
} from "../api/mcp-servers";
import { disablePlatformServer } from "../api/mcp-catalog";
import { ApiError } from "../api/client";
import { CreateMcpServerDrawer } from "../components/CreateMcpServerDrawer";
import { AddMcpServerDrawer } from "../components/mcp_catalog/AddMcpServerDrawer";
import { PageHeader } from "../components/PageHeader";
import { buildUnifiedRows, type UnifiedRow } from "./mcpServerRows";

type ProbeState =
  | { kind: "idle" }
  | { kind: "testing" }
  | { kind: "connected"; count: number; tools: McpTool[] }
  | { kind: "unreachable" };

function errMsg(err: unknown): string {
  if (err instanceof ApiError) return `${err.code}: ${err.message}`;
  if (err instanceof Error) return err.message;
  return "unknown error";
}

export function SettingsMcpServers() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const navigate = useNavigate();

  const [rows, setRows] = useState<UnifiedRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [addOpen, setAddOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState<McpServer | null>(null);

  const [probes, setProbes] = useState<Record<string, ProbeState>>({});

  const reload = useCallback(() => {
    setLoading(true);
    Promise.all([listMcpServers(), listAvailableMcpServers()]).then(
      ([servers, available]) => {
        setRows(buildUnifiedRows(servers, available));
        setLoading(false);
      },
      (err: unknown) => {
        setError(err instanceof Error ? err.message : "unknown error");
        setLoading(false);
      },
    );
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const probe = useCallback(
    async (name: string) => {
      const current = probes[name];
      if (current?.kind === "connected" || current?.kind === "testing") return;
      setProbes((prev) => ({ ...prev, [name]: { kind: "testing" } }));
      try {
        const tools = await listMcpServerTools(name);
        setProbes((prev) => ({
          ...prev,
          [name]: { kind: "connected", count: tools.length, tools },
        }));
      } catch {
        setProbes((prev) => ({ ...prev, [name]: { kind: "unreachable" } }));
      }
    },
    [probes],
  );

  const handleToggle = useCallback(
    async (row: McpServer) => {
      try {
        await updateMcpServer(row.name, { enabled: !row.enabled });
        reload();
      } catch (err) {
        message.error(errMsg(err));
      }
    },
    [message, reload],
  );

  const handleDelete = useCallback(
    async (name: string) => {
      try {
        await deleteMcpServer(name);
        reload();
      } catch (err) {
        message.error(errMsg(err));
      }
    },
    [message, reload],
  );

  const handleRemovePlatform = useCallback(
    async (catalogId: string | null) => {
      if (catalogId === null) {
        message.error(t("mcp_servers.failed_to_load"));
        return;
      }
      try {
        await disablePlatformServer(catalogId);
        reload();
      } catch (err) {
        message.error(errMsg(err));
      }
    },
    [message, reload, t],
  );

  const openCreate = useCallback(() => setAddOpen(true), []);
  const openEdit = useCallback((row: McpServer) => {
    setEditing(row);
    setEditOpen(true);
  }, []);
  const closeEdit = useCallback(() => {
    setEditOpen(false);
    setEditing(null);
  }, []);

  const renderProbeStatus = useCallback(
    (name: string, staticTag: React.ReactNode) => {
      const s = probes[name] ?? { kind: "idle" };
      if (s.kind === "idle") return staticTag;
      if (s.kind === "testing") {
        return (
          <Space size={4}>
            <Spin size="small" />
            <span>{t("mcp_servers.testing")}</span>
          </Space>
        );
      }
      if (s.kind === "connected") {
        return <Tag color="green">{t("mcp_servers.connected", { count: s.count })}</Tag>;
      }
      return <Tag color="red">{t("mcp_servers.unreachable")}</Tag>;
    },
    [probes, t],
  );

  const columns: ColumnsType<UnifiedRow> = [
    {
      title: t("mcp_servers.col_name"),
      key: "name",
      render: (_: unknown, row: UnifiedRow) => {
        const name = row.source === "tenant" ? row.server.name : row.displayName;
        const sourceTag =
          row.source === "tenant" ? (
            <Tag>{t("mcp_servers.source_custom")}</Tag>
          ) : (
            <Tag color="blue">{t("mcp_servers.source_platform")}</Tag>
          );
        return (
          <Space size={6}>
            <Typography.Text strong>{name}</Typography.Text>
            {sourceTag}
          </Space>
        );
      },
    },
    {
      title: t("mcp_servers.col_transport"),
      key: "transport",
      render: (_: unknown, row: UnifiedRow) =>
        row.source === "tenant" ? (
          <Tag>{row.server.transport === "streamable_http" ? "Streamable HTTP" : "SSE"}</Tag>
        ) : (
          <span style={{ color: "var(--ew-text-tertiary, #666)" }}>—</span>
        ),
    },
    {
      title: t("mcp_servers.col_url"),
      key: "url",
      ellipsis: true,
      render: (_: unknown, row: UnifiedRow) =>
        row.source === "tenant" ? (
          <Tooltip title={row.server.url}>
            <Typography.Text ellipsis style={{ maxWidth: 200 }}>
              {row.server.url}
            </Typography.Text>
          </Tooltip>
        ) : (
          <Typography.Text type="secondary">{t("mcp_servers.platform_hosted")}</Typography.Text>
        ),
    },
    {
      title: t("mcp_servers.col_auth"),
      key: "auth",
      render: (_: unknown, row: UnifiedRow) => {
        const auth = row.source === "tenant" ? row.server.auth_type : row.authType;
        const color = auth === "bearer" ? "blue" : auth === "oauth2" ? "geekblue" : "default";
        const label = auth === "bearer" ? "Bearer" : auth === "oauth2" ? "OAuth" : "None";
        return <Tag color={color}>{label}</Tag>;
      },
    },
    {
      title: t("mcp_servers.col_status"),
      key: "status",
      render: (_: unknown, row: UnifiedRow) => {
        if (row.source === "platform") {
          return renderProbeStatus(
            row.name,
            <Tag color="green">{t("mcp_servers.status_enabled_platform")}</Tag>,
          );
        }
        return renderProbeStatus(
          row.server.name,
          <Tag color={row.server.enabled ? "green" : "default"}>
            {row.server.enabled
              ? t("mcp_servers.status_enabled")
              : t("mcp_servers.status_disabled")}
          </Tag>,
        );
      },
    },
    {
      title: t("mcp_servers.col_tools"),
      key: "tools",
      render: (_: unknown, row: UnifiedRow) => {
        const name = row.source === "tenant" ? row.server.name : row.name;
        const s = probes[name];
        if (s?.kind === "connected") return <span>{s.count}</span>;
        return <span style={{ color: "var(--ew-text-tertiary, #666)" }}>—</span>;
      },
    },
    {
      title: t("mcp_servers.col_actions"),
      key: "actions",
      render: (_: unknown, row: UnifiedRow) => {
        if (row.source === "platform") {
          const isOauth = row.authType === "oauth2";
          return (
            <Space size={4}>
              {isOauth ? (
                <Button
                  size="small"
                  type="link"
                  data-testid={`ms-authorize-${row.name}`}
                  onClick={() => navigate("/settings/mcp-oauth")}
                >
                  {t("mcp_servers.needs_authorize")}
                </Button>
              ) : (
                <Button
                  size="small"
                  data-testid={`ms-test-${row.name}`}
                  loading={probes[row.name]?.kind === "testing"}
                  onClick={() => void probe(row.name)}
                >
                  {t("mcp_servers.test")}
                </Button>
              )}
              <Popconfirm
                title={t("mcp_servers.remove_confirm", { name: row.displayName })}
                onConfirm={() => void handleRemovePlatform(row.catalogId)}
              >
                <Button size="small" data-testid={`ms-remove-${row.name}`}>
                  {t("mcp_servers.remove")}
                </Button>
              </Popconfirm>
            </Space>
          );
        }
        const s = row.server;
        return (
          <Space size={4}>
            <Button
              size="small"
              data-testid={`ms-test-${s.name}`}
              loading={probes[s.name]?.kind === "testing"}
              onClick={() => void probe(s.name)}
            >
              {t("mcp_servers.test")}
            </Button>
            <Button size="small" data-testid={`ms-edit-${s.name}`} onClick={() => openEdit(s)}>
              {t("mcp_servers.edit")}
            </Button>
            <Button
              size="small"
              data-testid={`ms-toggle-${s.name}`}
              onClick={() => void handleToggle(s)}
            >
              {s.enabled ? t("mcp_servers.act_stop") : t("mcp_servers.act_run")}
            </Button>
            <Popconfirm
              title={t("mcp_servers.delete_confirm", { name: s.name })}
              onConfirm={() => void handleDelete(s.name)}
            >
              <Button size="small" danger data-testid={`ms-delete-${s.name}`}>
                {t("mcp_servers.delete")}
              </Button>
            </Popconfirm>
          </Space>
        );
      },
    },
  ];

  const expandedRowRender = useCallback(
    (row: UnifiedRow) => {
      const name = row.source === "tenant" ? row.server.name : row.name;
      const s = probes[name] ?? { kind: "idle" };
      if (s.kind === "idle" || s.kind === "testing") {
        return (
          <div style={{ padding: "8px 0" }}>
            <Space size={4}>
              <Spin size="small" />
              <span>{t("mcp_servers.tools_loading")}</span>
            </Space>
          </div>
        );
      }
      if (s.kind === "unreachable") {
        return (
          <div style={{ padding: "8px 0" }}>
            <Tag color="red">{t("mcp_servers.unreachable")}</Tag>
          </div>
        );
      }
      if (s.tools.length === 0) {
        return (
          <div
            style={{ padding: "8px 0", color: "var(--ew-text-tertiary, #666)" }}
            data-testid={`ms-tools-${name}`}
          >
            {t("mcp_servers.no_tools")}
          </div>
        );
      }
      return (
        <div style={{ padding: "8px 0" }} data-testid={`ms-tools-${name}`}>
          <Space size={[4, 8]} wrap>
            {s.tools.map((tool) => (
              <Tooltip key={tool.name} title={tool.description || undefined}>
                <Tag style={{ cursor: "default" }}>{tool.name}</Tag>
              </Tooltip>
            ))}
          </Space>
        </div>
      );
    },
    [probes, t],
  );

  const emptyText = (
    <div style={{ textAlign: "center", padding: "32px 0" }} data-testid="ms-empty">
      <Plug size={32} strokeWidth={1.25} style={{ opacity: 0.35, marginBottom: 8 }} />
      <div style={{ fontWeight: 600, marginBottom: 4 }}>{t("mcp_servers.empty_title")}</div>
      <div
        style={{
          color: "var(--ew-text-tertiary, #666)",
          marginBottom: 16,
          maxWidth: 360,
          margin: "0 auto 16px",
        }}
      >
        {t("mcp_servers.empty_hint")}
      </div>
      <Button type="primary" onClick={openCreate}>
        {t("mcp_servers.add")}
      </Button>
    </div>
  );

  return (
    <div data-testid="ms-root">
      <PageHeader
        icon={<Plug size={18} strokeWidth={1.5} />}
        title={t("mcp_servers.page_title")}
        subtitle={t("mcp_servers.subtitle")}
        actions={
          <Button type="primary" data-testid="ms-add" onClick={openCreate}>
            {t("mcp_servers.add")}
          </Button>
        }
      />

      {error !== null && (
        <Alert
          type="error"
          showIcon
          data-testid="ms-error"
          message={t("mcp_servers.failed_to_load")}
          description={error}
          style={{ marginBottom: 16 }}
        />
      )}

      <Table<UnifiedRow>
        data-testid="ms-table"
        rowKey="key"
        loading={loading}
        dataSource={rows}
        pagination={false}
        locale={{ emptyText }}
        columns={columns}
        expandable={{
          expandedRowRender,
          onExpand: (expanded, row) => {
            if (expanded) {
              void probe(row.source === "tenant" ? row.server.name : row.name);
            }
          },
        }}
      />

      <AddMcpServerDrawer
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onSaved={() => {
          setAddOpen(false);
          reload();
        }}
        onEnabledChange={reload}
      />

      <CreateMcpServerDrawer
        open={editOpen}
        onClose={closeEdit}
        onSaved={() => {
          closeEdit();
          reload();
        }}
        editing={editing}
      />
    </div>
  );
}
```

> `import React` 已隐式(JSX 自动运行时);`React.ReactNode` 若在本项目需要显式引入,按既有其它页面写法补 `import type { ReactNode } from "react"` 并把 `React.ReactNode` 换成 `ReactNode`(以 `pnpm typecheck` 绿为准)。

- [ ] **Step 4: 跑测试确认通过 + 改 story**

改 `SettingsMcpServers.stories.tsx`:把原先只 mock `listMcpServers` 的地方补 mock `listAvailableMcpServers`(返回 `[]` 或一条 `source:"platform"` 示例),确保 story 不因新增第二个接口调用而挂。

Run: `pnpm --filter admin-ui test -- SettingsMcpServers mcpServerRows && pnpm --filter admin-ui typecheck && pnpm --filter admin-ui lint`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add apps/admin-ui/src/pages/SettingsMcpServers.tsx apps/admin-ui/src/pages/SettingsMcpServers.stories.tsx apps/admin-ui/src/pages/__tests__/SettingsMcpServers.test.tsx
git commit -m "feat(mcp): MCP 服务器统一列表(平台+自定义)+ 平台行只读/移出/需授权"
```

---

## Self-Review(计划自检)

- **Spec 覆盖:** §1.2 功能断层→T1+T3+T5;§2 术语→T2;§4.1 统一列表→T3+T5;§4.2 抽屉刷新→T4;§4.3/§4.4 OAuth/目录文案→T2;§5 后端增强→T1。全覆盖。
- **类型一致:** `buildUnifiedRows`/`UnifiedRow`(T3)在 T5 消费签名一致;`AvailableMcpServer.display_name?/auth_type?`(T3)与 T1 后端字段对齐;`onEnabledChange`(T4)在 T5 传 `reload`。
- **Placeholder 扫描:** 无 TBD/TODO;每处改动附完整代码。两处"以 mypy/typecheck 绿为准"的注记是明确的二选一裁决,非占位。
- **Scope:** 单一实现计划,5 任务,均独立可测可评审。

## 执行建议

推荐 **subagent-driven-development**:T1–T4 相互独立、T5 依赖 T1/T3/T4,逐任务派发 + 任务间评审。
