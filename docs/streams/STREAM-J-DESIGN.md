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
| **J.12** | 学习 / 反馈闭环 | 缺失 | trajectory 采集 + feedback（G.6）→ 策划数据集 → eval / 微调输入;离线数据驱动（区别于 J.7 运行期自改）| J-19 |
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
| **中间件链**（4 锚点）| `helix-runtime/runtime/middleware/` | J.3 记忆注入（复用 `DynamicContextMiddleware`）、J.11 路由（`around_llm_call`）、J.12 trajectory 采集（`after_llm_call`）|
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
| `0025_trajectory` | `trajectory`、`eval_dataset` | J.12 J.13 | tenant RLS |

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

### 9.1 现状（2026-05-20 设计 PR 修订）

**已落地（M0 内已交付）**：
- ✅ **热会话 + TTL reaper**（Mini-ADR J-10）：`supervisor.acquire(user_id)` 取/建 per-`(tenant,user)` 会话；`release` 对会话沙盒 = no-op 留热（对无 user_id 临时沙盒销毁）；`exec` 刷新 `last_used_at`；reaper 按 `last_used_at + session_idle_ttl_s`（默认 15min）回收（`supervisor.py:119-291` + `reaper.py:39-58` + `store.py:92-126`）
- ✅ **持久卷创建 + 登记**：`user_workspace` 表 + `sandbox_instance.user_id / workspace_id`（迁移 0018）+ `sandbox_instance.last_used_at`（迁移 0020）；runtime_provider 用 `--volume {workspace_volume}:/workspace` 挂载（`runtime_provider.py:109-122`）；首次 acquire 自动创建卷
- ✅ **per-session 锁 + held-pipe**：同会话并发 exec 经 `asyncio.Lock` 串行化（`supervisor.py:211-218`）；held-pipe 一容器多 exec 协议完整

**未落地（本设计 PR 锚定 M0 补强清单）**：
- ❌ **Volume quota 准入**（J-29 第 1 项）：`user_workspace.size_bytes` 字段有记录但 supervisor 写文件前无 enforcement；单用户能爆磁盘
- ❌ **Volume backup pipeline**（J-29 第 2 项）：daily rsync 到对象存储 + 7 天保留 + restore runbook 全无；卷数据无 DR 路径
- ❌ **At-rest 加密文档化**（J-29 第 3 项）：宿主机 LUKS / 云厂托管磁盘加密的依赖关系未文档化（落实 P0 #9）
- ❌ **Volume lifecycle / 销毁**（新 Mini-ADR J-36）：用户软删除 / `user_workspace` 整 row 删除时无级联卷归档 → hard delete 流程
- (b) 跨 host 调度 → 推 M1-A（与 sandbox warm pool 同期）

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

### 10.1 现状

run 只吐 SSE 文本流;沙盒里生成的文件随沙盒销毁即丢。用户拿不到 agent 产出的文件 / 文档 / 代码。

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

### 10.4 整合点

`orchestrator/tools/`（`save_artifact` / `list_artifacts` builtin）、`sse.py`（`artifact` 事件类型）、`sandbox-supervisor`（工作区文件读取端点 —— 临时只读容器挂卷）、control-plane run API（产物列表 + 内容下载端点,下载时回填 size/sha256）、J.15 持久卷（内容载体）。

> **对标**：deer-flow `present_file`、hermes `file_tools`。helix 加**版本化 + 表登记**,因为 per-user 持久形态下产物要跨会话留存可追溯。

---

## 11. J.4 — Sub-agent / 多智能体

### 11.1 现状

单体 agent,无委派。复杂任务无法拆给专长子 agent。

### 11.2 设计与边界

- **agent-as-tool**：每个声明的子 agent 包装成一个**命名 `SubAgentTool`**,父 agent 像调工具一样委派子任务（命名工具给 LLM 比单一 `task` 工具更清晰的选择信号）。
- 子 agent 用 `agent_factory.build_agent()` 递归装配,拿独立 `run_id`;子 `thread_id` 由父 `run_id` + 工具 `call_id` 派生。
- **隔离与安全**：父的 `CancellationToken` 经 `ToolContext.cancellation_token` 穿透到子（复用现有协作式取消链)。
- **成本护栏走结构化,非 token 预算** —— helix 没有运行期 token 预算（只有月级 `TokenBudgetLedger` + 事后 `TokenReservation` 记账),故原计划"父预算下钻、子拿 30%"移除。改用:递归深度 ≤ `MAX_SUBAGENT_DEPTH`(3) × 每 agent `workflow.max_iterations` —— 全递归树 LLM 调用数结构化有界。
- **深度在构建期计数**：`build_agent(subagent_depth=...)`,顶层 depth=0;depth 达上限的 agent 构建时**不注册任何 `SubAgentTool`**(结构化递归终止)。跨 manifest 环(A↔B)由此兜底。
- **`ChildAgentBuilder` 回调**:orchestrator 无法解析 `agent_ref`(`AgentSpecStore` 只在 control-plane)。control-plane 注入一个 `async (*, tenant_id, name, version, depth) -> BuiltAgent` 回调进 `ToolEnv`,内部 `AgentSpecStore.get` + 递归 `build_agent` + 深度键缓存。
- **边界**：M0 是父→子单向、**顺序**委派树,不做子 agent 间横向通信 / 黑板协作;并行扇出、子进度 SSE 流式推迟 M0 后。

