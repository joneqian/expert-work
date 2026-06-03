# Stream V — MCP Server 管理（租户自助注册远程 server + agent 表单按 server 选择）设计先行

> MCP 方向见 [memory:project_mcp_direction_client_only]:helix 是 **MCP client**,消费外部 MCP 生态(GitHub / Postgres / Linear / Notion),不自建 server。本 Stream 补齐"运行时齐全、管理面全缺"的能力缺口——从"配置一个 MCP server"到"在 agent 里勾选它"这条链路,目前**一格 UI 都没有**。

## 0. 背景 / 缺口（dogfood 实测发现 2026-06-03）

运行时早已生产级,但管理面/用户面几乎全缺:

**已有(后端运行时):**
- ✅ MCP 客户端 + 连接池 + 工具适配(`orchestrator/tools/mcp.py`):stdio(M0)+ 远程 `SseMCPClient`/`StreamableHttpMCPClient`(真实现,包 `mcp` SDK,header 注入 token,`repr=False` 防泄漏)。
- ✅ `MCPServerConfig` 已支持远程:`transport(stdio|sse|streamable_http)` + `url` + `auth_type(none|bearer|oauth2)` + `auth_config["token_ref"]→secret:// URI`(bearer 已校验要 token_ref;oauth2 fail-fast,Mini-ADR U-12 未实现)。
- ✅ 三层模型已设计:平台 server 清单(ops JSON,`command` 起子进程=RCE 故意不开 UI,Mini-ADR E-17)/ 租户 `mcp_allowlist`(能用哪些 server)+ `mcp_credentials`(`{server: secret_ref}`)+ `credentials_mode`(Stream O)/ agent `MCPToolSpec.allow_tools`(工具名过滤)。

**几乎全缺(管理 UI / 用户面):**
1. **Agent 表单 MCP = 一个开关 + 一个手打逗号分隔工具名的文本框**(`FormView.tsx` `af-tool-mcp`/`af-mcp-allow`)。**根本没法"选 server"**——manifest `MCPToolSpec` 连 server 选择字段都没有,agent 默认拿租户所有 allowed server 的工具,只能按工具名瞎打字滤。
2. **无枚举接口**:UI 不知道"本租户能用哪些 server / 这些 server 提供哪些工具",所以只能让用户手打。
3. **租户层 `mcp_allowlist` 只读**(`SettingsTenantConfig.tsx` 仅展示),`mcp_credentials`/`credentials_mode` 完全无 UI。
4. **无自助注册**:租户加不了自己的远程 MCP server(各自的 GitHub PAT),只能等 ops 改磁盘文件。

**用户拍板(2026-06-03):**
1. **server 来源 = 租户自助加远程 server**(URL + token,HTTP/SSE,无 RCE);stdio/command server 仍 ops-only。这条最贴 GitHub/Linear 那种真实托管 MCP,能彻底闭环。
2. **agent 层选择粒度 = 先选 server,再(可选)窄到工具**(给 `MCPToolSpec` 加 `servers` 字段)。
3. **平台 stdio server(ops 信任)与租户远程 server(自助)并存**,不是用注册表取代 allowlist。
4. **按租户建 pool**(私有 token 隔离)。

## 1. Mini-ADRs

- **V-1 租户 MCP server 注册表(新表 `tenant_mcp_server`)**:tenant-scoped + RLS。列 `id` UUID PK / `tenant_id` UUID(RLS)/ `name`(租户内唯一,= manifest 引用的 server 名)/ `transport`(`sse`|`streamable_http`,**拒 stdio**——stdio 起子进程是 RCE,只能 ops 配)/ `url` / `auth_type`(`none`|`bearer`)/ `token_secret_ref`(`secret://...`,bearer 时必填)/ `timeout_s`(默认 30)/ `enabled` Bool / `created_at` / `created_by`。**token 明文绝不进此表**——走 encrypted secret store(Stream T 已落地),表里只存 ref。persistence store `base/sql/memory` 三件套,镜像 `SqlPlatformSecretStore` 模式。

