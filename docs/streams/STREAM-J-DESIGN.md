# Stream J — Agent Harness 能力补全（设计先行）

> 落实 [docs/ITERATION-PLAN.md](../ITERATION-PLAN.md) § Stream J。
> 执行 [architecture/08-AGENT-CAPABILITY-ASSESSMENT](../architecture/08-AGENT-CAPABILITY-ASSESSMENT.md) § 5 的决策 —— 把 26 维能力矩阵的 14 个认知 / harness 缺口补到生产级。
>
> **覆盖范围**：J.1–J.15 共 15 个子项。本文件是 Stream J 的总设计 —— 锁定总体架构、跨切面数据模型、实现顺序与依赖、以及每个子项的范围边界 / 架构 / 接口 / 整合点 / Mini-ADR。每个子项的 PR 在此基础上做更细的局部设计（设计先行规则递归适用）。

设计先行规则（[memory:feedback_design_first_iteration.md](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_design_first_iteration.md)）：
所有总体架构 / 跨切面接口 / Mini-ADR 必须在编码前于本文件锁定；每个子项 PR 只执行本文件对应章节 + 其局部细化设计。

> **对标纪律**：deer-flow / hermes-agent 作能力基线，校准"成熟长什么样"+ 找差距。**结论是独立设计,不照抄**（[memory:general-platform-positioning](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_general_platform_positioning.md)）。

> **2026-05-20 未交付项审计补强**：按 [memory:complete-not-minimal](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_complete_not_minimal.md) + [memory:no-design-choice-disguise](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_no_design_choice_disguise.md) 对 J 剩余 8 项的 (c) 维度做补强，新增 Mini-ADR J-21 ~ J-29（见 § 19 末尾）。补强点：J.4 trajectory + budget telemetry / J.5 拆 4 PR + SLO / J.7 范围缩到 J.7a / J.8 必加超时 + audit trail + UI / J.9 lifecycle + quota / J.10 重试 + quota + event + persistence / J.12 与 L7 修剪 / J.13 拆 3 子项 / J.15 volume quota + backup + encryption。各项实施前 PR 必须先修订对应 § 9-§ 18 反映本次补强。

> **2026-05-20 J.6 完成补审**：J.6（PR #167-#171）在上一轮已交付审计时还在进行中（明确"本审计不评"），本次未交付项审计也复述"不重评"。事后补审 9 维 (c) 红线发现 6 维 gap：upload quota 缺位 / upload audit trail 不完整 / image lifecycle 零实现 / Path B VL fallback 单 provider 硬失败 / multi-image 集成测试缺 / EXIF strip + 内容扫描全无 / PII in images 零覆盖。新增 Mini-ADR J-30 ~ J-35（见 § 19 末尾）：J-30 quota 接入 C.5 / J-31 audit trail / J-32 lifecycle / J-33 VL fallback / J-34 EXIF strip + multi-image 测试 / J-35 NSFW + PII in images 显式 (a) 推 M2。J.6 ITERATION-PLAN 行加 4 个 (c) 补强子项 J.6.补强-1~4 + 1 个 (a) 决策项 J.6.决策-5。

> **2026-05-20 J.15 设计 PR**：探查 sandbox-supervisor 现状发现 **§ 9.1 "已落地"声明属实** —— 热会话 + TTL reaper + named volume 创建 + 持久 100% 已交付（supervisor.py + reaper.py + 迁移 0018/0020）。真正待做的是 J-29（quota + backup + encryption）+ J-36（volume lifecycle，新增）共 4 项 M0 补强。本次设计 PR：(1) § 9.1 改写明确"已落地 vs 待做"基线；(2) § 9.5 新增 4 子节详化 J-29 + J-36 实施设计；(3) § 19 新增 Mini-ADR J-36 volume lifecycle 三档。后续 J.15-补强-1 PR（quota + lifecycle）+ J.15-补强-2 PR（backup + 加密文档）按本设计实施。

> **2026-05-21 J.13a 设计 PR**：原 § 18 是"四点纲领"骨架（Mini-ADR J-20）；J-28 已先于本 PR 把 J.13 拆为 J.13a M0 / J.13b M1 / J.13c M1。本次 PR：(1) § 18 全文重写按 J-28 落实 J.13a 实施设计 —— 7 已交付能力（J.1 / J.2 / J.3 / J.6 / J.11 / J.14 / J.15）各定 eval module + dataset + metric + threshold，8 个 deferred 能力写 skeleton stub；(2) § 19 新增 Mini-ADR J-37（per-cap metric 类型矩阵）+ J-38（baseline 文件 = checked-in YAML + 能力 PR 同步更新）+ J-39（LLM-judge 模型选型 Haiku 4.5 + temperature=0.0 + N=3 重跑）；(3) baseline 制品锁定路径 `tools/eval/baselines/m0_gate_baseline.yaml`，Stream M Gate Exit Criteria 直接读它。后续 J.13a 实施 PR 按本设计执行；J.13b / J.13c 在 § 18.7 仅占位，M1 早期或并入 M2-D 再展开。

---

## 1. 范围 & 边界

### 1.1 In-scope（J.1–J.15）

| 子项 | 能力 | 当前成熟度 | 本 Stream 交付 | Mini-ADR |
|------|------|-----------|---------------|----------|
| **J.14** | 租户内 per-user 隔离 | 缺失 | `(tenant_id, user_id)` 复合 scope —— `user_id` 升为一等列;thread / 记忆 / 工作区 / 产物按用户归属 + 授权 | J-1 J-2 |
| **J.1** | 规划 / 任务分解 | 缺失 | `planner` 图节点 + `plan_execute` workflow;计划与 todo 进 `AgentState`;`update_plan` 工具供重规划 | J-3 J-4 |
| **J.2** | 反思 / 自我修正 | 骨架 | `reflect` 节点 —— 受控触发的自我批判 / 修正（非现有 `loop_detection` 病态退化保护）| J-5 |
| **J.11** | Model 路由 | 骨架 | `ModelSpec.routing` —— 按步骤类别（规划 / 工具汇总 / 视觉）选模型;`LLMRouter` 加路由策略 | J-6 |
| **J.3** | 长期记忆 | 缺失 | `memory_item` 表（pgvector,per-`(tenant,user)`）+ 记忆 store + 检索;经现有 `DynamicContextMiddleware` 注入 | J-7 J-8 |
| **J.15** | 有状态 per-user 执行环境 | 缺失 | per-user 持久卷 + 沙盒会话生命周期（活沙盒复用 / 空闲 hibernate / 快速 restore）;**临时算力 + 持久卷**,不走 CRIU | J-9 J-10 |
| **J.9** | 产物 / Artifact 管理 | 缺失 | `artifact` 表 + 持久卷存内容;版本化;经 run API + SSE `artifact` 事件回传 | J-11 |
| **J.4** | Sub-agent / 多智能体 | 缺失 | agent-as-tool —— 每个声明的子 agent 作为命名 `Tool`;取消链穿透;构建期深度上限(无 token 预算下钻) | J-12 |
| **J.5** | 知识 / 检索（RAG）| 缺失 | `knowledge_search` 工具（混合检索:向量 + 全文 RRF → LLM 重排）+ `knowledge_base`/`document`/`chunk` 表（复用 J.3 pgvector）;强解析 + 结构/语义/表格感知切块;文档可运维、异步摄取;租户隔离 | J-13 |
| **J.6** | 多模态输入 | 骨架 | 图像输入 —— **能力解析式双路**:视觉主模型走 `HumanMessage` content block(直看像素)、文本主模型走 `ask_image` 工具 + 单独 VL 模型;统一经对象存储摄取;构建期按 `ModelSpec.supports_vision` 决议 | J-14 |
| **J.8** | 人在回路 / 审批 | 缺失 | LangGraph `interrupt()` 审批节点;run 暂停 → control-plane 暴露审批请求 → API resume;危险操作按策略门控 | J-15 |
| **J.7** | Skill + skill 进化 | 缺失 | skill = 可复用能力包（prompt 片段 + 工具集 + 可选代码）;`skill` 库表;agent 可习得 / 精化 skill（有界,非无界自改）| J-16 J-17 |
| **J.10** | 调度 / 触发 | 缺失 | cron / 事件 / webhook 触发器;`trigger` 表 + control-plane scheduler;触发式 run | J-18 |
| **J.12** | 学习 / 反馈闭环 | 缺失 | L7 trajectory + feedback（G.6）→ 规则候选 + 人工策划 → `eval_dataset`（与 J.13 共用,按 tenant+agent 归集）;离线数据驱动（区别于 J.7 运行期自改）| J-19 J-43 |
| **J.13** | eval 强化 | 骨架 | 评估 G.4/G.5,落实升级：J.1–J.14 的逐能力 eval 场景 + 在线采样 eval + CI 回归门 | J-20 |

### 1.2 Out-of-scope（明确推迟）

| 推迟项 | 落地 | 备注 |
|-------|------|------|
| 用户级 RLS 强隔离覆盖全部表 | 不做 | Mini-ADR J-1 —— 硬隔离边界 = 租户;租户内用户 = 同公司同信任域,`user_id` 走一等列 + 应用层授权,仅 per-user 数据表加防御性用户级 RLS |
| LLM 微调 / 训练管线 | M2+ | J.12 只交付到"策划数据集",不含训练 |
| 多模态**输出**（图像 / 音频生成）| M1+ | J.6 仅输入侧 |
| skill marketplace / 跨租户 skill 共享 | M1+ | J.7 限租户内 skill 库 |
| sandbox warm pool（预热空沙盒）| M1-A | 与 J.15 的 per-user **有状态**沙盒是两回事（评估 08 § 4 注） |
| 向量库独立中间件（Milvus / Qdrant）| 视规模 M1+ | J.3 / J.5 先用 Postgres `pgvector`,够用不引外部依赖 |
| 分布式 scheduler（多副本选主）| M1+ | J.10 M0 单副本 scheduler |
| 图像 NSFW / 恶意 SVG / 病毒扫描 | M2 | Mini-ADR J-35 —— M0 用户 = 同公司同信任域,SVG sanitizer / NSFW 模型 / OCR 是另一套范式;M2-D 或新 stream M2-H 承接 |
| 图像 PII redaction（OCR + redact）| M2 | Mini-ADR J-35 —— D.2 PII redactor 仅文本;图里 PII 走 OCR pipeline 是另一套范式 |

### 1.3 验收（Stream J Exit）

1. 26 维能力矩阵（评估 08 § 2）无"缺失 / 骨架"遗留（eval 按 J.13 结论判定）。
2. 每个 J.x 子项接入 live agent 路径,单测 + 集成测试,80% 覆盖,CI 8/8 绿,零技术债收尾。
3. canonical per-user 持久 agent 端到端跑通：多轮对话跨会话保持记忆、产物在持久工作区留存、空闲 hibernate 后新消息快速 restore。
4. ITERATION-PLAN § Stream J 全部 checkbox 勾选,文字与实现一致。

---

## 2. 总体架构

### 2.1 目标产品形态（设计锚点）

平台要支撑的目标形态（评估 08 § 4,2026-05-18 用户确认）：**租户 = 公司,用户 = 公司的员工 / 客户;每个用户拥有自己的、持久的 agent 实例 —— 对话状态 + 长期记忆 + 持久工作区。空闲自动释放算力,新消息快速还原。** canonical agent 即此形态本身。

这一形态把 15 个子项分成三类角色：

- **支撑 per-user 持久性的基座**：J.14（隔离 scope）、J.3（长期记忆）、J.15（持久执行环境）、J.9（产物）。
- **补齐认知 / harness 能力面**：J.1 规划、J.2 反思、J.11 路由、J.4 sub-agent、J.5 RAG、J.6 多模态、J.8 人在回路、J.7 skill。
- **运营 / 演进闭环**：J.10 调度、J.12 学习闭环、J.13 eval。

### 2.2 心智模型：无状态计算 + 持久状态

helix 现有架构已是"无状态计算 + 持久状态（checkpointer）"。per-user"实例" **不是**一个长驻进程 —— 它 = `thread` + `checkpoint` + 长期记忆行 + 持久卷。空闲零算力成本。**Stream J 不引入 instance manager 长驻进程**;"实例"是数据,算力按需起落（[memory:target-product-form](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/project_target_product_form.md)）。

### 2.3 整合点全景

15 个子项全部嵌入现有四个扩展面,**不新建并行架构**：

| 现有扩展面 | 文件 | Stream J 接入 |
|-----------|------|--------------|
| **LangGraph 图节点** | `orchestrator/graph_builder/builder.py` `build_react_graph()` | J.1 `planner` 节点、J.2 `reflect` 节点、J.8 `interrupt` 审批节点 |
| **中间件链**（4 锚点）| `helix-runtime/runtime/middleware/` | J.3 记忆注入（复用 `DynamicContextMiddleware`）、J.11 路由（`around_llm_call`）|
| **工具注册表** | `orchestrator/tools/` `ToolRegistry` | J.4 sub-agent-as-tool、J.5 `knowledge_search`、J.9 artifact 工具、J.1 `update_plan` 工具、J.6 `ask_image` 工具（Path B）|
| **AgentSpec / 持久化** | `helix-protocol/` `AgentSpec`、`helix-persistence/` | 全部子项的声明字段 + 新表（迁移 0015+）|

### 2.4 `AgentState` 扩展

`orchestrator/state.py` 的 `AgentState`（checkpointer 持久）新增字段 —— 每个子项 PR 增量加,带 reducer：

```python
class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]   # 现有
    step_count: int                                        # 现有
    max_steps: int                                         # 现有
    # Stream J 增量：
    plan: Plan | None                                      # J.1 —— 当前计划 + todo
    reflections: Annotated[list[Reflection], add]          # J.2 —— 反思记录
    pending_approval: ApprovalRequest | None               # J.8 —— 待审批门
```

> J.4 递归深度**不进 `AgentState`** —— 它是 `build_agent(subagent_depth=)` 的构建期参数(决定该 agent 的图是否挂 `SubAgentTool`),而非运行期可变状态。

> 加字段对 checkpointer 向前兼容（缺字段读旧 checkpoint 时取默认）—— 每个 PR 在 reader 侧 `state.get(key, default)`。

### 2.5 `AgentSpec` 扩展

`helix-protocol/agent_spec.py` 的 `AgentSpecBody` 新增**可选**声明块（默认关闭,不破坏现有 manifest）：

| 字段 | 子项 | 内容 |
|------|------|------|
| `model.routing: RoutingSpec \| None` | J.11 | 按步骤类别选模型的策略 |
| `memory: MemorySpec`（已存在,扩充）| J.3 | 长期记忆开关 / 检索 top-k / 写回策略 |
| `planning: PlanningSpec \| None` | J.1 | 规划开关 / 重规划触发条件 |
| `reflection: ReflectionSpec \| None` | J.2 | 反思触发点 / 预算上限 |
| `subagents: list[SubAgentSpec]` | J.4 | 可委派的子 agent 声明 |
| `knowledge: KnowledgeSpec \| None` | J.5 | 绑定的知识库 |
| `skills: list[str]` | J.7 | 启用的 skill 名 |
| `triggers: list[TriggerSpec]` | J.10 | cron / event / webhook 触发器 |
| `workflow.type` | J.1 | `plan_execute` 值激活规划循环（枚举已存在）|

### 2.6 新增持久化（迁移 0015 起）

> 每个子项 PR 一个迁移文件,**expand-contract,只向前**（STREAM-I-DESIGN § 7 纪律）。`0016` 是 `0016_drop_app_user`（J.14 收尾,删占位表）,故 J.3 长期记忆落在 `0017`。

| 迁移 | 表 | 子项 | RLS |
|------|-----|------|-----|
| `0015_tenant_user` | `tenant_user` 注册表 + `thread_meta.user_id` 列 | J.14 | tenant RLS |
| `0017_long_term_memory` | `memory_item`（pgvector）| J.3 | tenant + user 组合 RLS |
| `0018_user_workspace` | `user_workspace`、扩 `sandbox_instance` | J.15 | 无 RLS（supervisor 持有,同 `sandbox_instance`）|
| `0019_artifact` | `artifact`、`artifact_version` | J.9 | tenant + user 组合 RLS |
| `0020_sandbox_last_used` | 扩 `sandbox_instance`（`last_used_at`）| J.15 | 无 RLS（supervisor 持有）|
| `0021_knowledge_base` | `knowledge_base`、`knowledge_document`、`knowledge_chunk`（pgvector）| J.5 | tenant RLS |
| `0022_knowledge_chunking_hybrid` | 扩 `knowledge_base`（per-KB 切块参数）、扩 `knowledge_chunk`（`content_tsv` 全文检索列 + GIN）| J.5 | — |
| `0023_skill` | `skill`、`skill_version` | J.7 | tenant RLS |
| `0024_trigger` | `trigger`、`trigger_run` | J.10 | tenant RLS |
| `0034_eval_dataset` | `eval_dataset`、`curation_candidate` | J.12 J.13 | tenant RLS |

---

## 3. 实现顺序与依赖

### 3.1 波次

依赖驱动,分四波;一波内子项可并行（各自一 PR）：

```
Wave 0 ── J.14 隔离基座 ────────────────────────┐ (一切 per-user 数据的前置)
                                                │
Wave 1 ── J.1 规划 ─ J.2 反思 ─ J.11 路由       │ (纯认知,无 per-user 数据依赖,可与 W0 并行)
                                                │
Wave 2 ── J.3 长期记忆 ─ J.15 执行环境 ─ J.9 产物  ◄┘ (依赖 J.14;J.9 依赖 J.15)
                                                │
Wave 3 ── J.4 sub-agent ─ J.5 RAG ─ J.6 多模态   ◄┘ (J.4 依赖 J.1 取消/预算;J.6 独立)
                                                │
Wave 4 ── J.8 人在回路 ─ J.10 调度 ─ J.7 skill ─ J.12 学习闭环 ─ J.13 eval
                                                  (J.12 依赖 J.13 数据集格式;J.13 收尾,验所有子项)
```

### 3.2 关键依赖

- **J.14 是硬前置**：J.3 / J.15 / J.9 的表都带 `user_id`,必须先有 `tenant_user` 注册表 + scope 约定。J.14 与 Wave 1 可并行（Wave 1 不碰 per-user 数据）。
- **J.9 依赖 J.15**：产物落在持久工作区,工作区由 J.15 建。
- **J.4 依赖 J.1**：sub-agent 委派需要父 agent 先能规划"把子任务交给谁";且复用 J.1 引入的协作式取消链(穿透进子 run)。
- **J.6 独立**：能力解析式双路 —— 视觉主模型走 content-block（无需路由）、文本主模型走 `ask_image` 工具;工具的 VL 模型经 `vision:` manifest 块声明,不走 J.11 路由规则,故 J.6 不依赖 J.11。
- **J.13 收尾**：eval 强化要给 J.1–J.14 每项写 eval 场景,故排最后。**J.12 / J.13 共用 `eval_dataset` 格式**,J.13 先定格式 J.12 再产数据。

### 3.3 一 PR 一子任务

15 个子项 = 15 个起步 PR。大子项（J.15 执行环境、J.7 skill）允许在其局部设计里再拆 2–3 个 PR。每 PR：局部设计先行 + TDD + 零技术债收尾。

---

## 4. J.14 — 租户内 per-user 隔离

### 4.1 现状

helix 隔离做到 **per-租户**：Postgres RLS（GUC `app.tenant_id`）+ `build_rls_sessionmaker` 每事务 `set_config`。`user_id` 不是一等隔离 scope —— thread / session 归属靠授权层临时判断,无可用的持久"用户"实体（Stream C 的 `app_user` 占位表无 ORM 无消费方）。per-user 持久 agent 形态要求"用户"成为一等实体。

### 4.2 设计与边界

| 问题 | 决策 |
|------|------|
| 隔离强度 | **硬隔离边界 = 租户**(RLS 不变);租户内用户 = 同公司同信任域 —— `user_id` 走**一等列 + 应用层授权**,不给所有表加用户级 RLS（Mini-ADR J-1）|
| 防御纵深 | per-user **数据表**（`memory_item` / `user_workspace` / `artifact`）额外加用户级 RLS 策略（GUC `app.user_id`）—— 这些表泄漏后果重,值得纵深 |
| `user_id` 来源 | `Principal`（已携带 `subject_type` / `subject_id` / `tenant_id`）→ control-plane `resolve()` upsert `tenant_user` → `user_id` = `tenant_user.id` |
| 复合 scope key | thread / 记忆 / 工作区 / 产物 全部按 `(tenant_id, user_id)` 归属;run API 入参带 `user_id` |
| 新建 vs 复用 | Stream C 迁移 0004 已有占位表 `app_user`（全局身份、`username` 全局唯一、纯 OIDC key、无 ORM 无消费方）。**J.14 不复用它**——其 schema 对多租户有缺陷且属 Stream C 平台 auth 预留;另建干净的、多租户正确的注册表 `tenant_user`（Mini-ADR J-1a）|

### 4.3 接口与数据模型

```python
# 迁移 0015_tenant_user
class TenantUserRow(Base):                 # 表 tenant_user(RLS 租户隔离)
    id: UUID                               # 代理键 = user_id
    tenant_id: UUID                        # RLS scope
    subject_type: str                      # user / service_account / service
    subject_id: str                        # OIDC sub / SA id
    display_name: str | None
    created_at, last_active_at: datetime
    # UNIQUE(tenant_id, subject_type, subject_id)

# thread_meta 加列 user_id: UUID | None —— 裸列,无 FK
#   (仓库 FK-light 风格 + 规避 FORCE-RLS 与 FK 校验交互)
```

- `TenantUserStore.resolve()` —— 按 `(tenant_id, subject_type, subject_id)` 幂等 upsert,每次 bump `last_active_at`;memory + sql 两实现。
- **J.14 不加用户级 RLS GUC** —— Mini-ADR J-1:`thread_meta` 维持租户级 RLS,`user_id` 走应用层授权。`app.user_id` GUC + `current_user_id_var` 推迟到 **J.3**(首个用户级 RLS 表 `memory_item`)随之落地。
- control-plane:会话创建时 `resolve()` 解析 + stamp `thread_meta.user_id`;读 / run / 状态流转做所有权校验(thread 有 `user_id` 时仅属主或 admin 可访问)。
- `AgentSpec` 无需改 —— `user_id` 是运行期上下文,非 manifest 声明。
- **拆 2 PR**:PR1 = 持久化基座(`tenant_user` 表 / 模型 / 协议 / store + `thread_meta.user_id`);PR2 = control-plane 接入与所有权强制。

### 4.4 整合点

`helix-persistence/rls.py`、control-plane 的 `RLSContextMiddleware`（Stream C）、run API（加 `user_id` 入参 + thread_id 派生改为 `(tenant, user, conversation)`）。

> **对标**：deer-flow / hermes 是单用户工具,无此语境 —— J.14 是 helix 作为企业平台的独有维度,无可抄,纯独立设计。

---

## 5. J.1 — 规划 / 任务分解

### 5.1 现状

纯单步 ReAct（`agent → tools → agent` 循环）,无 planner、无 todo。复杂多步任务靠 LLM 隐式在 `messages` 里自己记 —— 易跑偏、无显式进度。

### 5.2 设计与边界

- `planner` 图节点：`START → planner → agent`,把任务分解成有序 `Plan`（步骤 + todo 状态）写入 `AgentState.plan`。
- `WorkflowSpec.type == "plan_execute"`（枚举已存在）激活规划循环;`react` 保持纯 ReAct 不变。
- 重规划：`update_plan` 工具供 agent 在执行中修订计划;`reflect` 节点（J.2）也可触发重规划。
- **边界**：planner 不做分布式编排,就是单 agent 的显式 todo 跟踪 + 重规划。深度多 agent 编排归 J.4。

