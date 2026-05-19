# Stream J — Agent Harness 能力补全（设计先行）

> 落实 [docs/ITERATION-PLAN.md](../ITERATION-PLAN.md) § Stream J。
> 执行 [architecture/08-AGENT-CAPABILITY-ASSESSMENT](../architecture/08-AGENT-CAPABILITY-ASSESSMENT.md) § 5 的决策 —— 把 26 维能力矩阵的 14 个认知 / harness 缺口补到生产级。
>
> **覆盖范围**：J.1–J.15 共 15 个子项。本文件是 Stream J 的总设计 —— 锁定总体架构、跨切面数据模型、实现顺序与依赖、以及每个子项的范围边界 / 架构 / 接口 / 整合点 / Mini-ADR。每个子项的 PR 在此基础上做更细的局部设计（设计先行规则递归适用）。

设计先行规则（[memory:feedback_design_first_iteration.md](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_design_first_iteration.md)）：
所有总体架构 / 跨切面接口 / Mini-ADR 必须在编码前于本文件锁定；每个子项 PR 只执行本文件对应章节 + 其局部细化设计。

> **对标纪律**：deer-flow / hermes-agent 作能力基线，校准"成熟长什么样"+ 找差距。**结论是独立设计,不照抄**（[memory:general-platform-positioning](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_general_platform_positioning.md)）。

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
| **J.4** | Sub-agent / 多智能体 | 缺失 | agent-as-tool —— 子 agent 作为 `Tool` 派生;父子 token 预算 + 取消链穿透;深度上限 | J-12 |
| **J.5** | 知识 / 检索（RAG）| 缺失 | `knowledge_search` 工具 + `knowledge_chunk` 表（复用 J.3 的 pgvector 基建）;按租户隔离 | J-13 |
| **J.6** | 多模态输入 | 骨架 | 图像 / 文件输入 —— 扩 `HumanMessage` content block;多模态 handler;经 J.11 路由到 vision 模型 | J-14 |
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
| **中间件链**（4 锚点）| `helix-runtime/runtime/middleware/` | J.3 记忆注入（复用 `DynamicContextMiddleware`）、J.6 多模态 handler（`before_llm_call`）、J.11 路由（`around_llm_call`）、J.12 trajectory 采集（`after_llm_call`）|
| **工具注册表** | `orchestrator/tools/` `ToolRegistry` | J.4 sub-agent-as-tool、J.5 `knowledge_search`、J.9 artifact 工具、J.1 `update_plan` 工具 |
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
    subagent_depth: int                                    # J.4 —— 递归深度,防失控
```

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
| `0021_knowledge_base` | `knowledge_base`、`knowledge_chunk`（pgvector）| J.5 | tenant RLS |
| `0022_skill` | `skill`、`skill_version` | J.7 | tenant RLS |
| `0023_trigger` | `trigger`、`trigger_run` | J.10 | tenant RLS |
| `0024_trajectory` | `trajectory`、`eval_dataset` | J.12 J.13 | tenant RLS |

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
Wave 3 ── J.4 sub-agent ─ J.5 RAG ─ J.6 多模态   ◄┘ (J.4 依赖 J.1 取消/预算;J.6 依赖 J.11)
                                                │
Wave 4 ── J.8 人在回路 ─ J.10 调度 ─ J.7 skill ─ J.12 学习闭环 ─ J.13 eval
                                                  (J.12 依赖 J.13 数据集格式;J.13 收尾,验所有子项)
```

### 3.2 关键依赖

- **J.14 是硬前置**：J.3 / J.15 / J.9 的表都带 `user_id`,必须先有 `tenant_user` 注册表 + scope 约定。J.14 与 Wave 1 可并行（Wave 1 不碰 per-user 数据）。
- **J.9 依赖 J.15**：产物落在持久工作区,工作区由 J.15 建。
- **J.4 依赖 J.1**：sub-agent 委派需要父 agent 先能规划"把子任务交给谁";且复用 J.1 引入的取消 / token 预算下钻。
- **J.6 依赖 J.11**：多模态输入要路由到 vision 模型,vision 路由是 J.11 的一部分（对标 hermes `image_routing.py`）。
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