- **V-2 token 走 encrypted secret store**:注册/换 token 时 body `token: SecretStr` → `secret_store.put("helix-agent/tenant/<tid>/mcp/<name>", value)` → 表里存返回的 `secret://` ref([memory:project_web_paste_key_direction] 同款路径,与 LLM key web 粘贴一致)。换 token = secret 新版本,ref 字符串不变。运行时 `secret_store.get(parse_secret_ref(ref))` 解析,注入 bearer header(沿用既有 `_RemoteMCPClientBase.resolved_headers`,U-11 token 不进 dataclass repr)。

- **V-3 manifest `MCPToolSpec.servers` 字段**:
  ```python
  class MCPToolSpec(BaseModel):
      type: Literal["mcp"] = "mcp"
      servers: list[str] = Field(default_factory=list)      # 新:空=本租户全部可用 server
      allow_tools: list[str] = Field(default_factory=list)  # 旧:空=选中 server 的全部工具
  ```
  **向后兼容**:老 manifest 无 `servers` → 默认空 → 行为同今天(全租户 server)。`extra="forbid"` 不受影响(新增已声明字段)。

- **V-4 按租户 pool 构建(运行时最大改动)**:`tools/assembly.py` 的 `_register_mcp` 现遍历**进程级** pool 按租户 allowlist 过滤。改为取**并集**再过滤:
  - **平台 stdio pool**(ops JSON):进程级共享不变,按 `mcp_allowlist` 过滤。
  - **租户远程 pool(新)**:agent build 时从 `tenant_mcp_server` 注册表读本租户 `enabled` 远程 server → 解析 `token_secret_ref` 注入 header → 建/缓存该租户 `MCPClient`(沿用 `MCPServerPool`,N=5 cap,U-13 熔断)。
  - 取并集后按 agent `MCPToolSpec.servers`(空=全部)过滤 server,再按 `allow_tools` 过滤工具名。
  - 跨界解耦:沿用 `mcp_allowlist_provider` 闭包先例(orchestrator **不** import helix-common);新增 `tenant_mcp_servers_provider: Callable[[str], Awaitable[Sequence[ResolvedMCPServer]]]`,control-plane 侧绑定 registry + secret_store 解析成已注入 header 的配置。

- **V-5 注册 API + 同步探测**:`POST /v1/mcp-servers`(tenant admin,`require("...","write")`)收 `{name, transport, url, auth_type, token?}` → put token → **注册时同步探测**:连一次 + `list_tools`,失败返 422 带原因(**不写库**),成功才落表(避免存进一堆连不上的死 server)。`GET`(列表,不返 token)/ `PATCH`(改 url/token/enabled)/ `DELETE`(删,**先查 agent manifest 引用**,有则 409 列出引用者)。

- **V-6 发现接口(agent 表单用)**:`GET /v1/mcp-servers/available` 返「平台 allowlist server + 租户远程 server」合并清单(agent 表单 server 候选源,带工具数);`GET /v1/mcp-servers/{name}/tools` live `list_tools` 返 `[{name, description}]`(表单展开勾选),失败返 503 + 原因(不缓存陈旧工具列表)。

- **V-7 SSRF 防护**:租户填任意 URL → server 端去连/探测 → 可探内网。**注册 + 探测 + 运行时连接**三处统一过 URL 校验:拒私网段(RFC1918 / loopback / link-local / metadata 169.254.169.254)+ 仅允许 https(dev 可放开 http)。校验放共享 util,三个调用点都用。**之前的设计没覆盖这条,本 Stream 显式补。**

- **V-8 Agent 表单 MCP 段重做**:替换开关+文本框。勾「启用 MCP」→ **server 多选**(来自 `/available`,显示名+工具数)→ 每个选中 server 可**展开**(调 `/tools`)→ 工具 checkbox(全不勾=该 server 全要)。写 `setMcpServers`/`setMcpAllowTools`(`form_model.ts` 加 immutable accessor,merge-preserving)。testid `af-mcp-server-{name}`/`af-mcp-tool-{name}`;i18n en/zh-CN 同步。

- **V-9 租户 MCP server 管理页**:新页 `/settings/mcp-servers`(导航「MCP 服务器」,tenant admin)。表格(名/传输/URL/状态/工具数)+「添加 server」抽屉(沿用 Stream U `CreateTenantDrawer` 范式):名/transport 下拉/URL/auth 下拉/token(`type=password` 不回显)+「测试连接」按钮(调探测,显示拉到几个工具)。删除/停用行内操作。

