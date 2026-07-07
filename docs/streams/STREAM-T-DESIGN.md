# Stream T — 平台 Embedding/Rerank 配置 + 长期记忆默认化(设计先行)

> 状态:设计先行(PR A)。后续 PR B–E 按本文 Mini-ADR 实现,按 wave 合并。
> 日期:2026-06-02。

## 0. 背景与范围

### 0.1 触发问题
用户拍板的产品方向(2026-06-02,见记忆 `project_longterm_memory_default_embedder_platform`):

1. **长期记忆是默认能力,不是 opt-in** —— 没有长期记忆的 agent 没有商业价值(纯 chat = 别人用豆包就行)。与目标产品形态(每用户持久 agent = 对话 + 长期记忆 + 持久工作区)一致。
2. **embedder / rerank 是平台系统级配置**,不是每个 agent / 知识库各配一次,而且要有 **UI 让管理员自己配**(选 provider + model + 确认 key),不靠 env 写死。
3. 不做纯 chat agent。

### 0.2 现状(已审计)
- embedder/reranker 的 **provider+model 是 env-only 设置**:`settings.py` `embedding_provider="qwen"` / `embedding_model="text-embedding-v4"` / `rerank_provider="qwen"` / `rerank_model="qwen-plus"`(:142-175)。**没有 DB 表、没有 UI**。
- **启动时定死一次**:`resolve_embedder(...)` / `resolve_reranker(...)`(`runtime.py:326-365`)在 `app.py:660-680` lifespan 里读 env 设置建好,传进 MemoryEnv → `make_agent_builder`。运行期改 env 不生效。
- embedder/reranker 用的 provider **与 agent 主模型 provider 解耦**:key 走平台凭证 `resolve_provider`(`ResolvingEmbedder.embed()` 内,`runtime.py:242-260`),provider/model 来自上面的平台设置。
- 长期记忆开关在 manifest:`spec.memory.long_term: LongTermMemorySpec | None`(`agent_spec.py:249-283`),默认 `None`(关)。`spec.knowledge` 配知识库才用 RAG。
- build-time gate:声明 `memory.long_term` 但 embedder 为 None → `AgentFactoryError`(`agent_factory.py:888`);`supported_providers` 不含 embedding provider → `resolve_embedder` 返回 None(`runtime.py:343`)。
- 平台凭证已有完整机制(Stream P/Q):`platform_provider_secret` 表(refs)+ `encrypted_secret` 金库(Stream Q)+ `PlatformSecretsService.effective_provider_credentials()`(`platform_secrets.py:48`)+ `GET/PUT /v1/platform/credentials`(`api/platform_config.py:150-383`)+ 平台设置页 `SettingsPlatformConfig.tsx`(凭证编辑 modal)。
- 模型目录 `models_for_provider(p)` 每项带 `embeddings: bool`(`model_catalog.py`),可据此过滤"有 embedding 模型的 provider / embedding 模型"。

