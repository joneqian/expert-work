# Stream S — 可视化 Manifest 编辑器(设计先行)

> 状态:设计先行(PR A)。后续 PR B–E 按本文 Mini-ADR 实现,按 wave 合并。
> 日期:2026-06-01。

## 0. 背景与范围

### 0.1 触发问题
当前注册 / 编辑 agent 只有一个**纯 Monaco YAML 编辑器**(建 agent 的 Create 抽屉 +
`智能体详情→配置清单` 标签)。对会写 manifest 的人够用,但对"公司从零用起来"里
**不懂 YAML 的管理员不友好**:建一个 agent 要手写整份 manifest;换个模型要再写一遍。

### 0.2 目标形态(用户拍板)
做一个 **VS Code Settings 式**的编辑器:可视化表单为主、原始 YAML 为逃生舱,二者
**双向**。覆盖**整份 manifest**——模型、系统提示词、工具、MCP、记忆、沙箱、审批门、
可观测等**全字段**。建 agent 时模型从**已配 key 的 provider** 下拉选,选完即用,不用写 YAML。

### 0.3 锁定决策(逐项确认)
1. **全字段都做成表单**(不是只做常用字段)。
2. **schema 驱动自动生成**表单(非手搓每个 section),后端导出 JSON Schema,前端用
   React JSON Schema Form 渲染,`uiSchema` 精修高频字段 —— 永不漂移、可维护。
3. **切换标签时同步**(非实时双向):单一数据源 = 内存 manifest 对象,表单/YAML 各为
   视图,切标签时序列化/解析 + 校验,非法不让切。
4. **模型 = provider 下拉(只列已配 key 的)+ 模型名从内置目录选**,选定**自动带出
   vision 能力**;`fallback` 同。模型目录用**最新在售模型**(实现时逐 provider 官网/搜索核对)。
5. create + edit **复用同一个 `<ManifestEditor>` 组件**。

## 1. 关键事实(已审计,file:line)

- `AgentSpec` 及其子模型全是 Pydantic `BaseModel`(`packages/expert-work-protocol/src/expert_work/protocol/agent_spec.py`:`ModelSpec`:74 / `SandboxSpec`:233 / `MemorySpec`:279 / `RoutingSpec`:301 / `KnowledgeSpec`:313 / `VisionSpec`:343 / `CacheSpec`:381 …)→ `AgentSpec.model_json_schema()` 可导出完整 JSON Schema。
- MCP 在 manifest 中:`MCPToolSpec`(agent_spec.py:574,`type:"mcp"` + `allow_tools` 过滤);tools 为 `BuiltinToolSpec | HTTPToolSpec | MCPToolSpec` 联合(:590)。MCP server 本身租户级(`tenant_config.mcp_servers`)。
- provider 全集 `PROVIDER_CATALOG`(`protocol/provider_catalog.py`):anthropic/openai/azure/self-hosted/kimi/glm/deepseek/qwen/doubao。
- 平台凭证视图 `GET /v1/platform/credentials`(`api/platform_config.py`:159)列每 provider 的 source(env/db/unset)+ enabled —— 模型下拉据此求"已配 key 且启用"的交集。
- 主模型 key 解析不被 `supported_providers` 拦(`runtime.py:137` `make_provider_key_resolver`);但**长期记忆的 embedder/reranker 被拦**(`runtime.py:343`)→ 选无 embedding 的 provider 时默认模板要关长期记忆。
- ModelSpec 已有 `supports_vision`/`api_key_ref: str|None`/`base_url`/azure 字段;`provider` 是 Literal 全集。
- 现有编辑面:`apps/admin-ui/src/components/CreateAgentDrawer.tsx`(Monaco,建)+ `pages/agent_detail` 的 manifest 标签(Monaco,改)。后端注册端点 `POST/PUT /v1/agents`,`ManifestLoader` 为权威校验。

## 2. Mini-ADRs(统一 S- 前缀)

- **S-1** 全字段表单,schema 驱动:后端 `GET /v1/agents/schema` 返回 `AgentSpec.model_json_schema()`;前端 RJSF(`@rjsf/antd` + `@rjsf/validator-ajv8`)自动渲染。后端加字段→表单自动出现,零漂移。`uiSchema` 仅做布局/控件覆盖,不复制 schema。
- **S-2** 表单 ⇄ YAML 同步 = 切标签时转换。单一数据源 = 内存 manifest 对象。切到 YAML:`dump(对象)`;切回表单:`parse → ajv 校验`,合法才渲染,非法红字留在 YAML。避免实时解析半句 YAML 的跳变与非法中间态。
- **S-3** 模型选择 uiSchema 自定义控件:`model.provider` 下拉 = `/v1/model-catalog` 返回的(目录 ∩ 已配 key 且启用 provider);`model.name` 下拉 = 该 provider 目录项;选定**自动写 `supports_vision`**。`fallback[]` 复用同控件。
- **S-4** 模型目录后端维护:`MODEL_CATALOG: provider → [{name, vision, embeddings, context_window?, deprecated?}]`,与 `provider_catalog.py` 并列。`GET /v1/model-catalog` 返回目录 ∩ 已配 provider。实现期逐 provider 核对**最新在售模型**;`deprecated` 不进下拉。新模型 = 加目录条,不动前端。
- **S-5** 新建默认模板随能力自适应:开新 agent 预填 canonical 精简默认;按所选模型能力裁剪 —— 无 vision→`supports_vision:false` 且不塞 vision 块;无可用 embedding provider→默认关长期记忆 + 黄条提示(否则 `supported_providers` gate 运行时炸)。
- **S-6** 三层校验:① 表单内 RJSF/ajv 实时(类型/必填/范围);② YAML→表单切换时 parse+ajv;③ 保存时后端 `ManifestLoader` 权威(跨字段规则,如审批工具名必须在 tools 中存在),错误回显编辑器。后端始终是最终权威。
- **S-7** create + edit 复用 `<ManifestEditor>`:替换 `CreateAgentDrawer`(建)与 `智能体详情→配置清单`(改)两处 Monaco;行为一致。
- **S-8** i18n 走 `zh-CN.ts` 顶部已定的术语约定(智能体/运行/触发器/记忆/工具/提供方/密钥…,Trace/Span + 技术标识保留);新页面文案 en/zh 同步,符合 Admin UI 设计基线。

