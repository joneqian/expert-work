# Stream W — MCP Connector Catalog（平台精选目录 + 租户实例化 + entitlement 地基）设计先行

> 大方向见 [memory:project_platform_centralized_governance]:2026-06-03 用户拍板把 helix 从"租户自助"推向"平台中心化治理 + 变现"。MCP 决策 = **平台目录 + 租户实例化**(Zapier/Dify/Coze 模式)。本 Stream **演进** Stream V,不废弃、不破坏任何已上线行/端点。MCP 仍只做 client,见 [memory:project_mcp_direction_client_only](方向不变,只是治理从自助改目录)。

## 0. 背景 / 缺口

Stream V 已让租户自助注册远程 MCP server(URL + token,自己探测落库,encrypted secret 存 token)。但**纯自助 = 结构混乱**:

- 每个租户各填各的 URL,平台无法治理"哪些连接器是官方推荐/可信的"。
- 无法把"高级连接器"作为 premium 卖点(无变现抓手)。
- 新租户面对空白表单,不知道能接什么(引导差)。

**关键洞察(为什么不能"完全平台独占")**:MCP 连接天然 **per-company** —— 租户 A 连的是*它自己*的 GitHub 组织 + *它自己*的 token,租户 B 连的是*它的* Salesforce。平台物理上不可能替所有人持有"那一个" GitHub 连接。

**正解 = 平台拥有「连接器类型」,租户用自己凭证「实例化」**:
- 平台(system_admin)维护精选**目录**:"官方 GitHub 连接器"、"官方 Postgres 连接器"…定义 transport / URL 模板 / 需要哪些凭证 / 档位门控。
- 租户从目录选一项,填**自己的**凭证 → 产出一个绑定到本租户的可连 server(落 `tenant_mcp_server`,带 `catalog_id`)。
- premium 目录项按租户 `plan` 档位门控(变现抓手)。

**用户拍板(2026-06-03,AskUserQuestion):MCP = 平台目录 + 租户实例化。**

**本 Stream 还顺带交付 entitlement 共享地基**(`tier_satisfies`),Stream X(平台 Skill 库)/ Y(LLM 档位)/ Z(分账)都复用 —— 设计一次。

## 1. Mini-ADRs

- **W-1 平台目录表 `mcp_connector_catalog`(新迁移 `0055_mcp_connector_catalog`)**:平台级,**照抄 `0050_encrypted_secret` 的 NULL-tenant RLS 模式**(不是 `tenant_mcp_server` 的严格相等)。列:`id` UUID PK / `tenant_id` UUID **NULL**(恒为 NULL=平台全局;保留列 + `IS NOT DISTINCT FROM` policy,未来 per-tenant 私有目录是非迁移变更)/ `name`(slug,复用 `^[a-z0-9][a-z0-9_-]{0,63}$`,= 实例默认名)/ `display_name` / `description` / `category`(默认 `general`)/ `icon` NULL / `transport`(CHECK `IN ('sse','streamable_http')`)/ `url_template`(纯 URL 或带 `{placeholder}`)/ `auth_type`(CHECK `IN ('none','bearer')`)/ `auth_schema` JSONB(声明租户需提供的字段,见 W-5)/ `required_tier`(CHECK `IN ('free','pro','enterprise')`,默认 `free`,变现门控)/ `enabled` Bool / `created_at` / `updated_at` / `updated_by`。唯一索引 `(COALESCE(tenant_id,'00000000-...'::uuid), name)`(Postgres NULL 互不相等,plain UNIQUE 不去重)。RLS `ENABLE`+`FORCE` + `IS NOT DISTINCT FROM` policy。persistence `mcp_connector_catalog/{base,sql,memory}.py` + `models/` + protocol records,镜像 `SqlPlatformSecretStore`;SQL store 所有访问包 `bypass_rls_session()`(平台表)。

