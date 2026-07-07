# 对话中心化运营 IA — (用户 × 对话) 维度重组

> 起因:Agent 详情的**配置**面是 agent 级(对),但**运营/数据**面把真实运营单位 (user_id × conversation) 拍平成了 agent 级或租户级**扁平列表**,且「一次对话」本身**没有页面**。运营者答不了头号问题:「用户 X 昨天那次对话到底发生了啥」——只能在扁平 run 列表里翻,拼不出线程,也找不到某用户的历史。
>
> 本稿把运营 IA 从「扁平 run 列表 + 孤立 memory/artifacts tab」重组为 **用户 → 对话 → run 三层下钻**,数据全来自 Expert Work 自有 `agent_run` / `token_usage`(租户 RLS 天然安全),深度 LLM trace 继续外链 Langfuse(system_admin,跨租户红线见 [ADR-0005](../adr/0005-observability-stack.md))。延伸 [admin-ui-nav-ia](./admin-ui-nav-ia.md)。

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

- `GET /v1/conversations?agent_name=&agent_version=&user_id=&status=&q=&has_error=&since=&limit=&offset=` —— `agent_run` 按 `thread_id` 分组:`thread_id, user_id, agent_name, agent_version, first_at, last_at, run_count, last_status, has_error, has_pending`。聚合 token 复用 `token_usage.totals_by_trace_ids`(收集该 thread 全 run 的 trace_id)。tenant scope,RLS 天然安全。
  - **运营过滤**(#888/#889/#890 落地):`has_error`(≥1 失败终态 run,≠ `status=failed` 线程生命周期)、`has_pending`(≥1 run 停在审批闸,"needs a human")与 `since`(活动窗口:≥1 run 在该时刻后创建)由 `RunStore.thread_ids_with_runs(since, only="failed"|"pending")` 先解出线程集,多过滤各自解集后在 API 层**求交**,再作 `thread_ids` 交给 `ThreadMetaStore` —— 各 store 守各表(与 users rollup 同型)。
  - **分页契约**:`offset` 翻页;响应 `total` 为真实计数(`ThreadMetaStore.count_*` 与 list 共享同一 WHERE 构建,total 不会与页漂移)。cursor 分页当前不需要(运营浏览器深翻页少)。
  - **排序**:对话浏览器固定「末次活跃」倒序(`order_by="last_activity"`:max(run.created_at) 子查询 outerjoin,run-less 线程 coalesce 回退创建时间)——旧线程刚报错要浮顶,创建时间序会把它沉底。其他 `list_by_tenant` 消费者(会话历史等)默认 `created_at` 不变。
- `GET /v1/conversations/{thread_id}` —— 该 thread 的 runs[](含状态/时间戳/trace_id)+ 聚合摘要(总 token / llm_calls / 模型 / 成本 fast-follow)。
- **最大未知**:跨 run 的**统一消息 transcript**(用户/助手轮)存 LangGraph checkpoint(keyed by thread_id),现无读 API。**M1 对话详情先做「摘要 + run 列表」**,每 run 消息仍走现 `RunDetail` 事件流。统一 transcript 标 **M1.5**(需评估 checkpoint 读端点成本)。

### M1.5 — 统一消息 transcript(盘点后定稿:后端零改动)

盘点结论(2026-07-02):「无读 API」的假设**已过期** —— Playground 会话历史早已落了读路径:

- **`GET /v1/sessions/{thread_id}/messages`**(`api/runs.py` `get_thread_messages`)直接
  `checkpointer.aget_tuple()` 读最新 checkpoint(`messages` channel 是 `add_messages`
  append reducer,最新 checkpoint 即含全量历史,单次读够),筛 human/ai 文本轮返回
  `{role: user|assistant, content}`。无 agent rebuild,失败降级空列表。
- **鉴权已适配运营场景**:`caller_owns_thread` 对 tenant admin 全租户放行
  (`_user_scope.py`),运营者看成员对话直通;普通用户仍只见本人。
- 前端 SDK `getSessionMessages`(`api/sessions.ts`)现成。

**M1.5 = 纯前端**:`ConversationDetail` 在摘要与 run 列表之间加「消息」面
(user/assistant 轮),复用现端点。已知限制(接受,不阻塞):

1. 端点无 `tenant_id` 参数 —— system_admin 从跨租户浏览器下钻他租户对话时,消息面
   拿不到(thread_meta 按 home tenant 查 → 404)→ 前端降级隐藏消息面,不炸页。
   按需 fast-follow 补参数。
2. 只含 user/assistant 文本轮 —— tool/system 轮 by design 走 per-run `RunDetail`
   事件流(职责分离:transcript 给运营看对话,事件流给调试看执行)。

### M2 — 用户(盘点后定稿)

盘点结论(2026-07-02):数据底座全齐 —— `agent_run` 有 `user_id` +
`ix_agent_run_tenant_user_created` 索引;**`token_usage` 有
`agent_name/agent_version/user_id` 三列 + `token_usage_tenant_user_time_idx`**
(migration 0096),per (agent×user) token rollup 直接 GROUP BY,**零 trace join**;
`tenant_user` 有 `display_name/created_at/last_active_at` 可 join 出人话名。

**后端新建:**

- `RunStore.aggregate_by_users(agent_name, agent_version, tenant_id)` ——
  `agent_run` 按 `user_id` GROUP BY:`conversation_count(COUNT DISTINCT thread_id),
  run_count, error_count, last_active_at(MAX created_at)`(照 `aggregate_by_threads`
  的 ABC + InMemory + Sql 三件套)。
- `TokenUsageStore.totals_by_users(tenant_id, agent_name, agent_version)` ——
  token_usage 直接按 `user_id` GROUP BY 聚合(该 agent 维度)。
- `GET /v1/agents/{name}/{version}/users` —— rollup + join `tenant_user`
  (display_name)。envelope,tenant-scoped(agent 详情本身 per-tenant,跨租户不做)。
- **`/v1/memory` + `/v1/artifacts` 补 `?user_id=` 过滤**(盘点确认缺口):tenant
  admin 治理视角看指定成员;非 admin 传非本人 user_id → 403 fail-fast(沿
  `caller_owns_thread` 的 admin 语义,不引入新权限面)。
- 用量 tab:`/v1/usage/tokens` 补 `user_id` 过滤(token_usage 索引已备)。
  per-user **成本**跟随成本 fast-follow,M2 先 token。
- migration:`thread_meta_tenant_agent_user_idx (tenant_id, agent_name, user_id)`
  —— conversations 列表按 (agent, user) 过滤加速(现仅 `(tenant_id, user_id)`)。

**可直接复用(零改动):**`GET /v1/conversations?agent_name=&user_id=`(M1 已有
全部过滤)、`ConversationDetail`、messages transcript(M1.5)。

**前端:**

- `agent_detail/UsersTab.tsx` —— 用户列表(display_name / user_id / 对话数 /
  run 数 / token / 末次活跃),行点击 → 用户详情。
- `pages/UserDetail.tsx`(路由 `/agents/:name/:version/users/:userId`)——
  四 tab:对话(listConversations user 过滤)/ 记忆 / 产物 / 用量。
- `AgentDetail` 加「用户」tab(运营区,列于对话 tab 旁)。
- SDK `api/users.ts` + memory/artifacts SDK 补参数;i18n 双语 / stories /
  Playwright / TenantScope 对账(SE-8 全走)。

### M4 — 对话全文搜索(2026-07-02 立项,用户拍板:浏览器 + agent 详情对话 tab 都要)

**动机**:`q` 只搜标题;运营真实问法是「用户说过 XX 的那个对话在哪」。标题是首条消息
截断,内容搜索缺位 = 定位链路断头。

**数据源约束(盘点定案)**:消息全历史存 LangGraph `checkpoints` 表(`AsyncPostgresSaver.setup()`
自建,非 alembic 管),channel_values 是序列化 blob——**SQL 无法对 blob 下推子串匹配/索引**,
且该表无 tenant_id/RLS。即时解 checkpoint 搜索 = O(threads) 拉 blob 解包,不可分页、不可索引,否决。

**方案:写侧镜像 + trigram 索引**(唯一能让搜索走索引、走 RLS、走既有组合机制的路):

- **`thread_message` 镜像表**(migration 0106):`(thread_id, seq)` PK、`tenant_id` NOT NULL、
  `role`(user/assistant)、`content` TEXT、`created_at`(镜像时刻;checkpoint 内消息无时间戳)。
  `thread_id` FK→thread_meta ON DELETE CASCADE(D.3 TTL 删线程自动清)。
  RLS = ENABLE+FORCE `tenant_isolation`(照 0097/0005 policy)。
  索引:`GIN (content gin_trgm_ops)`(`CREATE EXTENSION IF NOT EXISTS pg_trgm`,先例 0017 vector)。
  为什么 trgm 不是 tsvector FTS:PG 默认分词不支持中文;trgm 加速的是 `ILIKE '%q%'` 子串
  匹配——语义与现有标题搜索完全一致,中英通吃。已知退化:<3 字符查询索引利用率低,
  正确性不受影响。
- **`thread_message_sync` 水位表**:`thread_id` PK、`tenant_id`、`synced_at`、`message_count`。
  per-thread 水位;**回填 = 水位行缺失**,自动收敛,无一次性脚本。
- **`TranscriptMirrorSweep`**(control-plane,照 `ApprovalTimeoutSweep` 结构,interval loop + batch):
  每 tick 选「水位缺失 OR ∃ agent_run.updated_at > synced_at」的 thread(batch 限流),
  `checkpointer.aget_tuple()` 读最新 checkpoint(`durable_checkpointer` 装配点 app.py 已有),
  human/ai 文本轮(复用 `message_text`,与 `GET /v1/sessions/{id}/messages` 同一提取逻辑,
  抽共享函数防漂移)→ `INSERT ON CONFLICT (thread_id, seq) DO NOTHING` + 水位 upsert。
  幂等:add_messages 是 append-only reducer,seq=数组下标稳定。跨租户写走
  `bypass_rls_session`(sweep 是平台级,Stream N 契约)。**搜索可见延迟 ≤ sweep 间隔**
  (默认 60s,运营场景可接受)。
- **搜索接线**:`ThreadMessageStore.search_thread_ids(tenant_id|None, q, limit=500) -> set[UUID]`
  ——与 `thread_ids_with_runs` 完全同型。endpoint:`q` 给定时先解内容命中集,
  thread_meta list/count 的 q 条件升级为 `title ILIKE OR thread_id ∈ content_hits`
  (store 加 `q_thread_ids` 参数,list/count 共享 `_filter_clauses` 防漂移);
  命中集 ≥500 → `X-Limit-Capped`(既有机制)。**agent 详情对话 tab 走同一 endpoint,
  天然获得**(前端补搜索框即可)。
- **安全**:镜像内容与 checkpoint 同库同租户,不扩大暴露面且反而补上 RLS;
  审计 details 不记 q 原文(log-injection 前科),记 `q_content_search: bool`。

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
- 删 `WORKSPACE_ITEMS` 的 `artifacts`(产物新家 = 用户详情产物 tab,M2 落地后才可删);路由收敛。
- `memory` → 租户级「记忆治理」:label 已随 M1b-2 改「记忆治理」,M3 补 MemoryAdmin
  的 user 过滤 UI(复用 M2 的 `?user_id=`)+ agent 详情 MemoryTab **删除**
  (维度错位:记忆是 per-user 跨 agent 资产,挂 agent 下误导,§2 已判)。
- 面包屑统一:`Agent / 用户 X / 对话 #a1b2 / run #c3d4`。

### 实施切分(2026-07-02 拍板)

| PR | 内容 | 依赖 | 状态 |
|----|------|------|------|
| PR-1 | M1.5 消息面(纯前端)+ 本设计文档更新 | 无 | ✅ #883 |
| PR-2 | M2 后端:两 store 聚合 + users 端点 + memory/artifacts/usage `user_id` 参数 + migration + 测试 | 无 | ✅ #884 |
| PR-3 | M2 前端:UsersTab + UserDetail 四 tab + SDK + i18n/stories/e2e | PR-2 | ✅ #885 |
| PR-4 | M3 收口 + **H.8-F1**(实施期发现:artifacts 动作端点全是 caller-only,直接删顶层会砍 download/delete 能力 → 拍板补全再删):四动作端点 `?user_id=` admin 目标(统一 `resolve_target_user_id` 闸,supervisor 读天然按 user 参数化零改动)+ 产物全动作面迁用户详情 + 顶层 artifacts 删 + MemoryTab 删 + MemoryAdmin user 过滤 | PR-3 | ✅ #886 |
| PR-5 | 浏览器运营补齐:过滤(#888)→ 真分页 + since(#889)→ 末次活跃排序 + has_pending(#890)→ 过滤 URL 化 + 自动刷新 + 返回链(#891) | PR-4 | ✅ |
| PR-6 | M4 全套:migration 0106 + `ThreadMessageStore` + `TranscriptMirrorSweep` + `q` 内容搜索接线 + 前端(浏览器 placeholder 升级 + agent 详情对话 tab 搜索框/真分页)+ 测试 | 无 | 本 PR |

## 7. 非目标(有意不做)

- Langfuse 不取代深度 LLM trace(只读观测 + 单实例无 RLS 跨租户红线);不在 Expert Work 重建 trace 树。
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
