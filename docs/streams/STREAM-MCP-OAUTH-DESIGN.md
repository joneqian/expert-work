# Stream MCP-OAUTH — per-user OAuth 2.1 for MCP connectors

> 解锁 OAuth-only 的托管 MCP 连接器(Notion / Linear / Jira / Confluence / Sentry /
> Asana …)。这些 server 的托管端点**只支持交互式 OAuth 2.1 授权码流程**,不接受
> 粘贴静态 token,因此现有 `auth_type ∈ {none, bearer}` 的 catalog 无法接入。
> 本 Stream 给 catalog 增加 `oauth2`,实现 MCP 授权规范要求的 per-user 授权流程。

**拍板(AskUserQuestion):** 授权主体 = **per-user**(每用户授权自己的账号,agent 以
该用户身份访问);首个端到端验证连接器 = **Linear**。

---

## 1. 背景:MCP 授权规范(已核实)

MCP 授权 = **OAuth 2.1 + PKCE(强制)** + 资源/授权服务器**元数据发现**:

1. client 调 MCP server → `401` + `WWW-Authenticate` 指向 **Protected Resource
   Metadata**(RFC 9728)。
2. 从 PRM 发现 **Authorization Server Metadata**(RFC 8414)→ 拿 authorize /
   token 端点。
3. 浏览器跳转 authorize(带 `code_challenge`/PKCE + `state`)→ 用户登录授权 →
   回调带 `code`。
4. `code` + `code_verifier` 换 `access_token`(短期)+ `refresh_token`。
5. 后续以 `Authorization: Bearer <access_token>` 调 MCP server;过期用
   refresh_token 续。

动态客户端注册(DCR / RFC 7591)已被规范标注 deprecated(转向 Client ID Metadata
Documents);**本 Stream 优先用静态预注册的 client_id**(在平台为每个连接器登记一次
OAuth app),DCR/CIMD 留后续。

## 2. 现状与改造点(grep 核实)

- `CatalogAuthType` / `McpServerAuthType` = `Literal["none","bearer"]` →
  **加 `"oauth2"`**(双 Literal:protocol + 任何 control-plane 镜像;[[project_audit_literal_drift]] 同理)。
- orchestrator `MCPServerConfig.auth_type` 已含 `oauth2`,但 `_build_mcp_client`
  对 oauth2 **fail-fast**(`MCPOAuthNotImplementedError`,Mini-ADR U-12)→ 本 Stream
  替换为"解析 per-user access token → 注入 Bearer 头(临过期先 refresh)"。
- `Principal.subject_id: str`(用户身份)、`tenant_id: UUID` → 连接按
  **(tenant_id, subject_id, connector)** 维度存。
- `TenantMcpPoolService` 仅按 `tenant_id` 缓存 pool;OAuth 连接器是 per-user →
  **pool 解析要按 (tenant_id, user_id) 维度**(见 Mini-ADR OA-4)。
- token 进 `secret_store`(明文不入 DB,只存 `secret://` ref;沿用 U-11)。

## 3. 数据模型

新表 **`mcp_oauth_connection`**(per-user 连接实例,与 `tenant_mcp_server` 区分:
后者是 bearer/none 的租户级实例,前者是 oauth2 的用户级实例):

| 列 | 说明 |
|---|---|
| `id UUID` PK | |
| `tenant_id UUID` FK, NOT NULL | RLS 租户隔离 |
| `user_id TEXT` NOT NULL | = `principal.subject_id` |
| `catalog_id UUID` FK → mcp_connector_catalog | 来自哪个 catalog 连接器 |
| `name TEXT` | 解析后的 server 名(同 slug 规则) |
| `status TEXT` | `pending`(发起未回调)/ `connected` / `expired` / `revoked` / `error` |
| `access_token_ref TEXT` NULL | `secret://…/access`,临过期由 refresh 刷新 |
| `refresh_token_ref TEXT` NULL | `secret://…/refresh` |
| `token_expires_at TIMESTAMPTZ` NULL | 提前 N 秒触发 refresh |
| `scopes TEXT` | 已授权 scope |
| `resolved_url TEXT` | 实例化时快照(同 W-2 快照原则) |
| `last_refresh_at` / `last_error` | #2 风格的状态可观测(与 health Stream 一致) |
| `created_at` / `updated_at` | |
| unique `(tenant_id, user_id, catalog_id)` | 一个用户对一个连接器一条连接 |

catalog 侧:`auth_type` 增 `"oauth2"`;新增可空列 `oauth_client_id`、
`oauth_scopes`(平台为该连接器登记的 OAuth app 信息)。token 端点/PRM 走运行时发现,
不入库。

**PKCE/state 暂存**:`pending` 连接的 `code_verifier` + `state` 短期存(进程内 TTL
缓存或 `mcp_oauth_connection` 上的临时列 + 过期清理);回调用 `state` 找回。