- **W-2 `tenant_mcp_server` 加 `catalog_id`(新迁移 `0056_mcp_server_catalog_id`)**:加**一列** `catalog_id UUID NULL REFERENCES mcp_connector_catalog(id) ON DELETE RESTRICT`。`NULL`=off-catalog 自定义行(**所有 Stream V 现有行,零回填、零行为变更**);非 NULL=目录实例。`ON DELETE RESTRICT` + delete-in-use 409,防目录项被有活实例时删掉(同 `mcp_servers.py:480-502` 既有 manifest 引用 409 模式)。**实例化时把解析后的 `url`/`transport`/`auth_type` 快照到行上**——目录后续改模板**不**静默改活实例(显式 re-sync 是 follow-up);代价是 `tenant_mcp_pool.py` / `assembly.py` **零改动**(仍读 `record.url/transport/auth_type/token_secret_ref`)。protocol `TenantMcpServerRecord` 加 `catalog_id: UUID | None = None`;catalog 绑定 create 后不可改(re-instantiate 重绑,同 V 的"auth-type 改动 out of scope")。

- **W-3 entitlement 原语(X/Y/Z 共享)**:**不建新权限表**。复用现成 `tenant_config.plan`(`TenantPlan` free/pro/enterprise,`protocol/tenant_config.py:76,121`,从未用于 gating)。在 **`helix-protocol`**(`protocol/entitlement.py`)加 `TIER_ORDER = {free:0,pro:1,enterprise:2}` + `tier_satisfies(tenant_tier, required_tier) -> bool`。**放 helix-protocol 而非 helix-common**:它是纯 `TenantPlan` 函数,且 helix-common 未声明对 helix-protocol 的依赖——放 protocol(各服务都已依赖的底层)避免新增跨包依赖。被门控资源各加 `required_tier` 列,在**写入/实例化时**门控(非运行热路径→降级不打断在跑的 agent;需要时另起 sweep job 停超档实例)。实例化端点:租户 `plan` vs catalog `required_tier`,不满足返 403 `MCP_CATALOG_TIER_REQUIRED`。详见 [memory:reference_billing_meter_and_entitlement]。

- **W-4 自定义逃生口 + per-tenant kill-switch**:保留 off-catalog 自定义注册(不破坏 V 契约;Zapier/Dify/Coze 都留 BYO 路径),但加 `tenant_config.allow_custom_mcp_servers`(默认 `true`,平台可 per-tenant 设 `false` 强制 catalog-only,服务高治理客户)。`TenantConfigRecord/Patch` + `tenant_config` 列各加一字段(复用 `memory_purge_enabled` 等模式)。`POST /v1/mcp-servers`(自定义,`catalog_id=None`)在 flag false 时返 403 `MCP_CUSTOM_DISABLED`;目录实例化端点不受影响。UI 把"自定义"降级为 "Advanced / 自定义 server" 次级入口,目录实例化为主路径。

- **W-5 `auth_schema` 形态**:声明式字段列表 `{key, label, kind: "secret"|"param", required: bool}`。`kind="secret"`(如 GitHub token)→ 写 encrypted secret store 绑 `token_secret_ref`(复用 `_token_secret_name` + `secret_store.put`,与 V 同款);`kind="param"`(如 `org`)→ 填 `url_template` 占位。bearer 常见形态 = 单个 secret 字段。protocol 定 `McpConnectorAuthSchema`(在新 `protocol/mcp_connector_catalog.py`,与 `McpConnectorCatalogRecord/Patch/Upsert` 并列)。

- **W-6 平台目录 CRUD API(system_admin)**:新路由 `api/mcp_catalog.py`,前缀 `/v1/platform/mcp-catalog`。`POST`/`GET`(按 category/tier 过滤)/`GET/{id}`/`PATCH/{id}`/`DELETE/{id}`(409 if 有 `tenant_mcp_server.catalog_id` 引用)。RBAC 加 `mcp_catalog` 资源(admin write);平台 scope 故额外 require `principal.is_system_admin`。写经 `bypass_rls_session()`。