## 3. 架构与数据流

```
后端(control-plane)
  GET /v1/agents/schema   → AgentSpec.model_json_schema()(当前 schema)
  GET /v1/model-catalog   → provider→[模型+能力],已和平台凭证求交集
  POST/PUT /v1/agents     → 现有注册端点(ManifestLoader 权威校验)

前端 <ManifestEditor>(create + edit 复用)
  顶部标签:【表单】/【YAML】
  state: manifestObject(单一数据源)
  ├─ 表单:RJSF(schema=/agents/schema, formData=manifestObject, uiSchema=高频精修)
  │        onChange → setManifestObject
  └─ YAML:Monaco(value = dump(manifestObject))
           离开 YAML 标签 → parse + ajv 校验 → setManifestObject(非法则拦截)
  保存:dump(manifestObject) → POST/PUT /v1/agents → 后端校验 → 错误回显
```

## 4. 组件与落点

- **新** `apps/admin-ui/src/components/manifest-editor/`:`ManifestEditor.tsx`(标签 + state + 同步)、`FormView.tsx`(RJSF 封装 + uiSchema)、`YamlView.tsx`(Monaco 封装)、`widgets/ModelSelect.tsx`(provider+model 联动下拉)、`defaults.ts`(默认模板 + 按能力裁剪)、`schema.ts`(拉取 + 缓存 JSON Schema)。
- **新** `apps/admin-ui/src/api/manifest_schema.ts` / `model_catalog.ts`:两个 GET 端点的 SDK。
- **改** `CreateAgentDrawer.tsx` → 内嵌 `<ManifestEditor mode="create">`;`agent_detail` manifest 标签 → `<ManifestEditor mode="edit" value=…>`。
- **新** 后端 `api/agent_schema.py`(`/v1/agents/schema`)、`api/model_catalog.py`(`/v1/model-catalog`)、`protocol/model_catalog.py`(`MODEL_CATALOG`)。

## 5. PR 拆分(Stream S,按 wave 合并)

- **A**(设计)本文 + ITERATION-PLAN backlog。
- **B**(后端)`/v1/agents/schema` + `MODEL_CATALOG`(官网核对最新模型)+ `/v1/model-catalog`(求交集);单测。
- **C**(前端内核)`<ManifestEditor>` = RJSF + 表单/YAML 双标签 + 切换同步 + ajv 校验;替换 Create 抽屉;vitest + Playwright。
- **D**(前端模型选择 + 默认模板)`ModelSelect` 联动控件 + `defaults.ts` 能力自适应 + 黄条提示 + i18n;测试。
- **E**(编辑接入)接 `智能体详情→配置清单` 标签;create+edit 两条 e2e + axe;收尾。

> 关键路径 A→B→C→D→E;C 依赖 B 两端点。每 PR CI-green + 零债 6 条。

## 6. 风险

| # | 风险 | 缓解 |
|---|------|------|
| 1 | RJSF 自动表单"丑/不产品级" | `uiSchema` 对高频字段(模型/提示词/工具/MCP)定制控件与分组;antd 主题统一;冷门字段接受默认控件。 |
| 2 | JSON Schema 的 `oneOf`(tools 联合)RJSF 渲染体验差 | tools 用自定义 array 控件 + 类型选择器(builtin/http/mcp),不用裸 oneOf。 |
| 3 | 表单⇄YAML 往返丢字段 / 格式漂移 | 单一数据源对象;`dump`/`parse` 用同一 YAML 库;后端往返测试守。 |
| 4 | 模型目录过期 | 实现期逐官网核对;`deprecated` 标记;目录是后端单点,易更新。 |
| 5 | 默认模板能力裁剪不全 → 运行时 gate 炸 | S-5 显式按 vision/embedding 裁剪 + 黄条;保存前端不拦(后端权威),但默认值给对。 |
| 6 | 新前端依赖(RJSF)体积/维护 | 成熟社区库;仅 admin-ui 内部;按需 import。 |
| 7 | schema 端点暴露内部结构 | 只读、需 auth;就是 manifest 契约本身,非敏感。 |

## 7. Verification(完成 = 不写 YAML 也能建出能跑的 agent)

- 后端:schema 端点返回合法 JSON Schema;model-catalog 按已配 provider 过滤 + 能力正确;manifest YAML 往返不丢字段。
- 前端:RJSF 按 schema 渲染全字段;表单⇄YAML 切换同步 + 非法拦截;模型下拉求交集 + 选模型自动设 vision;默认模板按能力裁剪;create + edit 两条流;Playwright + axe 过。
- 端到端:管理员**只用表单**选 deepseek + 填名 → 注册 → 员工对话真实回话(全程不碰 YAML)。
- 每 PR:`pre-commit` / `pytest -m "not integration"` / mypy / 前端 typecheck+test+build+storybook+e2e;push 前 preflight。

## 8. 后续(显式不做)

- manifest 版本 diff / 回滚 UI;表单字段级帮助文档抽取;多 manifest 模板库(行业模板)。
- 实时双向同步(S-2 选了切标签同步);schema 端点的客户端长缓存失效策略。
- 模型目录自动从 provider API 拉取(本版手维护)。