### 5.3 接口与数据模型

```python
# helix-protocol
class PlanStep(BaseModel):
    id: str
    description: str
    status: Literal["pending", "in_progress", "done", "skipped"]
class Plan(BaseModel):
    goal: str
    steps: list[PlanStep]
    revision: int
class PlanningSpec(BaseModel):              # AgentSpecBody.planning
    enabled: bool = False
    replan_on_error: bool = True
    max_replans: int = 3
```

`planner` 节点用 LLM 结构化输出生成 `Plan`;计划进 system context 供后续步骤对齐。

### 5.4 整合点

`graph_builder/builder.py`（加节点 + 条件边）、`state.py`（`plan` 字段）、`tools/`（`update_plan` builtin 工具）、`agent_factory.py`（按 `workflow.type` 装配）。

> **对标**：deer-flow `TodoMiddleware`（todo 作中间件）、hermes `todo` + Kanban。helix 取**节点 + 状态字段**而非中间件 —— 计划是核心控制流,放节点比放中间件锚点更显式可控。

---

## 6. J.2 — 反思 / 自我修正

### 6.1 现状

仅 `LoopDetectionMiddleware`（`after_llm_call`）—— 检测重复 tool_call 的**病态退化保护**,不是真正的"评估自己做得对不对"。

### 6.2 设计与边界

- `reflect` 图节点：受控触发的自我批判 —— 评估"当前进展是否在朝目标走 / 上一步结果是否有问题",产出 `Reflection`,可修正路线或触发 J.1 重规划。
- **触发受控,不是每步都反思**（成本）：触发点 = 计划里程碑完成 / 工具报错 / 出最终答案前。`ReflectionSpec.budget` 限总反思次数。
- 与 `loop_detection` 并存,职责不同：loop_detection 防机械重复,reflect 防语义跑偏。

### 6.3 接口与数据模型

```python
class Reflection(BaseModel):
    trigger: Literal["milestone", "tool_error", "pre_final"]
    critique: str
    action: Literal["continue", "revise", "replan"]
class ReflectionSpec(BaseModel):
    enabled: bool = False
    triggers: list[str] = ["tool_error", "pre_final"]
    budget: int = 5
```

### 6.4 整合点

`graph_builder/builder.py`（`reflect` 节点 + 条件边回 `agent` / `planner`）、`state.py`（`reflections` 列表字段,reducer = append）。

> **对标**：hermes 后台 review loop（daemon 自评）。helix 取**同步图内节点**而非后台 daemon —— M0 单体进程内,同步节点更简单且可控,不引后台进程。

---

## 7. J.11 — Model 路由

### 7.1 现状

`ModelSpec.fallback` 给的是**故障转移**链(主模型挂了换备);一个 agent 全程锁一个模型。无"按这一步的难度 / 成本 / 模态选模型"。

### 7.2 设计与边界

- `ModelSpec.routing: RoutingSpec` —— 按**步骤类别**选模型：`planner` 步用强模型、`tool_result_summarize` 用便宜模型。
- `LLMRouter` 在 `fallback`（已有）之外加**路由策略**：调用前按当前步骤类别选 provider handle。
- **边界**：路由按**声明式规则**（步骤类别 → 模型),不做基于 token 预测的动态难度估计（过度工程）。

### 7.3 接口与数据模型

```python
class RouteRule(BaseModel):
    when: Literal["planning", "reflection", "tool_summarize", "default"]
    model: ModelSpec                        # 复用 ModelSpec(可带自己的 fallback)
class RoutingSpec(BaseModel):
    rules: list[RouteRule]
```

`LLMRouter` 选 handle：先按 `routing.rules` 命中步骤类别 → 再走该模型的 `fallback` 链。步骤类别由调用方（图节点）经 `MiddlewareContext.payload["route_class"]` 传入。

### 7.4 整合点

`agent_factory.py` `build_llm_router()`、`helix-runtime` 的 `LLMRouter`、`around_llm_call` 中间件链、`helix-protocol` `ModelSpec`。

> **对标**：hermes `image_routing.py` 把 vision 单做一条路由。helix 的步骤类别路由是通用机制;多模态的 VL 模型选择**不走**路由规则 —— 路由换的是"节点跑哪个模型",VL 是"工具背后的模型",两个轴,VL 模型经 J.6 的 `vision:` manifest 块声明(§ 13)。

---

## 8. J.3 — 长期记忆

### 8.1 现状

只有单 run 的 checkpointer（短期对话状态）。跨会话 = 失忆 —— per-user 持久 agent 形态的核心缺口。`DynamicContextMiddleware` 已有"注入 memory"的槽位,但无后端。

### 8.2 设计与边界

- `memory_item` 表（pgvector,per-`(tenant_id, user_id)`）—— 跨会话持久记忆。
- **记忆层次保持简单**：`fact`（稳定事实 / 偏好）+ `episodic`（过往交互摘要）两类,不做评估 08 提过的 5 层细分（过度工程,先两类够用）。
- 检索：run 开始时按当前消息语义检索 top-k 记忆 → 经 `DynamicContextMiddleware` 注入 system context。
- 写回：run 结束时一个 `memory-extraction` 后处理（LLM 抽取本轮值得长期记的事实 / 摘要）→ 去重后写表。**边界**：写回是 run 后异步处理,不阻塞响应。

### 8.3 接口与数据模型

```python
# 迁移 0016_long_term_memory
class MemoryItemRow(Base):                  # 表 memory_item
    id: UUID
    tenant_id, user_id: UUID                # 复合 scope(J.14)
    kind: Literal["fact", "episodic"]
    content: str
    embedding: Vector(1536)                 # pgvector,模型见 Mini-ADR J-8
    source_thread_id: str
    created_at, last_used_at: datetime

class MemorySpec(BaseModel):                # 扩充现有 AgentSpecBody.memory
    long_term_enabled: bool = False
    retrieve_top_k: int = 5
    write_back: bool = True
```

`MemoryStore` 协议（`helix-persistence/`）：`retrieve(tenant_id, user_id, query, k)` / `write(items)`。

### 8.4 整合点

`helix-persistence/`（新 `memory` 子包 + `MemoryStore`)、`DynamicContextMiddleware`（接 store,填记忆槽）、run 收尾钩子（`agent_factory` 的 `hooks` 或 control-plane run 后处理）。

> **对标**：deer-flow 跨会话结构化记忆、hermes 持久 `MEMORY.md`（单文件）。helix 取**结构化表 + 向量检索**而非单文件 —— 多租户多用户规模下,文件不可检索不可隔离。

---

## 9. J.15 — 有状态 per-user 执行环境

### 9.1 现状（2026-05-21 J.15 收尾 — M0 全交付）

**已落地（M0 内已交付）**：
- ✅ **热会话 + TTL reaper**（Mini-ADR J-10）：`supervisor.acquire(user_id)` 取/建 per-`(tenant,user)` 会话；`release` 对会话沙盒 = no-op 留热；`exec` 刷新 `last_used_at`；reaper 按 `last_used_at + session_idle_ttl_s`（默认 15min）回收（`supervisor.py:119-291` + `reaper.py:39-58` + `store.py:92-126`）
- ✅ **持久卷创建 + 登记**：`user_workspace` 表 + `sandbox_instance.user_id / workspace_id`（迁移 0018）+ `sandbox_instance.last_used_at`（迁移 0020）；runtime_provider 用 `--volume {workspace_volume}:/workspace` 挂载；首次 acquire 自动创建卷
- ✅ **per-session 锁 + held-pipe**：同会话并发 exec 经 `asyncio.Lock` 串行化；held-pipe 一容器多 exec 协议完整
- ✅ **Volume quota 准入**（J.15-补强-1 / Mini-ADR J-29 第 1 项 / PR #211）：迁移 0026 加 `size_limit_bytes` 列（默认 10 GiB）；`quota_enforcer.py` 预检 + release 后异步对账；超 quota → HTTP 429；audit `WORKSPACE_QUOTA_DENIED`
- ✅ **Volume lifecycle 状态机 第 1→2 档**（J.15-补强-1 / Mini-ADR J-36 / PR #211）：`mark_workspace_deleted` 公共 API soft-delete + 强销毁 warm session；soft-deleted 卷 acquire → HTTP 410 Gone；迁移 0026 加 `deleted_at` + `archived_object_key` + CHECK invariant + partial index
- ✅ **Volume lifecycle 状态机 第 2→3 档物理回收**（J.15-补强-2 / PR #212）：`lifecycle.py:VolumeLifecycleManager.archive_pending` 扫 list_pending_archive → `DockerClient.archive_volume` tar.gz → ObjectStore put → `mark_archived` → `remove_volume`；reaper 每 tick 触发
- ✅ **Daily backup pipeline**（J.15-补强-2 / Mini-ADR J-29 第 2 项 / PR #212）：app.py lifespan 起 `_run_daily_backup` 任务循环到 `workspace_backup_hour`（默认 03:00 UTC）→ `lifecycle.backup_active(now=date)` 扫 `list_active` → 每卷写 ObjectStore key `volume-backups/{tenant}/{user}/{YYYY-MM-DD}/{volume_name}.tar.gz`；rolling window 7 天
- ✅ **DLQ retry (K7 模式)**（J.15-补强-2 / PR #212）：迁移 0027 `volume_backup_dlq` 表 + `VolumeBackupDLQ` ABC + SQL/In-Memory 实现；reaper 每 tick `drain_dlq(limit=16)`；backoff 1m → 5m → 30m → 2h → 6h → dead-letter 365d
- ✅ **At-rest 加密文档化**（J.15-补强-2 / Mini-ADR J-29 第 3 项 / PR #212）：`docs/runbooks/deployment.md` 新增 "Volume at-rest encryption" 章节锁定阿里云 ECS 数据盘加密 / 自托管 LUKS / macOS FileVault 强约束 + 部署 checklist
- ✅ **Restore 演练能力**（J.15-补强-2 / PR #212）：`tools/persistence/restore_volume.py` 库 + CLI（优先 J-36 archive，次选 J-29 daily）+ `docs/runbooks/volume-restore.md` operator 手册（pre-flight / find artifact / 跑 script / 验证 / 升级 SQL / failure modes）

**显式推 M1（按 [memory:no-design-choice-disguise] 不允许"软推迟"）**：
- 跨 host 调度 → M1-A（与 sandbox warm pool 同期）
- 90 天 archive hard-delete（retention-cleanup-job 加 volume 维度）→ M1
- Recovery API（un-soft-delete workspace）→ M1
- Multipart streaming `ObjectStore.put`（M0 单批 in-mem 1.5 GiB cap 够用）→ M1

历史背景：沙盒 per-run 临时无状态（Mini-ADR F-2）的设计已被 J.15 的"per-user 持久卷 + 热沙盒会话"取代；无 `user_id` 的 run 仍走 tmpfs 临时沙盒兼容（见 § 9.2）。

### 9.2 设计与边界

**架构：持久卷 + per-user 热沙盒会话**（评估 08 § 4 推荐;Mini-ADR J-9 / J-10）：

- **持久卷**：每个 `(tenant_id, user_id)` 一个 docker named volume,存工作区文件 / 中间产物。卷长存,由 `user_workspace` 表登记。
- **per-user 热沙盒会话**：每个 `(tenant_id, user_id)` 至多一个 ACTIVE 沙盒容器,挂载该用户的卷到 `/workspace`（替代 `--tmpfs`）,**跨 run / 跨消息保持热**。`acquire`（带 `user_id`）语义 = 取或建该用户的会话沙盒;有热会话则复用、免 `docker run` 冷启动。held-pipe 的 runner 协议本就支持一容器多次 exec。
- **空闲 TTL 释放**（Mini-ADR J-10）：每次 exec 刷新 `last_used_at`;reaper 回收 `last_used_at + session_idle_ttl_s`（默认 15min）之外的 `IN_USE` 会话 —— `docker rm -f`,**卷保留**。空闲超 TTL 才销毁,而非每个 run 结束。
- **"restore" = 冷启动新容器挂暖卷**：会话被空闲回收后,下条消息 `acquire` 起全新 `docker run` 挂已有卷 —— 上一轮文件就在卷里。TTL 窗口内的连续消息走热路径、零冷启动。
- **无 `user_id` 的 run**：退回 per-run 临时 tmpfs 沙盒（机器触发、无用户绑定 —— 无会话可热）,向后兼容。
- **不走 CRIU 容器快照、不走 `docker stop`/`start` hibernate**——held-pipe（`docker run -i` 持管道）把容器寿命绑死在 attached 子进程,停 / 起同一容器无法重连管道。held-pipe 世界里"hibernate"只能是销毁 + 冷启动重建;持久卷保证零数据损失。

### 9.3 接口与数据模型

```python
# 迁移 0018_user_workspace —— user_workspace 表 + sandbox_instance 加
#   user_id / workspace_id（已落地）
# 迁移 0020_sandbox_last_used —— sandbox_instance 加 last_used_at:reaper 按
#   last_used_at + session_idle_ttl_s 回收空闲热会话（非 acquired_at + timeout_s）
# SandboxState 不变 —— 热会话就是 IN_USE（确实在用,只是不在 exec）
```

sandbox-supervisor 变更：
- `acquire`（带 `user_id`）→ 取或建 `(tenant,user)` 热会话;`release` 对会话沙盒 = no-op（留热）,对无 `user_id` 的临时沙盒 = 销毁（不变）。
- `exec` 刷新 `last_used_at`;同会话并发 exec 经 per-session 锁串行化（held-pipe 一次一 exec）。
- reaper 判定口径从 `acquired_at + timeout_s + grace` 改为 `last_used_at + session_idle_ttl_s`。
- 取消（Mini-ADR F-8）`destroy` 仍强销毁会话 —— 下条消息冷启动。
- `default_max_sandboxes` 默认上限抬高 —— 热会话长期占配额槽,上限实质 = 同时活跃用户数。

### 9.4 整合点

`sandbox-supervisor/supervisor.py`（会话取/建/复用、per-session 锁）、`sandbox-supervisor/reaper.py`（`last_used_at + TTL` 口径）、`sandbox-supervisor/runtime_provider`（named volume 挂载替 tmpfs）、`sandbox_instance` 模型 + 迁移、`SandboxSupervisorSettings`（`session_idle_ttl_s`、抬高的 `default_max_sandboxes`）。`exec_python` 工具不变 —— 仍 acquire→exec→release,热复用全在 supervisor 侧。

> **对标**：hermes Daytona / Modal 持久后端（托管平台）、deer-flow 无。helix 自托管,用 docker named volume + 热会话生命周期自建 —— 不引外部托管沙盒依赖。

### 9.5 M0 补强（J-29 / J-36）—— 生产级数据保护 + lifecycle

> **2026-05-20 J.15 设计 PR 新增**。9.1-9.4 描述的"热会话 + 持久卷"主体已交付；本节锁定 J-29（quota + backup + encryption）+ J-36（lifecycle）共 4 项的实施设计。各项的实施 PR 在此基础上做局部细化。

#### 9.5.1 Volume quota 准入（J-29 第 1 项）

**数据模型变更**（迁移 0026）：
```python
# user_workspace 表加 size_limit_bytes 列
class UserWorkspaceRow(Base):
    # 既有字段：id, tenant_id, user_id, volume_name, size_bytes, last_accessed_at, created_at
    size_limit_bytes: int  # 默认从 SandboxSupervisorSettings.default_workspace_size_limit_mb × 1MiB 算
```

**Enforcement 模式**：
- **预检式 + 异步对账**（不在 exec 路径做实时 fs walk —— 太慢）：
  - **预检**：`acquire()` 时检查 `size_bytes < size_limit_bytes`，超 quota → `QuotaExceededError`（HTTP 429 同 B.2 限流语义）
  - **写后对账**：每次 `release()` 异步 sample `du -sh /workspace` 更新 `size_bytes`（粗粒度；不阻塞 exec）
  - **强对账**：reaper 周期（默认 15min）对 IDLE 会话精确 du 一次（已挂载状态下 du 不锁卷）
- **写超 quota 的兜底**：宿主机文件系统级 quota 由 J-29 第 3 项的 LUKS / 云盘 quota 兜底（超 quota 时写失败 → sandbox 进程获 ENOSPC，正常错误传播，不需 supervisor 显式处理）

**新建文件**：`services/sandbox-supervisor/src/sandbox_supervisor/quota_enforcer.py`
- `QuotaEnforcer.check(workspace_row) -> None | raise QuotaExceededError`
- `QuotaEnforcer.refresh_size(workspace_row, sandbox_id) -> int`（du -sh 取 size_bytes）

**整合点**：`supervisor.acquire()` 调 `QuotaEnforcer.check`；`supervisor.release()` 起 `asyncio.create_task(QuotaEnforcer.refresh_size())` fire-and-forget。reaper 周期触发对账。

**Settings**：`SandboxSupervisorSettings.default_workspace_size_limit_mb: int = 10240`（默认 10 GB，manifest `policies.workspace_size_limit_mb` 可 override）。

**Audit**：超 quota → `AuditAction.QUOTA_EXCEEDED`（已有 K1 模式，加 resource="workspace_volume"）。

#### 9.5.2 Volume backup pipeline（J-29 第 2 项）

**架构**（复用 audit-backup-worker pattern，但目标是 docker volume 不是 PG 表）：
- 新独立服务 / cron job `volume-backup-worker`（或并入既有 audit-backup-worker），daily 1 次（off-peak，default 03:00 local）
- 流程：(1) 枚举所有 `user_workspace.deleted_at IS NULL` 行；(2) per-volume 起 throwaway 容器挂卷 `cat /workspace | tar` 流式到 ObjectStore；(3) 写 `volume_backup` 表登记 backup 元数据
- 对象存储键：`volume-backups/{tenant_id}/{user_id}/{YYYY-MM-DD}/{volume_name}.tar.zst`
- 保留期：默认 7 天；retention-cleanup-job 加 volume_backup 维度扫描（同 K3 模式）
- 失败处理：单卷失败不阻塞其他卷；进 K7 模式 DLQ（`volume_backup_dlq` 表，1m→5m→30m→2h→6h backoff）

**Restore 路径**：
- 手工 runbook 触发：`tools/persistence/restore_volume.py --tenant <X> --user <Y> --date <YYYY-MM-DD>`
- 流程：(1) 从 ObjectStore 拉对应 tar；(2) 创建新 volume 名（避免冲突现有热会话）；(3) 流式还原到新卷；(4) 操作员审核后手工把 `user_workspace.volume_name` 改新名（不自动 swap）
- Restore 演练：testcontainers 集成测试（同 K15 PG restore 模式）+ runbook `docs/runbooks/volume-restore.md`

**新建文件**：
- `services/volume-backup-worker/` 或 `services/audit-backup-worker/` 加 volume 维度
- `tools/persistence/restore_volume.py`
- `docs/runbooks/volume-restore.md`
- 迁移 0027 加 `volume_backup` + `volume_backup_dlq` 表

**Settings**：`SandboxSupervisorSettings.volume_backup_enabled: bool = True` / `volume_backup_retention_days: int = 7` / `volume_backup_schedule_cron: str = "0 3 * * *"`。

#### 9.5.3 At-rest 加密文档化（J-29 第 3 项）

**决策**：M0 不在 helix 代码层做加密，**依赖宿主机 / 云磁盘加密**（落实 P0 #9）：
- 阿里云 ECS：默认数据盘加密（ESSD AES-256-XTS，与 KMS Secrets Manager 同 region 同 key）
- 自托管 Linux：宿主机 LUKS / dm-crypt（部署文档强约束 `/var/lib/docker` 在加密分区）
- macOS dev：FileVault（dev 环境，不要求生产强约束）

**整合点**：
- `docs/runbooks/deployment.md` 加 "Volume at-rest encryption" 章节
- `infra/docker-compose.yml` README 注明 `/var/lib/docker` 加密前置条件
- 不引入 helix 应用层加密（容器侧 LUKS / cryfs 增加复杂度 & 性能损 vs 收益 < 1，平台架构红线）

#### 9.5.4 Volume lifecycle / 销毁（新 Mini-ADR J-36）

**Why**：用户软删除 / `user_workspace` 整 row 删除时无级联卷归档 → 卷变成"orphan"长存磁盘，违反 J.9 artifact lifecycle 同款原则（J-25/J-32 已铺保留期范式）。

**数据模型变更**（迁移 0026 一并加，与 quota 同迁移）：
```python
class UserWorkspaceRow(Base):
    # ...
    deleted_at: datetime | None  # soft-delete 时间戳
    archived_object_key: str | None  # 软删除后 archive 到对象存储的 key
```

**Lifecycle 三档**：
1. **active**：`deleted_at IS NULL` —— 热会话可挂载，正常 backup
2. **soft-deleted**：`deleted_at IS NOT NULL` 且 `archived_object_key IS NULL` —— acquire 拒绝（404）；reaper 触发 archive job：tar.zst 卷内容到 ObjectStore `volume-archive/{tenant}/{user}/{volume_name}.tar.zst` + 填 `archived_object_key` + 删除 docker volume
3. **archived**：`archived_object_key IS NOT NULL` —— 卷已物理销毁，archive 保留 90 天（manifest `policies.workspace_archive_retention_days` 可配）；retention-cleanup-job 90 天后 hard delete archive + row

**触发路径**：
- 用户主动删除（control-plane `DELETE /v1/users/{id}/workspace` 端点，等 H.4 Admin UI 接入）—— 触发 soft-delete
- tenant_user 整体删除（GDPR forget-me）—— 级联 soft-delete 所有 workspace
- M0 仅做 soft-delete + archive job + hard delete cron；恢复 API 推 M1（与 J.9 artifact 恢复同期）

**新建文件**：
- `services/sandbox-supervisor/src/sandbox_supervisor/lifecycle.py`（archive + hard delete cron 入口）
- 迁移 0026 加 deleted_at + archived_object_key 列
- 集成测试覆盖：soft-delete → reaper archive → hard delete 完整链路

**Audit**：`AuditAction.WORKSPACE_DELETE` / `WORKSPACE_ARCHIVE` / `WORKSPACE_HARD_DELETE`（三档 audit trail，同 K1 / K6 模式）。

---

## 10. J.9 — 产物 / Artifact 管理

> **2026-05-21 J.9 收尾设计 PR**：~50% 已实装（protocol DTO + 双表 RLS migration + Store + tools + GET list/download + 三套测试），Mini-ADR J-25（2026-05-20 修订）锁定 M0 还要补 lifecycle + quota + DELETE/PATCH + audit + versions endpoint + eval 6 项。本次设计修订 + **新加 § 10.5 MIME-aware download + XSS 防护**（deer-flow 对比启发的 (c) 红线 — helix 当前 `application/octet-stream` 一刀切下载是设计纪律缺口，HTML/SVG inline 渲染有 stored XSS 风险，按 [memory:complete-not-minimal] 修订），同步 Mini-ADR J-25 加第 (4) 项。

### 10.1 现状

run 只吐 SSE 文本流;沙盒里生成的文件随沙盒销毁即丢。用户拿不到 agent 产出的文件 / 文档 / 代码。

**当前已实装**（main，Mini-ADR J-11 范围）：protocol DTO `Artifact` / `ArtifactVersion` + 迁移 0019_artifact 双表 + `ArtifactStore` ABC/SQL/InMemory + `SaveArtifactTool` / `ListArtifactsTool` + `GET /v1/artifacts` 列表 + `GET /v1/artifacts/download?name=` 经 supervisor 读 volume + 首次读懒回填 size/sha256 + 三套测试。