- `ModelSpec.routing: RoutingSpec` —— 按**步骤类别**选模型：`planner` 步用强模型、`tool_result_summarize` 用便宜模型、含图像输入路由到 vision 模型。
- `LLMRouter` 在 `fallback`（已有）之外加**路由策略**：调用前按当前步骤类别 + 消息模态选 provider handle。
- **边界**：路由按**声明式规则**（步骤类别 → 模型),不做基于 token 预测的动态难度估计（过度工程）。

### 7.3 接口与数据模型

```python
class RouteRule(BaseModel):
    when: Literal["planning", "reflection", "tool_summarize", "vision", "default"]
    model: ModelSpec                        # 复用 ModelSpec(可带自己的 fallback)
class RoutingSpec(BaseModel):
    rules: list[RouteRule]
```

`LLMRouter` 选 handle：先按 `routing.rules` 命中步骤类别 → 再走该模型的 `fallback` 链。步骤类别由调用方（图节点）经 `MiddlewareContext.payload["route_class"]` 传入。

### 7.4 整合点

`agent_factory.py` `build_llm_router()`、`helix-runtime` 的 `LLMRouter`、`around_llm_call` 中间件链、`helix-protocol` `ModelSpec`。

> **对标**：hermes `image_routing.py`（vision 路由）。helix 推广为通用步骤类别路由,vision 只是其中一条规则。

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

### 9.1 现状

沙盒 per-run 临时无状态（Mini-ADR F-2）：每次 `acquire()` 全新 `docker run`、`--tmpfs /workspace`，run 结束即销毁。无持久工作区 —— 用户上一轮产出的文件下一轮就没了。

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

- **agent-as-tool**：子 agent 包装成一个 `Tool`,父 agent 像调工具一样委派子任务。
- 子 agent 用 `agent_factory.build_agent()` 装配,拿独立 `thread_id`（父 thread 的子）。
- **隔离与安全**：父的 `CancellationToken` 穿透到子（复用现有协作式取消链）;父的 token 预算下钻分配给子;`AgentState.subagent_depth` 限递归深度（防失控派生）。
- **边界**：M0 是父→子单向委派树,不做子 agent 间横向通信 / 黑板协作。

### 11.3 接口与数据模型

```python
class SubAgentSpec(BaseModel):              # AgentSpecBody.subagents
    name: str
    agent_ref: str                          # 引用另一个 AgentSpec(name@version)
    description: str                        # 暴露给父 agent 当工具描述
    token_budget_fraction: float = 0.3      # 父预算的占比上限
MAX_SUBAGENT_DEPTH = 3
```

`SubAgentTool(Tool)`：`call()` 内 `build_agent(子 spec)` → 跑子 run（带派生的 cancellation token + 预算）→ 子 run 最终答案作 `ToolResult.content` 回父。

### 11.4 整合点

`tools/`（`SubAgentTool` + 注册）、`agent_factory.py`（递归装配,depth 检查）、`helix-runtime` 取消链 / token 预算（下钻）、`helix-protocol` `SubAgentSpec`。

> **对标**：deer-flow `subagents/executor.py`、hermes `delegate_tool`。helix 取 agent-as-tool —— 与现有 `ToolRegistry` 无缝;委派即工具调用,自动获得现有的取消 / 预算 / 审计基建。

---

## 12. J.5 — 知识 / 检索（RAG）

### 12.1 现状

无向量库 / 检索。agent 只能靠 `web_search` 工具拿外部信息,无法 ground 在租户私有知识上。

### 12.2 设计与边界

- **检索作工具,非自动注入**（Mini-ADR J-13）：`knowledge_search` 工具,agent 按需查;不在每次 LLM 调用前自动塞检索结果（自动注入污染上下文、不可控）。
- 后端复用 J.3 的 pgvector 基建：`knowledge_chunk` 表（per-租户,文档切块 + 向量）。
- 知识库摄取（文档 → 切块 → 嵌入 → 入库）是一条独立 ingest 路径,M0 提供 control-plane API + 简单切块,不做高级 chunking 策略。
- **边界**：评估 08 把 RAG 标为"非 table-stakes,三参考项目皆弱"。helix 做**够用的 RAG**：单一 pgvector 后端 + 工具检索,不引 Milvus / 不做 rerank / 不做 GraphRAG。

