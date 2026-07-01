# 对话中心化运营 IA — (用户 × 对话) 维度重组

> 起因:Agent 详情的**配置**面是 agent 级(对),但**运营/数据**面把真实运营单位 (user_id × conversation) 拍平成了 agent 级或租户级**扁平列表**,且「一次对话」本身**没有页面**。运营者答不了头号问题:「用户 X 昨天那次对话到底发生了啥」——只能在扁平 run 列表里翻,拼不出线程,也找不到某用户的历史。
>
> 本稿把运营 IA 从「扁平 run 列表 + 孤立 memory/artifacts tab」重组为 **用户 → 对话 → run 三层下钻**,数据全来自 helix 自有 `agent_run` / `token_usage`(租户 RLS 天然安全),深度 LLM trace 继续外链 Langfuse(system_admin,跨租户红线见 [ADR-0005](../adr/0005-observability-stack.md))。延伸 [admin-ui-nav-ia](./admin-ui-nav-ia.md)。

## 1. 数据模型:三层(已核实)

| 层 | 键 | 内容 | 性质 |
|---|---|---|---|
| **Agent 定义** | `(name, version)` | manifest / prompt / 技能 / 触发器 / 配置历史 | 共享配置 |
| **用户实例** | `(agent, user_id)` | 长期记忆 + 产物/工作区 + 用量 + 对话列表 | per-user 持久资产 |
| **对话** | `(agent, user_id, session_id = thread_id)` | 消息线程 + 其下 N 个 run + 事件 + 本次 token/成本 | 运营原子记录 |

关键事实:

- 一个 `thread_id` 含**多个** run(每轮/每次 resume 一个 run)。**对话 = `agent_run` 按 `thread_id` 分组**。
- `session_id`(API)= `conversation_id` = LangGraph `thread_id`。同 (user, session) → 对话继续;省略 session_id → 新 thread(uuid4)。`session_id` 作用域限 (user, agent)。
- `user_id` 是应用自有字符串 id(≤255),首次使用铸入 `tenant_user`。
- **产物挂用户层**(`/v1/artifacts` 按 (tenant, user) 归属,keyed by name+version,tied to 用户持久工作区;跨对话 run 写同一工作区),**不是**对话层。记忆同样是 per-user、跨 agent 资产(Mini-ADR H-13)。
- `token_usage`(G.9)per-LLM-call,有 `trace_id`(nullable)无 `run_id` → 按 `trace_id` 关联 run(`agent_run.trace_id == token_usage.trace_id`)。

## 2. 现状盘点(问题定位)

| 现 tab / 页 | 数据维度 | 判断 |
|---|---|---|
| 概览 / 配置清单 / 技能 / 触发器 | agent 配置 | ✅ 维度对 |
| 历史 | agent **配置修订**(manifest revision) | ⚠️ 数据对,名字误导(像对话历史)|
| 调试台 | 调用者本人一次测试对话 | ⚠️ 测试台,不反映多用户多对话 |
| 运行 tab | 本 agent **全用户×全对话** 扁平列表 | ⚠️ 维度太粗 |
| 记忆 tab | 租户全量 per-user(无 agent 过滤)| ❌ 维度错位(挂 agent 下误导成本 agent 记忆)|
| 顶层 /runs | 跨 agent 扁平 run 列表 | ⚠️ 无对话分组、无用户下钻 |
| 顶层 /artifacts | 按 (tenant,user) 扁平 | ⚠️ 该归用户层 |
| **对话(thread)** | —— | ❌ **无页面**(缺失原语)|

## 3. 目标 IA

```
Agent 详情
├─ 定义(配置)  概览 · 配置清单 · 技能 · 触发器 · 配置历史 · 调试台   ← 现状,仅「历史→配置历史」改名
└─ 运营         用户 tab
                 └─ 用户详情 (agent × user_id)
                      ├─ 对话列表
                      │    └─ 对话详情 (thread) ── run 列表 → RunDetail(现有,per-run)
                      ├─ 记忆   ← 现 MemoryTab 迁入(按该 user 过滤)
                      ├─ 产物   ← 现 ArtifactsList 迁入
                      └─ 用量   ← per-user rollup

顶层 nav
  智能体 · 对话(全局浏览器,原 /runs 改造) · 审批 · 知识 · 技能 · 触发器 · 评测 · 编排 · 市场 · Webhook
  设置组:… · 记忆治理(租户级,原 /memory)          ← 产物顶层删
```

面包屑贯穿:`Agent / 用户 张三 / 对话 #a1b2 / run #c3d4`。

### 三个新原语

**① 对话详情页(核心)** — 一次 (user, session) 全貌:线程摘要(首末时间 / run 数 / 聚合 token / 成本 / 有无 error / pending)+ **run 列表**(状态/耗时/token/错误,点进 = 现 `RunDetail`)+ 事件。深度 trace 外链 Langfuse(system_admin)。

**② 用户详情页** — (agent, user_id) 实例:对话列表 + 记忆 + 产物 + 用量 rollup。记忆/产物 tab 从 agent 详情迁此。