**尚缺**（Mini-ADR J-25 + 本次 § 10.5 修订）：lifecycle / quota / DELETE/PATCH / versions endpoint / audit / MIME-aware Content-Type + XSS 防护 / eval module。

### 10.2 设计与边界

- **artifact = agent 产出的具名文件**（文档 / 代码 / 数据）。内容留在 J.15 的持久卷,元数据 + 版本进 `artifact` / `artifact_version` 表（control-plane 持有,tenant + user 组合 RLS）。
- 版本化：同名 artifact 重复产出 → 新 `artifact_version` 行,保留历史;`artifact.latest_version` 指向最新。
- agent 经 `save_artifact` / `list_artifacts` 工具显式登记产物。**边界**（Mini-ADR J-11）：不是自动扫工作区全部文件,而是 agent 显式声明"这个是产物"。
- **元数据回传**：SSE `artifact` 事件（run 中登记即推送元数据）+ control-plane run API 的产物列表端点。
- **内容回传经 supervisor**：产物内容在持久卷里,只有 sandbox-supervisor（持有 docker）能读 —— control-plane 不直接碰卷内部文件系统。supervisor 新增一个工作区文件读取操作（临时 `--rm` 只读容器挂卷 `cat`）;control-plane 的内容下载端点代理到它。`size_bytes` / `sha256` 登记时为空,**首次读取内容时算出并回填** `artifact_version` —— 这样 `save_artifact` 不依赖 supervisor。

### 10.3 接口与数据模型

```python
# 迁移 0019_artifact —— 两表均带 tenant_id/user_id + tenant+user 组合 RLS
class ArtifactRow(Base):                    # 表 artifact —— 逻辑产物
    id: UUID
    tenant_id, user_id: UUID
    name: str                               # 逻辑名,(tenant,user,name) 唯一
    kind: Literal["document", "code", "data", "other"]
    latest_version: int
class ArtifactVersionRow(Base):             # 表 artifact_version —— 每个版本
    id: UUID
    artifact_id: UUID                       # 裸 UUID 列,无 FK（FORCE-RLS footgun）
    tenant_id, user_id: UUID                # 反范式,套同款组合 RLS
    version: int
    path_in_workspace: str                  # 持久卷内相对路径
    size_bytes, sha256: ... | None          # 惰性回填（首次读取内容时算出）
    created_at: datetime
    created_in_thread: str
```

**J.9 收尾迁移（新）**：lifecycle 字段加 `deleted_at: datetime | None`（soft-delete 时戳）+ `archived_object_key: str | None`（卷满 archive-to-objectstore 后的对象键）+ partial index `WHERE deleted_at IS NULL`（list 端点默认过滤）。

### 10.4 整合点

`orchestrator/tools/`（`save_artifact` / `list_artifacts` builtin）、`sse.py`（`artifact` 事件类型）、`sandbox-supervisor`（工作区文件读取端点 —— 临时只读容器挂卷）、control-plane run API（产物列表 + 内容下载端点,下载时回填 size/sha256）、J.15 持久卷（内容载体）。

> **对标**：deer-flow `present_file`、hermes `file_tools`。helix 加**版本化 + 表登记 + lifecycle/quota/audit/RLS**,因为 per-user 持久形态下产物要跨会话留存可追溯，多租户企业平台必须有数据清理 + 资源保护 + 审计追溯。

### 10.5 Download endpoint MIME-aware Content-Type + XSS 防护（M0 — (c) 红线）

> 本节由 2026-05-21 helix-vs-deer-flow 对比新加。当前 download endpoint（`services/control-plane/src/control_plane/api/artifacts.py:108`）一律返 `application/octet-stream` —— 安全但**对 HTML / SVG / 主动内容缺纵深防御**（若未来加入 inline preview / API 客户端忽略 octet-stream 直接渲染 → stored XSS）。Mini-ADR J-25 第 (4) 项确认 M0 含此项。

**Content-Type 推断**（按 artifact `kind` + `path_in_workspace` 文件扩展名）：

| Kind / 扩展 | Content-Type | Content-Disposition |
|------------|--------------|---------------------|
| `data` / 任意 / 无扩展 / 未识别 | `application/octet-stream` | `attachment; filename=...`（强制下载） |
| `document` / `.md` / `.txt` / `.log` | `text/plain; charset=utf-8` | `inline; filename=...` |
| `document` / `.json` / `.yaml` / `.yml` / `.toml` | `application/json` / `application/x-yaml` / `application/toml` | `inline; filename=...` |
| `code` / `.py` / `.js` / `.ts` / `.go` / `.rs` / `.java` 等 | `text/plain; charset=utf-8` | `inline; filename=...` |
| image 类（`.png` / `.jpg` / `.jpeg` / `.gif` / `.webp`） | `image/{png\|jpeg\|gif\|webp}` | `inline; filename=...` |
| **`.html` / `.htm` / `.xhtml` / `.svg` / `.xml` / `.mathml`** | `text/html` 等真实类型 | **`attachment; filename=...` 强制下载** — XSS 红线 |
| **任何 unknown** | `application/octet-stream` | **`attachment` 强制下载**（fallthrough 安全） |

**实现要点**：
- 推断函数 `_infer_content_type(kind, path) -> tuple[str, ContentDisposition]` 独立模块 `_artifact_mime.py`，**白名单驱动**（unknown → octet-stream + attachment fallthrough）—— 拒绝 `mimetypes.guess_type` 因为它把 SVG 推断为 `image/svg+xml` inline 是 XSS 通道。
- 始终设置 `X-Content-Type-Options: nosniff`（防 IE/旧浏览器 sniff override）。
- `filename` 走 RFC 6266 `filename*=UTF-8''<percent-encoded>` + ASCII fallback。
- 安全测试：HTML / SVG / XHTML / 内嵌 `<script>` 的 .txt 全部断言 `Content-Disposition: attachment`。

**对标 deer-flow**：`backend/app/gateway/routers/artifacts.py:99-202` 已有同款逻辑（active content 强制 attachment）。helix 把 kind 字段进规则表 → 比 deer-flow 单按扩展名更严谨。

### 10.6 Lifecycle（M0 — Mini-ADR J-25 范围）

**保留期 + soft-delete + archive 三档**（与 J-25 J.15 volume + J-32 image upload 同款范式）：

| 状态 | 触发 | 数据可见性 | 内容存储 |
|-----|------|----------|---------|
| **active** | `save_artifact` 写入 | list 可见 / download 可见 | J.15 user volume |
| **soft-deleted** | `DELETE /v1/artifacts/{name}` 或 user 销毁级联 | list 默认隐藏（`?include_deleted=true` 可见）/ download 404 | volume 文件仍在 |
| **archived** | retention cron 跑到 + soft-delete 超 30 天 / 或卷满迁出 | list `?include_archived=true` 可见 / download restore-required | tar.zst 进 ObjectStore，volume 删 |
| **hard-deleted** | archived 超 60 天 / 或租户彻底销毁 | 不可见 | row 删除 + ObjectStore 删除 |

- **默认保留期**：90 天 active → 自动 soft-delete（manifest 可配 `policies.artifact_retention_days`）。
- **Cleanup job**：复用 `retention-cleanup-job` 现有服务（K3 已铺），加 artifact 维度扫描（同 image_upload J-32 模式）。
- **卷满 archive**：J.15 卷写满时（quota 满 90%）触发 archive job 把 oldest soft-deleted 迁 ObjectStore 释放卷空间。
- **级联**：user_workspace soft-delete（J-36）级联 soft-delete 该 user 全部 artifact；tenant 销毁同款级联 hard-delete。

### 10.7 Quota 接入 Stream C.5（M0 — Mini-ADR J-25 范围）

`QuotaService` 扩两类计费（与 token / sandbox 平行）：

| 类目 | 单位 | 默认上限 | 触发点 |
|------|------|---------|--------|
| `artifact_download_count_30d` | 次 | 1000 / user / 30d | `GET /v1/artifacts/download` |
| `artifact_storage_bytes` | bytes | 1 GiB / user | `save_artifact` tool 调用 + 卷满 archive 时减计 |

- 准入检查在端点入口 / tool dispatch 前；超 quota 返回 `429`（与 B.2 限流同 status），audit 记 `ARTIFACT_QUOTA_REJECT`。
- `ReservationReaper` 复用现有逻辑（30d 滚动窗口 sliding count）。
- `helix_artifact_quota_reject_total{type=download|storage}` counter 出现，Prometheus scrape。

### 10.8 Audit Trail（M0 — Mini-ADR J-25 范围）

新增 `AuditAction` 三态（同 K1 / K6 模式）：

| Action | 触发 | 关键字段 |
|--------|-----|---------|
| `ARTIFACT_SAVE` | `save_artifact` tool 完成 | tenant_id / user_id / artifact_name / version / kind / size_bytes / 调用方主体 |
| `ARTIFACT_DELETE` | `DELETE /v1/artifacts/{name}` | tenant_id / user_id / artifact_name / soft-delete 时戳 / 调用方主体 |
| `ARTIFACT_UPDATE` | `PATCH /v1/artifacts/{name}` | tenant_id / user_id / artifact_name / 变更字段（kind / metadata）/ 调用方主体 |

J.14 cross-tenant artifact reject 自动化测试以 audit trail 作 verification 锚点（"cross-tenant DELETE 必须返 404 且不留 audit 记录"）。

### 10.9 控制平面新增 endpoints（M0 — 配合 § 10.5~10.8）

| Endpoint | 用途 |
|---------|------|
| `DELETE /v1/artifacts/{name}` | soft-delete（接 quota / audit） |
| `PATCH /v1/artifacts/{name}` | 改 kind 或 metadata（M0 仅 kind，metadata 字段推 M1） |
| `GET /v1/artifacts/{name}/versions` | 历史版本列表（H.4 UI 需要） |

所有端点继承现有 404 不泄漏 cross-user 语义。

### 10.10 Eval module（M0 — Mini-ADR J-25 + J-37 范围）

`tools/eval/artifact.py` + `tools/eval/datasets/artifact/m0_baseline.yaml`，pass-rate ≥ 0.80。场景覆盖：

- save_artifact_basic / version_increments / list_artifacts_excludes_deleted
- delete_then_list_excludes / delete_then_download_404 / patch_kind_then_list_reflects
- versions_list_desc / download_mime_text / download_mime_image / download_html_forces_attachment / download_svg_forces_attachment
- quota_storage_reject / quota_download_count_reject / cross_user_404

接 `run_baseline.py` runner，J.9_artifact 切 PASS。

### 10.11 不做项（M0 边界）

- ❌ **`.skill` ZIP artifact + Install 按钮**（deer-flow 启发跨 J.7/J.9 集成）—— 推 M1-K（J.7b skill 进化一起）
- ❌ **前端 inline preview / 类型感知渲染** —— H.4 admin UI 范畴
- ❌ **病毒扫描** —— Mini-ADR J-25 已显式 (a) 推 M2
- ❌ **IM 渠道 ResolvedAttachment** —— Stream A 范畴
- ❌ **多模态 `kind=image` 特化** —— M0 通过 path 扩展名 + Content-Type 推断已足够区分；增加 enum 推 M1 与 H.4 一并

---

## 11. J.4 — Sub-agent / 多智能体

> **2026-05-21 J.4-补强-2 设计 PR**：原 § 11.2 写"M0 是父→子单向、**顺序**委派树，并行扇出推 M0 后"——按 [memory:complete-not-minimal](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_complete_not_minimal.md) + [memory:no-design-choice-disguise](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_no_design_choice_disguise.md) 红线，"顺序"是把弱能力包装成设计选择：子 agent 之间彼此独立（不共享 sandbox session），理论可并发但 L.L6 `plan_stages` 因 `is_read_only=False` 拆 stage 串行，是工程实现限制而非设计意图。本次设计修订 + 新增 Mini-ADR J-40：M0 内交付真正的并行 fan-out（asyncio.gather + cycle detection + global deadline + fan-in 聚合 + AgentState 扩展），原 M2-B 推迟取消。

### 11.1 现状

J.4 核心 5 PR 已交付（#151 / #152 / #154 / #220 / #221）：
- agent-as-tool 顺序委派 + `CancellationToken` 穿透
- `MAX_SUBAGENT_DEPTH=3` 结构化递归终止
- Mini-ADR J-21 trajectory 单独记录 + budget telemetry（`iteration_used` / `llm_call_count` / `wall_clock_ms` 入 `ToolResult.meta`）
- J.4 eval 8 case PASS

未交付（J.4-补强-2 范围）：
- 并行 fan-out（父 LLM 一轮调 N 个 SubAgentTool 真正并发执行）
- 构建期 cycle detection（即使深度未达 `MAX_SUBAGENT_DEPTH`，A→B→A 应拒绝）
- Global deadline 经 config 传播（父建立，子继承不重置）
- Fan-in 聚合（N 子 outcome 入父 `AgentState.subagent_invocations` 通道，含 6 态 `SubagentStatus`）

### 11.2 设计与边界

- **agent-as-tool**：每个声明的子 agent 包装成一个**命名 `SubAgentTool`**,父 agent 像调工具一样委派子任务（命名工具给 LLM 比单一 `task` 工具更清晰的选择信号）。
- 子 agent 用 `agent_factory.build_agent()` 递归装配,拿独立 `run_id`;子 `thread_id` 由父 `run_id` + 工具 `call_id` 派生。
- **隔离与安全**：父的 `CancellationToken` 经 `ToolContext.cancellation_token` 穿透到子（复用现有协作式取消链)。
- **成本护栏走结构化,非 token 预算** —— helix 没有运行期 token 预算（只有月级 `TokenBudgetLedger` + 事后 `TokenReservation` 记账),故原计划"父预算下钻、子拿 30%"移除。改用:递归深度 ≤ `MAX_SUBAGENT_DEPTH`(3) × 每 agent `workflow.max_iterations` —— 全递归树 LLM 调用数结构化有界。
- **深度在构建期计数**：`build_agent(subagent_depth=...)`,顶层 depth=0;depth 达上限的 agent 构建时**不注册任何 `SubAgentTool`**(结构化递归终止)。
- **构建期 cycle detection**（Mini-ADR J-40，2026-05-21 新增）：`agent_factory.build_agent` 经 `spec_store` DFS 遍历 `subagents.agent_ref`，发现 A→B→A 这类环立刻抛 `AgentFactoryError("sub-agent delegation cycle detected: A → B → A")`。深度上限作纵深防御保留。
- **`ChildAgentBuilder` 回调**:orchestrator 无法解析 `agent_ref`(`AgentSpecStore` 只在 control-plane)。control-plane 注入一个 `async (*, tenant_id, name, version, depth) -> BuiltAgent` 回调进 `ToolEnv`,内部 `AgentSpecStore.get` + 递归 `build_agent` + 深度键缓存。
- **并行 fan-out**（Mini-ADR J-40，2026-05-21 新增）：父 LLM 一轮发 N 个 SubAgentTool 调用时，`SubAgentTool.spec.is_parallel_safe=True` 让 L.L6 `plan_stages` 把它们放同一 stage；同 stage 内 `asyncio.gather(return_exceptions=True)` + 复用 `MAX_TOOL_WORKERS=5` 信号量并发执行。`return_exceptions=True` 而非 `TaskGroup`——一个子失败不连带 cancel 兄弟，让父 LLM 看 partial 结果。
- **Global deadline**：父 manifest 可选 `policies.run_deadline_s`；`sse.run_agent` 算 `deadline_at = monotonic + deadline_s` 经 `config["configurable"]["deadline_at"]` 传播；`ToolContext.deadline_at` 透出给 `SubAgentTool.call()`，子 config 继承不重置（避免每层重置导致树深度乘以单子 deadline）。若 `deadline_at - monotonic() <= 0` 直接 `RunCancelledError`。
- **Fan-in 聚合**：每个 SubAgentTool 还是返回 ToolResult（tools_node 收 N 个 ToolMessage 一起喂回 LLM），同时经 `ToolResult.state_updates` 写 `AgentState.subagent_invocations` 通道。父 state 中拿到 N 条 `SubAgentInvocation` 后：iteration_used 加总 / llm_call_count 加总 / wall_clock_ms **取 max**（并行 wall clock 不能加总）/ status 6 态各 entry 自带。
- **边界**：仍不做子 agent 间横向通信 / 黑板协作；子 SSE 进度流推迟 M2-B；TIMED_OUT 由 global deadline 触发后映射，非主动调度。

### 11.3 接口与数据模型

```python
class SubAgentSpec(BaseModel):              # AgentSpecBody.subagents 的条目
    name: str                               # 暴露给父 LLM 的工具名(snake_case)
    agent_ref: str                          # 引用已部署 AgentSpec —— "name@version"
    description: str                        # 工具描述,父 LLM 据此决定委派

MAX_SUBAGENT_DEPTH = 3
ChildAgentBuilder = async (*, tenant_id, name, version, depth) -> BuiltAgent


# 2026-05-21 J.4-补强-2 新增（packages/helix-protocol/.../subagent.py）
class SubagentStatus(StrEnum):              # 6 态，DeerFlow 2.0 同款
    PENDING = "pending"                     # 已派工，未开始执行
    RUNNING = "running"                     # 执行中
    COMPLETED = "completed"                 # 成功完成
    FAILED = "failed"                       # 工具层异常（非取消、非超时）
    CANCELLED = "cancelled"                 # 取消（父或全局 deadline 触发）
    TIMED_OUT = "timed_out"                 # 超过 global deadline

@dataclass(frozen=True)
class SubAgentInvocation:                   # AgentState.subagent_invocations 元素
    task_id: UUID                           # sub_run_id（PR #220 已生成）
    sub_thread_id: UUID                     # PR #220 已生成
    name: str                               # SubAgentSpec.name
    agent_ref: str                          # SubAgentSpec.agent_ref（含 version）
    child_depth: int
    status: SubagentStatus
    result_excerpt: str                     # 子答案截断（≤ N 字，避免长文本入 state）
    error: str | None                       # FAILED / TIMED_OUT 时填
    started_at: datetime
    finished_at: datetime | None
    iteration_used: int                     # 同 PR #220 meta
    llm_call_count: int
    wall_clock_ms: int

# AgentState（services/orchestrator/.../state.py）扩展通道
class AgentState(TypedDict):
    # ... 既有通道 ...
    subagent_invocations: Annotated[list[SubAgentInvocation], add]   # operator.add 拼接

# ToolContext（services/orchestrator/.../tools/registry.py）扩展
@dataclass(frozen=True)
class ToolContext:
    # ... 既有字段 ...
    deadline_at: float | None = None         # time.monotonic 时间戳，跨层继承不重置

# ToolSpec（services/orchestrator/.../tools/registry.py）扩展
@dataclass(frozen=True)
class ToolSpec:
    # ... 既有字段 ...
    is_parallel_safe: bool = False           # True → plan_stages 允许同 stage 并发
```

校验(`AgentSpec._check_subagents`):拒绝自引用(子 `agent_ref` 指向本 agent)、重复工具名、与已声明 builtin 工具名冲突;`agent_ref` 必须 `name@version` 格式。

构建期 cycle detection（`agent_factory._detect_subagent_cycle`）：从父 manifest `subagents` 出发，递归 resolve `agent_ref` 经 `spec_store`，以"访问中"/"已完成"双集合做 DFS——访问中节点被再次访问即环。

`SubAgentTool(Tool)`：`spec.is_parallel_safe=True`；`call()` 内经 `ChildAgentBuilder` 构建子 agent → 检查 `ctx.deadline_at` → `child_graph.ainvoke(...)` → 子 run 末条 `AIMessage` 作 `ToolResult.content` 回父；同时 emit `SubAgentInvocation` 到 `ToolResult.state_updates["subagent_invocations"]`。

### 11.4 整合点

`tools/`（`SubAgentTool` + `ChildAgentBuilder` 协议 + `_register_subagents`）、`agent_factory.py`（`build_agent(subagent_depth=)` 递归装配 + depth 上限 + cycle detection）、`tools/registry.py`（`ToolContext.cancellation_token` + `ToolContext.deadline_at` + `ToolSpec.is_parallel_safe` + `TOOL_ALLOWED_STATE_KEYS` 加 `subagent_invocations`）、`graph_builder/builder.py`（`plan_stages` 支持 `is_parallel_safe` 并发调度）、`state.py`（`subagent_invocations` 通道）、`sse.py`（`deadline_at` 经 config 注入）、`control-plane`（`make_child_agent_builder` + 深度键缓存 + lifespan 接线）、`helix-protocol`（`SubAgentSpec` + `SubagentStatus` + `SubAgentInvocation`）。

> **对标**：deer-flow `subagents/executor.py`、hermes `delegate_tool`。helix 取 agent-as-tool —— 与现有 `ToolRegistry` 无缝;委派即工具调用,自动获得现有的取消 / 预算 / 审计基建。

---

## 12. J.5 — 知识 / 检索（RAG）

### 12.1 现状

无向量库 / 检索。agent 只能靠 `web_search` 工具拿外部信息,无法 ground 在租户私有知识上。

### 12.2 设计与边界

> **范围**：本节原写"够用的 RAG";经两轮评审扩为**生产级 RAG 设施** —— 文档可运维、摄取异步化、强解析、token 计量的结构/语义/表格感知切块、混合检索 + 重排。原则:**功能可少,能力不可弱**。仍守住的有原则边界见末尾。

- **检索作工具,非自动注入**（Mini-ADR J-13）：`knowledge_search` 工具,agent 按需查;不在每次 LLM 调用前自动塞检索结果（自动注入污染上下文、不可控）。
- **块激活**：`knowledge:` manifest 块存在即激活 `knowledge_search` 工具(不进 `tools:` / `KNOWN_BUILTINS`),对齐 J.4 `subagents:` 块 → `SubAgentTool` 的先例。
- 后端复用 J.3 的 pgvector 基建:`knowledge_chunk` 表(租户级,文档切块 + 向量),`embedding` 用 `Vector(EMBEDDING_DIM)`(env 驱动,与 `memory_item` 同)。
- **KB 分组**:一租户多命名知识库;manifest `knowledge_base_refs` 绑定子集,名→id 在 `knowledge_search` 调用期解析(KB 可后建)。
- **文档生命周期**:`knowledge_document` 表;文档可列举 / 重摄取(替换 chunk)/ 删除;KB 可删除(级联)。
- **摄取异步化**:上传即返回,后台 `asyncio.Task` 跑 解析→切块→嵌入→入库;`knowledge_document.status`(pending/processing/ready/failed)可查。
- **文档解析**:pymupdf4llm(PDF 高质量,带标题)+ MarkItDown 兜底(docx/pptx/xlsx + PDF 稀疏回退)。
- **切块**:**token 计量**(中文按字符切偏差大)、**结构感知**(Markdown 标题/段落/列表/代码/表格边界)、**表格感知**(表整体保留,超限按行切留表头)、**语义切块**(无子标题的长结构单元按相邻块嵌入相似度落断点)、**标题路径前缀**(每 chunk 拼所属小节标题链);`chunk_max_tokens` / `chunk_overlap_tokens` **per-KB 可配**。
- **混合检索**:向量召回 + 关键词召回(Postgres 全文,`knowledge_chunk.content_tsv`)→ **RRF 融合** top-N → **LLM-rerank** → top-k。中文分词在应用侧用 jieba(Postgres 原生 `tsvector` 不分中文词),`simple` 配置;rerank LLM 部署级配置,未配则退化纯向量。
- **有原则的边界(不做)**:单一 pgvector 后端,不引 Milvus(pgvector 已是完整生产级后端,换后端不增能力);不做 GraphRAG(另一套检索范式,独立 stream);知识库租户共享,per-user 私有知识是独立产品特性、另行决策。