- **V-10 审计双 Literal 同步**:新增 `AuditAction`(`MCP_SERVER_CREATE`/`MCP_SERVER_UPDATE`/`MCP_SERVER_DELETE`)+ `ResourceType`(`MCP_SERVER`)。`AuditAction` 是 protocol 单一 StrEnum;`ResourceType` protocol + control-plane **两处同改**([memory:project_audit_literal_drift])。审计记 `name/transport/url/actor`,**绝不记 token 值**。

- **V-11 范围边界**:oauth2 MCP(U-12 follow-up,本 Stream 只 none/bearer)/ 平台级共享远程 server 注册(system_admin,本 Stream 只租户级)/ stdio server UI 注册(永远 ops-only,RCE)= out of scope。`credentials_mode` "platform" 既有逻辑不动。

## 2. 架构图（数据流）

```
[注册]   admin-ui SettingsMcpServers ─POST /v1/mcp-servers {name,url,auth,token}
            → secret_store.put(token)→secret:// ref → 探测(连+list_tools, SSRF 校验 URL)
            → 成功落 tenant_mcp_server 表(只存 ref) | 失败 422
[发现]   agent 表单 ─GET /v1/mcp-servers/available→ 平台 allowlist ∪ 租户远程(带工具数)
            ─GET /v1/mcp-servers/{name}/tools→ live list_tools
[选择]   FormView 勾 server + 展开勾工具 → manifest tools:[{type:mcp, servers:[...], allow_tools:[...]}]
[运行]   agent build ─tenant_mcp_servers_provider→ registry 读 + secret 解析 + 注入 header
            → 租户远程 pool ∪ 平台 stdio pool(allowlist 过滤)
            → _register_mcp 按 servers 过滤 server + allow_tools 过滤工具
```

## 3. PR 切分（~7 PR，每个 CI 绿 + 零债 6 条）

- **V-A**(设计)`stream-v/a-design` — 本文档 + ITERATION-PLAN backlog。**Mini-ADR V-1~V-11**。
- **V-B**(注册表后端)`stream-v/b-registry` — `tenant_mcp_server` model + migration + persistence store(base/sql/memory)+ protocol 类型 `TenantMcpServerRecord` + SSRF 校验 util(V-7);单测 CRUD/RLS/SSRF 拒私网。
- **V-C**(注册 API + 探测)`stream-v/c-api` — CRUD 端点 + 注册时探测(连+list_tools)+ token→encrypted store + DELETE 引用检查 + 审计双 Literal(V-10);API 测(注册/换 token/非 admin 403/token 不入日志/探测失败 422/删被引用 409/SSRF 422)。
- **V-D**(按租户 pool 运行时)`stream-v/d-runtime` — `tenant_mcp_servers_provider` 闭包 + 租户 pool 构建 + token 解析注入 + `_register_mcp` 并集逻辑 + 运行时 SSRF 校验;单测 manifest servers 过滤/token 注入/平台+租户并存/pool 缓存复用。
- **V-E**(manifest schema)`stream-v/e-schema` — `MCPToolSpec.servers` + 向后兼容 + canonical manifest 验证;单测旧 manifest 无 servers=全部、servers 过滤生效。
- **V-F**(发现接口 + 租户管理 UI)`stream-v/f-ui-registry` — `/available` + `/{name}/tools` 端点 + `/settings/mcp-servers` 页 + 添加抽屉 + 测试连接 + SDK + i18n;Storybook/Playwright/axe。
- **V-G**(agent 表单选择器)`stream-v/g-agent-form` — FormView MCP 段重做(server 多选 + 工具展开)+ form_model accessor + i18n;Playwright 勾 server/展开工具/存读往返。

> **关键路径** A→B→C→D(后端链);E 可与 C/D 并行;F 依赖 C;G 依赖 E+F。每 PR CI-green + 零债 6 条。

## 4. 风险