### 11.3 接口与数据模型

```python
class SubAgentSpec(BaseModel):              # AgentSpecBody.subagents 的条目
    name: str                               # 暴露给父 LLM 的工具名(snake_case)
    agent_ref: str                          # 引用已部署 AgentSpec —— "name@version"
    description: str                        # 工具描述,父 LLM 据此决定委派

MAX_SUBAGENT_DEPTH = 3
ChildAgentBuilder = async (*, tenant_id, name, version, depth) -> BuiltAgent
```

校验(`AgentSpec._check_subagents`):拒绝自引用(子 `agent_ref` 指向本 agent)、重复工具名、与已声明 builtin 工具名冲突;`agent_ref` 必须 `name@version` 格式。

`SubAgentTool(Tool)`：`call()` 内经 `ChildAgentBuilder` 构建子 agent → `child_graph.ainvoke(...)`(同步跑在工具 `call()` 内)→ 子 run 末条 `AIMessage` 作 `ToolResult.content` 回父。

### 11.4 整合点

`tools/`（`SubAgentTool` + `ChildAgentBuilder` 协议 + `_register_subagents`）、`agent_factory.py`（`build_agent(subagent_depth=)` 递归装配 + depth 上限）、`tools/registry.py`（`ToolContext.cancellation_token`）、`control-plane`（`make_child_agent_builder` + 深度键缓存 + lifespan 接线）、`helix-protocol` `SubAgentSpec`。

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

### 14.1 现状

run 一旦启动,人无法中途审批 / 纠偏。危险操作（如 sub-agent 派生、外部写操作）无人工门。

### 14.2 设计与边界

- 用 LangGraph 原生 `interrupt()`：`approval` 节点在危险操作前 `interrupt`,run 暂停并 checkpoint。
- control-plane 暴露暂停态：`GET /runs/{id}`（含 `pending_approval`）+ `POST /runs/{id}/resume`（批准 / 拒绝 / 修改后继续）。
- 危险操作按 `PolicySpec` 声明门控（哪些工具 / 操作要审批）。
- **边界**：M0 是同步审批门（run 挂起等人）;不做异步通知 / 超时自动决策（M1）。

### 14.3 接口与数据模型

```python
class ApprovalRequest(BaseModel):           # AgentState.pending_approval
    node: str
    action_summary: str
    proposed_args: dict
class ApprovalDecision(BaseModel):          # resume API 入参
    decision: Literal["approve", "reject", "modify"]
    modified_args: dict | None
```

### 14.4 整合点

`graph_builder/builder.py`（`approval` 节点 + `interrupt()`）、`state.py`（`pending_approval`）、checkpointer（暂停态持久,本就支持）、control-plane run API（resume 端点）、`PolicySpec`（门控声明）。

> **对标**：hermes 中断 + 审批门、deer-flow `ask_clarification`。helix 直接用 LangGraph `interrupt` —— 与现有 checkpointer 暂停 / 恢复机制天然契合。

---

## 15. J.7 — Skill + skill 进化

### 15.1 现状

无 skill 概念。每个 agent 的能力全靠 manifest 静态声明,无可复用、可习得的能力单元。

### 15.2 设计与边界

- **skill = 可复用能力包**：具名,含 prompt 片段（如何做某类任务）+ 工具子集 + 可选代码片段。租户内 skill 库。
- agent 经 `AgentSpec.skills: list[str]` 启用 skill;装配时 skill 的 prompt / 工具并入。
- **skill 进化（有界）**：agent 可经 `author_skill` / `refine_skill` 工具,把"这次摸索出的有效做法"沉淀成新 skill 或精化已有 skill → 写 `skill` 库（新 `skill_version`)。**边界**：进化 = 受控地写 skill 库行,**不是无界自我代码修改**;新 skill 默认 `draft` 状态,需显式启用（或经 J.8 审批）才生效。
- 拆分：J.7 局部设计可拆 2 PR —— (a) skill 概念 + 库 + 静态启用;(b) skill 进化（author/refine + draft 工作流）。