### 12.3 接口与数据模型

```python
# 迁移 0021_knowledge_base —— 三表,均租户级 RLS;0022 加 chunk 配置 + 全文检索列
class KnowledgeBaseRow(Base):               # 表 knowledge_base
    id, tenant_id, name, created_at         # (tenant_id, name) 唯一
    chunk_max_tokens, chunk_overlap_tokens  # 0022 —— per-KB 切块参数
class KnowledgeDocumentRow(Base):           # 表 knowledge_document
    id, tenant_id, kb_id, filename
    status, error, chunk_count              # (tenant_id, kb_id, filename) 唯一
    created_at, updated_at
class KnowledgeChunkRow(Base):              # 表 knowledge_chunk
    id, tenant_id, kb_id, document_id
    chunk_index, content
    embedding: Vector(EMBEDDING_DIM)        # HNSW cosine 索引
    content_tsv: tsvector                   # 0022 —— 混合检索关键词侧,GIN 索引

class KnowledgeSpec(BaseModel):             # AgentSpecBody.knowledge
    knowledge_base_refs: list[str]          # ≥1,去重校验
```

### 12.4 整合点

`tools/`（`knowledge_search` 工具 + `KnowledgeRetriever` 混合检索,`knowledge:` 块激活）、`helix-persistence` 新 `knowledge` 子包(`KnowledgeStore` ABC + SQL/内存 + jieba 全文分词)、control-plane(KB / 文档 API + 后台 `KnowledgeIngestionRunner` + 解析/切块)、`helix-protocol` `KnowledgeSpec` + 知识 DTO。

> **对标**：deer-flow / hermes 都靠外部 search / FTS5,RAG 弱。helix 自建生产级 RAG:强解析 + 结构/语义/表格感知切块 + 混合检索 + 重排 + 完整文档运维。

### 12.5 实施进展 + SLO baseline（2026-05-21 收尾，Mini-ADR J-22）

**Mini-ADR J-22 拆 4 阶段实施 — 8 PR 全合 main**：

| 阶段 | 范围 | PR |
|------|------|----|
| **J.5a** 数据层 + 异步摄取 | `KnowledgeSpec` / 知识 DTO（`KnowledgeBase` / `KnowledgeDocument` / `KnowledgeChunk` / `DocumentStatus`）+ 迁移 0021/0022 + `KnowledgeStore` ABC + `InMemory` / `Sql` 两实现 + per-KB `chunk_max_tokens` / `chunk_overlap_tokens` + `content_tsv` 全文检索列 + `KnowledgeIngestionRunner` 后台 `asyncio.Task` 跑 解析→切块→嵌入→入库 + `knowledge_document.status` 状态机 | #155 / #156 / #157 / #162 |
| **J.5b** 解析 + 切块 | `pymupdf4llm`（PDF 高质量带标题）+ `MarkItDown` 兜底（docx / pptx / xlsx + PDF 稀疏回退）+ token 计量切块（中文按字符切偏差大 → tokenizer 计 token）+ 结构感知（Markdown 标题 / 段落 / 列表 / 代码 / 表格边界）+ 表格感知（表整体保留，超限按行切留表头）+ 语义切块（无子标题的长结构单元按相邻块嵌入相似度落断点）+ 标题路径前缀 | #158 / #159 / #160 |
| **J.5c** 混合检索 + rerank | `KnowledgeRetriever`（vector 召回 + Postgres 全文 jieba 关键词召回 → RRF 融合 → LLM-rerank → top-k）+ `knowledge_search` 工具（`knowledge:` 块激活，未配 rerank 退化纯向量）+ `Reranker` Protocol + `LLMReranker` 实现 | #161 |
| **J.5d** 文档运维 API | `POST/GET/DELETE /v1/knowledge/bases` + `POST/GET/DELETE /v1/knowledge/bases/{kb_id}/documents`（重摄取替换 chunks，删除级联 chunks）+ control-plane wire `KnowledgeIngestionRunner` 进 lifespan + `KnowledgeRetriever` 经 `ToolEnv` 注入 orchestrator | #162 |

**M0 SLO baseline**（Mini-ADR J-22 锁定 — 单 control-plane 副本，单机 PG + MinIO 部署）：

| SLO 维度 | M0 目标 | 验证方式 |
|---------|---------|---------|
| 摄取延迟 P95 | < 30s / 文档（1MB Markdown）/ < 120s / 文档（10MB PDF） | `test_knowledge_ingestion.py` 计时（CI 跑） |
| 文档 ready 时间（上传 → status=ready） | < 5s for in-memory store；< 30s for SQL backend with embeddings | E2E `test_knowledge_e2e.py` |
| 查询 P95（含 RRF 融合，无 LLM rerank） | < 200ms / KB 10K chunk @ HNSW | `test_sql_knowledge_store.py` benchmark |
| 重摄取幂等性 | 同 filename 重摄取 → chunks 全替换 + chunk_index 重排 0..N + 旧 chunk soft-deleted（pg_class.relkind row）| `test_sql_knowledge_store.py::test_upsert_document_resets` |
| recall@k（M0 eval baseline） | pass_rate ≥ 0.80 AND recall@k ≥ 0.70 over 11-case dataset | `tools/eval/rag.py` (sample=11, threshold 锁定，2026-05-21 实测 1.00 / 1.00) |

J.13a baseline 锚定：`tools/eval/baselines/m0_gate_baseline.yaml` `J.5_rag` 行 — `pass_rate=1.00 / recall_at_k=1.00 / sample_size=11 / status=PASS`。

---

## 13. J.6 — 多模态输入

### 13.1 现状

`HumanMessage` 留了 content block 槽,但两个 LLM adapter（OpenAI / Anthropic）都把消息内容拍平成纯文本、丢弃非文本块;`RunRequest` 只有 `input: str`。图像输入进不来。

### 13.2 设计与边界 —— 能力解析式双路

helix 多租户平台同时面对**统一多模态模型**（Claude / GPT / Qwen-VL，看图不掉推理）与**拆分模型族**（Qwen3-Max 文本强、Qwen-VL 单独且推理弱;GLM / DeepSeek 同理）。单一架构必有一类租户变弱：

- 纯 content-block（图像进 `HumanMessage`、整轮交多模态模型）→ 拆分模型租户的含图轮推理降级到弱 VL 模型;
- 纯 `ask_image` 工具（主模型不看像素、隔文本瓶颈）→ 统一多模态模型租户有损、无跨模态推理。

故 **A / B 双路,由 agent 构建期按 manifest 声明的主模型能力决议**：

- **能力来源**：`ModelSpec.supports_vision: bool`（默认 `false`），manifest 作者**显式声明** —— 平台不猜模型名（模型层出不穷,猜表必脆）。
- **摄取（两路共用）**：用户经独立上传端点 `POST /v1/sessions/{thread_id}/uploads` 上传图 → 落对象存储（MinIO,ADR-0004 键 `{tenant_id}/uploads/{thread_id}/{id}{ext}`）→ 产出 `helix://image/...` ref。`HumanMessage` 只携带 ref;base64 绝不进 checkpointer。
- **Path A**（主模型 `supports_vision: true`）：image ref 解析成 content block 挂 `HumanMessage`,主模型直接看像素;adapter 拼 wire payload 前一刻经 `ImageResolver` 把 ref 解析为 base64。不激活 `ask_image`。
- **Path B**（主模型 `supports_vision: false`）：ref 作文本引用留在 `HumanMessage`,激活 `ask_image(image_ref, question)` 工具（`question` 必填、可反复盘问）—— 工具路由到 manifest `vision:` 块单独声明的 VL 模型,VL 答文本经 `ToolResult` 回主循环;主推理模型全程纯文本、不降级。
- **失败用例**：主模型不支持视觉、无 `vision:` 块,本轮却上传了图 → run 组装期 **422**,响亮报错,绝不静默丢图。对称地,`vision:` 块出现在视觉主模型上 → 构建期 `AgentFactoryError`(路径唯一、不含糊)。
- **边界**：仅**图像**输入。非图像文件输入走 J.15 持久工作区（`exec_python` 读）或 J.5 知识库,不在 J.6 范围。多模态输出（生成图 / 音频）推迟（§ 1.2）。base64 每轮重发的 provider 端图像缓存推迟 M1。

### 13.3 接口与整合点

无新表 —— 对象存储键 + 消息 ref 即记录。

```python
class ModelSpec(BaseModel):                 # 增字段
    supports_vision: bool = False
class VisionSpec(BaseModel):                # AgentSpecBody.vision —— 仅 Path B 用
    model: ModelSpec                        # ask_image 路由到的 VL 模型
```

- `helix-protocol`：`ModelSpec.supports_vision`、`VisionSpec`、新 `multimodal.py`（`ImageRef` + `parse_image_ref` 纯函数 parser，两服务共用）。
- `orchestrator`：provider adapter（`openai.py` / `anthropic.py`）content-block 翻译 + `ImageResolver` 注入（Path A 与 `ask_image` 共用）;新 `tools/vision.py` `AskImageTool`;`build_agent` 构建期按 `supports_vision` 分叉。
- `control-plane`：新 `api/uploads.py` 上传端点;`api/runs.py` 按主模型能力分叉 `HumanMessage` 形态 + 422 守卫;lifespan `make_object_store` + `ObjectStoreImageResolver` 接线。
- 对象存储复用 `helix-runtime` 既有 `ObjectStore` / `S3CompatibleObjectStore`（J.6 是首个消费者）。

> **对标**：deer-flow `view_image`（工具门控）、hermes 全 vision 路由（整轮交 VL）。helix **不二选一** —— 按租户主模型能力在构建期决议,统一模型租户走零损 content-block、拆分模型租户走解耦 `ask_image` 工具,每类租户都不变弱（原则"功能可少、能力不可弱"）。

---

## 14. J.8 — 人在回路 / 审批

> **2026-05-22 J.8 收尾设计 PR**：实装 ~5-10%（仅 `audit_log` 表 + `PolicySpec` 容器 + `AgentState` 框架就绪，零 approval 节点 / 零 resume endpoint / 零审批 DTO）。本次设计修订按 2026-05-22 helix-vs-deer-flow 对比新增 **§ 14.5 agent 主动请求路径**（deer-flow `ask_clarification` 启发 — 声明式门控之外加 agent 自主请求路径，用户复审拉进 M0）+ **§ 14.6 reason_kind 分类** + **§ 14.7 超时 fallback 详化** + **§ 14.8 audit 详化** + **§ 14.9 边界**，并同步 Mini-ADR J-24 加 (4)(5) 两项。

### 14.1 现状

run 一旦启动,人无法中途审批 / 纠偏。危险操作（如 sub-agent 派生、外部写操作）无人工门。

**当前已实装**：`audit_log` 表 + `AuditAction` enum + emit/write 路径（Stream D.1）；`PolicySpec` 容器（通用字段，审批字段未加）；checkpointer 暂停/恢复机制。**尚缺**：approval DTO / `AgentState.pending_approval` / approval 检查 / resume endpoint / 超时 job / `ask_for_approval` tool / eval。

### 14.2 设计与边界

- **暂停机制 = end-and-resume**（2026-05-22 deer-flow 对比后定）：`tools_node` 在并行 staging 之前做审批前置检查 —— 命中 gated tool / `ask_for_approval` → 写 `AgentState.pending_approval` + 图路由到 `END`，run 以 `RunStatus.PAUSED` 结束并 checkpoint 持久。**不用 LangGraph 原生 `interrupt()`** —— helix `tools_node` 是 L.L6 并行分阶段调度（`asyncio.gather`），`interrupt()` 的"节点 resume 时整体重跑"语义与之难干净接合；deer-flow `ClarificationMiddleware` 已验证 `goto=END` + checkpoint 续跑这套范式在生产可行。
- control-plane 暴露暂停态：`GET /v1/runs/{id}`（含 `pending_approval`）+ `POST /v1/runs/{id}/resume`（批准 / 拒绝 / 修改后继续）—— resume 写 `ApprovalDecision` 进 checkpoint state 后重新 `graph.ainvoke(None, config)` 从 checkpoint 续。
- **两条触发路径并存**（2026-05-22 修订）：
  1. **声明式门控**（主路径）—— 危险操作按 `PolicySpec.approval_required_tools` 声明门控，平台强制，agent 不可绕过。
  2. **agent 主动请求**（§ 14.5，deer-flow 启发）—— agent 跑到不确定决策点时调 `ask_for_approval` builtin 工具主动请求人确认。
- **边界**：M0 是同步审批门（run 挂起等人）。M0 含 24h 超时自动 reject（Mini-ADR J-24 — 不再"软推迟"）；不做异步通知 / 多审批人 / 升级链（M1）。

### 14.3 接口与数据模型

```python
ApprovalReasonKind = Literal[              # § 14.6 — 借鉴 deer-flow 5 类型
    "policy_gate",        # 声明式门控触发（PolicySpec.approval_required_tools）
    "missing_info",       # agent 缺信息
    "ambiguous_requirement",
    "approach_choice",    # agent 在多方案间犹豫
    "risk_confirmation",  # agent 自评高风险操作
]
class ApprovalRequest(BaseModel):           # AgentState.pending_approval
    request_id: str                         # 稳定哈希(thread_id+node+content) 防重试重复
    node: str
    reason_kind: ApprovalReasonKind
    action_summary: str
    proposed_args: dict
    requested_at: datetime
    timeout_at: datetime                    # requested_at + policies.approval_timeout_s
class ApprovalDecision(BaseModel):          # resume API 入参
    decision: Literal["approve", "reject", "modify"]
    modified_args: dict | None              # 仅 decision="modify" 时
    decided_by: str                         # 审批人 subject id
```

`PolicySpec` 新增两字段（§ 14.2 主路径门控）：

```python
class PolicySpec(BaseModel):
    ...
    approval_required_tools: list[str] = []   # 这些工具 dispatch 前 interrupt
    approval_timeout_s: int = 86400           # 默认 24h；超时 auto-reject
```

### 14.4 整合点

`graph_builder/builder.py`（`approval` 节点 + `interrupt()`）、`state.py`（`pending_approval`）、checkpointer（暂停态持久,本就支持）、control-plane run API（`GET` 含 pending_approval + `POST .../resume` 端点）、`PolicySpec`（门控声明）、`tools/approval.py`（`ask_for_approval` builtin + middleware）、approval-timeout job（24h 扫描 auto-reject）。

> **对标**：hermes 中断 + 审批门、deer-flow `ask_clarification`。helix 直接用 LangGraph `interrupt` —— 与现有 checkpointer 暂停 / 恢复机制天然契合。helix 比 deer-flow 多：声明式门控（平台强制不可绕过）+ typed `ApprovalDecision` 3 态 + 24h 超时 + audit trail。

### 14.5 agent 主动请求路径（M0 — deer-flow 启发，用户 2026-05-22 复审拉进）

声明式门控（§ 14.2 路径 1）是平台强制规则 —— "敏感工具必须人审批"。但 agent 跑到运行期才知道的不确定决策点（"我不确定用方案 A 还是 B"、"我打算清空这个目录，确认吗"）也需要主动开口。新增 `ask_for_approval` builtin 工具：

```
ask_for_approval(reason_kind: str, action_summary: str, proposed_args: dict) -> str
```

- agent 调 `ask_for_approval` 时 `tools_node` 按名特判（不走 `_dispatch_tool`）→ 构造 `ApprovalRequest`（`reason_kind` 取 agent 传入值）→ 写 `pending_approval` + 路由 END（同声明式门控）。
- resume `decision="approve"` → `tools_node` 续跑期正常派发原 tool_call。
- resume `decision="reject"` → `tools_node` 合成 `"[approval rejected] <reason>"` `ToolMessage`，LLM 看到拒绝换策略（**不终止 run** —— 与声明式门控的 reject 不同：门控 reject 平台否决整个 run（`approval_outcome="rejected"` → END）；agent 主动请求的 reject 只否这次询问，run loop 回 `agent` 继续）。
- resume `decision="modify"` → `tools_node` 用 `modified_args` 重写 tool_call args 后派发。
- 与声明式门控**共享同一套** `goto=END`/resume/`AgentState.pending_approval`/`approval_resume`/audit 机制 —— 唯一区别是 reject 语义（见上）。

### 14.6 审批原因分类 reason_kind（M0 — deer-flow 启发）

`ApprovalReasonKind` 枚举（见 § 14.3）借鉴 deer-flow `ask_clarification` 的 5 种类型。声明式门控触发的 `ApprovalRequest` 固定 `reason_kind="policy_gate"`；agent 主动请求的取 agent 传入值。用途：Admin UI（H.3）按类型过滤 / 排序；audit 分析"哪类审批最频繁"。

### 14.7 超时 fallback（M0 — Mini-ADR J-24）

- `ApprovalRequest.timeout_at = requested_at + policies.approval_timeout_s`（默认 24h，manifest 可配）。`agent_approval` 行持久化 `timeout_at`（§ 14.3a）。
- `retention-cleanup-job` 加 approval-timeout 扫描 pass（J.8-step3b）：扫 `agent_approval` `status='pending' AND timeout_at < now()` → `mark_decided(status=TIMEOUT, decided_by='system')`。超时后该 run 的 `POST .../resume` 返回 409（already-decided），暂停的 checkpoint 逻辑死亡（不再有 run 永占审批槽）。
- 取舍：(c) 红线 —— 无超时 = 无限期 pending run 永占审批槽 = 资源泄漏（Mini-ADR J-24）。

### 14.3a agent_approval 持久表（M0 — J.8-step3b）

helix `RunManager` 纯内存，暂停 run 无持久落点。新建 `agent_approval` 表（migration 0031，tenant RLS）登记每个暂停 run：`run_id`（唯一）/ `thread_id` / `tenant_id` / `request_id` / `node` / `reason_kind` / `action_summary` / `proposed_args` / `requested_at` / `timeout_at` / `status`（pending/approved/rejected/modified/timeout）/ `decided_by` / `decided_at` / `modified_args`。run 暂停时 `sse.py` 写一行；resume 端点 + `GET` + 超时 job 都读它（control-plane 重启后仍可用）。其逻辑父表 `agent_run`（裸 run 生命周期表）由 Mini-ADR J-41 提前到 M0（迁移 0032）—— `agent_approval` 自此是 `agent_run` 的审批扩展子表，不再扛 run 生命周期。

### 14.8 Audit Trail（M0 — Mini-ADR J-24）

新增 `AuditAction` 两态：

| Action | 触发 | 关键字段 |
|--------|-----|---------|
| `APPROVAL_REQUESTED` | run 暂停（`sse.py` 写 `agent_approval` 行后） | tenant_id / run_id / node / reason_kind / action_summary |
| `APPROVAL_DECIDED` | resume endpoint | tenant_id / run_id / decision / decided_by / request_id |

`resource_type` Literal 加 `"approval"`。超时 job 的 verdict 记在 `agent_approval` 行（`status=timeout` / `decided_by='system'` / `decided_at`）—— 该行本身即 tenant-RLS 的可查终态记录，超时路径不另发 `audit_log` 行（retention-cleanup-job 无 AuditLogger，M1 可补）。

### 14.9 不做项（M0 边界）

- ❌ **多审批人 / 升级链**（任一审批 / 全部审批 / 升级 manager）→ M1 配 H.3 高级 UI
- ❌ **成本阈值自动审批**（"超 X token 必审批"）→ M1 与 J.12 一起
- ❌ **异步通知**（Slack / 邮件 / webhook）→ M1 与 Stream A 渠道
- ❌ **审批 SLA 监控指标** → M1+
- ❌ **Admin UI 审批面板代码** → H.3 stream（J.8 仅交 API + audit，UI 接入由 H.3 做）

---

## 15. J.7 — Skill + skill 进化

### 15.1 现状

无 skill 概念。每个 agent 的能力全靠 manifest 静态声明,无可复用、可习得的能力单元。

### 15.2 设计与边界

- **skill = 可复用能力包**：具名,含 prompt 片段（如何做某类任务）+ 工具子集 + 可选代码片段。租户内 skill 库。
- agent 经 `AgentSpec.skills: list[str]` 启用 skill;装配时 skill 的 prompt / 工具并入。
- **skill 进化（有界）**：agent 可经 `author_skill` / `refine_skill` 工具,把"这次摸索出的有效做法"沉淀成新 skill 或精化已有 skill → 写 `skill` 库（新 `skill_version`)。**边界**：进化 = 受控地写 skill 库行,**不是无界自我代码修改**;新 skill 默认 `draft` 状态,需显式启用（或经 J.8 审批）才生效。
- 拆分：J.7 局部设计拆 J.7a / J.7b 两阶段（见 Mini-ADR J-23）—— **M0 仅做 J.7a**：skill = prompt 片段 + tools 子集（**不含 code 字段**）；静态启用 + 版本化 + draft 闸门 + admin API + 版本固定 + ZIP import/export + telemetry + conflict 拒绝构建 + 安全防护。**J.7b 推 M1+**：skill 进化（`author_skill` / `refine_skill`）+ code 字段执行边界 + supporting files + LLM moderation + public 内置库 — 见 ITERATION-PLAN § M1-K。

### 15.3 接口与数据模型（J.7a M0）

```python
# 迁移 0029_skill —— 双表 + tenant RLS
class SkillRow(Base):                       # 表 skill
    id: UUID
    tenant_id: UUID
    name: str                               # (tenant_id, name) 唯一
    status: Literal["draft", "active", "archived"]
    latest_version: int                     # 指向 skill_version.version；status=active 时由 loader 取
    created_at, updated_at

class SkillVersionRow(Base):                # 表 skill_version
    id: UUID
    tenant_id: UUID                         # 冗余存 RLS 用
    skill_id: UUID
    version: int                            # (skill_id, version) 唯一，递增
    prompt_fragment: str                    # Markdown 多段允许
    tool_names: list[str]                   # tool 子集
    description: str                        # M0 新加（J.7a-补强-1）
    category: str | None                    # M0 新加
    required_models: list[str]              # M0 新加，build 期校验
    # code: str | None                      # J.7b 才加，M0 显式不要
    authored_by: Literal["human", "agent"]
    created_at

# helix-protocol
class SkillVersion(BaseModel):              # API DTO
    id: UUID
    skill_id: UUID
    version: int
    prompt_fragment: str
    tool_names: list[str]
    description: str
    category: str | None
    required_models: list[str]
    authored_by: Literal["human", "agent"]
    created_at: datetime

class Skill(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    status: SkillStatus                     # StrEnum draft|active|archived
    latest_version: int
    description: str                        # 最新版的 description 冗余存
    category: str | None
    created_at: datetime
    updated_at: datetime

# AgentSpec.skills: list[str] —— M0 元素允许两种形态
#   "foo"     → 绑 skill name=foo + latest skill_version (skill.status='active')
#   "foo@3"   → pin skill name=foo + skill_version.version=3 (allow archived/draft)
# Validator regex: r"^[a-z][a-z0-9_-]{0,63}(@\d+)?$"
```

### 15.4 整合点（J.7a M0）

