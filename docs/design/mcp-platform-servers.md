# 设计 — 平台配置 MCP server + 租户选择使用(方向修正)

状态:草案(待评审) · 2026-06-24 · 分支 `mcp/platform-servers-redesign`

## 1. 背景 / 问题

现有 MCP 目录走「**模板 + 租户实例化**」模型:平台管理员定义连接器**模板**(`url_template` 带 `{param}` 占位 + `auth_schema` 一组**让租户填**的字段),租户必须实例化时填自己的 URL 参数/密钥才能用。

产品 owner 拍板这是**错方向**。本意是:

> **平台可以配 MCP server(真服务,平台填好 URL + 认证),租户选择使用。租户也可以建自己的 MCP server。**

即 MCP server 有两个**作用域**:
- **平台级**:system_admin 配好一个**可直接用**的 server,发布给租户;租户**选择启用**,不用填。
- **租户级**:租户自己建私有 server(租户填全部)。

平台级 server 的认证支持两类(owner 确认「两种都要」):
- **A. 共享凭证** —— 平台填一个 token,启用的租户/用户**共用一个身份**。适合无敏感数据的公共工具。
- **B. per-user OAuth** —— 平台只配 URL+transport,标 oauth2;**每个用户各自授权自己账号**(核心隔离场景,已建好)。

## 2. 现状(grounded,实现前核过)

### 2.1 数据层
- **`mcp_connector_catalog`**(NULL-tenant 平台行,RLS;迁移 0055/0062):`url_template`(带 `{param}`)、`auth_type`(none/bearer/oauth2)、`auth_schema`(JSONB,`fields[]={key,label,kind(secret|param),required}`)、`oauth_client_id`、`oauth_scopes`、`required_tier`、`enabled`。admin 端点 `/v1/platform/mcp-catalog`(`api/mcp_catalog.py`,RBAC `mcp_catalog` + `_require_system_admin`)。
- **`tenant_mcp_server`**(租户私有;迁移 0056/0090):`url`(实例化时快照的具体 URL)、`auth_type`、`token_secret_ref`(`secret://`)、`custom_headers_ref`/`custom_header_names`、`sse_read_timeout_s`、`catalog_id`(FK,non-NULL=从目录实例化)。
- **`mcp_oauth_connection`**(per-(tenant,user);迁移 0063):oauth2 的 per-user token,FK→catalog。
- **`tenant_config.mcp_allowlist`**(Stream O):**名字列表**,过滤平台池里哪些 server 该租户可见。

### 2.2 运行期三池(`assembly.py` `_register_mcp`)
- **平台池** `build_mcp_pool`(`runtime.py:969`):从**静态 JSON 文件**(`settings.mcp_servers_config_file`)启动建一次、进程级;bearer 走 operator 填的 `auth_config["token_ref"]`→`SecretStore`。**无 admin-UI、无 DB**。
- **租户池** `tenant_mcp_pool`:`tenant_mcp_server` 行。
- **user-OAuth 池** `user_mcp_oauth_pool`:`mcp_oauth_connection` 行。
- **合并优先级 平台 > 租户 > user-OAuth**;平台池按 per-tenant `mcp_allowlist`(名字)过滤;三池都汇成 `MCPServerConfig`(name/transport/url/headers/auth_type/auth_config{token_ref,headers_ref,client_id}/timeout_s/sse_read_timeout_s)。

### 2.3 前端
- 平台:`SettingsMcpCatalog` → `CatalogEntryDrawer`(扁平模板表单 + `AuthSchemaBuilder` + `url_template`)。
- 租户:`SettingsMcpServers` → `AddMcpServerDrawer`(browse 目录 → `InstantiateCatalogForm` 填字段 / `OAuthConnectForm` 授权)+ `CreateMcpServerDrawer`(自建,**已 tab 化** 基本/请求头/配置)。
- **截图「重复按钮」真相**:`SettingsMcpCatalog` 页面 header「新建连接器」+ 空态各一个 add 按钮(抽屉 footer 是单个);顺手清理。

## 3. 目标模型

### 3.1 概念:作用域 × 认证

```
MCP server
├─ 作用域 = 平台(system_admin 配,租户选择启用)
│   ├─ 认证 无            → 直接用
│   ├─ 认证 Bearer(共享)  → 平台填 token,启用租户共用身份(A)
│   └─ 认证 OAuth(per-user)→ 平台填 client_id,每用户各自授权(B,已建)
└─ 作用域 = 租户(租户自建私有,租户填全部)  ← 已建,不动
```