### 15.3 接口与数据模型

```python
# 迁移 0020_skill
class SkillRow(Base):                       # 表 skill
    id: UUID
    tenant_id: UUID
    name: str
    status: Literal["draft", "active", "archived"]
    latest_version: int
class SkillVersionRow(Base):                # 表 skill_version
    id: UUID
    skill_id: UUID
    version: int
    prompt_fragment: str
    tool_names: list[str]
    code: str | None
    authored_by: Literal["human", "agent"]
```

### 15.4 整合点

`agent_factory.py`（装配时展开 `skills` → 并入 prompt / 工具）、`tools/`（`author_skill` / `refine_skill`）、新 `skill` 模型 + store、`helix-protocol`（`AgentSpecBody.skills`）。

> **对标**：deer-flow skill installer + 进化、hermes 自主创建 loop。helix 关键独立设计：**进化产物默认 draft + 需启用**,防无界自改失控 —— 企业平台不能让 agent 静默改自己的能力面。

---

## 16. J.10 — 调度 / 触发

### 16.1 现状

纯 `POST /runs` 同步请求-响应。agent 不能定时跑 / 被事件触发。

### 16.2 设计与边界

- 三类触发器：`cron`（定时）、`event`（内部事件,如另一 run 完成）、`webhook`（外部 HTTP 入站）。
- control-plane 内一个 **scheduler 组件**（M0 单副本,APScheduler 或等价）扫 `trigger` 表 → 到点 / 命中即发起 run。
- 触发式 run 与请求式 run 共用执行路径,只是发起来源不同;触发记录进 `trigger_run`。
- **边界**：M0 单副本 scheduler（无选主 / 无分布式锁,§ 1.2);webhook 入站要过认证（复用 Stream C API Key）。

### 16.3 接口与数据模型

```python
# 迁移 0021_trigger
class TriggerRow(Base):                     # 表 trigger
    id: UUID
    tenant_id, user_id: UUID
    agent_ref: str
    kind: Literal["cron", "event", "webhook"]
    config: dict                            # cron 表达式 / 事件名 / webhook 路径
    enabled: bool
class TriggerSpec(BaseModel):               # AgentSpecBody.triggers
    kind: Literal["cron", "event", "webhook"]
    config: dict
```

### 16.4 整合点

control-plane 新 `scheduler` 模块 + lifespan 启停、run API（触发式发起路径)、`trigger` 模型、`helix-protocol` `TriggerSpec`。

> **对标**：hermes 完整 cron 系统、deer-flow 无。helix 做 cron + event + webhook 三类,够覆盖"非请求-响应 agent"。

---

## 17. J.12 — 学习 / 反馈闭环

### 17.1 现状

G.6 已采 👍/👎 feedback,但无闭环 —— 数据躺着,不回流改进 agent。

### 17.2 设计与边界

- **trajectory 采集**：`after_llm_call` 中间件记录完整 run trajectory（输入 / 计划 / 工具调用 / 输出),写 `trajectory` 表。
- **数据集策划**：把 trajectory + feedback（G.6）关联 → 经筛选（如低评分 run、纠正过的 run）产出 `eval_dataset` 行 —— 格式与 J.13 共用。
- 用途：喂 J.13 的 eval-set（回归 + 能力评估）;为未来微调留数据。
- **边界**：J.12 只交付到"策划好的数据集",**不含训练 / 微调**（§ 1.2,M2+）。区别于 J.7 skill 进化 —— J.7 是运行期 agent 自改能力,J.12 是离线数据驱动的人 / 流程改进。

### 17.3 接口与数据模型

```python
# 迁移 0022_trajectory
class TrajectoryRow(Base):                  # 表 trajectory
    id: UUID
    tenant_id, user_id: UUID
    thread_id: str
    steps: JSONB                            # 规范化的 run 轨迹
    feedback_id: UUID | None                # 关联 feedback(G.6)
    created_at: datetime
class EvalDatasetRow(Base):                 # 表 eval_dataset(J.13 共用)
    id: UUID
    tenant_id: UUID
    name: str
    input: JSONB
    expected: JSONB | None
    source: Literal["golden", "trajectory", "regression"]
```

### 17.4 整合点

新 `TrajectoryMiddleware`（`after_llm_call`)、`feedback` 模型（G.6,关联)、`tools/eval`（G.4 数据集消费方)、新 `trajectory` / `eval_dataset` 模型。

> **对标**：hermes trajectory→dataset。helix 同思路,并把数据集格式与 J.13 eval 统一。