- `helix-protocol`：`AgentSpecBody.skills: list[str]` validator（双形态正则）+ `protocol/skill.py`（`Skill` / `SkillVersion` / `SkillStatus` DTO）
- `helix-persistence`：迁移 0029 + ORM (`SkillRow` / `SkillVersionRow`) + `SkillStore` ABC + InMemory + SQL 实现
- `orchestrator/agent_factory.py`：build 期展开 `spec.skills` → 解析 (name, version?) → 查 `SkillStore` → prompt 片段按 manifest 声明顺序拼（用 `<skill name="X" version="N">{prompt_fragment}</skill>` 包裹防 prompt injection）+ tool_names 集合并（重叠 → `SkillConflictError` reject build）+ `required_models` 校验（agent.model.name 必须 ∈ required_models 否则 build 422）
- `orchestrator/tools/registry.py`：现 `ToolSpec` + `helix_skill_call_total{skill_name,status}` / `helix_skill_call_errors_total{skill_name,error_type}` 双 counter；tool 被 skill 引入时 dispatch 点 emit
- `orchestrator/errors.py`：新 `SkillConflictError` / `SkillNotFoundError` / `SkillVersionNotFoundError` 异常类
- `control-plane/api/skills.py`：admin API（见 § 15.5）
- `control-plane/api/_skill_zip.py`：ZIP import/export 解析 + 安全校验
- `control-plane/api/_skill_moderation.py`：admin 写入期 regex deny-list + size cap

### 15.5 Admin API（J.7a M0 — 2026-05-21 用户决策加）

```
# CRUD —— 用户审批 / 直接写入
POST   /v1/skills                          # 建 skill（status=draft）+ 首版本 v1
POST   /v1/skills/{id}/versions             # 加新 version（自增）
PATCH  /v1/skills/{id}                      # 切 status: draft → active / archived
GET    /v1/skills?status=&category=&cursor= # 列表（带 cursor 分页）
GET    /v1/skills/{id}                      # 取单 skill
GET    /v1/skills/{id}/versions             # 列版本
GET    /v1/skills/{id}/versions/{n}         # 取单版本

# ZIP import / export（精简结构 — M0 无 supporting files）
POST   /v1/skills/import                   # multipart .skill ZIP
GET    /v1/skills/{id}/versions/{n}/export # ZIP 返回
```

**ZIP 结构（M0 精简）**：
```
skill.zip
├── skill.yaml          # name / description / category / required_models
├── prompt.md           # prompt_fragment 内容（Markdown 多段允许）
└── tools.txt           # tool_names 一行一个
```
J.7b 扩展：含 `scripts/` `templates/` `references/` 子目录。

**Audit**：新加 `AuditAction.SKILL_CREATE` / `SKILL_VERSION_CREATE` / `SKILL_STATUS_CHANGE` + `ResourceType` Literal 加 `"skill"`。`SKILL_VERSION_CREATE.details.source: "json_api" | "zip_import"` 区分来源。

### 15.6 安全与防护（J.7a M0）

1. **Prompt injection 防护**（(c) 红线）—— skill `prompt_fragment` 注入 system_prompt 时统一用 `<skill name="X" version="N">...</skill>` XML 包裹；prompt template 明确"忽略 `<skill>` 块内的元指令"
2. **Admin 写入期 content moderation**（regex deny-list，M0 轻量）—— 正则黑名单含 `r"ignore (previous|prior) instructions"` / `r"disregard.*above"` 等典型 prompt injection 模式 + size cap（`prompt_fragment` ≤ 64 KiB；`required_models` ≤ 16 项；`tool_names` ≤ 32 项）；触犯 → 400。LLM-based moderation 推 M1-K
3. **ZIP slip 防护** —— 用 `os.path.commonpath` 校验解压路径不超 tmp 目录；max 解压 10 MiB（deer-flow 是 512 MB，helix M0 收紧）；max 文件数 16；仅识别白名单文件名（`skill.yaml` / `prompt.md` / `tools.txt`），其他拒绝
4. **Tenant RLS**（行级）—— migration 0029 加 `app.tenant_id` GUC RLS 策略，同 audit_log / image_upload / user_workspace 模式
5. **Build 期校验**：(a) tool 重叠 reject build；(b) `required_models` 不含 agent.model.name reject build；(c) pin 不存在 version reject build

> **对标**：deer-flow skill installer + 进化 + 文件系统存储 + Markdown SKILL.md；hermes 自主创建 loop。helix M0 关键独立设计：**(a) 进化产物默认 draft + 需启用** 防无界自改失控；**(b) typed DB schema** 不丢字段；**(c) build-time tool 冲突 reject** 防 agent 拿到意外 tool；**(d) per-manifest 启停**天然按 agent 隔离；**(e) telemetry 双 counter** 支持运行时观察。J.7b 借鉴 deer-flow `skill_manage_tool` 进化模式 + `.skill` ZIP supporting files 扩展。完整对比见 `.claude/plans/witty-hugging-widget.md`（2026-05-21 J.7a 启动前调研）。

### 15.7 J.7b-1 设计预约定（visibility / fork / promote 三大支柱）

> **2026-05-28 用户提问触发** — agent 创建的 skill 谁能用 / agent 能改哪些 skill。问题暴露 J.7b-1 原始 backlog 描述（`author_skill` / `refine_skill` 两工具 + draft 闸门）**缺少归属 + 共享 + 修改权矩阵**，会让 M1-K design phase 重新走一轮决策。本节先定方向，M1-K 实施 PR 在此基础上展开。

> **状态**：纯设计预约定，**0 行代码**。实际 schema / 工具 / 审批 surface 全部在 M1-K J.7b-1 实施 PR 内交付。

#### 15.7.1 三大支柱

helix 形态是 **per-user persistent agent**（[memory:target-product-form]），agent 学到的 skill 大部分是 user-specific 偏好 / context，少部分是团队通用知识。如果 default tenant-shared，会污染同事 + 泄露用户偏好，违反基础信任。三个支柱守住价值线：

1. **Default `agent_private`**：agent 创建的 skill 默认仅创建者 agent 可见 + 可用（不像 J.7a admin 创建的 skill 是 tenant 维度自动可见）。
2. **Fork 是经验复用的核心通路**：agent 看见 tenant-shared 或 admin 创建的 skill，**能 fork 一份到自己 scope 改造**，不影响源 — 类比 GitHub fork。这是把 skill 库变成 commons 的关键设计。
3. **Promote 是显式审批**：agent 觉得"这条经验值得入团队库"，发起 `propose_skill_to_tenant(skill_id, reason)` → tenant admin 审 → 通过后 visibility 从 `agent_private` → `tenant`。Promote 不是 publish（publish 是 status → active，由 U-24 publish gate 把守，正交维度）。

#### 15.7.2 数据模型扩展（M1-K J.7b-1 落地，本节列契约）

`Skill` 加 3 列（migration 待定，先占编号 `0045_skill_authorship`）：

```python
class Skill(BaseModel):
    ...
    # M1-K J.7b-1 — visibility 维度，与 lifecycle status 正交。
    # ``agent_private`` → 仅 created_by_agent_id 的 agent 可读 + 可引用
    # ``tenant`` → 同 tenant 任何 agent 可读 + 可引用（J.7a admin 创建的 skill
    #             默认就是 tenant 维度，相当于上来就 tenant）
    visibility: Literal["agent_private", "tenant"] = "tenant"
    # 创建者 agent id；human 创建为 NULL；fork 来的 skill 填新 owner agent id。
    created_by_agent_id: UUID | None = None
    # Fork 谱系。``None`` 表示原创；非空 = 从 source_skill_id fork 来的。
    # Fork 不复用 SkillVersion（D3 immutability），fork 时 copy 当前 latest_version
    # 内容 + supporting_files 到新 Skill 的 v1（authored_by="agent"）。
    forked_from: UUID | None = None
```

#### 15.7.3 修改权操作矩阵

| skill 来源 vs agent 操作 | `refine_skill`（改产生新 version） | `fork_skill` | `archive_skill` | `propose_promote` | `delete` | `pin` | `status → active` |
|------|-----|-----|-----|-----|-----|-----|-----|
| **自己 author**（`created_by_agent_id == self`） | ✅ | n/a | ✅ | ✅（仅 agent_private） | ❌ | ❌ | ❌（走 U-24 + 默认人审） |
| **其他 agent author（tenant-shared）** | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **admin/human author** | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **system / J.7b-5 public** | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |

**判定规则**：`agent 可操作 = (authored_by == "agent" AND created_by_agent_id == self.id) AND 操作 ∈ {refine, archive, propose_promote}` + `fork` 对任何 visible skill 开放。所有"提权类"操作（delete / pin / publish→active）admin-only。

理由收紧：
- agent **不能改 admin / human skill** — platform-curated 内容是公司 policy / SOP，agent 改了违反规则
- agent **不能改其他 agent 的 skill** — 防 agent 间接破坏（A 改 B 用的 skill 里塞 prompt injection）
- agent **不能 delete** — Curator 90 天后自动 archive 已足够回收 + 数据保留可追溯
- agent **不能 pin** — Sprint #4 已加 pin 必 admin（高危）+ 一般情况 agent 无 admin role 自然 403；保持
- agent **不能 status→active** — Sprint #3 U-24 已把守（高危 skill 必 admin）；M1-K J.7b-1 实施时建议**所有 agent-authored skill 默认走 admin 审**（不仅高危）

#### 15.7.4 新增 audit actions（per [memory:audit-literal-drift]）

```
SKILL_AUTHORED_BY_AGENT       # agent 通过 author_skill 创建新 skill
SKILL_REFINED_BY_AGENT        # agent 通过 refine_skill 改自己 skill（产生新 version）
SKILL_FORKED                  # agent 通过 fork_skill 复制别人 skill 到自己 scope
SKILL_PROMOTE_REQUESTED       # agent 通过 propose_skill_to_tenant 发起 promote
SKILL_PROMOTE_APPROVED        # tenant admin 审过 promote → visibility 改 tenant
SKILL_PROMOTE_REJECTED        # tenant admin 拒 promote → 保持 agent_private
```

protocol AuditAction StrEnum 单源（M1-K J.7b-1 实施时一并加）。

#### 15.7.5 与已有机制的衔接

| 已有机制 | 与 J.7b-1 的关系 |
|---------|------------------|
| U-24 high-risk publish gate（Sprint #3） | 透明复用 — agent author 高危 skill → DRAFT → propose active → admin 审（gate 自动触发） |
| Curator（Sprint #4） | agent_private skill 也走 Curator stale/archive，但 `created_by_agent_id` 列让 UI 能 filter "看 agent X 创建的" |
| Sprint #4 high-risk pin 防御 | agent 创建后 pin 工具不可调（无 admin role）— 自然封死自我提权路径 |
| J.7b-5 system skill 库 | system 是 visibility=`tenant` + tenant_id 是平台租户的特殊形态；fork_skill 对 system skill 同样有效 |
| J.7a 现有 admin-created skill | visibility 字段默认 `tenant`，行为无变化（M0 部署的 skill 保持现状） |

#### 15.7.6 M1-K design phase 准入条件

J.7b-1 实施 PR 启动前，design PR 必须基于本节扩展以下：

- [ ] `propose_promote` 审批流细化：admin 一票否决 vs 多 admin 投票 vs 自动通过条件
- [ ] Fork 行为细化：fork 的 skill 名字默认 `{原名}-fork-{agent短id}` 还是 agent 自命名
- [ ] 速率限制：per-agent 每小时 author / refine / fork 上限（防 agent 自我爆炸创建）
- [ ] 防误学约束：agent author skill 时的 LLM judge prompt（参考 Hermes Skill review prompt "什么坚决别写" 4 条 — environment failure / negative tool assertions / session-specific transients / one-off narratives）
- [ ] Admin UI:agent-authored skill 在 SkillsList 是单独 tab 还是混在主列表 + filter

---

## 16. J.10 — 调度 / 触发

### 16.1 现状

纯 `POST /v1/sessions/{tid}/runs` 同步请求-响应。agent 不能定时跑 / 被外部事件触发。

### 16.2 设计与边界（2026-05-22 deer-flow 对比修订 — Mini-ADR J-42）

- **两类触发器**：`cron`（定时）、`webhook`（外部 HTTP 入站）。`event`（内部事件触发，如另一 run 完成）+ PG NOTIFY **推 M1** —— M0 无具体消费场景，更像 workflow 串联原语（Mini-ADR J-26 (3) 修订 / J-42）。
- **scheduler = 轮询 `agent_trigger` 表**（非 APScheduler）。control-plane 内一个单副本后台 worker，复用 `ReservationReaper` 范式（`start` / `stop` / `run_once` / `_loop`），每 ~60s 扫 `agent_trigger`，`croniter` 算 cron 下次触发，到点即起 run。`agent_trigger` 表是唯一真相源 —— APScheduler 自带 `SQLAlchemyJobStore` 会与之形成双真相源（每次增删改启停双写、易漂移），故弃用（Mini-ADR J-26 (4) 修订 / J-42）。
- 触发式 run 与请求式 run **共用执行路径**（`run_agent` 发起链），只是发起来源不同、无 SSE 消费者；每次触发记录进 `trigger_run`。
- **触发式 run 的 thread**：每次触发起一个新 thread —— 一次 cron / webhook run 是独立会话；`config.seed_input` 作首条 user 消息。
- **边界**：M0 单副本 scheduler（无选主 / 无分布式锁，§ 1.2 + Mini-ADR J-18）；webhook 入站经**每触发器独立 secret token** 鉴权（非复用全权 API key — J-42）。

### 16.3 接口与数据模型

迁移 `0033_agent_trigger` —— 两表，均 tenant RLS（`agent_trigger` 命名避开 `trigger` SQL 保留字）：

```python
class AgentTriggerRow(Base):                 # 表 agent_trigger
    id: UUID
    tenant_id: UUID
    user_id: UUID | None
    agent_name: str
    agent_version: str
    name: str                                # 触发器名，(tenant, agent, name) 唯一
    kind: Literal["cron", "webhook"]
    config: dict                             # cron: {expr, seed_input} / webhook: {seed_input}
    enabled: bool
    source: Literal["manifest", "api"]       # 来源（manifest 声明 / API 创建）
    webhook_secret_hash: str | None          # webhook kind 专用；明文仅创建时返回一次
    last_fired_at: datetime | None
    created_at, updated_at: datetime

class TriggerRunRow(Base):                   # 表 trigger_run
    id: UUID
    tenant_id: UUID
    trigger_id: UUID                         # → agent_trigger（裸列，FK-light）
    run_id: UUID | None                      # → agent_run（run 起成功后回填）
    status: Literal["fired", "succeeded", "failed", "retrying", "dead_letter"]
    attempt: int                             # DLQ 重试计数，1 起
    next_retry_at: datetime | None
    error: str | None
    triggered_at: datetime

class TriggerSpec(BaseModel):                # AgentSpecBody.triggers 元素
    name: str
    kind: Literal["cron", "webhook"]
    config: dict
```

`helix-protocol` 加 `TriggerKind` Literal + `TriggerSpec`。manifest 声明的触发器在 agent 部署时 upsert 进 `agent_trigger`（keyed by `(tenant, agent, name)`，`source="manifest"`）；API 创建的是独立行（`source="api"`）—— J.7 skill 同款双路。`TriggerStore` ABC + `InMemoryTriggerStore` + `SqlTriggerStore`（`agent_approval` / `agent_run` store 同款范式）。

### 16.4 scheduler 组件

`TriggerScheduler` —— control-plane 内单副本后台 worker（`ReservationReaper` 范式）。`run_once` 每轮：(1) 扫 `agent_trigger`（`kind=cron`、`enabled`、`croniter(expr, last_fired_at)` 下次触发 ≤ now）→ 复用 `run_agent` 发起链起 run（新 thread、无 SSE）→ 写 `trigger_run` 行 + 更新 `last_fired_at`；(2) 扫 `trigger_run`（`status=retrying`、`next_retry_at ≤ now`）→ 按 DLQ 重试（§ 16.6）。单次 `run_once` 异常不崩进程（计数 + 继续）。lifespan 启停同 reaper。

### 16.5 webhook 入站

入站 endpoint `POST /v1/webhooks/{trigger_id}` —— 独立 `/v1/webhooks` path 前缀（非 `/v1/triggers/.../webhook`），整段经 `AuthMiddleware` 前缀豁免，而 `/v1/triggers` CRUD 仍走正常鉴权。改用**每触发器独立 secret token** 鉴权：创建 webhook 触发器时生成 secret，SHA-256 哈希存 `webhook_secret_hash`，明文仅返回一次（API key 同款）；入站请求带 secret（`X-Helix-Webhook-Secret` header），端点 `hmac.compare_digest` 校验，失败 403、触发器不存在 / 非 webhook / 已禁用一律 404。命中即经 RLS-bypass 按 id 解析触发器（无租户上下文）→ 复用 `fire_trigger` 起 run + 写 `trigger_run`。

### 16.6 DLQ 重试（M0 — Mini-ADR J-26 (1)）

failed trigger run → K.K7 范式 DLQ 重试：backoff `1m→5m→30m→2h→6h`，`_MAX_ATTEMPTS=5`。scheduler `run_once` 三遍扫描：(1) cron 起 run；(2) **reconcile** —— 扫 `fired` 的 `trigger_run`，读其 `agent_run` 终态：success → `succeeded`；error/timeout → `retrying`（算 `next_retry_at`）或 attempt 用尽 → `dead_letter`；interrupted → `failed`；(3) **retry** —— 重投 `next_retry_at` 已到的 `retrying` 行（attempt++、新 run、`fired`）。重试状态全落 `trigger_run`（`attempt` / `next_retry_at` / `status`）。

### 16.7 scheduler quota（M0 — Mini-ADR J-26 (2)）

cron 触发器创建（`POST /v1/triggers`）时，直接 `count_cron_by_tenant` 比对 `settings.max_cron_triggers_per_tenant`（默认 100）—— 超额 429。**不走 `QuotaService`** —— 触发器数是「当前计数」上限，不适配 `QuotaService` 的预留 / 速率模型（强行接入会引入计数器漂移，同 APScheduler jobstore 双真相源问题）；表本身即权威计数（Mini-ADR J-26 (2) 修订 / J-42）。

### 16.8 Audit Trail（M0）

新 `AuditAction`：`TRIGGER_CREATE` / `TRIGGER_UPDATE` / `TRIGGER_DELETE` / `TRIGGER_FIRE`。`resource_type` Literal 加 `"trigger"`。

### 16.9 Eval module（M0）

`tools/eval/trigger.py` + `datasets/trigger/m0_baseline.yaml` —— 确定性场景（cron 下次触发计算 / webhook secret 校验 / DLQ backoff 排程 / quota 强制）。`run_baseline.py` 激活 `J.10_trigger` runner。

### 16.10 不做项（M0 边界）

- ❌ **`event` 触发器 + PG NOTIFY** → M1（无 M0 具体消费场景；Mini-ADR J-26 (3) 修订 / J-42）
- ❌ **分布式 scheduler / 多副本选主** → M1+（§ 1.2 + Mini-ADR J-18）
- ❌ **HMAC 载荷签名**（webhook 防重放 / 完整性）→ M1（M0 用 secret token 鉴权）
- ❌ **webhook 完成回调**（run 完成后 POST 回调用方）→ M1

### 16.11 整合点

control-plane 新 `scheduler` 模块 + lifespan 启停、新 `triggers` API（CRUD + webhook 入站）、`run_agent` 触发式发起路径、`agent_trigger` / `trigger_run` 模型、`helix-protocol` `TriggerSpec` + `AgentSpecBody.triggers`、Stream C.5 `QuotaService`、`audit_log`。

> **对标**：deer-flow 无调度 / 触发系统（`after_seconds` / `webhook` 字段是死代码）。helix 做 cron + webhook 两类生产级触发器（DLQ / quota / 鉴权齐全），`event` 推 M1。

---

## 17. J.12 — 学习 / 反馈闭环

### 17.1 现状

G.6 已采 👍/👎 feedback（`feedback` 表）、L7 已把完整 run trajectory 落 ObjectStore（PR #202，按 outcome 分流），但**两者各躺各的** —— trajectory 不回流改进、feedback 只用于前端显示一个图标。无"线上数据 → 策划 eval 数据集"的闭环。

### 17.2 设计与边界（2026-05-22 deer-flow 对比修订 — Mini-ADR J-43）

- **J.12 = 策划层**,不重造采集层。两个输入端已就位：L7 trajectory ObjectStore（完整 messages，4 outcome 分流）+ G.6 `feedback` 表（👍/👎，挂 `thread_id`）。J.12 **不写** `trajectory` PG 表（Mini-ADR J-27 —— L7 ObjectStore 已是底座，再建 PG 表 = 重复实现）。
- **策划机制 = 规则候选 + 人工策划**（Mini-ADR J-43）。后台 `CurationWorker` 按规则从 trajectory + feedback 排出**候选**（`curation_candidate` 表）；人工 review 后 promote（→ `eval_dataset` 行，标注 expected）/ dismiss。纯人工 = 退化成 CRUD 表（能力弱）；纯规则自动 = 👎 run 的正确 expected 无人能机器生成、数据集含噪声 —— 故取规则筛 + 人工判的组合。
- **候选生成 = 后台 worker 预生成**（Mini-ADR J-43）。`CurationWorker` 单副本后台进程（复用 `TriggerScheduler` / `ReservationReaper` 范式），周期扫 trajectory ObjectStore + feedback → 规则命中即 upsert `curation_candidate` 行；策划 API 直读该表（扫描成本不在请求路径上）。
- **候选规则**（3 类信号）：👎 feedback → `negative_feedback`；`failed` / `max_steps` outcome → `failed_outcome`；👍 feedback → `positive_feedback`（作 golden 正例）。前两类是回归材料、第三类是 golden 材料。
- **scope = (tenant, agent)，非 per-instance**。helix 目标形态是 per-user 持久 agent 实例 —— 同一 agent 下每用户一个隔离实例。feedback / trajectory 的**采集**天然 per-instance（挂 `thread_id` = 某用户实例的某次会话）；但策划**产出** `eval_dataset` 按 **(tenant, agent_name)** 归集 —— 学习闭环改的是 agent 定义（prompt / tools / model），改一次所有实例受益，"回归测试某个实例"不成立（实例 = agent 定义 + 该用户私有 memory / workspace）。J.12 汇聚一个 agent 所有用户实例的 trajectory + feedback → 策划 → 产出该 agent 的数据集。per-instance 个性化属 J.3 记忆，不在 J.12。
- **关联键**：trajectory / feedback 均挂 `thread_id`（`feedback` 表无 `run_id`）；`thread_meta` 表记 `thread_id → agent_name / agent_version / user_id` —— worker 由此盖上 agent 身份，`user_id` / `trajectory_key` 留作**溯源**（采自哪个实例 / 哪条 trajectory），非 scope 键。
- **`eval_dataset` 与 J.13 共用**（Mini-ADR J-20）：策划产出落 `eval_dataset` PG 表（tenant RLS）；导出 CLI 把 `eval_dataset` 行写成 `tools/eval/datasets/<name>/*.yaml` 供 J.13 eval module 消费。`eval_dataset` PG 表 = "策划数据集源"，checked-in baseline YAML 仍是 Gate 制品（Mini-ADR J-38，两者分离）。
- **边界**：J.12 只交付到"策划好的数据集",**不含训练 / 微调**（§ 1.2,M2+,Mini-ADR J-19）。区别于 J.7 skill 进化 —— J.7 是运行期 agent 自改能力,J.12 是离线数据驱动的人 / 流程改进。

### 17.3 接口与数据模型

迁移 `0034_eval_dataset` —— 两表,均 tenant RLS：