- **W-7 租户实例化流程(扩 `mcp_servers.py`,不 fork)**:
  - `GET /v1/mcp-servers/catalog` —— 列目录,每项返 `auth_schema` / `required_tier` / 计算的 `entitled: bool`(`tier_satisfies` vs 本租户 plan),UI 据此显示锁定的 premium 项。读目录经 `bypass_rls_session()`(见 W-8)。
  - `POST /v1/mcp-servers/catalog/{catalog_id}/instances` —— 实例化。body `{name?, params:{...}, secrets:{field_key: SecretStr}}`。流程(**全程复用 V 的 helper**):① 读目录项(bypass_rls,404 if 缺/disabled)② 档位门控 `tier_satisfies` → 403 ③ 按 `auth_schema` 校验 params/secrets,从 `url_template`+params 解析 `url`,过 `validate_remote_url`(SSRF,V 已有)④ `probe_remote_mcp`(**不动**)⑤ `secret_store.put` → `token_secret_ref` ⑥ `store.create(..., catalog_id=entry.id)`(store 加 `catalog_id` kwarg,additive default None)⑦ audit + `_invalidate_tenant_mcp`(**不动**)。
  - `GET /v1/mcp-servers/available`(`mcp_servers.py:316-333`):`source:"tenant"` 项补 `catalog_id`/`catalog_name`,UI 区分目录实例 vs 自定义。其余不变。

- **W-8 RLS 陷阱(最高风险)**:**租户读平台目录必须走 `bypass_rls_session()`** —— 租户请求已设 `app.tenant_id`,`IS NOT DISTINCT FROM` policy 会把 NULL-tenant 目录行**全部隐藏**(租户会看到空目录)。目录是"对所有租户公开的参考数据"(像价目表),不是租户私有数据。catalog list / instantiate 读目录都在 service 层 bypass。PR 必带 RLS 回归测试(租户 session 不 bypass 读不到 NULL 行、bypass 读得到;两租户直连互不可见对方实例)。

- **W-9 运行时零改动(兼容锚)**:`tenant_mcp_pool.py`(`_record_to_config` 只读 url/transport/auth_type/token_secret_ref/enabled/name)与 `assembly.py` `_register_mcp` 租户 pool 循环**不改一行**。这是 W-2 快照解析值的回报。PR 4 复跑 V 的 pool 测验证。

- **W-10 审计双 Literal 同步**:新增 `AuditAction`(`MCP_CATALOG_CREATE`/`_UPDATE`/`_DELETE`)+ `ResourceType`(`MCP_CONNECTOR_CATALOG`)。`AuditAction` protocol 单一 StrEnum;`ResourceType` protocol + control-plane **两处同改**([memory:project_audit_literal_drift])。审计记 `name/category/required_tier/actor`,**绝不记租户实例化时填的 secret 值**。

- **W-11 范围边界**:目录模板改→活实例 re-sync(follow-up,现为快照)/ per-tenant 私有目录(列已留 NULL,本 Stream 只平台全局)/ oauth2 connector(沿用 V 只 none/bearer)/ à-la-carte per-feature entitlement(现 tier 比较够用)= out of scope。

## 2. 迁移链与数据模型要点

- 链:`0055_mcp_connector_catalog`(down_revision = 当前 head,即 `0054_tenant_mcp_server`)→ `0056_mcp_server_catalog_id`(down_revision=`0055...`)。FK 目标必须先建(0055 在 0056 前)。revision id `0055_mcp_connector_catalog`=26 / `0056_mcp_server_catalog_id`=26,均 ≤32([memory:feedback_alembic_revision_id_32_chars])。两个 downgrade 都是干净 drop。
- 迁移安全:`catalog_id` nullable + 无回填 → 所有 Stream V 行 = 合法"自定义"实例。PR 2 带迁移测:在有 V 旧行的库上跑 0054→0055→0056,断言旧行全 `catalog_id=NULL` 且仍可读可用。

## 3. PR 切分(每个 CI 全绿、零技术债)