### 0.3 关键纠错(回炉 PR D)
PR D(#361)的 `ModelSelectField` 里"**主模型 provider 没 embedding 模型 → 提示长期记忆不可用**"**概念是错的**:embedding 用的是**平台 embedding provider**,与 agent 主模型 provider 无关(DeepSeek 主模型 + 平台 OpenAI/通义 embedding 完全合法)。Stream T 删掉这个误导提示;真正的 gate 是"平台是否配了 embedding"。

## 1. 锁定决策(逐项确认 2026-06-02)
1. **生效方式 = 立即生效(动态解析)**:embedder/reranker 从"启动定死"改成"运行期读当前平台配置";UI 配完下一个新 agent / 下一轮 embed 就用上,不重启。
2. **未配门控 = 挡住 + 引导**:平台没配 embedding 时,建 agent 挡住并引导去平台设置配;不静默降级建残缺 agent。
3. **rerank 一起做,可选**:embedding/rerank 同一配置面;rerank 不配则知识库检索降级成 RRF 融合顺序(不报错)。
4. **平台全局**:配置是 deployment 级单例;per-tenant override 明确不做(出口后续按需)。
5. **记忆默认开落在 UI 默认模板层**:AgentSpec schema `memory.long_term` 默认仍 `None`(不破坏存量 manifest/测试);新建 agent 的**默认模板**带 `memory.long_term`。

## 2. Mini-ADRs(统一 T- 前缀)

- **T-1 存储**:新**专用单行表 `platform_embedding_config`**(非 secret,只存选择):`embedding_provider` / `embedding_model` / `rerank_provider`(nullable)/ `rerank_model`(nullable)/ `updated_at` / `updated_by`。tenant-less、无 RLS、bypass-RLS 写(平台全局,镜像 `platform_provider_secret` 套路)。备选通用 JSONB `platform_config`(tenant_config 套路)——本迭代只一个配置,YAGNI 选专用表。
- **T-2 服务**:`PlatformEmbeddingConfigService`:DB 行优先、无行回落 env settings(向后兼容)、30s TTL 缓存、写时失效(镜像 `PlatformSecretsService`)。暴露 `effective_embedding_config() → (provider,model)|None`、`effective_rerank_config() → (provider,model)|None`。
- **T-3 动态解析**:`DynamicResolvingEmbedder(config_service, resolver, secret_store)` —— `embed()` 时读当前配置 → 解析 key → embed;无配置则 raise(由门控保证不会走到)。`DynamicResolvingReranker` 同,无配置降级。`make_agent_builder`/MemoryEnv 从"定死 embedder"换成动态实现。`app.py` 接线相应改。
- **T-4 写路径 API**:`GET /v1/platform/embedding-config`(返回 provider/model/rerank_provider/rerank_model/各自 key 是否已配)+ `PUT /v1/platform/embedding-config`(写选择)。system_admin gate + 审计(`AuditAction` 新值两处 Literal 同步)。校验:model 在目录且 `embeddings=true`、provider 的平台 key 已配(没配则 422 引导先配 key)。
- **T-5 记忆默认开 + 三层门控**:① 建 agent UI:`effective_embedding_config()` 为 None → 挡住 + 引导去 `/settings/platform` 配;② build-time gate 保留(防御);③ UI 默认模板含 `memory.long_term`。onboarding/getting-started 把"配 embedding"列为必配前置。
- **T-6 回炉 PR D**:删 `ModelSelectField` 的 `providerHasEmbeddings(主模型 provider)` 误导提示及相关 catalog 用法;`model-select-no-embeddings` 语义下线(embedding 与主模型解耦)。
- **T-7 平台配置 UI**:`/settings/platform` 加 **Embedding & Rerank** 区(复用凭证编辑 modal 套路):embedding provider 下拉(只列**有 embedding 模型**的 provider)→ model 下拉(只列 `embeddings:true`);rerank 同(可选);提交前校验该 provider 平台 key 已配,没配给"先去配 key"引导。
- **T-8 i18n + 可观测**:新页文案 en/zh 同步;配置变更走审计;embedder 解析失败 metric(`expert_work_embedder_resolve_failed_total` 之类)+ "平台 embedding 未配置" 在平台设置页红条。

## 3. 架构与数据流

```
平台管理员
  /settings/platform → Embedding & Rerank 区
    PUT /v1/platform/embedding-config {embedding_provider, embedding_model, rerank_provider?, rerank_model?}
      → platform_embedding_config 表(单行 upsert)+ 校验 model 目录/embeddings + provider key 已配
      → PlatformEmbeddingConfigService 缓存失效

agent build / 运行
  make_agent_builder → DynamicResolvingEmbedder(config_service, resolver, secret_store)
    embed 时:effective_embedding_config() → (provider,model) → resolve_provider(tenant) key → embed
  memory.long_term 节点用它做召回/写回;knowledge_search 用它 + DynamicResolvingReranker

建 agent(UI)
  effective_embedding_config() 为 None → 挡住 + 引导去平台设置(记忆默认开,需 embedder)
```

## 4. 组件与落点

- **新** 后端:`migrations/versions/00XX_platform_embedding_config.py`(表,id≤32 字符)、`packages/expert-work-persistence/.../models/platform_embedding_config.py` + store、`services/control-plane/.../platform_embedding_config.py`(`PlatformEmbeddingConfigService`)、`api/platform_embedding_config.py`(两个端点)、`runtime.py`(`DynamicResolvingEmbedder/Reranker` + 接线)、`app.py`(换接线)。
- **改** 后端:`settings.py`(env 作为回落保留)、`agent_factory.py`/assembly build-time gate(沿用)、`AuditAction` Literal(protocol + control-plane 两处)。
- **新/改** 前端:`apps/admin-ui/src/api/platform_embedding_config.ts`(SDK)、`pages/SettingsPlatformConfig.tsx` 或新 `SettingsPlatformEmbedding.tsx`(配置区)、建 agent 入口的 embedding-未配引导、`manifest-editor/defaults.ts`(模板 memory-on)、`manifest-editor/widgets/ModelSelectField.tsx`(删误导提示)、i18n。

## 5. PR 拆分(Stream T,按 wave 合并)

- **A**(设计)本文 + ITERATION-PLAN backlog。
- **B**(后端核心)`platform_embedding_config` 表 + `PlatformEmbeddingConfigService`(env 回落)+ `DynamicResolvingEmbedder/Reranker` + app 接线;单测(DB 优先/env 回落/缓存失效/动态解析/无配置 raise/rerank 降级)。
- **C**(后端 API + 门控)`GET/PUT /v1/platform/embedding-config` + 校验(目录/embeddings/key 已配)+ build-time gate 复用 + 建 agent precheck;API 测 + 审计。
- **D**(前端配置 UI)平台设置 Embedding/Rerank 区 + SDK + i18n + Storybook/Playwright/axe。
- **E**(记忆默认化 + 回炉 + 收尾)默认模板 memory-on + 删 PR D 误导提示 + 建 agent "先配 embedding" 引导 + getting-started onboarding 必配 + create/edit/E2E。

> 关键路径 A→B→C→D→E。每 PR CI-green + 零债 6 条。E 之后跑 E2E 闭环:配 embedding → 建 memory-on DeepSeek agent → 真实多轮验证记忆召回。

## 6. 风险

| # | 风险 | 缓解 |
|---|------|------|
| 1 | 动态解析每 embed 读配置/解析 key 增延迟 | service 30s TTL 缓存;key 解析本就 per-embed(现状);provider/model 查表 O(1)。 |
| 2 | 改配置后已 cached 的 agent 仍用旧 embedder | 动态 embedder 每次 embed 读当前配置 → 下一轮即生效;无需 rebuild。文档写明"立即生效 = 下一次 embed"。 |
| 3 | 配了 embedding provider 但没配它的 key | PUT 校验 + 平台设置页红条 + 建 agent 引导;build-time gate 兜底。 |
| 4 | env 回落与 DB 行语义冲突 | DB 行存在即全权威;无行才回落 env;migration 不预填 DB(保持 env 行为不变)。 |
| 5 | 回炉删 PR D 提示影响既有测试 | 同 PR 改 ModelSelectField 测试;`model-select-no-embeddings` 相关断言一并删。 |
| 6 | alembic revision id ≤32 字符 / 双 Literal 审计漂移 | 表名简短;新 `AuditAction` protocol+control-plane 两处同改(见记忆 `project_audit_literal_drift`)。 |

## 7. Verification(完成 = 平台一次配好 embedding,所有 agent 默认带可用长期记忆)

- 后端:config service DB 优先/env 回落/缓存失效;动态 embedder 改配置后下一次 embed 用新 provider/model;无配置时 memory agent build 报错。
- API:PUT 校验 model∈目录且 embeddings、provider key 已配;非 admin 403;审计记录。
- 前端:配置区 provider 只列有 embedding 模型的、model 只列 embeddings;rerank 可选;没配 key 引导;建 agent 在平台未配 embedding 时挡住 + 引导。
- 端到端:平台配 embedding(如通义 text-embedding-v4 / OpenAI text-embedding-3-large)→ 用可视化编辑器建 memory-on 的 DeepSeek agent → 多轮对话验证记忆召回(全程 web,不写 YAML)。
- 每 PR:`pre-commit` / `pytest -m "not integration"` / mypy / 前端 typecheck+test+build+storybook+e2e;push 前 preflight。

## 8. 后续(显式不做)
- per-tenant embedding override;embedding 维度/chunk size 等高级参数 UI;rerank 用专用 rerank API(现 LLM-backed);embedding 配置自动探测可用 provider。