```python
class CurationCandidateRow(Base):            # 表 curation_candidate
    id: UUID
    tenant_id: UUID
    agent_name: str                          # 候选所属 agent（thread_meta 解析）
    agent_version: str | None                # 溯源：采自哪个 agent 版本
    thread_id: UUID                          # trajectory 的 thread（= 某用户实例的一次会话）
    user_id: UUID | None                     # 溯源：采自哪个用户实例
    trajectory_key: str                      # ObjectStore key，(tenant, trajectory_key) 唯一
    outcome: Literal["success", "failed", "max_steps", "cancelled"]
    signal: Literal["negative_feedback", "failed_outcome", "positive_feedback"]
    feedback_rating: Literal["up", "down"] | None   # thread_id 命中 feedback 时回填
    status: Literal["pending", "promoted", "dismissed"]
    eval_dataset_id: UUID | None             # promote 后回填
    detected_at: datetime                    # worker 首次识别为候选的时刻
    reviewed_at: datetime | None

class EvalDatasetRow(Base):                  # 表 eval_dataset（J.13 共用）
    id: UUID
    tenant_id: UUID
    agent_name: str                          # 数据集归属的 agent —— 按 (tenant, agent_name) 归集
    name: str                                # dataset 名（一个 agent 可有多个命名 dataset）
    input: dict                              # JSONB —— eval case 输入
    expected: dict | None                    # JSONB —— 期望输出（人工标注；golden/regression 必填）
    source: Literal["golden", "trajectory", "regression"]
    source_trajectory_key: str | None        # 溯源：来自哪条 trajectory
    source_user_id: UUID | None              # 溯源：来自哪个用户实例
    created_at, updated_at: datetime
```

`helix-protocol` 加 `EvalDatasetSource` / `CurationSignal` / `CandidateStatus` Literal + `EvalDatasetRecord` / `CurationCandidateRecord` 冻结 DTO。`CurationCandidateStore` ABC + `InMemory` + `Sql` 三件套、`EvalDatasetStore` 同款（`agent_trigger` / `agent_run` store 范式）。trajectory ObjectStore 当前**只有写无读 API** —— J.12 加 `TrajectoryReader`（按 `tenant / outcome / date` 前缀 `list` + `get` + JSONL 解析）。

### 17.4 CurationWorker 组件

`CurationWorker` —— control-plane 内单副本后台 worker（`TriggerScheduler` 范式）。`run_once` 每轮：跨租户（`bypass_rls`,reaper 范式）枚举 trajectory ObjectStore 新对象 → 对每条 trajectory：(1) join `thread_meta` 取 `agent_name / agent_version / user_id`；(2) join `feedback`（by `thread_id`）取 rating；(3) 规则判定（👎 / `failed`·`max_steps` / 👍）；(4) upsert `curation_candidate`（by `(tenant, trajectory_key)`，已存在即跳过）。单次 `run_once` 异常不崩进程（计数 + 继续）。lifespan 启停同 reaper / scheduler。

### 17.5 策划 API

- `GET /v1/curation/candidates` —— 列 `curation_candidate`（filter `agent_name` / `status` / `signal`，游标分页），读 PG 表（非实时扫 ObjectStore）。
- `GET /v1/curation/candidates/{id}` —— 候选详情,含从 ObjectStore 拉的完整 trajectory messages。
- `POST /v1/curation/candidates/{id}/promote` —— body `{name, input, expected, source}` → 建 `eval_dataset` 行（`agent_name` 取自候选）+ 候选 `status=promoted` + 回填 `eval_dataset_id`。
- `POST /v1/curation/candidates/{id}/dismiss` —— 候选 `status=dismissed`。
- `eval_dataset` CRUD —— `POST/GET/PATCH/DELETE /v1/eval-datasets`（`source=golden` 支持纯手工建例,无需 trajectory；`?agent_name=` 过滤）。

> 策划是 tenant-admin 职能 —— 策划人本就能看本租户全部 trajectory（候选详情展示完整 messages），promote 到 `eval_dataset` 不扩大可见面。

### 17.6 quota（M0）

`eval_dataset` 行数 per-tenant 上限 —— `POST /v1/eval-datasets` + promote 时 `count_by_tenant` 比对 `settings.max_eval_dataset_rows_per_tenant`（默认 1000），超额 429。同 J.10 cron quota —— "当前计数"上限,表本身即权威计数,不走 `QuotaService`。

### 17.7 导出（M0）

导出 CLI `tools/eval/export_dataset.py` —— 按 `name` 把 `eval_dataset` 行写成 `tools/eval/datasets/<name>/curated.yaml`（J.13a case 格式）。人工 review YAML diff 后 commit —— checked-in YAML 仍是 Gate 制品（Mini-ADR J-38），`eval_dataset` PG 表是其上游"策划源"。

### 17.8 Audit Trail（M0）

新 `AuditAction`：`EVAL_DATASET_CREATE` / `EVAL_DATASET_UPDATE` / `EVAL_DATASET_DELETE` / `CURATION_PROMOTE` / `CURATION_DISMISS`。`resource_type` Literal 加 `"eval_dataset"` / `"curation_candidate"`。

### 17.9 Eval module（M0）

`tools/eval/learning.py` + `datasets/learning/m0_baseline.yaml` —— 确定性场景（规则命中 👎 / failed trajectory、trajectory↔feedback by `thread_id` 关联、`thread_meta` 解析 agent 身份、promote 产出合法 `eval_dataset` 行、导出产出合法 YAML、golden / regression source、跨租户隔离）。`run_baseline.py` 激活 `J.12_learning` runner —— baseline 转 14 PASS / 0 DEFERRED。

### 17.10 不做项（M0 边界）

- ❌ **训练 / 微调管线** → M2+（§ 1.2,Mini-ADR J-19 —— J.12 终点是"策划好的数据集"）
- ❌ **`trajectory` PG 表 + `after_llm_call` 中间件** —— 不做（Mini-ADR J-27 —— L7 ObjectStore 已是底座）
- ❌ **策划 review 前端 UI** → Stream H（J.12 仅交 API + audit，对标 J.8 / H.3 分工）
- ❌ **`eval_dataset` → J.13 自动回归门** → J.13c M1（导出 CLI 是半自动：导出 + 人工 commit；CI 周跑自动化是 J.13c）
- ❌ **feedback 驱动的 prompt 自动改进 / RLHF** → M2-D（J.12 只到数据集，改进环节是 M2-D pipeline）
- ❌ **per-instance 个性化**（按单用户反馈调其实例 memory）→ 属 J.3 记忆,非 J.12

### 17.11 整合点

control-plane 新 `curation` 模块（worker + API）+ lifespan 启停、新 `eval-datasets` / `curation` API、L7 trajectory ObjectStore（读）、G.6 `feedback` 表 + `thread_meta` 表（读）、`eval_dataset` / `curation_candidate` 模型、`audit_log`、`tools/eval`（导出目标 + J.13 消费方）。

> **对标**：deer-flow 无学习 / 反馈闭环（无 trajectory→dataset→eval 管线、无策划层、无 eval harness）。其 feedback 采集层成熟,但 helix G.6 `feedback` 已对等;其 `RunJournal` ≈ helix L7 trajectory ObjectStore。helix 两个输入端均已就位,J.12 净新建策划层把两者接成 `eval_dataset`,设计领先。

---

## 18. J.13 — eval 强化（J-28 拆分后展开为 J.13a M0 / J.13b M1 / J.13c M1）

> **2026-05-21 J.13a 设计 PR**：原 § 18 是"四点纲领"骨架（Mini-ADR J-20）；J-28 已先把 J.13 拆为三子项。本节展开 J.13a 实施设计；J.13b / J.13c 在 § 18.7 仅占位。

### 18.1 现状

`tools/eval` 已落地两层基建 + L7 提供"线上 trajectory → 离线 dataset"上游底座：

- **G.4 通用 harness**（`tools/eval/helix_eval.py`）：prompt + machine-checkable assertions（`contains` / `regex` / `equals` / `not_contains`）+ mock provider（CI 内零 LLM 依赖）+ 可插 `complete(prompt) -> str` 真 LLM provider。
- **K12 memory recall 模板**（`tools/eval/memory_recall.py` + `datasets/memory_recall/zh_en_seed.yaml`）：per-capability evaluator 范式 —— 可插 Embedder + Store，输出 `recall@k` / `mrr@k`，已含 zh + en 各 4 case seed。
- **L7 trajectory ObjectStore**（PR #202）：完整 messages 按 outcome 分流（success / error / cancelled / timeout）落 ObjectStore — J.13a 离线 eval 的"线上 trajectory → 策划 dataset"上游底座，J.12 负责策划环节。

骨架已可用，缺的是**两件事**：
1. 没有 per-capability eval 场景集覆盖已交付的 7 个能力（J.1 / J.2 / J.3 / J.6 / J.11 / J.14 / J.15）—— Stream M Gate 缺锚点。
2. 没有 baseline 制品文件 —— 每周跑分数没地方"落"，回归无对照。

### 18.2 设计与边界（J-28 拆分确认）

J.13 按 Mini-ADR J-28 拆 3 子项，**M0 内仅交付 J.13a**：

| 子项 | 范围 | 工期 | 推后判定 |
|------|------|------|---------|
| **J.13a 逐能力 eval 场景集 + baseline 制品** | 7 已交付能力各 1 个 eval module + 8 deferred 能力 skeleton stub + `tools/eval/run_baseline.py` aggregator + `tools/eval/baselines/m0_gate_baseline.yaml` checked-in 制品 + LLM-judge provider 配 Haiku 4.5 | M0 内 | (c) 红线 — 不达标 Stream M Gate 无锚点 |
| **J.13b 在线采样 + LLM-judge 配额 + budget cap** | 生产 run 按比例采样 → LLM-judge 评分进 Grafana + per-tenant budget cap | M1 早期 / 并入 M2-D | (a) 独立 dashboard 范式，与离线 baseline 解耦 |
| **J.13c CI 回归门 + flakiness 缓解** | baseline 分数进 CI（drift > 阈值即红）+ N 次重跑置信区间 + 软门 / 硬门拆分 | M1 早期 / 并入 M2-D | (b) M0 PR 节奏紧；软门设计需先观察 J.13b 真实 flakiness 数据 |

M0 内 J.13a 严格按 (c) 红线交付：每个已交付能力都有 metric 落 baseline，不允许"覆盖率指标"代替"能力分数"。deferred 8 项写 skeleton stub（YAML 含 `status: DEFERRED` + 1 个 placeholder case + 对应 PR 锚 issue）—— 该能力 PR 着陆时把 stub 转实测。

### 18.3 J.13a 范围 —— 已交付 7 能力的 eval 场景设计（Mini-ADR J-37）

per-capability metric 类型按 Mini-ADR J-37 的"deterministic 优先、LLM-judge 限用"原则：

| 能力 | metric 类型 | sample size (M0) | threshold | eval module | dataset |
|------|------------|------------------|-----------|-------------|---------|
| **J.1 plan_execute** | `pass-rate + llm-judge`（plan 结构合法 + LLM judge 任务覆盖度评分 1-5）| ≥ 20 case | pass-rate ≥ 0.80 / judge mean ≥ 4.0/5.0 | `tools/eval/plan_execute.py` | `datasets/plan_execute/m0_baseline.yaml` |
| **J.2 reflect** | `pass-rate`（注入 bug seed 上 reflect 修正率 + 正确回答上不过度修正率）| ≥ 16 case（8 buggy + 8 correct）| 修正率 ≥ 0.75 / 假阳率 ≤ 0.20 | `tools/eval/reflect.py` | `datasets/reflect/m0_baseline.yaml` |
| **J.3 long-term memory** | `recall@5` + `mrr@5`（沿用 K12 模板，扩 zh + en + 多语种混合 + episodic 各 8 case，共 32 case）| 32 case | recall@5 ≥ 0.70 / mrr@5 ≥ 0.55 | `tools/eval/memory_recall.py`（K12 已落，J.13a 仅扩 dataset） | `datasets/memory_recall/m0_baseline.yaml`（沿 zh_en_seed 扩） |
| **J.6 multimodal** | `pass-rate`（Path A content block + Path B `ask_image` 各 1 套图像 keyword recall case，复用 mock 图像 fixture）| ≥ 12 case（Path A 6 + Path B 6）| pass-rate ≥ 0.80 per path | `tools/eval/multimodal.py` | `datasets/multimodal/m0_baseline.yaml` |
| **J.11 model routing** | `pass-rate`（input → expected_route 选择正确率 + fallback 触发条件）| ≥ 16 case（plan / reflect / vision / default 四个步骤类别 × 4 fallback 触发） | pass-rate ≥ 0.95 | `tools/eval/model_routing.py` | `datasets/model_routing/m0_baseline.yaml` |
| **J.14 per-user isolation** | `pass-rate`（cross-user query 拒绝率 + admin 旁路通过率 + machine identity 租户级通过率）| ≥ 12 case | pass-rate = 1.00（隔离类不容许部分通过） | `tools/eval/per_user_isolation.py` | `datasets/per_user_isolation/m0_baseline.yaml` |
| **J.15 persistent volume** | `pass-rate`（cross-run 文件持久 + quota 拒绝 + lifecycle 状态机转换 + archive backup restore drill）| ≥ 10 case | pass-rate ≥ 0.90 | `tools/eval/persistent_volume.py` | `datasets/persistent_volume/m0_baseline.yaml` |

**统一原则**：
- 每个 eval module 一个 `evaluate_set(...) -> CapabilityReport` 入口 + 自有 metric dataclass；`run_baseline.py` 通过 `import` + 反射收集每个 module 的 `evaluate_set`。
- mock provider 必须能跑（CI 内零 LLM 依赖）；real LLM provider 走 J-39 模型（Haiku 4.5）+ N=3 重跑取多数 / 平均。
- 含 `not_implemented_yet: true` 字段的 case 跳过不算分（用于 deferred 能力的占位 case）。
- 隔离类 metric（J.14）必须 pass-rate = 1.00 才算 PASS — 不允许"接近就行"。
- LLM-judge 仅用在 J.1 plan_execute 任务覆盖度评分；其余能力都有 deterministic metric（pass-rate / recall@k）— 不为了用 judge 而用 judge。

### 18.4 接口与数据模型

**eval module 协议**（与现有 `helix_eval.py` / `memory_recall.py` 同结构）：

```python
# 每个 tools/eval/<capability>.py 必须 export 3 个符号
async def evaluate_set(
    cases: Sequence[CapabilityCase],
    *,
    judge: JudgeCompletionFn | None = None,
    rerun_count: int = 3,
) -> CapabilityReport:
    """Run all cases through the capability harness; return aggregate + per-case."""

@dataclass(frozen=True)
class CapabilityReport:
    capability: str                  # e.g. "J.1_plan_execute"
    metric_type: str                 # see § 18.3 table
    aggregate_score: dict[str, float]  # metric_name -> score（多 metric 可并列）
    sample_size: int
    threshold: dict[str, float]
    status: Literal["PASS", "FAIL", "DEFERRED"]
    per_case: tuple[CapabilityCaseResult, ...]

def load_cases(path: Path) -> Sequence[CapabilityCase]:
    """Parse the per-capability YAML dataset."""
```

**aggregator**：

```python
# tools/eval/run_baseline.py
async def run_baseline(
    *,
    judge_model: str = "claude-haiku-4-5-20251001",
    rerun_count: int = 3,
    out_path: Path = Path("tools/eval/baselines/m0_gate_baseline.yaml"),
) -> dict[str, CapabilityReport]:
    """Discover every capability module under tools/eval/, run evaluate_set,
    aggregate into the baseline file. M0 deferred capabilities emit
    status: DEFERRED with empty score so the file shape is locked."""
```

**LLM-judge provider**：复用 `LLMRouter`（control-plane）的 `ModelSpec` 接口（不另起一套），但走独立 `ModelSpec.role="eval_judge"`，避免与生产 model 路由 / fallback 链耦合。

### 18.5 baseline 文件格式（Mini-ADR J-38）

```yaml
# tools/eval/baselines/m0_gate_baseline.yaml
# Checked into git. Refreshed weekly by tools/eval/run_baseline.py.
# (manual run during M0; CI scheduled job after J.13c.)
# Stream M Gate Exit Criteria reads this file directly.

metadata:
  generated_at: 2026-05-21T10:00:00Z
  helix_commit: <sha>
  judge_model: claude-haiku-4-5-20251001
  judge_temperature: 0.0
  rerun_count: 3
  embedder: helix-fake-embedder-v1  # 或真 embedder 名

capabilities:
  J.1_plan_execute:
    metric_type: pass-rate+llm-judge
    sample_size: 20
    threshold:
      pass_rate: 0.80
      judge_mean: 4.0
    score:
      pass_rate: 0.85
      judge_mean: 4.2
    status: PASS
  # ...
  J.3_memory_recall:
    metric_type: recall@5+mrr@5
    sample_size: 32
    threshold:
      recall_at_5: 0.70
      mrr_at_5: 0.55
    score:
      recall_at_5_zh: 0.75
      recall_at_5_en: 0.80
      mrr_at_5_zh: 0.62
      mrr_at_5_en: 0.68
    status: PASS
  J.4_sub_agent:
    metric_type: pass-rate
    sample_size: 0
    threshold: { pass_rate: 0.80 }
    score: {}
    status: DEFERRED
    deferred_reason: "J.4 not yet shipped; placeholder case in dataset"
```

**生命周期**：
- M0 周跑 → 手动执行 `python -m tools.eval.run_baseline` → 生成新 yaml 覆盖旧文件 → git commit
- 每次能力 PR 着陆，**必须**在同一 PR 内重跑 + 更新 baseline 行（能力增加 = baseline 同步）
- Stream M Gate 判定：每个非 `DEFERRED` 能力 `status: PASS`
- J.13c 着陆时把"git commit 更新 baseline"改为"CI 周跑 job + drift 阈值告警"

### 18.6 LLM-judge 选型（Mini-ADR J-39）

| 选型 | 理由 |
|------|------|
| **Haiku 4.5（`claude-haiku-4-5-20251001`）作 judge** | 90% 大模型质量、3x 成本优势；M0 周跑 ~7 能力 × ~20 case × 3 重跑 ≈ 420 调用，judge cost 可承受；CI 不跑真 LLM（mock provider） |
| `temperature=0.0` | judge 必须确定性，否则 baseline 抖动 |
| N=3 重跑取多数（pass-rate）/ 平均（连续值）| flakiness 缓解 M0 最小版本；J.13c 升级到含 confidence interval + 偏离阈值告警 |
| LLM-judge 范围严限于 J.1 plan_execute | 其他能力都有 deterministic metric（pass-rate / recall@k）；不"为用 judge 而用 judge"，judge 也会错 |

### 18.7 整合点

- **Stream M Gate**：`STREAM-M-DESIGN.md` § Exit Criteria 引用 `tools/eval/baselines/m0_gate_baseline.yaml` 的 `capabilities.*.status == PASS` 作为 Exit Criteria；J.13a baseline 是 M0 → M1 Gate 的硬锚点。
- **J.13b**（M1）：在线采样 → judge 评分 → Grafana metric（per-tenant budget cap 接 Stream C.5 QuotaService）。本节仅占位；M1 早期 / 并入 M2-D 再展开。
- **J.13c**（M1）：CI scheduled job 周跑 + drift > 10% 阈值即红 + N 次重跑取置信区间 + 软门 / 硬门拆分。本节仅占位。
- **J.12 学习闭环**：J.12 策划后的 `eval_dataset` 行可作为 baseline dataset 的补充来源（人工 / 规则筛选 trajectory → 加入 baseline yaml）；J.12 自身工作不阻塞 J.13a。
- **CI**：M0 内 J.13a 不进 CI 周跑（J.13c 才接）；M0 CI 内只跑 mock provider 单测，覆盖 harness 自身。

> **对标**：三参考项目 eval 都弱。helix J.13a 把"逐能力 baseline 制品 + LLM-judge"两件事一次拉到生产级雏形 —— Stream M Gate 的尺子是这套 baseline yaml，不是体感。

---

## 19. Mini-ADR 汇总

> 格式：决策 / 背景 / 备选 / 取舍。编号 J-1 起。

**J-1｜隔离硬边界 = 租户,user_id 走一等列 + 应用层授权**
背景：per-user 持久 agent 要求"用户"成为一等实体。备选：(a) 给所有表加用户级 RLS;(b) user_id 一等列 + 应用层授权,仅 per-user 数据表加防御性用户级 RLS。取舍：选 (b) —— 同租户用户 = 同公司同信任域,硬隔离边界本就是租户;全表用户级 RLS 增加每查询开销且语义过严（同租户审计 / 客服需跨用户读）。per-user **数据表**里,**control-plane 持有的**（记忆 `memory_item` / 产物元数据 `artifact`）泄漏后果重,加用户级 RLS 做纵深 —— control-plane 的 RLS session 设 `app.tenant_id` + `app.user_id` 两个 GUC。**例外:`user_workspace` 不加 RLS** —— 它由 sandbox-supervisor 持有,与 `sandbox_instance` 同属系统服务表;supervisor 经 mTLS 认证调用方 + 应用层按 `(tenant,user)` scope,DB session 不设 `app.*` GUC,套 RLS 会让 supervisor 每次查询失败。`thread_meta` 维持租户级 RLS —— `user_id` 是裸列,所有权在 store / 应用层校验;`app.user_id` GUC 推迟到 J.3 首个用户级 RLS 表。

**J-1a｜J.14 新建 `tenant_user`,不复用 Stream C 的 `app_user`**
背景：Stream C 迁移 0004 已建占位表 `app_user`（"local + OIDC-federated end users",但无 ORM、无 store、auth 代码未消费）。备选：(a) 复用 `app_user`;(b) 新建 `tenant_user`。取舍：选 (b) —— `app_user` 的 `username` 是**全局唯一**（多租户下两租户不能同名用户,是缺陷）、identity key 仅 `(oidc_issuer, oidc_subject)`（不适配 api-key / mTLS 主体）,且它是 Stream C 平台 auth 的预留 schema。复用需 `ALTER` 改动 Stream C 的表（违背 surgical 原则）。`tenant_user` 是干净的、多租户正确的注册表：`(tenant_id, subject_type, subject_id)` 复合唯一,`id` = 代理 `user_id`。`thread_meta.user_id` 用裸 UUID 列、无 FK —— 匹配仓库 FK-light 风格（cf. `feedback.thread_id`),并规避 `FORCE` RLS 表上 FK 引用完整性校验的已知 footgun。

**J-2｜checkpointer 表不引入 user_id**
背景：LangGraph checkpoint 表 M0 即非租户 RLS 表（评估反馈记录）。决策：J.14 沿用 —— 隔离靠 `thread_id` 命名编码 `(tenant, user, conversation)` + 应用层授权,不改 LangGraph schema。取舍：改 LangGraph 库表 schema 脆弱、随升级易碎;thread_id 编码足够。

**J-3｜规划用图节点 + 状态字段,不用中间件**
背景：deer-flow 把 todo 做成中间件。决策：helix `planner` 作图节点、`Plan` 进 `AgentState`。取舍：计划是核心控制流,放节点比中间件锚点更显式、可加条件边、可被 checkpointer 自然持久。

**J-4｜`plan_execute` 复用既有 workflow 枚举**
背景：`WorkflowSpec.type` 已有 `plan_execute` 值。决策：J.1 点亮它,不新增枚举。取舍：协议已留位,直接用。

**J-5｜反思用同步图内节点,不用后台 daemon**
背景：hermes 用后台 review daemon。决策：helix `reflect` 同步图节点。取舍：M0 单体进程,同步节点简单可控,不引后台进程 / 额外并发面。

