# MCP 界面与术语重设计 — 设计文档

**状态:** 设计待评审
**日期:** 2026-07-09
**范围:** admin-ui MCP 相关页面 + 少量 control-plane `/available` 接口增强。数据模型、迁移、运行时 OAuth 池不动。

---

## 1. 背景与问题

MCP 在 admin-ui 有 3 个租户/平台面向的入口,彼此重叠、术语打架,用户反馈"好混乱"。逐一落到代码:

### 1.1 导航重叠(`navModel.ts`)

| key | 当前 label | 路径 | 分组 |
|---|---|---|---|
| `settings-mcp-servers` | MCP 服务器 | `/settings/mcp-servers` | tenant-settings |
| `settings-mcp-oauth` | 我的 MCP 连接 | `/settings/mcp-oauth` | tenant-settings |
| `settings-mcp-catalog` | MCP 目录 | `/settings/mcp-catalog` | platform(system_admin) |

租户设置里并排"MCP 服务器"+"我的 MCP 连接",名字看不出差别 —— 一个是租户级服务器清单,一个是**当前登录用户**的 OAuth 授权,维度完全不同却像重复项。

### 1.2 "MCP 服务器"页看不到已启用的平台服务器(功能缺陷)

- `SettingsMcpServers.tsx:76` 读 `listMcpServers()` = `GET /v1/mcp-servers` = **只有 `tenant_mcp_server` 表的自定义行**。
- 但"添加"抽屉里启用一个平台连接器,走的是 `enablePlatformServer(id)` → 写 `tenant_config.mcp_allowlist`(一个名字数组,**不是** `tenant_mcp_server` 行)。
- 两张表互不相交 → 用户在抽屉里把 GitHub"启用"了,回到列表**永远看不到它**。看起来像"启用没生效"的 bug,实为 IA 断层。
- 雪上加霜:`AddMcpServerDrawer.tsx:93` 的启用 toggle 只 `setEntries` 改抽屉本地 state,**从不调 `onSaved`** → 父页面即使读对了接口也不会重新拉取。

后端其实已经有正解:`GET /v1/mcp-servers/available`(`mcp_servers.py:717`)把 allowlist(`source:"platform"`)和自定义行(`source:"tenant"`)合并返回。前端已有 `listAvailableMcpServers()` 绑定,但**只被 `McpToolPicker` 用了**,设置页没用上。

### 1.3 三个"启用"同名不同义

| 位置 | 文案 | 真实语义 |
|---|---|---|
| `mcp_catalog.col_enabled` | 启用 | 平台把某连接器模板**发布**给租户可见 |
| `mcp_catalog.enable/enabled`(CatalogBrowser toggle) | 启用/已启用 | 租户把某平台服务器**纳入**自己的 allowlist |
| `mcp_servers.status_enabled/disabled` | 已启用/已停用 | 自定义服务器的**运行开关** |

外加"连接器"(connector)/"MCP server"/"MCP 服务器"三个词混用。用户已明确:"那其实不都是 MCP server 么" → 主词统一成 **MCP 服务器**,"连接器"降级成来源标签。

---

## 2. 术语字典(全局约束,所有文案照此)

| 概念 | 唯一用词 | 说明 |
|---|---|---|
| 可被 agent 调用的服务器 | **MCP 服务器** | 唯一主词。列表、抽屉、目录一律用它 |
| 来源:平台提供、租户勾选启用 | 来源=**平台** | 从 MCP 目录纳入(allowlist) |
| 来源:租户自填 URL 注册 | 来源=**自定义** | `tenant_mcp_server` 行 |
| 平台侧的服务器模板库 | **MCP 目录** | admin 管理;条目 = 平台 MCP 服务器模板。不再叫"连接器" |
| 当前用户的 OAuth 授权 | **我的 MCP 授权** | user_id 维度;每人各自登录授权 |

**三个动作彻底改名,互不撞车:**

| 动作 | 动词 | 状态词 | 谁操作 |
|---|---|---|---|
| 平台发布模板到目录 | **发布** | 已发布 / 已下架 | 系统管理员(目录页) |
| 租户纳入平台服务器 | **启用** | 已启用 / 未启用 | 租户管理员(添加抽屉 / 列表移出) |
| 自定义服务器运行开关 | **运行** | 运行中 / 已停用 | 租户管理员(列表行) |

---

## 3. 目标 IA

导航仍是 3 项,不新增不删除,只改 label 与职责边界:

| key | 新 label | 路径 | 分组 | 职责 |
|---|---|---|---|---|
| `settings-mcp-servers` | **MCP 服务器** | `/settings/mcp-servers` | tenant-settings | 统一清单:平台+自定义一张表 |
| `settings-mcp-oauth` | **我的 MCP 授权** | `/settings/mcp-oauth` | tenant-settings | 当前用户的 OAuth 授权(user_id 维度) |
| `settings-mcp-catalog` | **MCP 目录** | `/settings/mcp-catalog` | platform | 系统管理员维护平台服务器模板 |