### 3.2 砍掉(模板模型的复杂度)
- `url_template` 的 `{param}` 占位符 + 实例化期 URL 参数替换 → **改具体 URL**。
- `auth_schema` 字段构造器 + 实例化期「租户填字段」流 → **删**。
- 理由:平台配好真 server(A 平台填 token / B per-user OAuth)+ 租户自建,已覆盖全部需求。原「平台列模板、租户填自己 token」的中间态(如各租户填自己 GitHub PAT)→ 由「租户自建私有 server」覆盖;GitHub 这类更该用 B(per-user OAuth)。**这是 owner 已认可的方向**(模板模型即「错方向」本身)。

## 4. 设计决策

### D1. 复用 `mcp_connector_catalog` 表承载平台 server(不新建表)
表已是 NULL-tenant 平台行,有 oauth 字段、RLS、admin 端点、租户列表端点。改造而非另起:
- `url_template` 列**保留列名、语义改具体 URL**(去占位符替换),避免 rename churn;文档 + 校验明确。
- 新增 **`bearer_token_ref TEXT NULL`** 列(`secret://`),承载 A 类平台共享 token。
- `auth_schema` 列**保留可空、停用**(不删列避免破坏性迁移;protocol/UI/实例化全部移除引用),后续可单独清。

### D2. 平台共享 token(A)= 平台级 SecretStore secret
- 平台填 bearer token → 写 `SecretStore`,key 形如 `helix-agent/platform/mcp/{catalog_name}/token`(平台命名空间,非 tenant);catalog 行只存 `bearer_token_ref`。
- 复用现有 `_build_mcp_client` 的 `token_ref`→`secret_store.get` 注入路径(平台池建客户端时解析)。**写穿不进 DB 明文**,沿用 bearer write-only / blank-to-keep 编辑范式。
- **安全:共享身份显式告警**。A 类所有启用租户/用户共用一份凭证 → 同一份数据。UI 在选 Bearart(共享)时明确提示「不适合按用户/租户隔离数据的服务(如 GitHub、业务系统),那些请用 OAuth」。平台 admin 自担选择。

### D3. 运行期平台池 DB-only(owner 拍板,dev 阶段不并存)
- 新增 **DB 平台池**:从 `mcp_connector_catalog` 读 `enabled` 且 `auth_type ∈ {none,bearer}` 的平台行 → `MCPServerConfig`(bearer 用 `bearer_token_ref`)。进程级缓存 + catalog 改动失效(镜像 `tenant_mcp_pool` 的 generation 失效范式)。**这是平台 server 的唯一受管来源**。
- **静态文件平台池保留为空闲遗留代码**:`mcp_servers_config_file` 当前未设 = 空、零参与;**不写文件×DB 两源合并逻辑**。catalog 只支持 sse/streamable_http,stdio 仅能经文件池。
- **stdio 延后**:dev 阶段无 stdio 需求。以后真要 → 加回「文件池(operator stdio,host 控,RCE 面永不上 UI)+ DB 池」平台层合并(~十几行,按 name 去重);**stdio 永不进 catalog/UI**(否则把 RCE 搬进 admin UI)。不现在做,不被锁死。
- **B(oauth2)平台行不进平台池** —— 仍走 user-OAuth 池(per-user),不变。

### D4. 「租户选择使用」= 复用并补 UI 的 `tenant_config.mcp_allowlist`
- 该名字列表已在过滤平台层。**A**:租户启用某平台 server → 名字进 allowlist → 该租户 agents 用共享凭证连。**B**:租户启用 oauth 平台 server → 名字进 allowlist → 该租户用户可见、可授权(per-user `mcp_oauth_connection` 再激活个人 token)。
- `required_tier` 仍为**能否启用**的闸(tier_satisfies,启用时校验非热路径)。allowlist = 已启用集合。
- 补 UI:租户「MCP 市场」页 —— 列 entitled 平台 server,A 给「启用」开关,B 给「启用」+ 用户「授权」。

### D5. 前端表单收敛成统一 tab 版(像 owner 给的参考)
- **平台「配 server」抽屉**(替换 `CatalogEntryDrawer` 模板表单):
  - **基本**:名称(slug)、显示名、描述、分类、图标、传输、**具体 URL**
  - **认证**:auth_type = 无 / Bearer(平台共享)/ OAuth(per-user);Bearer→平台 token(写 SecretStore,blank-to-keep);OAuth→client_id + scopes(+ 共享身份告警见 D2)
  - **高级**:required_tier、timeout、sse_read_timeout、enabled
  - 删 `url_template` 占位提示 + `AuthSchemaBuilder`;修页面重复 add 按钮
