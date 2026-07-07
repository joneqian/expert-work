# 设计 — web_search:SearXNG 当免费 builtin 默认 + Tavily 降级为平台 MCP

状态:M1 入 main(#841)· M2 本分支 `feat/web-search-tavily-mcp` · 2026-06-29

## 1. 背景 / 问题

新建 agent 跟它说一句「你好」就报:

```
agent manifest cannot be built: builtin 'web_search' declared but no
Tavily client is configured (ToolEnv.web_search_client)
```

根因:`web_search` 是**硬接 Tavily 的 builtin 工具**。manifest 显式声明它 → 构建期(收到第一条消息组装 tool registry)要一个 Tavily client → 平台没配 Tavily key → 按设计 fail-loud → LLM 没机会跑。

产品判断:
1. **网络搜索应是零配置默认能力**,不该逼每个部署先买 Tavily key。
2. Tavily 当**硬编码 builtin**与平台「**MCP client-only + 平台 MCP 目录(A 共享 bearer / B per-user OAuth)**」方向不一致——Tavily 官方有远程 MCP server,正好走目录。

**owner 拍板方向 A**:
- **SearXNG(自托管、开源 AGPL-3.0、免费、无 key)→ builtin `web_search` 的默认后端**。修 onboarding:manifest 仍写 `{builtin: web_search}`,后端底层换成 SearXNG,对 manifest 透明。
- **Tavily → 降级为平台 MCP 目录条目**(A 档共享 bearer,租户 opt-in)。删/弃硬编码 Tavily builtin client。
- 结果:**免费搜索=零配置默认;Tavily=opt-in premium 升级**,builtin 路径与 MCP 路径各司其职。

## 2. 现状(grounded,实现前核过)

### 2.1 builtin web_search 路径
- `KNOWN_BUILTINS` 含 `web_search`;**不在** `BASE_CAPABILITY_BUILTINS`(非恒装,仅 manifest 显式声明才注册)。
- `assembly.py:_register_web_search`:`env.web_search_client is None` → `raise AgentFactoryError`(显式声明 + 依赖缺失 = 真配置错,fail-loud 故意)。否则 `WebSearchTool(client=env.web_search_client, default_max_results=...)`。
- `web_search.py`:`TavilyClient` Protocol —— `async search(*, query, max_results, tenant_id) -> {"results":[{"title","url","content"}, ...]}`(其余键容忍忽略)。实现:`HTTPTavilyClient`(固定 key,调 Tavily REST `/search`)、`RecordingTavilyClient`(dev/test)。`ResolvingTavilyClient`(per-tenant key,在 runtime.py)。
- `ToolEnv.web_search_client: TavilyClient | None`(`assembly.py:111`)。
- 装配:`app.py:resolve_web_search_client(resolver, secret_store, supported_tools)` → `web_search` 不在 `supported_tools` 返 `None`,否则 `ResolvingTavilyClient`;塞进 `ToolEnv`。
- **门控**:`effective_supported_tools = supported_tools ∪ effective_platform_tool_credentials.keys()`;`effective_platform_tool_credentials` 在配了 `tavily_api_key_ref`(legacy,已标 deprecated)或 `platform_tool_credentials["web_search"]` 时含 `web_search`。即**只有配了 Tavily key,web_search 才 supported** → 没 key 必炸。

### 2.2 平台 MCP 目录(Tavily-MCP 落点)
- 平台 MCP server:`mcp_connector_catalog`(NULL-tenant 平台行)+ 运行期平台池(`build_mcp_pool`),支持 `transport: streamable_http | sse`、`auth_type: none|bearer|oauth2`、bearer 走 `auth_config.token_ref`→`SecretStore`,per-tenant `mcp_allowlist`(名字)过滤可见性。详见 [[mcp-platform-servers]]。
- **Tavily 官方远程 MCP**:`https://mcp.tavily.com/mcp/`,认证:API key(bearer / `?tavilyApiKey=`)**或** OAuth。→ **API key = type A 共享 bearer;OAuth = type B per-user**。两种都已被现有目录支持,**Tavily 侧零 Expert Work 代码**。

## 3. 关键架构决策

1. **builtin 后端可插拔,Protocol 留存,默认实现换 SearXNG**。`TavilyClient` Protocol 形状(`search(...) -> {results:[{title,url,content}]}`)provider 无关,保留(可改名 `WebSearchProvider`,但**为减小 diff、避免动 import 面,本轮不改名**,仅新增 `SearXNGClient` 实现它)。SearXNG `/search?format=json` 返回 `{results:[{title,url,content,engine}]}` → 与 Protocol 近 1:1,adapter 薄。

2. **web_search 的 supported 门控从「配了 Tavily key」改成「配了 web 搜索后端」**。新增后端配置(SearXNG base_url),配了即 `web_search ∈ effective_supported_tools`。SearXNG **无 key**,故门控不能再挂在 `platform_tool_credentials["web_search"]` 上。

3. **Tavily builtin 分两阶段退场**(评审拍板:分两阶段)。
   - **M1**:builtin 后端**可插拔且并存**——`web_search_searxng_base_url` 配了用 `SearXNGClient`(新免费默认,**优先**),否则回落已有 Tavily builtin(`ResolvingTavilyClient`)。`web_search` supported = SearXNG 配了 **或** Tavily key 配了。降一次性爆破面。
   - **M2**:Tavily 走 MCP 后,删 `HTTPTavilyClient`/`ResolvingTavilyClient`/`resolve_web_search_client` 的 Tavily 分支 + `tavily_api_key_ref`(本就 deprecated)+ `platform_tool_credentials["web_search"]` 的 gap-fill。builtin 后端唯一 = SearXNG。**back-compat**:见 §5。

4. **SearXNG 网络隔离**(安全)。SearXNG JSON API 是**免费上游代理**,公网暴露=被当爬虫农场白嫖。**只在 compose 内网暴露**(Expert Work 服务网段),不发布 host 端口;`settings.yml` 开 `search.formats:[json]` + 限 `server.bind_address` 内网 + 带 Redis 限流。SearXNG 的出网走它自己(查上游引擎),**不经 Expert Work per-agent egress 代理**(它是平台基础设施,非 agent 沙箱)。

5. **Tavily-MCP 用 type A(共享 bearer)做默认登记**。一把平台 Tavily key(`secret://`)共享给 opt-in 租户;B 档 per-user OAuth 留作后续(Tavily MCP 支持,但 per-user key 治理更重)。登记走现有目录流程,**非本设计新代码**——本设计只产出「登记说明 + 删 Tavily builtin」。

## 4. 计划

### M1 — SearXNG 当 builtin 默认后端(修 onboarding,自包含)
1. **infra**:`docker-compose`(dev + 部署)加 `searxng` service(`searxng/searxng:latest`)+ `redis`(若无共享实例);挂 `searxng/settings.yml`(开 json、限内网 bind、secret_key、限流);**不**映射 host 端口。
2. **`SearXNGClient`**(`orchestrator/tools/web_search.py`):实现 `TavilyClient` Protocol;`async search` → `GET {base_url}/search?q=...&format=json&...` → 映射 `{results:[{title,url,content}]}`(取 SearXNG 的 `title/url/content`)。httpx,超时/错误传播同 `HTTPTavilyClient`。
3. **settings**:加 `web_search_searxng_base_url: str | None`(如 `http://searxng:8080`)。`effective_supported_tools`:配了 base_url → 含 `web_search`。
4. **wiring**:`resolve_web_search_client`/`app.py` → base_url 配了则建 `SearXNGClient` 塞 `ToolEnv.web_search_client`。
5. **tests**:`SearXNGClient` 解析/映射(MockTransport,含空结果/错误);supported_tools 门控;assembly 注册路径(client 非 None 不再炸)。
6. **验证**:dev `make dev-up` 起 searxng;agent 声明 web_search,说「你好」不炸;真跑一次 web_search 工具拿到结果。

### M2 — Tavily 降级为平台 MCP + 删 builtin
1. **登记 = 走 admin-UI**(非 env-seed)。平台管理员:Settings → MCP 目录 → 新建连接器 → `transport: streamable_http`、`url: https://mcp.tavily.com/mcp/`、`auth: bearer(共享)`、**粘贴 Tavily key**(`CatalogConfigForm` 的 `Input.Password` → `POST /v1/platform/mcp-catalog` 的 `bearer_token` → 后端加密写 SecretStore 存 `bearer_token_ref`,key 不落明文/不进 env);租户 `mcp_allowlist` opt-in。**bearer 密钥绝不进 env/seed 文件**(env-templated seed 仅用于 OAuth client id 这类非密值)。该 UI/API 路径在本次前已全交付(`CatalogConfigForm` + `McpConnectorCatalogUpsert.bearer_token`),M2 无需新代码。
2. **删 Tavily builtin 代码**:`HTTPTavilyClient`、`ResolvingTavilyClient`、`resolve_web_search_client` 的 Tavily 分支、`tavily_api_key_ref`、`platform_tool_credentials` 对 `web_search` 的 gap-fill;清相关 import/测试。`TavilyClient` Protocol 名暂留(SearXNGClient 实现它),或本轮顺手改名 `WebSearchProvider`(**二选一,评审定**)。
3. **tests**:删 Tavily builtin 测;补「web_search builtin 后端=SearXNG 唯一」断言。
4. **admin-ui**(若需):平台配置加 SearXNG base_url 字段(或纯 env);Tavily 不再在 tool-credentials 出现。

## 5. 迁移 / back-compat

- **manifest 透明**:声明 `{builtin: web_search}` 的 agent,后端从 Tavily 换 SearXNG,**无需改 manifest**(Protocol 形状不变,工具名不变)。
- **已配 Tavily 的部署**:`tavily_api_key_ref` / `platform_tool_credentials["web_search"]` 删除后,这些部署的 builtin web_search 改由 SearXNG 提供;若仍要 Tavily,走 MCP 目录登记(§4 M2.1)。**这是行为变更**,需在 release note 显式标注(原 builtin Tavily 调用迁到 MCP 工具,工具名/schema 变 → 引用方需知)。
- **过渡期(评审拍板:要)**:M1 上 SearXNG 且**保留 Tavily builtin 作 fallback**(SearXNG 优先,未配 base_url 才回落 Tavily),M2 再迁 Tavily→MCP + 删 builtin。两 PR,降一次性爆破面。

## 6. 验证

- **unit**:SearXNGClient 映射/错误;supported_tools 门控;assembly 注册;删 Tavily 后无悬挂 import(全仓 grep `TavilyClient`/`tavily` 应只剩 Protocol 名或清零)。
- **live**(照 verify_live):dev 起 searxng,真 agent 声明 web_search → 说「你好」不炸 → 触发一次搜索拿到真实结果;Tavily 走 MCP 目录登记后,opt-in 租户 agent 经 MCP 工具搜索成功。
- **安全**:确认 searxng **无 host 端口**、仅内网可达;JSON API 不公网暴露。

## 7. Backlog(本轮不做)
- Tavily-MCP B 档 per-user OAuth 登记(治理更重)。
- SearXNG 引擎选择/权重调优、结果 rerank。
- web_search 结果接 citation/DLP 的细化(沿用现有 WebSearchTool 行为)。
- `TavilyClient` → `WebSearchProvider` 改名(若 M2 未顺手做)。
- 多后端并存 + 平台/租户级后端选择(现仅平台级单后端)。