1. **按租户 pool 生命周期**:每 agent build 连远程 server 慢/会泄连接 → pool 按租户缓存 + 复用 + 超时 + U-13 熔断;build 路径不能因一个 server 连不上整体失败(降级:跳过该 server + 记 warn)。
2. **注册时探测把 control-plane 变 MCP 客户端**:control-plane 要能跑远程 `MCPClient`(依赖在 orchestrator 包)——确认 control-plane 可调 orchestrator 的远程 client,或抽到共享层;探测有超时(默认 10s)避免挂死请求。
3. **删 server 的引用检查**:扫所有 agent manifest 的 `tools[].servers` 找引用 → 409 列引用者,而非静默坏 agent。manifest 存储是 JSON,跨行扫;大租户性能可接受(agent 数量级小)。
4. **token 全程 SecretStr**:注册 body、探测注入、审计、PATCH——任何一处 log 都泄;`repr=False` 已在 config 层,API 层把住(`SecretStr`、不日志、审计无值、`type=password` 不回显)。
5. **SSRF(V-7)**:租户填任意 URL → 探内网/metadata。注册+探测+运行时三处统一 URL 校验(拒私网/loopback/link-local/metadata,仅 https,dev 放开 http)。这条之前未覆盖,本 Stream 显式补,且必须三处一致(不能只在注册卡、运行时漏)。
6. **双 Literal / 审计漂移**:新 `AuditAction`(protocol 单一 StrEnum)+ `ResourceType`(protocol + control-plane 两处)([memory:project_audit_literal_drift]);CI mypy 不覆盖 control-plane/src,靠 pytest 兜。
7. **alembic id ≤32**(`0054_tenant_mcp_server`=22 OK);down_revision 必须 = 当前 head([memory:alembic-revision-id-32-chars],PR B 写时 grep 确认 head)。
8. **协议签名 sweep 扫 tools/eval**([memory:reference_protocol_sweep_includes_tools_eval]):`MCPToolSpec` 改字段、provider 闭包改签名,doubles/fixtures 全仓 grep 旧形态应为空。
9. **harness 拒 credentials/secrets 路径**([memory:harness-denies-credentials-paths]):新文件别用 "credentials"/"secrets" 命名(否则 Read/Edit/grep 受限);token util 命名避开。

## 5. Verification（本 Stream 完成 = 租户在 helix 后台自助接入远程 MCP，agent 表单按 server 勾选）

1. **V-B**:`tenant_mcp_server` CRUD round-trip;RLS 跨租户不可见;SSRF util 拒 `http://169.254.169.254`/`http://localhost`/RFC1918。
2. **V-C**:`POST /v1/mcp-servers {name,url,auth_type:bearer,token}` → 探测成功落表、DB 只有 `secret://` ref(无明文)、审计无 token;探测失败 → 422 不落库;非 admin 403;删被 agent 引用 → 409 列引用者;token 不在日志出现。
3. **V-D**:`build_agent` 单测——租户远程 server 工具注册成功(token 注入 header)、`MCPToolSpec.servers` 过滤生效、平台 stdio + 租户远程并存、一个 server 连不上不炸整体 build。
4. **V-E**:老 canonical manifest(无 `servers`)行为不变;加 `servers:["github"]` 只注册 github 工具。
5. **V-F**:`/settings/mcp-servers` 注册远程 server +「测试连接」显示拉到 N 个工具 + axe 过;`/available` 返平台∪租户清单。
6. **V-G**:agent 表单勾 MCP → server 多选(非手打)→ 展开某 server 看到 live 工具列表 → 勾选 → 存读往返正确写进 manifest;Playwright 验。
7. **端到端 dogfood**(需真远程 MCP server,如公开 GitHub MCP):租户管理员 `/settings/mcp-servers` 注册 → 测试连接通 → 建 agent 时表单勾选该 server + 几个工具 → Playground 让 agent 调该 MCP 工具 → 真实返回结果(token 全程只在 web 填过、加密存)。
8. **每 PR**:pre-commit(含 ruff-format / detect-private-key)/ pytest `-m "not integration"` / mypy / 前端 typecheck+test+build+storybook+e2e;push 前 preflight([memory:feedback_ruff_strict_lint_traps] / [memory:feedback_uv_lock_and_precommit_ruff])。