- **租户「选择使用」**:`CatalogBrowser` 卡墙保留;A 卡片改「启用」开关(无字段填写),B 卡片走现有 `OAuthConnectForm` 授权;删 `InstantiateCatalogForm` 的填字段流。
- **租户自建** `CreateMcpServerDrawer`:不动(已 tab 化)。

## 5. 后端改动清单

| 区域 | 改动 |
|---|---|
| 迁移 | `00XX`:catalog 加 `bearer_token_ref TEXT NULL`;`auth_type` CHECK 不变(已含 oauth2);`auth_schema` 留列停用 |
| protocol `mcp_connector_catalog.py` | `url_template` 语义注释改具体 URL;加 `bearer_token_ref`(Record)/ 写侧不暴露 ref 仅收 token;auth 一致性校验:bearer→需 `bearer_token_ref`(非 auth_schema secret 字段)、去 auth_schema 依赖;`McpConnectorAuthField/Schema` 标 deprecated |
| persistence catalog store | create/update/行映射加 `bearer_token_ref` |
| `api/mcp_catalog.py` | create/patch 收 `bearer_token`(明文)→ 写 SecretStore + 存 ref;`_public` 不回 ref(回 `has_bearer_token` 布尔);去 auth_schema |
| 运行期 `runtime.py` | 新 DB 平台池 builder(读 catalog none/bearer 行→config,bearer 用 bearer_token_ref);缓存 + 失效;与文件池同层合并 |
| `app.py` | 装配 DB 平台池 provider + 失效钩子(catalog 改动) |
| `api/mcp_servers.py` | 删/废 `instantiate_catalog_entry`(填字段流);租户 enable/disable 平台 server 端点(写 `tenant_config.mcp_allowlist`) |
| `tenant_config` | `mcp_allowlist` 读写端点(若无)+ RBAC |

## 6. 前端改动清单

| 区域 | 改动 |
|---|---|
| `CatalogEntryDrawer` | 重写为 tab 版(基本/认证/高级),去 `url_template` 占位 + `AuthSchemaBuilder`;加 Bearer 平台 token 输入 + OAuth client_id;共享身份告警 |
| `SettingsMcpCatalog` | 去重复 add 按钮;列加「认证类型」徽章(无/共享Bearer/OAuth) |
| `AuthSchemaBuilder` / `validation.ts` | 退场(删引用;文件可留待清) |
| `CatalogBrowser` | A 卡片「启用」开关;B 卡片保留授权 |
| `InstantiateCatalogForm` | 退场(A 改开关,无字段填写) |
| `OAuthConnectForm` | 保留(B) |
| `api/mcp-catalog.ts` | 类型去 `auth_schema`/`url_template` 占位语义,加 `bearer_token`(写)/`has_bearer_token`(读);加 enable/disable 平台 server fn |
| i18n / nav / stories / e2e | 同步 |