**③ 全局对话浏览器** — 原 `RunsList`(#876/#878 已富化,带 user 过滤 + token 列)改造:按 `thread_id` 分组,跨 agent,过滤 agent/user/状态。运营监控「今天哪些对话报错」+ 三层下钻的顶层落点。

## 4. 决策(已拍板)

1. **脊柱 = 用户→对话→run 三层下钻**(Agent 详情内)。备选「独立顶层对话浏览器」「只做对话层不做用户层」均否 —— per-user 持久 agent 是产品形态,用户是一等维度。
2. **记忆 + 产物下沉用户层**,顶层 `/artifacts` 删;`/memory` 降为一个**租户级治理聚合页**(跨用户治理有价值,主入口移用户层)。
3. **顶层 `/runs` 改造成全局对话/运行浏览器**(按 thread 分组),保留为跨 agent 运营监控入口 + 下钻落点。

## 5. 后端

### M1 — 对话

- `GET /v1/conversations?agent=&version=&user_id=&status=&q=&limit=&cursor=` —— `agent_run` 按 `thread_id` 分组:`thread_id, user_id, agent_name, agent_version, first_at, last_at, run_count, last_status, has_error, has_pending`。聚合 token 复用 `token_usage.totals_by_trace_ids`(收集该 thread 全 run 的 trace_id)。tenant scope,RLS 天然安全。
- `GET /v1/conversations/{thread_id}` —— 该 thread 的 runs[](含状态/时间戳/trace_id)+ 聚合摘要(总 token / llm_calls / 模型 / 成本 fast-follow)。
- **最大未知**:跨 run 的**统一消息 transcript**(用户/助手轮)存 LangGraph checkpoint(keyed by thread_id),现无读 API。**M1 对话详情先做「摘要 + run 列表」**,每 run 消息仍走现 `RunDetail` 事件流。统一 transcript 标 **M1.5**(需评估 checkpoint 读端点成本)。

### M2 — 用户

- `GET /v1/agents/{name}/{version}/users?limit=&cursor=` —— `agent_run` 按 `user_id` rollup:`user_id, conversation_count, last_active_at, total_tokens`。
- 用户详情**拼装**现有 per-user 端点(对话列表按 user 过滤 + `/v1/memories` + `/v1/artifacts` + 用量)。
- **须核后端缺口**:记忆端点是否支持 `user_id` 过滤(现 MemoryTab 是租户 per-user 全量,无 agent/user 过滤)。缺则补 `user_id` 查询参数。

## 6. 前端(SE-8 接线点全走)

### M1
- 新 `pages/ConversationDetail.tsx`(路由 `/conversations/:threadId`)。
- 新 `pages/agent_detail/ConversationsTab.tsx`(agent-scoped 对话列表)→ 替换现「运行」tab。
- `pages/RunsList.tsx` → 全局对话浏览器(thread 分组);nav label「运行记录 → 对话」。
- 「历史 → 配置历史」tab label + i18n。
- 新 `api/conversations.ts` SDK;`i18n/locales/{zh-CN,en}.ts` 双语;`CommandPalette` / Storybook / Playwright 同步。

### M2
- 新 `pages/UserDetail.tsx` + Agent 详情「用户」tab;记忆/产物 tab 迁入用户详情。

### M3
- 删 `WORKSPACE_ITEMS` 的 `artifacts`;`memory` → 租户级「记忆治理」(语义/分组明确);`router.tsx` 死链回收;面包屑统一。

## 7. 非目标(有意不做)

- Langfuse 不取代深度 LLM trace(只读观测 + 单实例无 RLS 跨租户红线);不在 helix 重建 trace 树。
- 不把 Langfuse 暴露给租户用户。
- 成本(人民币)列 → fast-follow(现定价是月度 rollup,无干净 per-run cost 函数)。
- 工具精确调用计数 → 先用 `llm_calls` 代理(未单独存)。
- 统一消息 transcript → 视 checkpoint 读 API 成本定 M1.5。

## 8. 验证

**M1**:选 agent → 对话 tab 见对话列表(每行 user / 末活跃 / run 数 / token / 错误红点)→ 进对话见 run 列表 + 聚合摘要 → 进 run 仍到现 RunDetail;全局浏览器按 user/状态过滤;legacy 无 trace_id 对话 token 显 0/「—」不炸。后端 `conversations` 分组 + 聚合单测 + 真 PG 跨租户不串。

**M2**:选用户 → 见其对话 + 记忆 + 产物 + 用量;记忆按该 user 过滤正确。

**M3**:顶层无 `/runs`(旧语义)/`/artifacts`;下钻链完整;`tsc -b` + 全量 vitest + axe 绿。

## 9. 坑(记忆在案)

- 改 nav/i18n label 必跑**全量** vitest;antd Select 虚拟列表 jsdom 不渲染(交互测走 e2e);Monaco/表单 aria-label。
- control-plane 别顶层 import orchestrator;跨租户读 FORCE-RLS 表需 `SET LOCAL ROLE audit_reader`(本查按 tenant scope,常规读即可)。
- SDK envelope vs raw:`get_run` 返裸 JSON、`list_runs` 返 `{success,data}`,新端点各按现状别混。
- 每 PR 同步 `ITERATION-PLAN.md`。