---

## 18. J.13 — eval 强化

### 18.1 现状

`tools/eval`（G.4 离线 harness）+ G.5 golden/regression 集 —— 三参考项目里最有意的 eval 故事,但 M0 为骨架级。

### 18.2 设计与边界

J.13 排**最后**,评估并落实升级（Mini-ADR J-20）：

1. **逐能力 eval 场景**：给 J.1–J.14 每项写 eval 场景（规划质量 / 反思有效性 / 记忆召回 / sub-agent 委派正确性 / 隔离不泄漏 …）—— 这是"26 维矩阵无缺口"的判定依据。
2. **在线采样 eval**：生产 run 按比例采样 → 自动跑评分（LLM-as-judge / 规则）→ 进 dashboard。
3. **CI 回归门**：golden/regression 集（含 J.12 策划的 `eval_dataset`）跑进 CI,回归即红。
4. eval-set 格式统一到 J.12 的 `eval_dataset` 表。

### 18.3 整合点

`tools/eval`（G.4 harness 升级）、`eval_dataset` 模型（J.12 共用)、CI（`.github/workflows/ci.yml` 加 eval 回归 job 或并入现有 `Test` job)、observability（在线 eval 指标进 Grafana)。

> **对标**：三项目 eval 都弱。helix 借 G.4/G.5 已有基建,J.13 把它从骨架推到生产级 —— 这是 canonical agent 的度量工具,也是 Stream J 验收的尺子。

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

**J-24｜J.8 审批超时 fallback + audit trail + Admin UI 审批面板接入（M0 必含）**
背景：J-15 原文写 "M0 不做超时 / 异步通知" —— 按 [[no-design-choice-disguise]] 不允许把"无超时 run 永远占 checkpointer 槽"包装成设计选择。决策：(1) M0 审批必含**默认 24h 超时 fallback**（manifest 可配，超时自动 reject + audit）；(2) 审批 trail 进 `audit_log` schema：审批人 / 时间 / 决策 / 修改入参；(3) Admin UI H.3 必含审批面板接入。取舍：(c) 红线 —— 无超时 = 资源泄漏 + 用户感知"agent 卡死"；无 audit trail = 不可追溯；无 UI = 审批门只能 API 操作。

**J-25｜J.9 artifact lifecycle + quota；病毒扫描 (a) 推 M2**
背景：J-11 原文只覆盖版本化 + RLS + supervisor 读取，lifecycle / quota / 病毒扫描三个维度未列。决策：(1) M0 加 artifact 保留期（manifest 可配 / 默认 90 天）+ DELETE / PATCH API + 卷满 / 用户超 quota 时的清理策略；(2) 下载频次 + 体积配额接入 Stream C.5 `QuotaService`；(3) 病毒扫描显式 **(a) 推 M2**（M0 用户 = 同公司风险低，但**必须显式决策**，不留空）。取舍：(c) 红线 —— 无 lifecycle 卷会爆；无 quota 单个用户能拖垮平台。

**J-26｜J.10 触发器 failure handling + quota + event 源 + persistence 4 条**
背景：J-18 原文只覆盖单副本推 M1+ + webhook 认证，failure handling / scheduler quota / event 源选型 / APScheduler 持久性未列。决策：(1) failed trigger run → K7 模式的 DLQ 重试（backoff 1m→5m→30m→2h→6h，5 次失败入死信）；(2) scheduler quota 接入 Stream C.5（单用户 / 单租户最大 cron 数）；(3) trigger event 源选型 = PG NOTIFY（M0 单 control-plane 副本下足够，M1+ 多副本时考虑 outbox 表）；(4) APScheduler 必须 `SQLAlchemyJobStore` 持久化到 PG，control-plane 重启不丢 cron tick。取舍：(c) 红线 —— 四项缺一 trigger 系统就是弱版。

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

## 20. 与现有文档的关系

- 上游：[architecture/08-AGENT-CAPABILITY-ASSESSMENT](../architecture/08-AGENT-CAPABILITY-ASSESSMENT.md)（缺口来源 + 决策）、[ITERATION-PLAN § Stream J](../ITERATION-PLAN.md)。
- 平行：J.14 是 Stream C（多租户）的深化、J.15 是 Stream F（沙盒）模型的演进 —— 相关设计见 [STREAM-C-DESIGN](./STREAM-C-DESIGN.md)、[STREAM-F-DESIGN](./STREAM-F-DESIGN.md)。
- 下游：每个 J.x 子项 PR 的局部细化设计;canonical per-user 持久 agent 的 manifest 设计（Stream J 完成后)。