## 7. 迁移 / 向后兼容
- seed 已空、目录现无行(本会话 #784 清空)→ 数据迁移风险低。
- 新列 `bearer_token_ref` 可空,旧行(无)不受影响。
- `auth_schema` 留列默认 `{}`,停读;`url_template` 旧值仍是 URL(旧模板含 `{param}` 的已无行)。
- 静态文件平台池不动,旧 operator 配置照跑。

## 8. 安全
- **A 共享身份隔离**:见 D2,UI 强告警 + 仅适合无隔离需求工具;敏感数据服务强制走 B。
- **SSRF**:平台填的具体 URL 仍过 `validate_remote_url`(挡私网/元数据);URL 不再有租户填的占位参数 → host-pivot 面消失(原 `_DISALLOWED_PARAM_CHARS` 防护随模板删除而不再需要)。
- **secret 存储**:平台 token 仅 `secret://` ref 进 DB,值进 SecretStore;日志只记 catalog 名不记值。
- **RBAC**:平台配 server = `mcp_catalog` write + system_admin;租户 enable = 租户 admin(`mcp_server` write 或新 `mcp_catalog` tenant-read + enable)。

## 9. 分期(迭代计划,待评审后细化)

- **P1 后端 — 平台 A 类共享 server**:迁移加 `bearer_token_ref` + protocol/persistence/api 改造(去 auth_schema 依赖,收平台 token)+ DB 平台池运行期 + 失效。**B 已建无需动核心**。
  - ✅ **P1a**(#786):迁移 + protocol/persistence/api 存平台 token(`bearer_token_ref`)。
  - ✅ **P1b-1**(#787):`platform_mcp_pool.py` 池服务(构建器)— catalog `none`/`bearer` 行 → 进程级 `MCPServerPool`,懒建 + 代际失效。
  - ✅ **P1b-2**:装配 — `ToolEnv.platform_mcp_pool`(assembly 与文件池同层、走 `mcp_allowlist` 闸、文件池胜命名冲突)+ 三处 builder(顶层/子 agent/worker)接 provider + `AgentRuntime.invalidate_all` + catalog API create/patch/delete 钩失效(池 + 全 agent 缓存)。**真栈 live 验待**(catalog 现空,需先 P3 配 server)。
- **P2 后端 — 租户 enable**:`mcp_allowlist` 读写端点 + tier 闸;删/废 instantiate 填字段流;**B 的 initiate 前加「租户已启用」校验**(名字在 allowlist 才许授权)。
  - **opt-in 语义(P1b-2 interim 反转)**:assembly 中 **DB 平台目录池改为 opt-in** —— server 名必须在 `mcp_allowlist` 才注册,空 allowlist = 零目录 server(不再「空=全可见」)。文件池(operator 静态、D3 定为 DB-only 不并存→实际空)保留 Stream O 语义(空=全),故复用同一 `mcp_allowlist` 无双闸冲突。决策 #4「先启用才可见」由此落地。
  - **enable/disable 端点(#789 + audit followup)**:`POST /v1/mcp-servers/catalog/{id}/enable` + `DELETE /v1/mcp-servers/catalog/{id}/enable`(disable)—— 解析目录项取 `name`,读 `tenant_config` → 增删 `mcp_allowlist` 名 → `_invalidate_tenant_mcp`(重建以重新过闸)。enable 带 tier 闸(`tier_satisfies`)+ 仅 `enabled` 目录项可选;两者幂等。RBAC = tenant admin(`mcp_server` write,同 instantiate)。审计 `mcp_catalog:enable`/`mcp_catalog:disable`(resource=`mcp_connector_catalog`,仅实际变更时)。无 `tenant_config` 行 → enable 返 409 `TENANT_NOT_CONFIGURED`(allowlist 落 tenant_config 首写需 display_name);disable 对无 config 幂等 no-op。
  - **`GET /catalog`**:每项加 `tenant_enabled`(name ∈ 租户 allowlist)供前端开关态(`enabled` 字段已被目录项自身的平台启用标志占用)。
  - **oauth `initiate` 闸**:`entry.name ∈ 租户 allowlist` 否则 403 `MCP_CATALOG_NOT_ENABLED`(A/B 统一租户启用,B 再叠 per-user 授权)。
  - **`instantiate_catalog_entry` 退场推迟到 P4**:#789 **未动** instantiate(仍为旧 auth_schema 填字段流);删除与前端 `InstantiateCatalogForm` 退场(P4)同步,避免中间态破坏。
- **P3 前端 — 平台配 server 表单**:`CatalogEntryDrawer` tab 重写 + 去 AuthSchemaBuilder + 修重复按钮。
- **P4 前端 — 租户选择使用**:`CatalogBrowser` A 开关 + B 授权;退场 `InstantiateCatalogForm`。
- **P5 收尾**:i18n/stories/e2e、文档(本设计 + runbook)、`auth_schema`/`AuthSchemaBuilder` 退场清理。

每期独立 PR + CI 绿 + live 验(A 真起共享 server 调通 / B 已 live 验过)。

## 10. 决策点(owner 全拍板,2026-06-24)

1. ✅ **D1 复用 catalog 表**(不新建 `platform_mcp_server` 表):加 `bearer_token_ref`,`url_template`/`auth_schema` 留列停用。
2. ✅ **D3 平台池 DB-only**:文件池留作空闲遗留代码,不并存;stdio 延后(以后加回文件池合并,永不上 UI)。
3. ✅ **D4 租户启用复用 `tenant_config.mcp_allowlist`**(不新建启用表)。**B(oauth)也加租户级启用闸**:tenant admin 先启用某 oauth 平台 server → 该租户用户才可见、可授权(A/B 统一走租户启用,B 再叠 per-user 授权)。
4. ✅ **砍 `auth_schema`/模板模型**:「平台上架、租户填自己 token」边角场景退给「租户自建私有 server」;GitHub 类用 B(per-user OAuth)。