**为什么 OAuth 独立成页而不折进统一列表:** 前两页是**租户维度**(一份配置全租户共享),第三页是**用户维度**(李四登录只看到李四的授权,王五看不到)。折在一起会把"这台服务器"和"我对这台服务器的授权"两个不同生命周期的东西混在一行,反而更乱。改为在统一列表里对 oauth2 平台服务器行挂一个"需你授权 →"跨链,跳到授权页。

---

## 4. 屏级设计

### 4.1 屏 1 —「MCP 服务器」统一列表(`SettingsMcpServers.tsx` 重写数据层)

**数据来源(两拉合并,后端仅小改):**

- `listMcpServers()` → 自定义行的**完整明细**(transport/url/auth/enabled/id),照旧。
- `listAvailableMcpServers()` → 取 `source==="platform"` 的行 = 平台已启用服务器(经 §5 增强后带 `display_name`/`auth_type`/`catalog_id`)。

合并成判别联合行模型:

```ts
type UnifiedRow =
  | { source: "tenant"; server: McpServer }                 // 完整明细,可编辑
  | { source: "platform"; name: string; displayName: string;
      authType: McpAuthType; catalogId: string };           // 只读,平台托管
```

**列:**

| 列 | 平台行 | 自定义行 |
|---|---|---|
| 名称 | `displayName`(带来源 Tag=平台) | `name`(带来源 Tag=自定义) |
| 传输 / URL / 认证 | —(平台托管,不暴露);认证列显示 `authType` | 照旧 |
| 状态 | 已启用(平台)+ 按需探测 已连接/连不上 | 运行中 / 已停用 + 按需探测 |
| 工具 | 探测计数(oauth2 除外) | 探测计数 |
| 操作 | 见下 | 测试 / 编辑 / 运行开关(运行中↔已停用)/ 删除 |

**平台行操作(按 `authType` 分支):**

- `bearer`/`none`:测试(探测工具)、**移出**(`disablePlatformServer(catalogId)` → 出 allowlist,乐观更新 + 重拉)。
- `oauth2`:不显示测试(后端探测返回 409),显示 **"需你授权 →"** 链接 → `/settings/mcp-oauth`;仍可移出。

**功能修复(本页核心):**

1. 数据源从"只读 `listMcpServers()`"改为"两拉合并" → 平台已启用服务器**出现在列表**。
2. 空态文案与"添加"按钮不变;`AddMcpServerDrawer.onSaved`/toggle 回调触发本页 `reload()`(见 4.2)。

### 4.2 屏 2 —「添加 MCP 服务器」抽屉(`AddMcpServerDrawer` + `CatalogBrowser` 文案/回调修)

结构不变(浏览目录 → 启用 / 高级自定义两条路),改三处:

1. **toggle 启用后通知父页刷新。** 新增可选 prop `onEnabledChange?: () => void`,在 `handleToggleEnable` 成功分支调用;`SettingsMcpServers` 传入 `reload`。这样启用平台服务器后,关抽屉即见列表新增行(修 §1.2 第二半)。
2. **卡片文案对齐术语。** 卡片主词"MCP 服务器";toggle 文案保持 启用/已启用(=租户启用);`shared_hint`/`oauth_badge` 保留,补一句区分:bearer/none=共享一份凭证,oauth2=每人各自授权。
3. **抽屉标题** `mcp_catalog.browser_title` "添加 MCP server" → "添加 MCP 服务器"。

### 4.3 屏 3 —「我的 MCP 授权」(`SettingsMcpOAuth.tsx` 文案改名,逻辑不动)

- 页面主词与导航 label 统一为"我的 MCP 授权"。
- `mcp_oauth.page_title` "我的 MCP 连接" → "我的 MCP 授权";`page_subtitle` 明确"仅你本人可见,基于你的账号"。
- 表格列/状态词(待授权/已连接/已过期/已撤销)不改语义,仅 `col_connector` "连接器" → "MCP 服务器"。
- 逻辑、接口、user_id 维度**完全不动**。

### 4.4 屏 4 —「MCP 目录」(`SettingsMcpCatalog.tsx` 术语-only)

- `mcp_catalog.col_enabled` "启用" → **"发布状态"**,值 已发布/已下架(区别于租户启用)。
- `subtitle`/`add`/`empty_hint`:"连接器" → "平台 MCP 服务器 / 服务器模板";`add` "新建连接器" → "新建服务器模板"。
- CRUD、认证类型、分类等**功能不动**,纯文案。

---

## 5. 后端改动 — `/available` 平台行增强(唯一后端触点)

`mcp_servers.py:717 list_available_mcp_servers`,当前平台行只有 `{name, source:"platform"}`。增强为携带渲染统一列表所需的最小字段:

```python
# allowlist 非空时,一次性取平台目录建 name→entry 映射(catalog 是 NULL-tenant,bypass RLS)
if cfg.mcp_allowlist:
    async with bypass_rls_session():
        by_name = {e.name: e for e in await catalog_store.list()}
    for name in cfg.mcp_allowlist:
        entry = by_name.get(name)
        if entry is None:            # 目录里已删,allowlist 残留 → 降级
            available.append({"name": name, "source": "platform"})
            continue
        available.append({
            "name": name,
            "source": "platform",
            "display_name": entry.display_name,
            "auth_type": entry.auth_type,
            "catalog_id": str(entry.id),
        })
```

- 纯**加字段**,`McpToolPicker`(只读 `name`/`source`)不受影响。
- 自定义行(`source:"tenant"`)**不动**。
- 已删除连接器的残留 allowlist 名降级为裸 `{name, source}`,列表按"未知平台服务器"渲染(仅"移出"可用)。
- 前端 `AvailableMcpServer` 类型加可选 `display_name?: string`、`auth_type?: McpAuthType`(`catalog_id?` 已存在)。

**不做:** 不给 `/available` 塞自定义行的 transport/url/auth(那是 `/v1/mcp-servers` 的职责,避免两处重复)。

---

## 6. i18n 改动清单(`en.ts` + `zh-CN.ts` 双语同改)

| 键 | 旧值(zh) | 新值(zh) |
|---|---|---|
| `nav.mcp_oauth` | 我的 MCP 连接 | 我的 MCP 授权 |
| `mcp_servers.add` | 添加 server | 添加 MCP 服务器 |
| `mcp_servers.status_enabled/disabled` | 已启用/已停用 | 运行中/已停用 |
| `mcp_catalog.browser_title` | 添加 MCP server | 添加 MCP 服务器 |
| `mcp_catalog.col_enabled` | 启用 | 发布状态 |
| `mcp_catalog.add` | 新建连接器 | 新建服务器模板 |
| `mcp_oauth.page_title` | 我的 MCP 连接 | 我的 MCP 授权 |
| `mcp_oauth.col_connector` | 连接器 | MCP 服务器 |

**新增键(统一列表平台行):**

- `mcp_servers.source_platform` = 平台 / `mcp_servers.source_custom` = 自定义
- `mcp_servers.platform_hosted` = 平台托管(URL/传输列占位)
- `mcp_servers.status_enabled_platform` = 已启用(平台)
- `mcp_servers.remove` = 移出 / `mcp_servers.remove_confirm` = 移出平台服务器 {{name}}?
- `mcp_servers.needs_authorize` = 需你授权

其余"连接器"字样按 §2 逐句核对;发布/启用/运行三词不得再混用。

---

## 7. 不在本次范围(YAGNI)

- 数据模型 / 迁移 / `tenant_mcp_server.catalog_id` 回填。
- 运行时 OAuth 池、`principal.subject_id` 维度逻辑。
- 目录页 CRUD 功能、健康探测机制、RLS。
- 平台行的"编辑 URL"(平台托管,租户无权改)。

---

## 8. 测试计划

**后端(pytest,control-plane):**
- `/available` 平台行携带 `display_name`/`auth_type`/`catalog_id`(catalog 命中)。
- allowlist 残留名(catalog 无对应)降级为裸 `{name, source}`,不 500。
- 自定义行 shape 回归不变。

**前端(vitest + Storybook):**
- `SettingsMcpServers`:平台行 + 自定义行同表渲染;平台行只读(无编辑/删除);oauth2 平台行显示"需你授权"链接、无"测试";bearer 平台行可"移出"。
- 抽屉 toggle 启用 → 父 `reload` 被调用(mock 断言)。
- 各页 story 更新到新文案。
- i18n:新增键 en/zh 均存在(键对齐校验若有)。

**手动冒烟:** 启用 GitHub(平台)→ 关抽屉 → 列表立即出现该行且标"平台"+"需你授权"。

---

## 9. 文件触点

**后端:**
- `services/control-plane/src/control_plane/api/mcp_servers.py`(`/available` 增强)
- `services/control-plane/tests/...`(对应单测)

**前端:**
- `apps/admin-ui/src/api/mcp-servers.ts`(`AvailableMcpServer` 加字段)
- `apps/admin-ui/src/pages/SettingsMcpServers.tsx`(两拉合并 + 判别联合行 + 平台行操作)
- `apps/admin-ui/src/components/mcp_catalog/AddMcpServerDrawer.tsx`(`onEnabledChange` 回调)
- `apps/admin-ui/src/components/mcp_catalog/CatalogBrowser.tsx`(文案对齐,如需)
- `apps/admin-ui/src/pages/SettingsMcpOAuth.tsx`(文案改名)
- `apps/admin-ui/src/pages/SettingsMcpCatalog.tsx`(术语-only)
- `apps/admin-ui/src/components/navModel.ts`(无需改结构;label 走 i18n)
- `apps/admin-ui/src/i18n/locales/{en,zh-CN}.ts`(§6 清单)
- 相关 `*.stories.tsx` / `*.test.tsx`