- **W0 设计文档**(本 PR):`STREAM-W-DESIGN.md` + `ITERATION-PLAN.md` 加 Stream W backlog。无代码。
- **W1 协议 + entitlement 原语**:`protocol/mcp_connector_catalog.py`(records + `McpConnectorAuthSchema`);`TenantMcpServerRecord` 加 `catalog_id`;`TenantConfigRecord/Patch` 加 `allow_custom_mcp_servers`;`helix-protocol` 加 `protocol/entitlement.py`(`TIER_ORDER`/`tier_satisfies`)。纯 schema + 单测(`tier_satisfies` 边界、auth_schema 校验),无 DB。
- **W2 持久化**:迁移 `0055`(catalog + RLS)+ `0056`(`catalog_id` 列);ORM models;`McpConnectorCatalogStore` base/sql/memory;`TenantMcpServerStore.create` 加 `catalog_id` kwarg + `tenant_config` 列。SQL + memory store 测、**RLS 测(W-8)**、**迁移安全测**。V 端点不动 ⇒ 绿。
- **W3 平台目录 CRUD API + RBAC**:`Resource` 加 `mcp_catalog` + grants;`api/mcp_catalog.py`(system_admin-gated,bypass_rls)接进 `create_app`。端点测含 authz(非 system_admin 403)+ delete-in-use 409。审计 Literal(W-10)。
- **W4 租户实例化 + 档位门控 + `/available` + 自定义 kill-switch**:catalog list + instantiate 端点;`tier_satisfies` 门控;`allow_custom_mcp_servers` 在自定义 POST 上 enforce;`/available` 补字段。全链路测(实例化→probe→secret→pool-invalidate)、档位拒、自定义关。**复跑 V pool 测**验证运行时未变(W-9)。
- **W5 Admin UI**:平台目录管理页(system_admin)+ 租户"从目录添加"向导(浏览→entitlement 锁标→`auth_schema` 驱动的凭证表单→测试→创建),扩 `CreateMcpServerDrawer.tsx`/`SettingsMcpServers.tsx`/`mcp-servers.ts`;自定义路径降级为 "Advanced"。i18n en/zh-CN 同步。
- **W6（可选）初始目录 seed**:GitHub / Postgres 等官方连接器,仿 `platform_secrets` env-seed,config flag 后。

## 4. Verification

1. **RLS(W-8,最高风险)**:租户 session 设了 `app.tenant_id` 时,经 bypass 能读 NULL-tenant 目录、不经 bypass 读到空;两租户直连互不可见对方实例。
2. **迁移安全**:有 V 旧行的库跑 0054→0055→0056,旧行全 `catalog_id=NULL` 且仍可用。
3. **E2E**:system_admin 建 "GitHub 连接器"(required_tier=pro)→ free 租户 `GET /catalog` 见该项 `entitled=false` → 实例化得 403 → 升 pro → 实例化(填自己 token)→ probe 通过 → Playground agent 选中该 server 调用工具。
4. **自定义 kill-switch**:关 `allow_custom_mcp_servers` 后 `POST /v1/mcp-servers` 返 403,目录实例化不受影响。
5. **运行时未回归**:V 的 pool / assembly 测全绿。
6. **通用**:`pre-commit run --all-files`、`pytest -m "not integration"`、mypy、前端 typecheck/test/build;CodeQL 不放 Protocol `...`/不 log secret 命名值;双 Literal 两处同改。

## 5. 复用锚点(exact files)

| 关注点 | 复用资产 | 文件 |
|---|---|---|
| 平台 NULL-tenant RLS 模板 | `encrypted_secret` | `packages/helix-persistence/migrations/versions/0050_encrypted_secret.py` |
| bypass-RLS 读写平台行 | `bypass_rls_session()` | `services/control-plane/src/control_plane/tenant_scope.py` |
| entitlement 原语来源 | `TenantPlan` / `tenant_config.plan` | `packages/helix-protocol/.../protocol/tenant_config.py:76,121` |
| 探测(不动) | `probe_remote_mcp` | `services/control-plane/src/control_plane/mcp_probe.py` |
| 池 + 失效(不动=兼容锚) | `TenantMcpPoolService` | `services/control-plane/src/control_plane/tenant_mcp_pool.py` |
| 加密凭证 | `secret_store.put` + `_token_secret_name` | `services/control-plane/src/control_plane/api/mcp_servers.py:146,227-234` |
| in-use 删除 409 模式 | manifest 引用 409 | `services/control-plane/src/control_plane/api/mcp_servers.py:480-502` |
| RBAC + system_admin 提权 | `is_allowed` / `is_system_admin` | `services/control-plane/src/control_plane/auth/rbac.py:151-169` |
| 租户 CRUD UI 壳 | drawer + settings 页 | `apps/admin-ui/src/{components/CreateMcpServerDrawer.tsx,pages/SettingsMcpServers.tsx,api/mcp-servers.ts}` |