**J-6｜Model 路由 = 声明式步骤类别规则,不做动态难度估计**
背景：可按 token 预测动态估难度选模型。决策：按"步骤类别 → 模型"声明式规则。取舍：动态难度估计不可解释、易抖动;声明式规则可审计、可测。

**J-7｜长期记忆两类（fact / episodic),不做 5 层细分**
背景：评估 08 提过 5 层记忆。决策：先 fact + episodic 两类。取舍：5 层是过度工程,两类覆盖主要场景;层次可后续加,表结构（`kind` 列）已留扩展位。

**J-8｜记忆 / 知识向量用 Postgres pgvector,不引外部向量库**
背景：可用 Milvus / Qdrant。决策：M0 用 pgvector。取舍：已有 Postgres,pgvector 在 M0 规模够用、零新增运维面;规模到瓶颈再迁（§ 1.2）。嵌入维度 1536（OpenAI `text-embedding-3-small` 量级,具体模型在 J.3 PR 定,表用 `Vector(1536)`)。

**J-9｜有状态执行环境 = 临时算力 + 持久卷,不走 CRIU**
背景：要"还原用户环境"。备选：(a) CRIU 容器进程快照;(b) 持久卷 + 重启容器。决策：(b)。取舍：CRIU 复杂脆弱、跨内核 / 镜像版本易碎;对话态归 checkpointer、文件态归持久卷,进程态无需快照。

**J-10｜per-user 热沙盒会话 + 空闲 TTL 释放**
背景：per-user 持久 agent 要求"用户活跃时沙盒保持热（连续消息免冷启动）、静默后释放算力"（memory:target-product-form,2026-05-18 用户确认）。J.15 首版曾改为"per-run 即销毁、只持久卷",经用户确认该决策丢失了"活沙盒复用"需求 —— 此 ADR 为修订版。
备选：(a) 容器 hibernate 状态机,空闲 `docker stop` → `start`;(b) per-run 即销毁,无热路径;(c) per-user 热会话,空闲超 TTL 销毁、下条消息冷启动重建。决策：(c)。
取舍：(a) 与 held-pipe 冲突 —— `docker run -i` 持管道,停 / 起同一容器无法重连,要改得重做 Stream F 传输核心。(b) 算力释放最彻底但每个 run 冷启动,丢了连续消息的热复用。(c) held-pipe 的 runner 协议本就支持一容器多次 exec —— 会话跨 run 保持 `IN_USE` 即热复用,reaper 按 `last_used_at + session_idle_ttl_s`（默认 15min）回收即"hibernate";销毁后下条消息冷启动挂暖卷,持久卷保证零数据损失。热会话长期占配额槽,故 `default_max_sandboxes` 上限抬高。

**J-11｜artifact 由 agent 显式登记,不自动扫工作区**
背景：工作区会有大量中间文件。决策：`save_artifact` 工具显式登记。取舍：自动扫会把临时文件当产物,噪声大;显式登记语义清晰、可版本化。

**J-12｜sub-agent = agent-as-tool,父子单向委派树;成本护栏走结构化深度而非 token 预算**
背景：多 agent 可做黑板 / 横向协作;失控派生需护栏。决策：M0 只做 agent-as-tool 父→子顺序委派树;护栏 = 构建期深度上限 3 × 每 agent `max_iterations`。取舍：agent-as-tool 复用现有 `ToolRegistry` / 取消链 / 审计,零新增基建;横向协作复杂度高、M0 不需要。原计划"父 token 预算下钻、子拿 30%"**移除** —— helix 无运行期 token 预算(仅月级 `TokenBudgetLedger`),无从下钻;深度 × `max_iterations` 已让全递归树 LLM 调用数结构化有界。orchestrator 无法解析 `agent_ref`,故 control-plane 注入 `ChildAgentBuilder` 回调进 `ToolEnv`。并行扇出 / 子进度流式推迟 M0 后。

**J-13｜RAG = 工具检索(不自动注入)的生产级 RAG 设施**
背景：RAG 可自动在每次调用前注入检索结果;原计划做"够用 RAG",排除 rerank / 混合检索 / 文档运维 / 强切块。决策(经两轮评审):(1) `knowledge_search` 工具,agent 按需查 —— 不自动注入(污染上下文、不可控、浪费 token);(2) 扩为**生产级 RAG 设施** —— 原则"功能可少、能力不可弱":文档可列举/重摄取/删除(只能追加无法运维,属刚需)、摄取异步化且状态可查、强解析(pymupdf4llm + MarkItDown)、token 计量的结构/语义/表格感知切块(per-KB 可配)、混合检索(向量 + Postgres 全文 RRF 融合 → LLM-rerank)。取舍:rerank 复用现有 LLM、无新依赖,未配则优雅退化纯向量;中文全文检索用应用侧 jieba 分词 + `tsvector('simple')`,免 Postgres 扩展。有原则的边界仍守 —— 单一 pgvector(不引 Milvus)、不做 GraphRAG(另一范式)、知识库租户共享(per-user 私有知识另立特性)。

**J-14｜多模态输入 = 能力解析式双路,仅图像输入,M0 不做生成输出**
背景：多模态含输入 + 输出;输入侧又有"图像进 content block、整轮交多模态模型"（A）与"`ask_image` 工具解耦、主模型不看像素"（B）两种架构。决策：(1) J.6 只做**图像输入** —— 非图像文件走 J.15 持久工作区 / J.5 知识库,生成输出推迟 M1;(2) A / B **不二选一** —— 由 agent 构建期按 `ModelSpec.supports_vision`（manifest 显式声明）决议:视觉主模型走 A（content block,零损、可跨模态）、文本主模型走 B（`ask_image` + `vision:` 块声明的 VL 模型,主推理模型不降级）。取舍：helix 多租户平台同时面对统一多模态模型（Claude / GPT）与拆分模型族（Qwen3-Max 文本强 + Qwen-VL 单独且弱）—— 纯 A 让拆分模型租户含图轮推理降级,纯 B 让统一模型租户有损、无跨模态;双路按租户能力决议,每类租户都不变弱（原则"功能可少、能力不可弱"）。VL 模型走 `vision:` manifest 块而非 J.11 路由规则 —— 路由换的是"节点跑哪个模型",VL 是"工具背后的模型",两个轴;故 J.6 不依赖 J.11。

**J-15｜人在回路用 LangGraph 原生 `interrupt()`**
背景：可自建暂停机制。决策：用 LangGraph `interrupt()` + 现有 checkpointer。取舍：`interrupt` 与 checkpointer 暂停 / 恢复天然契合,不重造轮子。

**J-16｜skill 进化产物默认 draft + 需显式启用**
背景：hermes agent 自主创建 skill loop。决策：agent author/refine 的 skill 默认 `draft`,需人工启用或经 J.8 审批才 `active`。取舍：企业平台不能让 agent 静默改自己能力面;draft 闸门保留人工 / 审批控制。

**J-17｜skill 库限租户内,不做跨租户 marketplace**
背景：skill 可跨租户共享成 marketplace。决策：M0 skill 库租户隔离。取舍：跨租户共享涉及信任 / 安全审查,M1+;租户内复用已满足核心价值。

**J-18｜scheduler M0 单副本,无选主**
背景：scheduler 多副本需选主 / 分布式锁。决策：M0 单副本。取舍：M0 单体部署,单副本够;分布式 scheduler 推 M1（§ 1.2）。

**J-19｜J.12 只到"策划数据集",不含训练**
背景：学习闭环终点可到微调。决策：J.12 交付 trajectory 采集 + 数据集策划,训练 / 微调推 M2+。取舍：训练管线是独立大工程;先把数据闭环建好,J.13 eval 即可消费,微调可后续接。

**J-20｜J.13 排最后,eval-set 格式与 J.12 统一**
背景：eval 要覆盖 J.1–J.14 全部能力。决策：J.13 收尾,逐能力写 eval 场景;`eval_dataset` 表 J.12 / J.13 共用。取舍：能力没实现完无法写其 eval 场景;统一格式避免两套数据 schema。

---

### 2026-05-20 未交付项审计补充（J-21 ~ J-29）

> 按 [memory:complete-not-minimal](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_complete_not_minimal.md) + [memory:no-design-choice-disguise](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_no_design_choice_disguise.md) 红线对 J 剩余 8 项的 (c) 维度补强。每条 Mini-ADR 都指向 § 20 的具体修订点。

**J-21｜J.4 sub-agent trajectory 单独记录 + budget telemetry 回传父**
背景：J-12 原文只覆盖 cancellation / 深度上限 / agent-as-tool 接口；trajectory 单独记录 + budget 统计性回传未列。决策：(1) 子 agent run 各自调 L7 `TrajectoryRecorder.record()`，落 ObjectStore 单独 key（`{prefix}/{tenant}/{outcome}/{date}/{sub_thread_id}.jsonl`）；(2) 子 agent 结束时把 iteration_used / llm_call_count / wall_clock_ms 汇总写入 `ToolResult.meta`，父 audit 链可见。取舍：(c) 红线 —— 没 trajectory 就 J.13 eval 时整树黑盒；没 budget telemetry 就父无法做成本归因。

**J-22｜J.5 RAG 执行层拆 4 PR + 加 SLO 列表**
背景：J-13 原文已是 (c) 强版（生产级 RAG 设施），但单 PR ~5000 LOC 会撑爆 "一 PR 一子任务" 原则。决策：J.5 实施拆 4 个独立 PR：J.5a 数据层 + 异步摄取 / J.5b 解析 + 切块 / J.5c 混合检索 + rerank / J.5d 文档运维 API；同时锁定 SLO baseline（摄取延迟 P95 / 文档 ready 时间 / 查询 P95 / 重摄取幂等性 / recall@k）。取舍：拆颗粒度不增范围；K12 memory recall eval gate 模板可复用为 J.5 recall SLO。

**J-23｜J.7 M0 仅做 J.7a 静态启用，J.7b 进化 + code 字段推 M1+**
背景：J-16/J-17 原文虽然有 draft 闸门，但未明确 skill 可观测、冲突合并语义、code 字段执行边界 3 个安全 footgun。决策：(1) M0 仅做 J.7a —— skill = prompt 片段 + tools 子集（**不含 code 字段**）；静态启用 / 版本化 / draft 闸门保留；(2) J.7b 进化（`author_skill` / `refine_skill`）+ code 字段执行边界推 M1+；(3) M0 J.7a 内必含 skill telemetry（调用频次 / 错误率 counter）+ 冲突合并语义文档化（prompt 拼接顺序 = manifest 声明顺序，tools 集合并 conflict 拒绝构建）。取舍：(c) 红线 —— 把"code 字段"和"skill 进化"硬塞 M0 = 安全债；J.7a 已有独立价值。

**2026-05-21 J.7a 启动前 deer-flow 对比调研修订**（用户复审三轮，详见 `.claude/plans/witty-hugging-widget.md`）：J-23 M0 范围扩 8 项（同维度 (c) 红线补强）：

1. **Admin CRUD API**（`POST/PUT/PATCH/GET /v1/skills` + cursor 分页 + filter）—— 否则 M0 只能 SQL 直写，不可 dogfood
2. **`.skill` ZIP import/export**（精简结构：`skill.yaml` + `prompt.md` + `tools.txt`；无 supporting files；zip slip 防护 + 10 MiB 解压上限 + 16 文件上限）
3. **`name@version` 版本固定**（`AgentSpec.skills` 元素允许 `"foo"` 绑 latest active 或 `"foo@3"` pin；pin archived 允许，pin 不存在 → build 422）—— 强 reproducibility
4. **Prompt `<skill>` XML 包裹**（(c) 红线）—— 防 skill 内容 prompt injection
5. **Admin 写入期 regex deny-list + size cap**（轻量正则；LLM moderation 推 M1）
6. **Skill 元数据扩字段**：`description` / `category` / `required_models`（typed DB 列；build 期校验 agent.model 在 required_models 内）
7. **Skill discovery `GET /v1/skills?status=&category=` + cursor 分页**（否则 H.4 user UI 不可用）
8. **`AuditAction` 加 `SKILL_CREATE` / `SKILL_VERSION_CREATE` / `SKILL_STATUS_CHANGE`** + `ResourceType` 加 `"skill"`

完整 M1+ J.7b backlog（agent 进化 / code 字段 / lazy loading / LLM moderation / public 内置库 / supporting files / per-thread 启停 / UI 元数据 + per-thread/A/B/canary 推 M2+）见 ITERATION-PLAN § M1-K Agent skill 进化。详细 § 15 已按本次修订重写。

**J-24｜J.8 审批超时 fallback + audit trail + Admin UI 审批面板接入 + agent 主动请求路径（M0 必含）**
背景：J-15 原文写 "M0 不做超时 / 异步通知" —— 按 [[no-design-choice-disguise]] 不允许把"无超时 run 永远占 checkpointer 槽"包装成设计选择。决策：(1) M0 审批必含**默认 24h 超时 fallback**（manifest 可配 `policies.approval_timeout_s`，超时自动 reject + audit）；(2) 审批 trail 进 `audit_log` schema：审批人 / 时间 / 决策 / 修改入参（新 `APPROVAL_REQUESTED` / `APPROVAL_DECIDED` action）；(3) Admin UI H.3 必含审批面板接入（J.8 仅交 API + audit，UI 代码由 H.3 stream 做）；(4) **2026-05-22 helix-vs-deer-flow 对比新加**：agent 主动请求路径 —— 新 `ask_for_approval` builtin 工具让 agent 在运行期不确定决策点主动请求人确认（与声明式 `PolicySpec.approval_required_tools` 门控并存；共享同一套 interrupt/resume；唯一区别是 reject 语义 —— 门控 reject 否决整个 run，agent 主动请求 reject 仅否这次询问 agent 继续）；(5) `ApprovalRequest.reason_kind` 枚举（borrow deer-flow 5 类型：policy_gate / missing_info / ambiguous_requirement / approach_choice / risk_confirmation）—— Admin UI 按类型过滤 + audit 分析。取舍：(c) 红线 —— 无超时 = 资源泄漏 + 用户感知"agent 卡死"；无 audit trail = 不可追溯；无 UI = 审批门只能 API 操作；agent 主动请求是 deer-flow 真优能力（agent 自主性），按 [[complete-not-minimal]] 拉进 M0。

**J-25｜J.9 artifact lifecycle + quota + audit + MIME/XSS；病毒扫描 (a) 推 M2**
背景：J-11 原文只覆盖版本化 + RLS + supervisor 读取，lifecycle / quota / 病毒扫描 / audit / download MIME 五个维度未列。决策：(1) M0 加 artifact 保留期（manifest 可配 / 默认 90 天）+ DELETE / PATCH / versions API + 卷满 / 用户超 quota 时的清理策略；(2) 下载频次 + 体积配额接入 Stream C.5 `QuotaService`；(3) **Audit trail 三态**（`ARTIFACT_SAVE` / `ARTIFACT_DELETE` / `ARTIFACT_UPDATE`）；(4) **2026-05-21 helix-vs-deer-flow 对比新加**：Download endpoint MIME-aware Content-Type + XSS 防护（HTML / SVG / 主动内容强制 `Content-Disposition: attachment`；白名单驱动 MIME 推断；`X-Content-Type-Options: nosniff`）—— 当前 `octet-stream` 一刀切是 (c) 红线设计纪律缺口，按 [memory:complete-not-minimal] 必加；(5) 病毒扫描显式 **(a) 推 M2**（M0 用户 = 同公司风险低，但**必须显式决策**，不留空）。取舍：(c) 红线 —— 无 lifecycle 卷会爆；无 quota 单个用户能拖垮平台；无 audit J.14 cross-tenant 测试无锚点；无 XSS 防护 HTML artifact 是 stored XSS 通道。

**J-26｜J.10 触发器 failure handling + quota + event 源 + persistence 4 条**
背景：J-18 原文只覆盖单副本推 M1+ + webhook 认证，failure handling / scheduler quota / event 源选型 / APScheduler 持久性未列。决策：(1) failed trigger run → K7 模式的 DLQ 重试（backoff 1m→5m→30m→2h→6h，5 次失败入死信）；(2) scheduler quota 接入 Stream C.5（单用户 / 单租户最大 cron 数）；(3) trigger event 源选型 = PG NOTIFY（M0 单 control-plane 副本下足够，M1+ 多副本时考虑 outbox 表）；(4) APScheduler 必须 `SQLAlchemyJobStore` 持久化到 PG，control-plane 重启不丢 cron tick。取舍：(c) 红线 —— 四项缺一 trigger 系统就是弱版。

**2026-05-22 J.10 启动前 deer-flow 对比修订（Mini-ADR J-42）**：(3) `event` 触发器 + PG NOTIFY **整体推 M1** —— M0 无具体消费场景，cron + webhook 已覆盖两个具体需求；(4) APScheduler + `SQLAlchemyJobStore` **弃用** —— 改 scheduler 轮询 `agent_trigger` 表（该表本就为 webhook / CRUD 而建，APScheduler jobstore 与之双真相源），`croniter` 仅做 cron 数学。(1) DLQ 重试不变（K.K7 范式）；(2) scheduler quota 改为创建时直接 `count_cron_by_tenant` 校验，不走 `QuotaService` —— 触发器数是「当前计数」上限，不适配 `QuotaService` 的预留 / 速率模型。详见 J-42 + § 16.6 / § 16.7。

**J-27｜J.12 与 L7 trajectory 分工修剪 —— J.12 不再写 trajectory PG 表**
背景：L7 已经把 trajectory 写 ObjectStore（JSONL / ShareGPT / 4 outcome 分流，PR #202 已合）；J-19 原文还要写 `trajectory` PG 表 + `after_llm_call` 中间件 = 重复实现。决策：修订 J.12 分工 —— L7 ObjectStore trajectory = **eval / 训练数据底座**（完整 messages，J.13 离线 eval 消费）；J.12 PG `eval_dataset` 表 = **策划后的 dataset**（人工 / 规则筛选 trajectory + expected 标注）；J.12 不再写 `trajectory` PG 表，middleware 改为"读 L7 ObjectStore + 关联 G.6 feedback → 策划"。取舍：(c) 红线 —— 重复实现违反"功能可少、能力不可弱"的反面（不是弱，是冗余）。

**J-28｜J.13 拆 3 子项：J.13a baseline (M0) / J.13b 在线采样 (M1) / J.13c CI 回归门 (M1)**
背景：J-20 原文工作量超大（14 套 eval 场景 + 在线采样 LLM-judge + CI 回归门 + 配额 + flakiness），M0 内难以一次性交付。决策：拆 3 个子项：(1) **J.13a 逐能力 eval 场景集**（M0 必交，锁定 canonical agent baseline，作为 Stream M Gate 锚点）；(2) **J.13b 在线采样 + LLM-judge 配额 + budget cap**（M1 早期）；(3) **J.13c CI 回归门 + flakiness 缓解**（N 次重跑 + 阈值软门设计，M1 早期 / 合并到 M2-D）。取舍：(c) 框架达标但**通过拆分而非缩减能力**保住 M0 进度。

**J-29｜J.15 volume quota + backup + at-rest encryption（生产级数据保护）；跨 host (b) 推 M1-A**
背景：J-9/J-10 原文只覆盖热会话生命周期 + TTL reaper，volume 自身的数据保护三个维度全缺。决策：(1) volume quota（单用户工作区最大 GB，manifest 可配 / 默认 10 GB）+ 准入检查（写文件前查 quota）；(2) volume backup（每天对所有 active user_workspace 卷 rsync 到对象存储 + 保留 7 天 + restore 演练 runbook）；(3) volume at-rest 加密（依赖宿主机 LUKS / 云厂托管磁盘加密，落实 P0 #9）；(4) 跨 host 调度 **(b) 推 M1-A**（与 sandbox warm pool 同期，M0 单机部署合理）。取舍：(c) 红线 —— 用户数据丢失 / 单用户爆磁盘 / 卷无加密都是产品级事故，M0 不能下移。

---

### 2026-05-20 J.6 完成补审（J-30 ~ J-35）

> J.6 5 个 PR（#167-#171）已合 main，但上一轮 audit 跳过（在进行中）+ 本轮 audit 复述"不重评"。事后补审 9 维 (c) 红线发现 6 维 gap。下面 6 条 Mini-ADR 是 (c) 红线补强 + 1 条 (a) 显式决策，配套 ITERATION-PLAN.md § Stream J 的 J.6.补强-1~4 + J.6.决策-5。

**J-30｜J.6 upload quota 接入 Stream C.5 QuotaService**
背景：`uploads.py` 当前无 quota 调用（端点完全免配额），单租户 / 单用户可上传无限图片拍死对象存储。J-14 原文未列。决策：扩 `QuotaService` 加 `image_upload_count_30d` + `image_storage_bytes` 两类计费（与 token / sandbox 平行），upload 端点准入检查 + ReservationReaper 同款释放语义；超 quota 返回 429（与 B.2 限流同 status）。取舍：(c) 红线 —— 上传是 expensive 资源（对象存储 + 后续 base64 进 LLM 调用），无 quota 是平台级安全债。

**J-31｜J.6 upload 单独 audit trail（不复用 SESSION_WRITE）**
背景：`uploads.py:122-124` 注释说"audit middleware 负责"但端点自身无 `emit()` 调用，运行时 `runs.py:259-268` 只记 `SESSION_WRITE input_len`，不带图元数据。决策：新增 `AuditAction.IMAGE_UPLOAD`（同 K1 `API_KEY_ROTATE` 模式），uploads 端点直接 emit；字段：`tenant_id` / `user_id` / `session_id` / `file_size` / `mime_type` / `object_key` / `sha256` / 上传时间 / 调用方主体（user / api-key / m2m）。取舍：(c) 红线 —— 上传是用户主动写操作，不审计 = 不可追溯 = 不合规。复用 `SESSION_WRITE` 字段不够（缺图元数据 + 与文本输入混在一起难查）。

**J-32｜J.6 image lifecycle（保留期 + DELETE API + TTL 清理）**
背景：上传后对象存储**永久留存** —— 无 DELETE API / 无 TTL 扫描 / 无 session / user 销毁时清理 hook。决策：(1) 新表 `image_upload`（id / tenant_id / user_id / session_id / object_key / size_bytes / sha256 / created_at / deleted_at 软删除）登记每张图；(2) `DELETE /v1/uploads/{id}` 端点（soft-delete + audit）；(3) per-tenant 默认保留期 90 天（manifest 可配 `policies.image_retention_days`），retention-cleanup-job 加 image 维度扫描（复用 K3 模式）；(4) session / user 销毁时级联 soft-delete 该 scope 内所有 image。取舍：(c) 红线 —— 同 J.9 artifact lifecycle 同款（J-25），不能图片无 lifecycle 而 artifact 有。

**J-33｜J.6 Path B VL 模型 fallback chain**
背景：`vision.py:113` 无 catch + retry，`VisionSpec.model` 单 provider 硬失败；L8 OAuth refresh 未在 VL 路径覆盖。决策：(1) `VisionSpec` 从 `model: ModelSpec` 升级为 `model: ModelSpec` + `fallbacks: list[ModelSpec] = []`（与 E.11 LLM Provider Fallback Chain 同结构）；(2) `AskImageTool` 内复用 `LLMRouter._call_one` 同款错误归类 + fallback 推进逻辑（不能让 VL 路径独造一套）；(3) L8 `OAuthCapableProvider` 协议天然覆盖 VL provider（VL adapter 同属 LLMProvider 子类）。取舍：(c) 红线 —— VL provider 故障率本就比主 LLM 高（Qwen-VL / GLM-4V 稳定性弱于 Qwen-Max / GLM-4），单 provider 硬失败 = 拆分模型租户故障率成本不可接受。