### 12.3 接口与数据模型

```python
# 迁移 0019_knowledge_base
class KnowledgeBaseRow(Base):               # 表 knowledge_base
    id: UUID
    tenant_id: UUID                         # 知识库租户级共享(非 per-user)
    name: str
class KnowledgeChunkRow(Base):              # 表 knowledge_chunk
    id: UUID
    kb_id: UUID
    content: str
    embedding: Vector(1536)
    source_doc, chunk_index: ...

class KnowledgeSpec(BaseModel):             # AgentSpecBody.knowledge
    knowledge_base_refs: list[str]
```

### 12.4 整合点

`tools/`（`knowledge_search` builtin）、`helix-persistence` `memory` 子包（共享 pgvector 检索代码）、control-plane（知识库 ingest API）、`helix-protocol` `KnowledgeSpec`。

> **对标**：deer-flow / hermes 都靠外部 search / FTS5,RAG 弱。helix 做最小可用 RAG,不过度投入。

---

## 13. J.6 — 多模态输入

### 13.1 现状

消息体留了槽,无 handler。图像 / 文件输入进不来。

### 13.2 设计与边界

- 输入侧：`HumanMessage` 用 LangChain content block（`{type: "image"|"text"}`）承载图像;文件输入落 J.15 持久工作区,消息里给路径引用。
- 多模态 handler 中间件（`before_llm_call`）：规范化 content block、校验大小 / 类型、含图像时在 `payload` 标 `route_class="vision"` 供 J.11 路由。
- **边界**：仅**输入**侧。多模态输出（生成图 / 音频）推迟（§ 1.2）。支持图像 + 文本文件,音视频推迟。

### 13.3 接口与整合点

无新表。`HumanMessage` content 走 LangChain 既有结构;新 `MultimodalInputMiddleware`（`helix-runtime/middleware/`）;control-plane run API 接受 multipart / base64 图像入参 → 转 content block;J.11 vision 路由。

> **对标**：deer-flow `view_image`、hermes 全 vision 路由。helix 把多模态做成**输入规范化中间件 + 路由规则**,与 J.11 协同。

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

**J-12｜sub-agent = agent-as-tool,父子单向委派树**
背景：多 agent 可做黑板 / 横向协作。决策：M0 只做 agent-as-tool 父→子委派树。取舍：agent-as-tool 复用现有 `ToolRegistry` / 取消链 / token 预算 / 审计,零新增基建;横向协作复杂度高、M0 不需要。深度上限 3 防失控派生。

**J-13｜RAG = 工具检索,不做 LLM 调用前自动注入**
背景：RAG 可自动在每次调用前注入检索结果。决策：`knowledge_search` 工具,agent 按需查。取舍：自动注入污染上下文、不可控、浪费 token;工具检索让 agent 自主决定何时需要外部知识。

**J-14｜多模态仅输入侧,M0 不做生成输出**
背景：多模态含输入 + 输出。决策：J.6 只做图像 / 文件输入。取舍：输入是 harness 能力面的 table-stakes;生成输出是模型能力 + 产物管线,推迟 M1。

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

## 20. 与现有文档的关系

- 上游：[architecture/08-AGENT-CAPABILITY-ASSESSMENT](../architecture/08-AGENT-CAPABILITY-ASSESSMENT.md)（缺口来源 + 决策）、[ITERATION-PLAN § Stream J](../ITERATION-PLAN.md)。
- 平行：J.14 是 Stream C（多租户）的深化、J.15 是 Stream F（沙盒）模型的演进 —— 相关设计见 [STREAM-C-DESIGN](./STREAM-C-DESIGN.md)、[STREAM-F-DESIGN](./STREAM-F-DESIGN.md)。
- 下游：每个 J.x 子项 PR 的局部细化设计;canonical per-user 持久 agent 的 manifest 设计（Stream J 完成后)。