## 4. 端点(per-user,RBAC `mcp_server`)

| 方法 | 路径 | 作用 |
|---|---|---|
| POST | `/v1/mcp-servers/catalog/{catalog_id}/oauth/initiate` | 发现 PRM/authz → 生成 PKCE+state → 建 `pending` 连接 → 返回浏览器 authorize URL |
| GET | `/v1/mcp-oauth/callback` | **公网回调**:校验 state(CSRF)→ code+verifier 换 token → 存 secret ref → 置 `connected` → invalidate 该用户 pool |
| GET | `/v1/mcp-servers/oauth/connections` | 列当前用户的 OAuth 连接 + 状态 |
| DELETE | `/v1/mcp-servers/oauth/connections/{id}` | 断开(撤销 + 删 secret + 删行 + invalidate) |

回调是平台公网 redirect URI(部署需登记到各连接器 OAuth app 的 allowlist;`state`
做 CSRF + 关联租户/用户)。

## 5. token 生命周期 / 运行时

- agent 构建时(server-side、用户不在场):按 (tenant_id, user_id) 解析该用户的
  OAuth 连接 → 取 access_token;**临过期(< skew)先 refresh**(refresh_token 换新
  access)→ 注入 `Authorization: Bearer`。
- refresh 失败(被撤销/refresh 过期)→ 连接置 `expired`/`revoked`,该连接器对该
  用户**不可用**(工具不装配 + 状态可见,提示重新授权);不拖垮其余工具。
- `_build_mcp_client` 的 oauth2 分支:从 `auth_config` 解析 access_token_ref(由
  pool 解析层在 refresh 后填入),注入 Bearer。

## 6. per-user pool 解析(OA-4)

`TenantMcpPoolService` 现按 tenant 缓存。引入 **per-(tenant,user) 维度**:OAuth 连接
按用户解析/缓存;bearer/none 的 `tenant_mcp_server` 仍按 tenant。两类合并进该用户
agent 的工具集。invalidate 粒度细化到 (tenant,user)(发起/回调/断开/refresh 失败时)。

## 7. 分阶段(每阶段独立 PR,设计先行已完成)

- **OA-0 设计**(本文档)。
- **OA-1 数据面**:`oauth2` 双 Literal + catalog oauth 列 + `mcp_oauth_connection`
  表/model/store + migration(revision ≤ 32 字符,[[feedback_alembic_revision_id_32_chars]])。
- **OA-2 OAuth 引擎**:PRM/authz 发现 + PKCE + initiate/callback + token 交换 + refresh;
  纯逻辑 + secret 存储,单测覆盖发现/PKCE/state/refresh。
- **OA-3 Linear 垂直切片**:catalog 加 Linear(oauth2)+ per-user pool 解析 +
  `_build_mcp_client` oauth2 分支 + 端到端(发起→回调→agent 用 Linear 工具)。
- **OA-4 per-user pool 维度** + 连接管理端点(list/disconnect)+ 状态可观测。
- **OA-5 扩连接器**:Notion / Jira / Sentry / Asana 进 catalog(复用引擎)。
- **OA-6 刷新/UX 硬化**:后台预刷新、撤销处理、过期提示、审计/metrics 补齐。

## 8. 安全

PKCE 强制;`state` 防 CSRF + 绑 tenant/user;redirect URI allowlist;token 仅
`secret://` ref(不入 DB / audit / 日志);scope 最小化;回调严格校验
`state`/`code`;RLS 租户隔离 + 行内 user_id 过滤(用户只见/删自己的连接)。
[[feedback_codeql_clear_text_logging_secret_name]] / [[feedback_codeql_log_injection_request_taint]] 注意脱敏。

## 9. Out of scope

- per-tenant 共享 OAuth 连接(本轮只 per-user;确认过)。
- DCR / CIMD(用静态预注册 client_id;后续按需)。
- 非 MCP 的通用 OAuth(本 Stream 专注 MCP 连接器)。

## 10. Verification

- 发现:对 Linear 端点解析 PRM(RFC 9728)→ authz metadata(RFC 8414)。
- PKCE/state:verifier/challenge 配对正确;回调 state 不匹配 → 拒。
- token:code 换 token 存 secret ref;临过期触发 refresh;refresh 失败置 expired。
- 隔离:用户 A 看不到/删不了用户 B 的连接;RLS 跨租户零行。
- e2e:发起 → 回调(mock 授权服务器)→ agent 以该用户身份成功调用 Linear 工具。
- 降级:连接 expired → 该连接器不装配,其余工具照常。

## 11. CI / 约束

`oauth2` 双 Literal(protocol + control-plane 镜像);migration revision ≤ 32 字符;
pytest `-m "not integration"`(回调/refresh/隔离的真 PG 用例进 integration);
CodeQL 脱敏(token / state 不进日志);每 PR 零技术债 + 同步 ITERATION-PLAN。