**J-34｜J.6 EXIF strip + multi-image 集成测试**
背景：(i) 上传图直接 `store.put()` 原样落对象存储，**EXIF 不剥离** —— 是 metadata exfil 通道（GPS 坐标 / 设备指纹 / 时间戳）；(ii) `test_runs_image_refs.py` 测了 Path A 多图块组装，但缺 "单 message 携带 3 张图 + Path A/Path B 端到端" 的集成测试。决策：(1) `uploads.py` 在 `store.put()` 前用 Pillow `Image.open(...).save(..., exif=b"")` 剥 EXIF（mime allowlist 已限到 png/jpeg/webp/gif，对 SVG 不开口）；(2) 新增 `test_multimodal_e2e_multi_image.py` 集成测试：upload 3 张图 → Path A 一次 send 三张 + Path B 三次 `ask_image` 调用、断言 EXIF 已剥 + 三张图都 land。取舍：(c) 红线 —— EXIF metadata exfil 是低成本 zero-day 攻击面，剥离成本 < 100 LOC + Pillow 已是依赖；multi-image 测试覆盖是 J-22 J.5 "SLO baseline" 同款 production-grade 测试纪律。

**J-35｜J.6 NSFW / 恶意 SVG / PII-in-images 显式 (a) 推 M2（不留空决策）**
背景：(i) 用户上传图的 NSFW / 恶意 SVG 内容扫描；(ii) 图里的 PII（身份证 / 票据 / 病历）走 OCR + redact —— 两件都是另一套范式（NSFW 需 vision 模型 / SVG 解析需 SVG-aware sanitizer / OCR 需独立 pipeline），M0 用户 = 同公司风险低，但按 [memory:no-design-choice-disguise] 不允许留空。决策：**显式标 (a) 推 M2**，配套：(1) STREAM-J-DESIGN.md § 1.2 Out-of-scope 表加这两条；(2) ITERATION-PLAN.md J.6 行加 J.6.决策-5 链到本 ADR；(3) M2-D（Eval Gate + 持续改进 pipeline）扩范围承接 NSFW 检测能力 / 或独立开新 stream `M2-H 图像内容安全`。取舍：(a) 显式决策不是弱版 —— SVG sanitizer 是另一套 lib 范式（如 bleach + svg-sanitizer）、OCR 是独立 pipeline、NSFW 模型是平台决策（接外部 API 还是自训）；M0 用户 = 同公司同信任域，攻击成本 vs 收益不平衡。

---

### 2026-05-20 J.15 设计 PR 补充（J-36）

> J.15 § 9 设计修订时识别：原设计只覆盖 volume 创建 + 热会话生命周期，未覆盖 volume 销毁路径，与 J.9 artifact lifecycle (J-25) / J.6 image lifecycle (J-32) 范式割裂。本 ADR 配套 § 9.5.4 补强。

**J-36｜J.15 volume lifecycle —— soft-delete → archive → hard delete 三档（M0 必含）**
背景：原 § 9 设计只覆盖 volume 创建 + 挂载 + 热会话生命周期，**未明确 volume 销毁路径**。用户软删除 / `user_workspace` 整 row 删除时无级联卷归档 → 卷变成 orphan 长存磁盘，违反 J.9 artifact lifecycle（J-25）+ J.6 image lifecycle（J-32）同款产品级 lifecycle 范式。决策：lifecycle 三档（active → soft-deleted → archived），soft-delete 触发后由 reaper 起 archive job tar.zst 到 ObjectStore（同 J-29 第 2 项 backup pipeline 复用），90 天后 hard delete archive + row；恢复 API 推 M1。新建 `lifecycle.py` + 迁移 0026 加 `deleted_at` + `archived_object_key` 列 + 三档 audit action（`WORKSPACE_DELETE` / `WORKSPACE_ARCHIVE` / `WORKSPACE_HARD_DELETE`）。取舍：(c) 红线 —— 平台必须有用户数据清理路径（合规 + 磁盘运维），与 J.9 artifact / J.6 image lifecycle 范式一致避免割裂。

---

### 2026-05-21 J.13a 设计 PR 补充（J-37 ~ J-39）

> J.13a 设计 PR 把 § 18 从"四点纲领"骨架重写为"7 已交付能力 × per-cap metric + baseline 制品"实施设计。本组 3 条 Mini-ADR 锁住三个关键决策：metric 类型矩阵、baseline 文件交付物形态、LLM-judge 模型选型。

**J-37｜J.13a per-capability metric 类型矩阵 —— deterministic 优先，LLM-judge 限用在 J.1**
背景：每个能力都可以套 LLM-judge 评分，但 judge 本身有误差 + 抖动 + 成本。决策：per-cap metric 按能力性质选 deterministic 还是 LLM-judge —— J.3 用 `recall@k`（K12 已落，K12 模板复用）/ J.11 / J.14 / J.15 用 `pass-rate`（隔离类 J.14 强制 1.00）/ J.2 用 `pass-rate`（修正率 + 假阳率双指标）/ J.6 用 `pass-rate`（Path A + Path B 双套）/ 只有 J.1 plan_execute 任务覆盖度无 deterministic 指标，用 `pass-rate（结构合法）+ LLM-judge mean（1-5 分）` 双指标。取舍：(c) 红线 —— "为用 judge 而用 judge"是把 eval baseline 从锚点变成噪声源；J.14 / J.15 这类安全类指标必须确定性 100%，不容许 judge 概率性判定。

**J-38｜J.13a baseline 文件 = checked-in YAML + 能力 PR 同步更新（不另起 PG 表）**
背景：baseline 可以放 PG `eval_dataset` 表（J.12 共用）或 checked-in YAML。决策：M0 用 checked-in YAML —— `tools/eval/baselines/m0_gate_baseline.yaml`，每次能力 PR 着陆同步更新 baseline 行；Stream M Gate Exit Criteria 直接读这个文件。备选：PG 表 + 后台 job。取舍：(b) 工程选择 —— M0 单 control-plane 副本 + 单 dev 团队，checked-in YAML 走 git review 流程更易看 diff / 审 baseline 变化 / 在 PR 内一起评审；PG 表方案需要额外 API + admin UI 才好运维，M0 投入不值。J.13c 着陆时升级为"CI 周跑 + commit baseline 更新 PR"自动化路径。`eval_dataset` PG 表仍由 J.12 管，定位是"策划数据集源"，不是"baseline 制品"。

**J-39｜J.13a LLM-judge 模型 = Haiku 4.5 + temperature=0.0 + N=3 重跑**
背景：LLM-judge provider 选型直接影响 baseline 成本 / 稳定性。决策：(1) judge 模型 `claude-haiku-4-5-20251001`（Haiku 4.5）—— 90% 大模型质量、3x 成本优势，M0 周跑 ~7 能力 × ~20 case × 3 重跑 ≈ 420 调用 judge cost 可承受；(2) `temperature=0.0` 强制 judge 确定性，否则 baseline 抖动违背"baseline 是锚点"语义；(3) N=3 重跑 + 多数票（pass-rate）/ 平均（连续值）作为 flakiness 缓解 M0 最小版本；(4) judge 走独立 `ModelSpec.role="eval_judge"`，不与生产 model 路由 / fallback 链耦合；(5) CI 内不跑真 judge（mock provider），周跑用真 judge。取舍：(c) 红线 —— judge 选型直接决定 baseline 可信度，必须显式锁定模型 ID + 参数；judge cost 不能成为不跑 baseline 的理由（用 Haiku 而非 Opus）；J.13c 升级到 confidence interval 是这条 ADR 的演进路径。

---

### 2026-05-21 J.4-补强-2 设计 PR 补充（J-40）

> J.4 核心 5 PR 已交付（#151 / #152 / #154 / #220 / #221）；本 ADR 把原"并行 fan-out 推 M2-B"反向：M0 内交付。背景是用户阅读 DeerFlow 2.0 断点续跑文章后提出"现阶段把能力做强、不推 M2"——两 Explore agent 验证 DeerFlow 真相（线程池 + 父 tool 串行轮询，并非 asyncio.gather 并发）+ 审 helix-agent gap（`AgentState` 不存 sub-agent 调用历史、L.L6 `plan_stages` 因 `is_read_only=False` 串行）。修订 § 11 并加本 ADR。

**J-40｜J.4 并行 fan-out + cycle detection + global deadline + fan-in 聚合（提前到 M0）**
背景：J-12 / J-21 原文明确"M0 是顺序委派树，并行扇出推 M2-B"。这是把"工程实现限制"包装成"设计选择"——子 agent 之间彼此独立（不共享 sandbox session、各拿独立 thread_id / run_id），理论可并发但 L.L6 `plan_stages` 因 `ToolSpec.is_read_only=False` 把每个 SubAgentTool 拆 stage 串行执行。按 [[no-design-choice-disguise]] 不允许"弱能力 = 设计意图"。决策：M0 内交付五项：(1) `ToolSpec.is_parallel_safe: bool = False` 新字段，`SubAgentTool.spec.is_parallel_safe = True`，`plan_stages` 同 stage 收集 `is_parallel_safe=True` 的 tool_calls 经 `asyncio.gather(return_exceptions=True)` 并发跑（复用 L.L6 `MAX_TOOL_WORKERS=5` 信号量）；(2) `agent_factory._detect_subagent_cycle` 构建期 DFS 经 `spec_store` 拒环（A→B→A 即使深度未达 `MAX_SUBAGENT_DEPTH` 也立刻抛 `AgentFactoryError`，深度上限作纵深保留）；(3) Global deadline 经 `config["configurable"]["deadline_at"]` + `ToolContext.deadline_at` 跨层传播（父建立、子继承不重置），子超时直接 `RunCancelledError`；(4) Fan-in 聚合：每个 SubAgentTool emit `SubAgentInvocation` 经 `ToolResult.state_updates` 写新通道 `AgentState.subagent_invocations`（`Annotated[list[SubAgentInvocation], add]` reducer，参照 `reflections`）；新增 6 态 `SubagentStatus` 枚举（`PENDING / RUNNING / COMPLETED / FAILED / CANCELLED / TIMED_OUT`）+ `SubAgentInvocation` frozen dataclass 入 `helix-protocol`；(5) 错误语义：`return_exceptions=True` 让一个子失败不连带 cancel 兄弟（父 LLM 看 partial 结果），父 cancel → 所有在跑子收取消传播 + invocation 全标 cancelled。取舍：(c) 红线 —— "顺序"意味着 N 个子委派 wall_clock 加总（用户感知 N 倍延迟），无法做 multi-agent orchestration 任何形式；M0 用户场景虽暂时不暴露 N 路并发委派需求，但 canonical agent (per-user 持久 agent) 与 J.10 trigger / J.8 HITL 一旦组合就会需要并发委派，等 M2-B 重做的成本远高于 M0 内做（要回头改已上线 SubAgentTool 接口 + tools_node 调度）。设计先行（[[design-first-iteration]]），4-PR 拆分见 ITERATION-PLAN J.4 行。

---

### 2026-05-22 J.8 收尾复审補強（J-41）

> J.8 6-PR 链（#242–#247）合并后复审 `RunManager` 持久化缺口。本 ADR 把 `runs/manager.py` 头注释 + § 2.6 + § 14.3a 记录的"`runs` 表 M1+"决策**部分翻案** —— 裸 run 生命周期层提前到 M0，排队 / 重试子系统仍留 J.10。

**J-41｜runs 持久化拆分 —— 裸 run 生命周期表 `agent_run` 提前到 M0，排队 / 重试子系统留 J.10**
背景：`RunManager`（`helix-runtime`，adapted from deer-flow）M0 砍掉 deer-flow 的持久 `RunStore` 背板，纯进程内 `dict` + 5 分钟 TTL（`cleanup(delay=300)`）；原决策记于 `runs/manager.py` 头注释 + § 2.6 + § 14.3a："`runs` 表 M1+"。J.8 收尾复审发现两处后果不是"明确推迟"，而是 [[no-design-choice-disguise]] 命中的隐性弱能力：(1) **`GET /v1/sessions/{tid}/runs/{rid}` 契约名实不符** —— docstring 写"a run's status"，实测只对 5 分钟内的活 run + 暂停 run（`agent_approval` 兜底）可靠，一个昨天成功的 run 查询返回 `404 run not found`，5 分钟 TTL 单独即触发不必重启；(2) **`agent_approval` 被迫成为"runs 表形状的洞"** —— 复制 `run_id / thread_id / tenant_id / user_id / requested_at` + 自带平行 `status`，只因没有 `agent_run.status` 可依赖，J.10 还要再建 `trigger_run`，三张表都挂 run 的影子。

决策：把"runs 表"拆成两个交付物，分属不同里程碑。**① 裸 run 生命周期表 `agent_run`（提前到 M0）** —— 持久化 `RunRecord` 现已在内存里全部追踪的字段，设计零猜测：

```
迁移 0032_agent_run —— agent_run 表，tenant RLS
  id            UUID PK            (= run_id)
  tenant_id     UUID NOT NULL
  user_id       UUID NULL
  thread_id     UUID NOT NULL
  status        TEXT NOT NULL      CHECK status IN (pending/running/success/error/timeout/interrupted/paused)
  on_disconnect TEXT NOT NULL      CHECK on_disconnect IN (cancel/continue)
  is_resume     BOOL NOT NULL DEFAULT false
  error         TEXT NULL          —— ERROR/TIMEOUT 的失败详情（nullable）
  created_at    TIMESTAMPTZ NOT NULL
  updated_at    TIMESTAMPTZ NOT NULL
  finished_at   TIMESTAMPTZ NULL   —— 进入终态的时刻
  索引：tenant_id / thread_id / (thread_id, status) 部分索引 WHERE status IN (pending, running)
  RLS：current_setting('app.tenant_id') GUC（与 agent_approval / image_upload / artifact 同款）
```

`RunStore` ABC + `InMemoryRunStore` + `SqlRunStore`（`helix-persistence`，与 `ApprovalStore` 同目录同范式）；`RunManager.__init__` 加可选 `store: RunStore | None`，`create / set_status / cancel` 内部镜像写库 —— `sse.py` 6 处 `set_status` + `runs.py` 2 处 `create` 全部自动获持久化；`cleanup(delay=300)` 只 pop 内存不删库行；`set_status` 加可选 `error: str | None`，终态时一并写 `error` + `finished_at`；`GET .../runs/{rid}` 把当前"`runtime_run_status` → None → 404"路径改为"内存未命中读 `SqlRunStore`"，消除 404 不对称；`agent_approval.run_id` 维持裸列（FK-light，J-1a），逻辑父表 = `agent_run`。**② run 排队 / multitask 策略 / 重试 / DLQ（留 J.10，Mini-ADR J-26）** —— 设计未锁（multitask 策略选项集、重试退避列形态、DLQ 独立表 vs 状态）；J-26 已规划 trigger failed run 的 DLQ 重试，run 排队压力本就来自 trigger。M0 不提前做 —— 凭空加 `multitask_strategy / retry_count / next_retry_at` 列 = 给不存在的子系统建空 schema（CLAUDE.md § 2），且 J.10 真正设计时大概率改形状反而多一次 migrate；J.10 着陆时经 expand-contract 给 `agent_run` 加列即可，表已在。

取舍：(c) 红线 —— `GET` 名实不符 + `agent_approval` 平行 status 是"把工程缺失包装成设计选择"，① 必须翻案进 M0；① 成本可控 —— 字段全部已知，与 `artifact` / `agent_approval` 同款 row→DTO store 范式，约 1 个实施 PR；② 留 J.10 不是"推迟弱能力" —— 它的设计本就属 J.10 那一轮（J-26），提前做是猜测 + 注定返工，违背 [[design-first-iteration]]；连带收益 —— `trigger_run`（J.10）从此是 `trigger_id + run_id` 链接行不再重扛 run 生命周期，H.3 run 列表 UI 的查询底座由 `agent_run` 提供（表 M0 就位，列表 endpoint 随 H.3 交付）。配套修订：§ 14.3a 注明 `agent_approval` 逻辑父表 `agent_run` 已在 M0；`runs/manager.py` 头注释"`runs` 表 M1+"改指向本 ADR；ITERATION-PLAN J.8 行加收尾复审補強子项。2-PR 拆分见 ITERATION-PLAN。

---

### 2026-05-22 J.10 启动前 deer-flow 对比（J-42）

> J.10（调度 / 触发）启动前按惯例做 deer-flow 对比。本 ADR 锁三个 J.10 设计决策，配套 § 16 重写 + Mini-ADR J-26 (3)(4) 修订。

**J-42｜J.10 调度机制 = 轮询 `agent_trigger` 表；M0 = cron + webhook（event 推 M1）；webhook 鉴权 = 每触发器独立 secret**
背景：J.10 启动前按惯例做 deer-flow 对比（`/Users/mac/src/github/deer-flow`）。结论：**deer-flow 基本无调度 / 触发系统** —— `after_seconds` / `webhook` 字段是死代码，唯一相关的 channel `MessageBus`（IM 入站消息 → run）属 helix Stream A（渠道）范畴、非 J.10。helix J.10 设计严格领先，对比无可拉进 M0 的能力，但暴露 3 个 J.10 设计决策需定。

决策：(1) **cron 调度 = 轮询 `agent_trigger` 表 + `croniter`**，非 APScheduler —— helix 无论如何需要 `agent_trigger` 表承载 webhook 触发器 + 用户 CRUD；APScheduler 自带 `SQLAlchemyJobStore` 会与之形成双真相源（每次增删改启停双写、易漂移）。改用单副本后台 worker 轮询（复用 `ReservationReaper` 范式），`croniter` 仅做 cron 表达式数学。修订 Mini-ADR J-26 (4)。(2) **M0 = cron + webhook 两类**，`event` 触发器 + PG NOTIFY 推 M1 —— `event`（内部事件触发，如另一 run 完成）M0 无具体消费场景，更像 workflow 串联原语；cron + webhook 已覆盖"定时跑"+"外部 HTTP 事件跑"两个具体需求，且都按生产级全做（DLQ / quota / 鉴权齐全）。修订 Mini-ADR J-26 (3)。(3) **webhook 鉴权 = 每触发器独立 secret token** —— § 16.2 原文"复用 Stream C API Key"会让外部 webhook 源（GitHub 等）持有全权 helix API key（权限过宽）；改为每 webhook 触发器一个独立 secret（哈希存储、明文仅创建时返回一次），泄漏只影响那一个触发器。HMAC 载荷签名推 M1。

取舍：(1) 单一真相源 > 双写同步，省 APScheduler 重依赖，轮询粒度（~60s）对 cron 足够；(2) `event` 推 M1 是 [[no-design-choice-disguise]] 合规的范围裁剪 —— cron / webhook 不削弱、`event` 显式记录边界，非"弱版伪装设计选择"；(3) 最小权限优于复用宽凭证，secret token 比 HMAC 简单且对 M0 足够（HMAC 防重放 M1 补）。6-PR 拆分见 ITERATION-PLAN。

---

### 2026-05-22 J.12 启动前 deer-flow 对比（J-43）

> J.12（学习 / 反馈闭环）启动前按惯例做 deer-flow 对比。本 ADR 锁两个 J.12 设计决策,配套 § 17 全重写（按 Mini-ADR J-27 删 `trajectory` PG 表 + 补 agent-scoped 数据模型）。

**J-43｜J.12 策划机制 = 规则候选 + 人工策划；候选生成 = 后台 CurationWorker 预生成；`eval_dataset` 按 (tenant, agent) 归集**
背景：J.12 启动前按惯例做 deer-flow 对比（`/Users/mac/src/github/deer-flow`）。结论：**deer-flow 无学习 / 反馈闭环** —— 无 trajectory→dataset→eval 管线、无策划层、无 eval harness。其 feedback 采集层（`FeedbackRow` + REST CRUD + thumbs UI）成熟,但 helix G.6 `feedback` 表已对等;其 `RunJournal` 事件日志 ≈ helix L7 trajectory ObjectStore（PR #202）。helix 两个输入端（trajectory + feedback）均已就位,对比无可拉进 M0 的能力,但暴露 J.12 设计决策需定。

决策：(1) **策划机制 = 规则候选 + 人工策划** —— 后台 worker 按规则（👎 feedback / `failed`·`max_steps` outcome / 👍 feedback）从 trajectory + feedback 排出候选,人工 review 后 promote（标注 expected → `eval_dataset` 行）/ dismiss。备选「纯人工」（无规则排序、人工直接浏览 trajectory）实质把"学习闭环"退化成一张 CRUD 表 —— 能力弱;备选「纯规则自动写入」（规则直接写 `eval_dataset`）的致命问题是 👎 run 的正确 expected 无人能机器生成、数据集必然含噪声。规则筛（收窄人工面）+ 人工判（保证 expected 质量）是唯一兼顾覆盖与质量的组合。(2) **候选生成 = 后台 `CurationWorker` 预生成** —— 单副本后台 worker（复用 `TriggerScheduler` / `ReservationReaper` 范式）周期扫 trajectory ObjectStore + feedback,规则命中即 upsert `curation_candidate` 表;策划 API 直读该表。备选「按需实时计算」在 trajectory 对象增多后会让策划视图请求随 `list_prefix` 线性变慢 —— 预生成把扫描成本挪出请求路径。(3) **`eval_dataset` 按 (tenant, agent_name) 归集,非 per-instance** —— helix 目标形态是 per-user 持久 agent 实例,feedback / trajectory 采集天然 per-instance（挂 `thread_id`）,但学习闭环改的是 agent 定义、改一次所有实例受益,"回归测试单个实例"不成立。`curation_candidate` / `eval_dataset` 加 `agent_name`（+ `agent_version`）维度,由 `thread_meta`（`thread_id → agent_name / user_id`）解析,无需新采集;`user_id` / `trajectory_key` 留作溯源。

取舍：(1) 规则候选 + 人工策划 = [[no-design-choice-disguise]] 合规 —— 不是"砍掉自动化的弱版",而是 expected 标注本质需要人（👎 run 没有机器可得的 ground truth）;规则承担"收窄"、人工承担"判定",各司其职。(2) 后台 worker 多一张 `curation_candidate` 表 + 一个需看护的进程,但换来策划 API 请求路径与 ObjectStore 规模解耦 —— 与 J.10 选轮询 scheduler 同理。(3) (tenant, agent) 归集是 per-user 持久 agent 形态的必然推论 —— per-instance 个性化是 J.3 记忆的事,J.12 是离线 agent / 流程改进。配套修订：§ 17 全重写、Mini-ADR J-27 补"后台 worker + agent-scoped"细化、Mini-ADR J-19 确认仍成立（不含训练）。5-PR 拆分见 ITERATION-PLAN。

---

## 20. 与现有文档的关系

- 上游：[architecture/08-AGENT-CAPABILITY-ASSESSMENT](../architecture/08-AGENT-CAPABILITY-ASSESSMENT.md)（缺口来源 + 决策）、[ITERATION-PLAN § Stream J](../ITERATION-PLAN.md)。
- 平行：J.14 是 Stream C（多租户）的深化、J.15 是 Stream F（沙盒）模型的演进 —— 相关设计见 [STREAM-C-DESIGN](./STREAM-C-DESIGN.md)、[STREAM-F-DESIGN](./STREAM-F-DESIGN.md)。
- 下游：每个 J.x 子项 PR 的局部细化设计;canonical per-user 持久 agent 的 manifest 设计（Stream J 完成后)。
