# helix-agent 当前实现状况报告（15 维度同口径）

> **本报告作用**：把 helix 当前实现按和 `hermes-deep-dive.md` 完全相同的 15 维度逐项盘点，作为后续 "helix vs Hermes gap analysis" 的**helix 这一边的事实底稿**。
>
> **范围声明**：
> - 仅描述 helix 当前实现的事实，**不**与 Hermes 做对比，**不**给"借鉴 / 应该补"清单 —— 那是下一份报告的工作。
> - 只评判事实，**不**评判 helix 设计的好坏。
> - 数据来源：源码 + `docs/ITERATION-PLAN.md`（用于标 M0/M1/M2/M3 阶段映射）+ `docs/streams/STREAM-*-DESIGN.md`（用于标"已设计未实现"vs"已实现"）。
> - 引用 stream 设计文档时显式标注 "(设计文档，非源码)"。
>
> **源码版本**：`helix-agent` @ `6e0e9edaa60b4fe91e3a00140ffeda64d766aec9`（HEAD on 2026-05-27）
> **报告生成日期**：2026-05-27
> **配套报告**：`docs/research/hermes-deep-dive.md`（同维度的 Hermes 事实底稿）

## 阅读指引

- **从第 0 章入**：因为 helix 是分布式微服务架构（与 Hermes 单进程 CLI 完全不同的形态），第 0 章会把整个 monorepo 的服务拓扑、依赖关系、当前所处的里程碑阶段讲清楚，后面 15 维度章节就不再重复解释。
- **每章统一 6 节模板**：
  1. **现状评级** — `生产级 / 部分实现 / 已设计未实现 / 不在 scope`（一句话定性 + ITERATION-PLAN 的 M0/M1/M2/M3 阶段标签）
  2. **设计立场** — helix 在这个维度的核心理念
  3. **关键代码路径** — 入口模块 / 关键类 / 关键函数，`file:line` 引用 + 简表
  4. **实现细节** — 数据结构、关键算法、配置项、扩展点（含代码摘录）
  5. **运行时行为** — 启动接入、调用链、状态流转（含时序/状态图）
  6. **局限与边界** — 客观说明 trade-off + 引 ITERATION-PLAN 写明"M1/M2/M3 计划补什么"
- **现状评级的含义**：
  - **生产级**：M0 已交付，零债 6 条核验过（无 TODO / 测试达标 / 文档同步 / 可观测齐全 / CI 全绿 / bug 不遗留），canonical agent 跑过 eval
  - **部分实现**：M0 交付了核心路径，但已知有维度未覆盖；剩下部分在 ITERATION-PLAN 显式有 M1/M2/M3 backlog
  - **已设计未实现**：有完整 stream 设计文档但代码未落
  - **不在 scope**：这是 helix 设计阶段做出的有意识取舍，**不是缺失**

---

# 第 0 章 — 整体架构概览

## 0.1 项目定位（来自 `docs/ITERATION-PLAN.md:7`）

> "Helix — 业务无关的多租户企业 Agent 执行引擎，用于替代 Dify。已有完整架构设计（Brain-Hands-Session 三层解耦）、4 阶段路线图（M0→M3）、24 项产品级 P0 基础设施清单"

这一行说清了 helix 的几个**根本性设计立场**：

1. **业务无关**：不绑定任何具体业务领域（不像 Dify 是对话产品），仅提供 agent 执行引擎；
2. **多租户企业级**：从第一天就考虑 tenant_id 全链路、RLS、quota；
3. **替代 Dify**：意味着 helix 的"客户"是希望自己控制 agent 运行栈、不想用 SaaS 的企业；
4. **Brain-Hands-Session 三层解耦**：控制面 / 编排引擎 / 沙箱执行物理隔离；
5. **4 阶段路线图**：M0 → M0→M1 Gate → M1 → M2 → M3，每阶段有 Exit Criteria。

这跟 Hermes 的"单进程、单 CLI、面向个人 power user"是**完全不同的产品形态**。后续每个维度的"评级"和"局限"都要在这个产品形态下读。

## 0.2 Monorepo 拓扑

```
helix-agent/                                        # 6e0e9ed @ 2026-05-27
├── packages/                                       # 4 个共享 Python SDK
│   ├── helix-common/                               # observability / metrics validator / lifecycle
│   ├── helix-persistence/                          # SQLAlchemy 2.0 + Alembic（migrations 0001..0038）
│   ├── helix-protocol/                             # Pydantic DTO 协议层
│   └── helix-runtime/                              # middleware chain / sandbox provider / secret store
│
├── services/                                       # 7 个独立部署的服务
│   ├── control-plane/                              # FastAPI — Manifest CRUD / SSE runs / IAM / Trigger
│   ├── orchestrator/                               # FastAPI + LangGraph — agent loop in-process（M0 不独立成镜像）
│   ├── credential-proxy/                           # aiohttp — 出站凭证注入
│   ├── sandbox-supervisor/                         # FastAPI — Docker + gVisor 容器生命周期
│   ├── audit-backup-worker/                        # 后台 — audit_log → S3 WORM
│   ├── event-log-archive-job/                      # 调度任务 — 半年后 event_log → S3
│   └── retention-cleanup-job/                      # 调度任务 — TTL 自动清理
│
├── apps/                                           # 前端
│   ├── admin-ui/                                   # React 19 + Vite + Antd 5
│   └── admin-ui-demo/                              # 设计阶段 mockup（H.1a）
│
├── tools/                                          # 工具脚本
│   ├── eval/                                       # eval harness + dataset 管理
│   ├── deploy/                                     # 蓝绿 + 加权 canary 脚本
│   ├── observability/                              # Prometheus rule / Grafana dashboard
│   ├── dev-certs/ / tls/                           # 本地开发 mTLS
│   └── persistence/                                # PG/audit restore drill
│
├── environments/                                   # dev.yaml / staging.yaml / prod.yaml
├── infra/                                          # docker-compose 全栈编排
└── docs/                                           # 架构 / 决策 / 设计 / runbook
    ├── ITERATION-PLAN.md                           # 921 行，主路线图
    ├── architecture/                               # 9 个顶层 + subsystems/
    ├── streams/                                    # 13 份 STREAM-*-DESIGN.md
    ├── adr/ / decisions/                           # 重大决策记录
    └── research/                                   # 本报告所在目录
```

## 0.3 代码量速查（仅源码，剔除 `.venv/`、`node_modules/`、生成产物）

| 项 | LOC |
|---|---|
| services 业务代码（7 服务 src/） | **29,389** |
| services 测试代码（tests/） | 30,896（**测试代码 > 业务代码**） |
| packages 业务代码（4 包） | **28,250** |
| tools/eval 等业务代码 | ~9,600 |
| **Python 业务代码合计** | **~67K LOC** |
| admin-ui `.tsx` | 12,730 |
| admin-ui `.ts` | 4,861 |
| **TypeScript 合计** | **~17.6K LOC** |

数据库 migration 数量：**38 个 Alembic 版本**（`packages/helix-persistence/migrations/versions/0001..0038`）。

> *说明*：业务代码 ≈ 测试代码这个比例反映 `docs/ITERATION-PLAN.md:74-78` 的"测试达标"硬要求（unit ≥ 85% / integration ≥ 70% 关键路径 / 无 skip / 无 xfail / 连跑 3 次稳定）。

## 0.4 关键技术栈

| 层 | 选型 | 关键证据 |
|---|---|---|
| 后端框架 | FastAPI + asyncio（Python 3.12） | 所有 service 入口 |
| 编排引擎 | **LangGraph**（StateGraph + PostgresSaver） | `services/orchestrator/src/orchestrator/runner.py:1-12` |
| ORM | SQLAlchemy 2.0 + Alembic | `packages/helix-persistence/migrations/` |
| 数据库 | PostgreSQL + **pgvector** + **Row-Level Security** | `migrations/versions/0021_knowledge_base.py` 等 |
| Checkpointer | `langgraph.checkpoint.postgres.PostgresSaver`（M0） | `services/orchestrator/src/orchestrator/runner.py:46-65` |
| 沙箱 OCI runtime | Docker CLI + **gVisor (runsc)** | `services/sandbox-supervisor/` |
| 对象存储 | S3-Compatible（MinIO dev / 阿里云 OSS prod） | `ADR-0004` |
| 凭据 | 阿里云 KMS Secrets Manager（M0）+ Vault（M1 评估） | `ADR-0007` |
| 认证 | OIDC + Keycloak + JWT；mTLS 服务间 | Stream C |
| 可观测 | Prometheus + Tempo + Loki + Grafana + Langfuse（自托管） | `infra/docker-compose.yml --profile observability` |
| 前端 | React 19 + Vite + Antd 5 + react-router-dom v7 | `apps/admin-ui/` |
| 队列 / 调度 | 自研（不用 APScheduler / Celery） | `services/control-plane/src/control_plane/scheduler.py` |

## 0.5 当前里程碑状态（来自 `docs/ITERATION-PLAN.md`）

| 阶段 | 状态 | 关键 streams |
|------|------|------------|
| Phase 0 — Pre-flight | ✅ 完成 | monorepo / CI / ADR / 测试基础设施 |
| **Phase M0 — Product-grade MVP** | ✅ **基本完成** | **Stream A-N 全部 14 个 stream 标 `[x]` 或主要子项 `[x]`** |
| M0→M1 Gate | 🟡 进行中 | canonical agent + eval baseline + dogfood 平行 30 天 |
| Phase M1 | ⏳ 未开始 | M1-A 沙盒池化、M1-K Skill 进化、M1-F Python 插槽 等 |
| Phase M2 | ⏳ 未开始 | Durable Execution / Multi-Agent fan-out / Memory archive |
| Phase M3 | ⏳ 未开始 | K8s + Helm + 内部 marketplace |

注意**单人 M0 估时 5-7 个月**，并且 Stream J（agent harness 补全）+ Stream K（capability hardening sprint）+ Stream L（Hermes-derived 单 turn 能力强化）三个 sprint 是后期补出来的：
- **Stream K（13 项 c 类弱版补强）** ✅ 2026-05-20 收尾
- **Stream L（8 项 Hermes-derived 能力，正是看了 Hermes 才识别的 gap）** ✅ 2026-05-20 收尾，PR 链 #198–#206
- **Stream J（14 项 agent harness 缺口）** 大部分子项 ✅ 完成，剩余 J.9 / J.15 等仍在推进

也就是说：**helix 当前已经把 Hermes 的 8 条单 turn 能力（L1-L8）作为生产级实现进了 main**，本报告会把这些显式标出。

## 0.6 ITERATION-PLAN 的两条硬性"自律"

读后面每个维度的"局限"小节前要知道：helix 团队对自己的开发流程有两条**硬性约束**（`docs/ITERATION-PLAN.md:39-97`）：

**迭代启动前置**（每个 stream 编码前必做 3 步）：
1. 架构设计 → 明确边界 / 数据流 / API / 失败模式 / 验证方案
2. 更新设计文档 → 已有模块改 `docs/architecture/`，新子系统新增 `subsystems/xx-*.md`，技术选型新增 ADR
3. 设计 self-review checklist

**迭代收尾标准**（每个 stream 视为完成前必满足 6 条）：
1. **代码干净** — 无 `TODO`/`FIXME`/`XXX`/`HACK`、无 `pass` / `raise NotImplementedError`
2. **测试达标** — unit ≥ 85%、integration ≥ 70% 关键路径、无 skip/xfail、连跑 3 次稳定
3. **文档同步** — `docs/architecture/` 与实现一致、ADR 合并、ITERATION-PLAN checklist 勾选
4. **可观测齐全** — emit metric + structured log + trace span、告警阈值已定义
5. **CI 全绿** — lint + mypy + test + 镜像构建 + 安全扫描全绿、CodeQL 无新增 high/critical
6. **bug 不遗留** — 要么本迭代修，要么明确写进下一迭代 checklist

这两条规则的意思是：**"已完成"的 stream 不允许带技术债**。每个维度的"现状评级"如果是"生产级"，意味着零债 6 条核验过；"部分实现"意味着 ITERATION-PLAN 里有显式 backlog。

## 0.7 服务拓扑（运行时调用关系）

```
            ┌──────────────────────────────────────────┐
            │           Browser (Admin UI)             │
            │       React 19 + Vite + Antd 5           │
            └────────────────┬─────────────────────────┘
                             │ HTTPS + OIDC PKCE
                             ▼
            ┌──────────────────────────────────────────┐
            │       nginx (TLS 终止 + 反向代理)         │
            └────────────────┬─────────────────────────┘
                             │
   ┌─────────────────────────┴─────────────────────────┐
   │                                                   │
   ▼                                                   ▼
┌───────────────────────┐                ┌──────────────────────────┐
│  control-plane        │   in-process   │  orchestrator (库形态)    │
│  FastAPI              │ ──────────────►│  LangGraph StateGraph    │
│  - Manifest CRUD      │ (M0 共进程；   │  - agent_node / tools_node│
│  - SSE runs            │  M1 拆服务)    │  - context compression   │
│  - IAM + RLS          │                │  - memory recall/writeback│
│  - Trigger scheduler  │                │  - sub-agent fan-out     │
│  - Curation worker    │                │  - trajectory recorder   │
│  - QuotaService       │                └─────────┬─────┬──────────┘
└───────┬───────────────┘                          │     │
        │                                          │     │
        │ mTLS                       LLM API ─────►│     │
        │                  (Anthropic/OpenAI/      │     │
        │                   self-hosted vLLM/      │     │
        │                   Kimi/GLM/etc.)         │     │
        │                                                │
        │ mTLS                                           │
        ▼                                                ▼
┌──────────────────────┐                       ┌──────────────────────┐
│ sandbox-supervisor   │                       │  credential-proxy    │
│ FastAPI              │                       │  aiohttp             │
│  - Docker CLI        │                       │  - SecretStore       │
│  - gVisor (runsc)    │                       │  - 出站凭证注入     │
│  - Workspace 持久化   │                       │  - 阿里云 KMS        │
└──────┬───────────────┘                       └──────────────────────┘
       │
       │ docker run -i + helix-sandbox-egress network
       ▼
┌──────────────────────┐
│  Sandbox 容器        │
│  (runsc + readonly + │
│   --cap-drop ALL)    │
└──────────────────────┘

旁路服务：
  - audit-backup-worker         → S3 WORM append
  - event-log-archive-job       → S3 半年归档
  - retention-cleanup-job       → TTL 清理（含 image_upload 软删除 + 物理删除）
```

**关键拓扑事实**：
- **orchestrator 不独立成镜像（M0）**：作为库被 control-plane in-process 调用（`Mini-ADR I-1`，见 STREAM-E-DESIGN § 2.6），独立服务拆分推 M1。
- **sandbox-supervisor 用 docker-out-of-docker**：容器化后挂宿主 `/var/run/docker.sock` 启沙盒兄弟容器（`Mini-ADR I-2`）。
- **全链路 mTLS**（A.10 / C.2）：control-plane ↔ orchestrator ↔ sandbox-supervisor 之间用 mTLS；用户面经 nginx TLS 终止。
- **沙箱出站走 `helix-sandbox-egress` Docker `--internal` 网络**（F.9）：连 `169.254.169.254` 这类元数据服务被拒；外部 HTTP 必须经 credential-proxy。

## 0.8 数据库表速查

按 `packages/helix-persistence/migrations/versions/` 命名顺序（截至迁移 0038）：

| 迁移 | 主表 | 用途 |
|------|------|------|
| 0001-0002 | `thread_meta` / `event_log` | LangGraph checkpoint + append-only 事件流 |
| 0005 | `audit_log` | 审计日志（A.4，append-only） |
| 0011 | `tool_config` / `tenant_config.mcp_servers` | 多租户 MCP server 配置 JSONB |
| 0013 | `memory_item` | pgvector 长期记忆（J.3） |
| 0017 | `memory_item.user_id` | 用户级 RLS |
| 0021-0022 | `knowledge_base` / `knowledge_document` / `knowledge_chunk` | RAG（J.5） |
| 0023 | `api_key.rotated_at` / `grace_period_s` | API Key rotation（K1） |
| 0024 | `memory_item.deleted_at` | soft-delete（K6） |
| 0025 | `memory_item.content_hash` / `memory_writeback_dlq` | dedup + DLQ（K7） |
| 0028 | `image_upload` | 多模态图像上传记录（J.6） |
| 0029 | `skill` / `skill_version` | 技能库（J.7a） |
| 0033 | `agent_trigger` / `trigger_run` | Cron + Webhook trigger（J.10） |
| 0034 | `eval_dataset` / `curation_candidate` | Eval pipeline（J.12/13） |
| 0035 | `role_binding.platform_scope` | 跨租户 system_admin（N） |
| 0036 | `token_usage` | 成本追踪（G.9） |
| 0037 | `agent_run.trace_id` | Langfuse trace 持久化（H.3） |
| 0038 | `run_event` | step-level 运行事件（H.3） |

---

# 维度 1 — Agent 循环（ReAct）

## 1.1 现状评级

**生产级**（M0 Stream E.6 ✅ + Stream L ✅）。LangGraph 标准 StateGraph 实现的 ReAct 双节点图，已有 13 个生产能力切面（Stream L 把 Hermes 单 turn 8 条 + 配套补强加进来）。完整的 SSE 流式 / 中断恢复 / 重试矩阵 / 阶段化工具并发 / 上下文压缩 / 轨迹记录都在主路径。

ITERATION-PLAN 锚点：
- 主 ReAct 实现：M0 Stream E.6 `[x]`
- 8 条 Hermes-derived 强化：M0 Stream L.L1-L8 `[x]`（2026-05-20 收尾）
- M1+ 没有进一步的 backlog（这块已经"零债收尾"）

## 1.2 设计立场

`services/orchestrator/src/orchestrator/state.py:9-17` 的 module docstring 直接给出了核心约束：

```python
"""Canonical LangGraph state shape for orchestrator graphs.
...
Every ``AgentState`` channel is checkpointed (dill), so **non-serialisable
runtime objects do not live here**. They travel via the
``config["configurable"]`` channel instead — it is per-invocation and not
checkpointed:

- Tenant binding (``tenant_id`` / ``session_id`` / ``run_id``) — LangGraph idiom.
- ``cancellation_token`` (E.15) — backed by a live ``asyncio.Event``.
- The ``LLMRouter`` holds its own provider chain + fallback state (E.11).
"""
```

设计立场可归纳为：

1. **Checkpointable-vs-Runtime 严格分离**：所有可序列化状态进 `AgentState`（PostgresSaver 落盘），不可序列化的 runtime object（cancellation token / LLM router）走 `RunnableConfig.configurable` 通道。这让"会话恢复"成为一等公民：只要 PostgresSaver 有 checkpoint，任何被取消的 run 都能从断点继续。
2. **细通道而非大状态对象**：`AgentState` 是 `TypedDict`，每个字段对应一个明确的语义（`step_count_refund_pending` / `failed_mutations` / `subagent_invocations` 等），不允许"一个大 dict 装一切"。
3. **LangGraph reducer 决定写入语义**：`messages` 用 `add_messages` reducer（append-only），`reflections` 用 `operator.add`（append），`step_count` 用默认 overwrite。新加字段必须显式声明 reducer。
4. **生产级 hardening 标准**：每个能力切面（如 Stream L 的 8 条）都要满足"零债 6 条"才能 merge。

## 1.3 关键代码路径

| 角色 | 文件 | 关键符号 |
|------|------|---------|
| LangGraph state | `services/orchestrator/src/orchestrator/state.py:41-123` | `class AgentState(TypedDict)`（12 字段） |
| 默认 step 上限 | `services/orchestrator/src/orchestrator/state.py:38` | `DEFAULT_MAX_STEPS = 20` |
| ReAct 图构建 | `services/orchestrator/src/orchestrator/graph_builder/builder.py` (754 行) | `build_react_graph(...)` |
| 阶段化工具调度 counter | `services/orchestrator/src/orchestrator/graph_builder/builder.py:103-113` | `_tools_stages_total` / `_tools_dispatched_total` |
| 错误摘要 cap | `services/orchestrator/src/orchestrator/graph_builder/builder.py:118` | `_ERROR_SUMMARY_MAX_CHARS = 500` |
| GraphRunner | `services/orchestrator/src/orchestrator/runner.py:29-65` | `class GraphRunner` |
| Dangling tool_call 修复 | `services/orchestrator/src/orchestrator/runner.py:67-105` | `sanitize_thread()` |
| SSE 流式执行 | `services/orchestrator/src/orchestrator/sse.py` (815 行) | `sse_consumer()` + `run_agent()` |
| 上下文压缩入口 | `services/orchestrator/src/orchestrator/context/compressor.py` (302 行) | `ContextCompressor` |
| LLM 路由 | `services/orchestrator/src/orchestrator/llm/router.py` (381 行) | `LLMRouter`（fallback tree） |
| Sub-agent 工具 | `services/orchestrator/src/orchestrator/tools/subagent.py` (525 行) | `SubAgentTool` |
| 工具调度 | `services/orchestrator/src/orchestrator/tools/scheduling.py` | `plan_stages()` + `MAX_TOOL_WORKERS = 8` |
| 轨迹记录 | `services/orchestrator/src/orchestrator/trajectory/recorder.py` (286 行) | `TrajectoryRecorder` |
| 中间件链 | `services/orchestrator/src/orchestrator/middleware_assembly.py` | 8 个 middleware 装配 |

## 1.4 实现细节

### 1.4.1 `AgentState` 的 12 个 channel

`state.py:112-123`（按源码字段顺序）：

```python
class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    step_count: int
    max_steps: int
    plan: NotRequired[Plan | None]
    reflections: NotRequired[Annotated[list[Reflection], add]]
    recalled_memories: NotRequired[list[MemoryItem]]
    step_count_refund_pending: NotRequired[int]
    failed_mutations: NotRequired[list[MutationOutcome]]
    subagent_invocations: NotRequired[Annotated[list[SubAgentInvocation], add]]
    pending_approval: NotRequired[ApprovalRequest | None]
    approval_resume: NotRequired[dict[str, Any] | None]
    approval_outcome: NotRequired[str | None]
```

每个字段都带一段 docstring 解释"谁写、谁读、reducer 是什么、什么时候 absent"（`state.py:42-110`）。关键设计模式：

- **`NotRequired` + 明确的 absent 语义**：`plan` 只在 `workflow.type==plan_execute` 时存在；`subagent_invocations` 只在 manifest 声明了 `subagents` 时存在；`pending_approval` 只在被审批暂停过的 run 上才有。
- **`Annotated[list, add]` / `Annotated[list, add_messages]`**：声明性 append-only 通道。`reflections` / `subagent_invocations` 用 `operator.add` 拼接；`messages` 用 LangGraph 的 `add_messages`（多了 ID 去重逻辑）。
- **窄通道分担状态**：`step_count_refund_pending`（Stream L.L5）单独一个 channel 而不是 hack 进 `step_count`，目的是审计可观察 —— tools 写 refund，下次 agent_node 减完再清零。

### 1.4.2 ReAct 图拓扑（`builder.py:1-50` 模块 docstring 原文）

```
START → agent ↔ tools → END
          │
          └─ END (when LLM stops issuing tool_calls or max_steps hit)
```

**agent 节点**：
- 调用注入的 `LLMCaller`（生产是 `LLMRouter`，测试是 deterministic fake）；
- 进入时 `step_count >= max_steps` → `raise MaxStepsExceededError`；
- 通过 `before_llm_call` middleware chain（E.3 dynamic context / E.5 PII redact / E.13 cache lookup），cache 命中时跳过 LLM 调用；
- 调 LLM 后走 `after_llm_call` chain（E.10.5 loop detection / E.13 cache store）；
- 上下文压缩在 agent 入口判断（Stream L.L2）。

**tools 节点**：
- 解析最近 `AIMessage.tool_calls`；
- 每个 tool 走 `before_tool_dispatch` chain（E.10 sandbox_audit）—— 拒就生成阻塞 ToolMessage 而不下发；
- 经 `plan_stages()` 把 `tool_calls` 分阶段，每阶段 `asyncio.gather()` 并发，最多 `MAX_TOOL_WORKERS = 8`；
- 任何未捕获工具异常 → 包成 `ToolMessage(content="[tool error] ...")` 而非 re-raise（Mini-ADR E-12），LLM 自行决定重试 / 换参 / 终止；
- 错误字符串截断 `_ERROR_SUMMARY_MAX_CHARS = 500`，防止 multi-MB stack trace 灌进 messages。

### 1.4.3 阶段化工具调度（Stream L.L6）

`builder.py:103-113`：

```python
_tools_stages_total = helix_counter(
    "helix_tools_stages_total",
    "Tool-call stages executed (Stream L.L6).",
)
_tools_dispatched_total = helix_counter(
    "helix_tools_dispatched_total",
    (
        "Individual tool calls dispatched within L6 stages — divide by "
        "stages to get average concurrency."
    ),
)
```

`plan_stages()` 算法（`tools/scheduling.py`）：
- 两个**只读**工具永不冲突；
- **写+写** / **读+写** 路径相交才冲突（`ToolSpec.path_args` 声明的路径参数）；
- 空 `path_args` + 非只读 → 保守与所有冲突（兜底防漏）。

效果：5 个 read-only 的 `web_search` 调用一个阶段并发；2 个写同一 artifact 的 `save_artifact` 串行。dispatched/stages 比值是平均并发度。

### 1.4.4 cancellation token 全链路

`AgentState` docstring `state.py:14-16`：

> "``cancellation_token`` (E.15) — backed by a live ``asyncio.Event``."

它在 `RunnableConfig.configurable` 里穿过 graph 每个节点，每个 LLM 调用、每个工具调用、sandbox 命令都协作式检查。具体路径：

- API 层（B.3）：FastAPI request 断开 → 生成 token；
- engine 层（E.15）：`agent_node` / `tools_node` 每次进入前检查；
- sandbox 端（F.7）：sandbox-supervisor 收到 cancellation → SIGKILL 容器，回收 workspace 资源。

### 1.4.5 GraphRunner 的两个关键能力

`services/orchestrator/src/orchestrator/runner.py`（整文件 105 行）：

**集中化 checkpointer 注入**：

```python
class GraphRunner:
    def __init__(self, *, checkpointer: BaseCheckpointSaver[Any]) -> None:
        self._checkpointer = checkpointer

    def compile(self, graph: StateGraph) -> CompiledStateGraph:
        """Compile graph with the configured checkpointer."""
        compiled = graph.compile(checkpointer=self._checkpointer)
        ...
```

所有 graph 共享同一个 PostgresSaver，意味着所有 run 都进同一个 durable checkpoint store。

**Dangling tool_call 修复**（`runner.py:67-105`）：

```python
async def sanitize_thread(
    self,
    graph: CompiledStateGraph,
    config: RunnableConfig,
    *,
    as_node: str = "tools",
) -> int:
    """Repair orphan ``tool_calls`` in a thread's checkpoint (E.15).

    Call before resuming a thread that may have been cancelled
    mid-tool-dispatch. Reads the thread's current state, computes
    placeholder ``ToolMessage``s for any unanswered ``tool_calls``
    ...
    """
    snapshot = await graph.aget_state(config)
    values = snapshot.values if isinstance(snapshot.values, dict) else {}
    messages = values.get("messages") or []
    placeholders = sanitize_dangling_tool_calls(messages)
    if placeholders:
        await graph.aupdate_state(config, {"messages": placeholders}, as_node=as_node)
        ...
    return len(placeholders)
```

关键巧思在 `as_node="tools"` —— 占位 ToolMessage 必须以"tools 节点刚执行完"的身份写回，否则 LangGraph 的条件边会把 thread 路由到 END。这是 cancellation + resume 的正确性边界。

### 1.4.6 Stream L 8 条单 turn 能力（已 merge in main，PR 链 #198–#206）

来自 `docs/ITERATION-PLAN.md:423-432`，每条都对应一个 Mini-ADR L-1 ~ L-8：

| L# | 能力 | 关键实现 |
|----|------|---------|
| L1 | Anthropic prompt caching | `_apply_cache_control` 给 system + 末尾 3 message 加 `cache_control=ephemeral`；leading SystemMessage 跨 turn 字节稳定（Mini-ADR L-1） |
| L2 | Token preflight + context compressor | `ContextCompressor` summarise-the-middle，超过 `context_window * threshold_pct` 触发 |
| L3 | Stream stale-detection | `LLMRouter._invoke_with_deadline` 套 `asyncio.wait_for(stream_deadline_s)`，超时 raise `LLMStreamStaleError` 走 fallback |
| L4 | File-mutation verifier footer | `tools_node` 用 mutation_classifier 检测未 land 的写操作，下轮 agent 收到 `<mutation-advisory>` HumanMessage |
| L5 | Iteration budget refund | `ToolResult.refund_iterations` 让 `update_plan` 等管理性操作不消耗用户预算 |
| L6 | Adaptive tool parallelization | `plan_stages` 冲突检测 + `asyncio.gather` + `MAX_TOOL_WORKERS=8` |
| L7 | Trajectory recording | ShareGPT JSONL → S3 ObjectStore，4 outcome 分流 |
| L8 | OAuth 401 自动 refresh + 重试一次 | `OAuthCapableProvider` Protocol + `LLMRouter._handle_unauthorized` |

注意：**这 8 条全部已 merge in main 并通过零债 6 条核验**（`docs/ITERATION-PLAN.md:433-441` 显式标 ✅）。不是 backlog，是已实现的能力。

## 1.5 运行时行为

```
HTTP POST /v1/sessions/{thread_id}/runs (SSE)
  │
  ├─ control-plane.api.runs.run_endpoint
  │     ├─ build_supervisor_client / build_llm_router / build_tool_registry
  │     ├─ runtime.get_agent(...) → CompiledStateGraph + middleware chain
  │     └─ asyncio.create_task(run_agent(...)) + sse_consumer 桥接
  │
  └─ run_agent(...)（services/orchestrator/sse.py）
        │
        ├─ GraphRunner.sanitize_thread(...)        ← resume 时修 dangling tool_call
        │
        └─ async for chunk in graph.astream(input, config={
               "configurable": {
                   "thread_id": session_id,
                   "tenant_id": tenant_id,
                   "user_id": user_id,
                   "cancellation_token": token,
               }
           }):
              │
              ├─ agent_node:
              │     ├─ step_count >= max_steps? → raise MaxStepsExceededError
              │     ├─ before_llm_call chain
              │     │     ├─ E.3 dynamic_context
              │     │     ├─ E.5 PII redact
              │     │     └─ E.13 cache lookup
              │     ├─ L.L2: estimate_tokens >= threshold? → ContextCompressor.compress()
              │     ├─ L.L4: read failed_mutations → 注入 <mutation-advisory> HumanMessage
              │     ├─ L.L5: step_count -= refund_pending; refund_pending = 0
              │     ├─ LLMRouter.complete(...)        ← E.11 fallback tree + E.12 限流 + L.L3 stale + L.L8 OAuth
              │     ├─ after_llm_call chain
              │     │     ├─ E.10.5 loop_detection
              │     │     └─ E.13 cache store
              │     └─ step_count += 1
              │
              ├─ 条件边：AIMessage.tool_calls 为空 → 走 reflect node 或 END
              │
              └─ tools_node:
                    ├─ plan_stages(tool_calls) → 分阶段
                    ├─ for stage in stages:
                    │     await asyncio.gather(*[_dispatch_tool(call) for call in stage])
                    │           ├─ before_tool_dispatch chain (E.10 sandbox_audit)
                    │           ├─ tool.call(args, ctx)
                    │           └─ classify_mutation(result) → 累 failed_mutations 写回 state
                    └─ 把所有 ToolMessage append 到 messages

终态（finish_reason==stop / max_steps / cancelled）
  │
  ├─ TrajectoryRecorder.record(messages, outcome)   ← L.L7 fire-and-forget
  ├─ trajectory → S3 trajectories/{tenant_id}/{outcome}/{YYYY}/{MM}/{DD}/{thread_id}.jsonl
  └─ event_log + audit_log 写入 Postgres
```

## 1.6 局限与边界

- **agent_node 内 LLM 调用本身仍线性**：阶段化并发是工具层；同一轮 LLM 调用只发一次。
- **`max_steps` 默认 20，远小于 Hermes 90**：M0 ReAct 默认更保守。复杂任务靠 manifest 覆盖。
- **没有"中断信号 TTL"**：cancellation_token 是 `asyncio.Event`，若没被节点轮询会被静默吞（跟 Hermes 同样的边界）。
- **Sub-agent 并行 fan-out 已实现（L.L6 + J.4-补强-2）**，但子 SSE 不流回父 —— M2-B 后续。
- **OAuth refresh 仅一次重试**：连续 401 直接走 fallback chain，不无限循环刷新。
- **持续未交付**：复杂度跟 cancellation_token 类似的"工具结果体积硬上限"在源码中没看到通用 cap（只有错误字符串的 500 char cap）—— 单工具内自己负责（E.7/E.8/E.9 + Mini-ADR E-10），跟 Hermes 一样的边界。

---

# 维度 2 — 自我改进（闭环学习 + 技能自创建）

## 2.1 现状评级

**部分实现**。
- **数据采集 + 反馈 + 候选鉴选**：M0 ✅（Stream G.6 反馈 API + Stream J.12 Curation Worker + Stream L.L7 Trajectory + Stream J.13 Eval Dataset）—— **基础设施齐全**。
- **Agent 自动写 skill**：❌ **不在 M0 scope**，规划在 **M1-K J.7b-1** `author_skill` / `refine_skill`（agent 在 run 期沉淀新 skill 进 draft + 用户审批 + 切 active）。
- **Curator / 库级整合**：❌ 没有 Hermes 那种"每 7 天合并 N 个 skill 进 umbrella"机制；M0 仅人工审 Curation Candidate。

ITERATION-PLAN 锚点：
- Stream J.12 Curation Worker：M0 ✅（`curation_worker.py`）
- Stream G.6 反馈 API：M0 ✅
- Stream L.L7 Trajectory：M0 ✅
- **M1-K J.7b-1**：`docs/ITERATION-PLAN.md:692` `[ ]`

## 2.2 设计立场

helix 的自我改进设计跟 Hermes 是**完全不同的哲学**：

- **Hermes**：会话结束 → 后台 fork agent → 自动决定要不要写 memory / 改 skill → daemon thread 写盘。**Agent autonomy 优先**。
- **helix**：会话结束 → 自动落 trajectory → 自动按 signal 生成 candidate → **必须人工审 → 才能 promote 进 eval dataset**。**Audit / governance 优先**。

这跟 helix 的产品定位（"业务无关的多租户企业引擎"）一致 —— 企业客户不接受 agent 自己改自己的 skill 库 + memory 没人审核。

设计文档显式表达（来自 `docs/streams/STREAM-J-DESIGN.md` § 12 推断）：
- agent → trajectory（自动）
- trajectory → curation candidate（自动，规则驱动）
- curation candidate → eval dataset（**人工审核**，control-plane API + Admin UI H.4）
- eval dataset → Eval Gate（自动，CI 强制）

## 2.3 关键代码路径

| 角色 | 文件 | 关键符号 |
|------|------|---------|
| Curation Worker | `services/control-plane/src/control_plane/curation_worker.py` | `class CurationWorker` |
| 信号分类 | `services/control-plane/src/control_plane/curation_worker.py:80-95` | `_classify(outcome, has_down, has_up)` |
| Feedback API | `services/control-plane/src/control_plane/api/feedback.py` | `POST /v1/sessions/{id}/runs/{run_id}/feedback` |
| Feedback 持久化 | `packages/helix-persistence/src/helix_agent/persistence/feedback_store.py` | `FeedbackStore` ABC + SQL impl |
| 轨迹记录器 | `services/orchestrator/src/orchestrator/trajectory/recorder.py` (286 行) | `TrajectoryRecorder` |
| Curation Candidate DTO | `packages/helix-protocol/src/helix_agent/protocol/eval_dataset.py` | `CurationCandidateRecord` |
| Eval Dataset DTO | 同上 | `EvalDatasetRecord` |
| 迁移 | `packages/helix-persistence/migrations/versions/0034_eval_dataset.py` | `eval_dataset` / `curation_candidate` 表 |
| Admin UI Curation 面板 | `apps/admin-ui/src/curation/` | （H.4 PR #298） |

## 2.4 实现细节

### 2.4.1 三层学习管道

```text
[L7 Trajectory Recording]
  └─ TrajectoryRecorder.record(record)
       └─ 写 S3: trajectories/{tenant_id}/{outcome}/{YYYY}/{MM}/{DD}/{thread_id}.jsonl
       └─ outcome ∈ {success, failed, max_steps, cancelled}

       │
       ▼

[J.12 Curation Worker]
  └─ 后台 worker 监听 trajectory 新写入 + G.6 feedback
       └─ _classify(outcome, has_down, has_up) → CurationSignal | None
       └─ 生成 CurationCandidateRecord (status=PENDING)

       │
       ▼

[J.13 Eval Dataset]
  └─ control-plane API: POST /v1/curation/{id}/promote
       └─ status: PENDING → PROMOTED
       └─ 自动创建 EvalDatasetRecord
       └─ eval_dataset_id 回填 candidate row

       │
       ▼

[G.4 Eval Gate]
  └─ tools/eval/helix_eval.py
       └─ 加载 YAML dataset
       └─ 跑 agent 对比 expected
       └─ baseline 卡 PASS / FAIL
```

### 2.4.2 信号分类的优先级（`curation_worker.py:80-95`）

来自之前探索确认的代码：

```python
def _classify(
    outcome: TrajectoryOutcome, *, has_down: bool, has_up: bool
) -> tuple[CurationSignal | None, FeedbackRating | None]:
    """negative signals win > positive — a 👎 is most actionable."""
    if has_down:
        return "negative_feedback", "down"
    if outcome in _FAILED_OUTCOMES:
        return "failed_outcome", None
    if has_up:
        return "positive_feedback", "up"
    return None, None
```

关键设计：

- **negative > failure > positive** 优先级（设计意图："👎 比 failure 更可执行，因为前者是用户判断后者只是机器观察"）；
- **没有信号就不生成 candidate**（不污染审核队列）；
- 一个 thread / run 一行 candidate（去重靠数据库 UNIQUE 约束）。

### 2.4.3 Eval Dataset 的两种 source

`packages/helix-protocol/src/helix_agent/protocol/eval_dataset.py`（推断结构）：

```python
class EvalDatasetRecord:
    id: UUID
    tenant_id: UUID
    agent_name: str
    name: str                           # dataset 名（非 UNIQUE）
    input: dict                          # 自由 schema
    expected: dict | None                # 期望输出
    source: "golden" | "trajectory" | "regression"
    source_trajectory_key: str | None    # L7 provenance
    source_user_id: UUID | None
    created_at: datetime
```

三种 source：
- **golden**：人工编写的"标准答案"；
- **trajectory**：从 curation candidate promote 来的；
- **regression**：bug 修复后的回归用例。

### 2.4.4 SkillStore 的 agent-author 路径（**M1-K backlog**）

`packages/helix-persistence/src/helix_agent/persistence/models/skill.py:31-66`（K6 spot-check 确认）：

```python
class SkillRow(Base):
    __tablename__ = "skill"
    id: UUID
    tenant_id: UUID
    name: str                            # [a-z][a-z0-9_-]{0,63}
    status: str                          # draft | active | archived
    latest_version: int                  # 1-based
    description: str
    category: str | None                 # tool_use | code | retrieval | ...
    created_at: datetime
```

Skill 三态机：`DRAFT → ACTIVE → ARCHIVED`。M0 只允许**管理员**通过 control-plane API 改 `status`。

**M1-K J.7b-1**（`docs/ITERATION-PLAN.md:692`）：

> `author_skill` / `refine_skill` —— agent 在 run 期沉淀新 skill 进 draft；用户审批后切 active；带 audit + 速率限制

也就是说 helix 已经为"agent 自动写 skill"留好了 schema 接口（status=draft），但 M0 不开放 agent 写入。

## 2.5 运行时行为

```
单 run 结束 (sse.py:run_agent 终态)
  │
  ├─ trajectory_recorder.record(TrajectoryRecord) ← fire-and-forget asyncio.create_task
  │     └─ swallow 所有错误 + 3 档 counter
  │
  ├─ 写 event_log + audit_log + trace_id
  │
  └─ 客户端可调 POST /v1/sessions/{id}/runs/{run_id}/feedback {rating: up/down}
        └─ FeedbackStore.upsert(row)

CurationWorker（后台周期 task）
  │
  ├─ 扫"新近的 trajectory + feedback"
  │
  └─ for each (thread_id, run_id):
        outcome = read_trajectory_outcome(...)
        has_down = check_feedback(rating="down")
        has_up = check_feedback(rating="up")
        signal, rating = _classify(outcome, has_down, has_up)
        if signal:
            CurationCandidateStore.upsert(...)  ← status=PENDING

Admin UI H.4 Curation 面板
  │
  └─ GET /v1/curation?status=pending
        └─ 人审 → POST /v1/curation/{id}/promote 或 /dismiss
              ├─ promote: status=PROMOTED, 创建 EvalDatasetRecord
              └─ dismiss: status=DISMISSED, 不进 dataset
```

## 2.6 局限与边界

- **没有 agent autonomy**（M0）：agent 不能在 run 中调用 `author_skill` / `refine_skill` / `memory(action=add)`。所有 memory / skill 改动**只能管理员手动**。
- **没有 curator 库整合**：M0 没有"每 7 天合并 N 个窄 skill 进 umbrella"机制；Skill 库整合需要管理员手动 archive + create new umbrella。
- **没有自动状态转移**：Skill `DRAFT → ACTIVE → ARCHIVED` 是手动 PATCH `status`，不像 Hermes 有 `apply_automatic_transitions` 跑时间启发式。
- **没有 background fork**：helix 不会在会话结束时自动 fork 一个新 agent 反思。
- **Feedback 只有 up/down 二元**：没有 multi-dimensional rating（accuracy / helpfulness / safety 分开）。
- **M1-K backlog 8 项**（`docs/ITERATION-PLAN.md:688-700`）：J.7b-1 `author_skill` / J.7b-2 `code` 字段执行 / J.7b-3 progressive loading / J.7b-4 LLM moderation / J.7b-5 public skill 库 / J.7b-6 supporting files / J.7b-7 per-agent 启停细化 / J.7b-8 UI 元数据。

---

# 维度 3 — 记忆系统

## 3.1 现状评级

**生产级**（M0 Stream J.3 ✅ + Stream K.K6 / K.K7 收尾 ✅）。三层架构：session messages（LangGraph checkpoint） / short-term（recall context window）/ long-term（pgvector 持久 + DLQ 重试 + dedup + user-level RLS）。M2 还会加 "Memory archive 层"（冷迁移）。

ITERATION-PLAN 锚点：
- Stream J.3 长期记忆三 PR：M0 ✅
- K.K6 memory CRUD：M0 ✅
- K.K7 writeback DLQ + dedup：M0 ✅
- K.K12 memory recall eval gate：M0 ✅
- M2-C Memory archive 层：`docs/ITERATION-PLAN.md:768-778`（M2 backlog）

## 3.2 设计立场

跟 Hermes 的"per-`$HERMES_HOME` 全局共享 MEMORY.md"完全不同，helix 的核心约束是：

1. **per-(tenant, user) RLS 隔离**：每条 memory 都带 `tenant_id` + `user_id`，Postgres RLS policy 强制；跨用户跨租户**绝对不可见**（test_runs_cross_tenant_sse_rejected 这类测试在 K.K2 锁住）。
2. **embedding 解耦**：通过 `Embedder` Protocol 注入，可换（生产用 qwen `text-embedding-v4` 兼容 `/v1/embeddings`）。
3. **写入 DLQ + dedup**：写 memory 失败入 DLQ，按 1m → 5m → 30m → 2h → 6h backoff 重试，5 次失败死信；UNIQUE 索引 `(tenant, user, content_hash) WHERE deleted_at IS NULL` 防重复。
4. **Soft delete**：`memory_item.deleted_at` 列；不真删除（K6 Mini-ADR K-4）。
5. **Memory 不在系统提示快照**：跟 Hermes 把 MEMORY.md 注入系统提示不同，helix 是 agent_node 跑前 `memory_recall_node` 显式调 `MemoryStore.recall()` 拿 top-k，作为 `recalled_memories` channel 传给 agent_node，最后渲染进 system context。

## 3.3 关键代码路径

| 角色 | 文件 | 关键符号 |
|------|------|---------|
| MemoryStore Protocol | `packages/helix-persistence/src/helix_agent/persistence/memory/base.py` | `class MemoryStore(ABC)` |
| SQL 实现 | `packages/helix-persistence/src/helix_agent/persistence/memory/sql.py` | `SqlMemoryStore` + pgvector |
| InMemory 实现 | `packages/helix-persistence/src/helix_agent/persistence/memory/memory.py` | 单测用 |
| DLQ Worker | `packages/helix-persistence/src/helix_agent/persistence/memory/dlq.py` + `control_plane/memory/` | `MemoryDLQWorker`（lifespan task） |
| Memory recall node | `services/orchestrator/src/orchestrator/graph_builder/memory.py` | `make_memory_recall_node(memory_store, embedder)` |
| Memory writeback node | 同上 | `make_memory_writeback_node(memory_store, embedder, dlq=None)` |
| Embedder Protocol | `packages/helix-runtime/src/helix_agent/runtime/embedder.py`（推断） | `Embedder` Protocol |
| Memory API（CRUD） | `services/control-plane/src/control_plane/api/memory.py` | `GET/PATCH/DELETE /v1/memory` + `/v1/memory/{id}` |
| 迁移 0013 | `packages/helix-persistence/migrations/versions/0013_memory.py` | `memory_item` 表 + pgvector 索引 |
| 迁移 0017 | `0017_memory_user_id.py` | 加 `user_id` + 用户级 RLS |
| 迁移 0024 | `0024_memory_soft_delete.py`（K6） | `deleted_at` + partial 索引 |
| 迁移 0025 | `0025_memory_dedup_dlq.py`（K7） | `content_hash` + `memory_writeback_dlq` 表 |

## 3.4 实现细节

### 3.4.1 三层架构对照表

| 层 | 容器 | 写入时机 | 读取时机 | 持久 |
|----|------|---------|---------|------|
| **Session messages** | `AgentState.messages`（LangGraph） | 每轮 agent / tools node | 每轮 agent_node 入参 | ✅ PostgresSaver checkpoint |
| **Short-term context** | LangGraph in-flight messages | 同 session messages | 同 session messages | （检查点失效后丢） |
| **Long-term memory** | `memory_item` 表（pgvector） | 每 turn 结束 `memory_writeback_node` | 每 turn 开始 `memory_recall_node` | ✅ Postgres + S3 归档（M2） |

### 3.4.2 `memory_item` 表结构（迁移 0013 + 0017 + 0024 + 0025 累计）

```sql
CREATE TABLE memory_item (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    user_id UUID NOT NULL,                    -- 0017 加，per-user RLS
    thread_id UUID NULL,                      -- 可选关联
    seq BIGINT,
    embedding vector(1536),                   -- pgvector，cosine similarity
    content TEXT NOT NULL,
    content_hash CHAR(64) NOT NULL,           -- 0025 加，pgcrypto SHA-256
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT now(),
    deleted_at TIMESTAMPTZ NULL,              -- 0024 加，soft delete
    FOREIGN KEY (tenant_id) REFERENCES tenant(id),
    FOREIGN KEY (thread_id) REFERENCES thread_meta(id)
);

-- pgvector ivfflat 索引
CREATE INDEX ON memory_item USING ivfflat (embedding vector_cosine_ops);

-- 0024 partial 索引：仅未删除行
CREATE INDEX ON memory_item (tenant_id, user_id, created_at)
  WHERE deleted_at IS NULL;

-- 0025 dedup UNIQUE 索引：同 tenant 同 user 同 hash 不允许重复
CREATE UNIQUE INDEX ON memory_item (tenant_id, user_id, content_hash)
  WHERE deleted_at IS NULL;

-- RLS policy（应用层 GUC set_config('app.tenant_id', ..., true) 后才能读）
CREATE POLICY memory_isolation ON memory_item
  FOR ALL USING (tenant_id = current_setting('app.tenant_id')::uuid);
```

### 3.4.3 MemoryStore Protocol（推断签名，验证可参考 K6 测试）

```python
class MemoryStore(Protocol):
    async def store(
        self, tenant_id: UUID, user_id: UUID, item: MemoryItem,
        embedding: np.ndarray | None = None,
    ) -> MemoryItem: ...

    async def recall(
        self, tenant_id: UUID, user_id: UUID, query_embedding: np.ndarray,
        limit: int = 5,
    ) -> list[MemoryItem]: ...

    async def list(
        self, tenant_id: UUID, user_id: UUID, limit: int = 50, offset: int = 0,
    ) -> list[MemoryItem]: ...

    async def patch(
        self, tenant_id: UUID, user_id: UUID, id: UUID, content: str,
        embedding: np.ndarray,
    ) -> MemoryItem: ...

    async def soft_delete(
        self, tenant_id: UUID, user_id: UUID, id: UUID,
    ) -> bool: ...

    async def write(
        self, tenant_id: UUID, user_id: UUID, item: MemoryItem,
        embedding: np.ndarray,
    ) -> MemoryItem:
        """ON CONFLICT (tenant, user, content_hash) DO NOTHING dedup."""
```

K6 关键 hardening 点（`docs/ITERATION-PLAN.md:391`）：
- 每端点强制 `caller_user_id`，machine principal 403；
- 跨 user 访问返回 **404**（不是 403，**隐藏存在性**，防探测）；
- PATCH 重新 embed 否则 503；
- DELETE 幂等。

### 3.4.4 Memory recall 节点（每 turn 开始）

`services/orchestrator/src/orchestrator/graph_builder/memory.py`（推断结构）：

```python
def make_memory_recall_node(
    memory_store: MemoryStore,
    embedder: Embedder,
) -> Callable[[AgentState], Awaitable[dict]]:
    """Returns async fn that:
    1. Embeds the latest HumanMessage
    2. Queries memory_store.recall() for top-k similar items
    3. Writes to state['recalled_memories']
    """
    async def _node(state: AgentState, config: RunnableConfig) -> dict:
        latest_user = _last_human_message(state["messages"])
        if latest_user is None:
            return {}
        query_emb = await embedder.embed_query(latest_user.content)
        tenant_id, user_id = _get_ids_from_config(config)
        recalled = await memory_store.recall(tenant_id, user_id, query_emb, limit=5)
        return {"recalled_memories": list(recalled)}
    return _node
```

agent_node 进入时把 `recalled_memories` 渲染进 system context 给 LLM。

### 3.4.5 Memory writeback + DLQ（每 turn 结束）

K7 hardening（`docs/ITERATION-PLAN.md:392`）：

```python
# memory_writeback_dlq 表（迁移 0025）
CREATE TABLE memory_writeback_dlq (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    user_id UUID NOT NULL,
    payload JSONB NOT NULL,
    attempt INT DEFAULT 0,
    next_retry_at TIMESTAMPTZ NOT NULL,
    last_error TEXT,
    status TEXT,                     -- pending | dead_letter
    created_at TIMESTAMPTZ DEFAULT now()
);

# MemoryDLQWorker（control_plane lifespan task）
BACKOFF_SEQUENCE = [60, 5*60, 30*60, 2*3600, 6*3600]  # 1m → 5m → 30m → 2h → 6h
MAX_ATTEMPTS = 5  # 5 次后 dead_letter

# emit 3 个 prom counter：
helix_memory_dlq_cycle_errors_total
helix_memory_dlq_dead_letters_total
helix_memory_dlq_retries_succeeded_total
```

### 3.4.6 K.K12 Memory recall eval gate

新 harness `tools/eval/memory_recall.py` + `tools/eval/datasets/memory_recall/zh_en_seed.yaml`（4 zh + 4 en）+ embedder-agnostic runner。SLO #6（slo.md）：

- M0 在 fake keyword-overlap embedder 上锁 recall@5 = 1.0（防 harness 回归）
- M1 目标 recall@5 ≥ 0.7 against real embedder

## 3.5 运行时行为

```
agent run（含 long-term memory 的 manifest）
  │
  ├─ START → memory_recall_node
  │     ├─ embedder.embed_query(last_human_msg.content)
  │     ├─ memory_store.recall(tenant_id, user_id, query_emb, limit=5)
  │     │     ├─ SET LOCAL app.tenant_id, app.user_id   ← RLS GUC
  │     │     ├─ SELECT ... WHERE deleted_at IS NULL
  │     │     │     ORDER BY embedding <-> query_emb LIMIT 5
  │     │     └─ 返回 MemoryItem 列表
  │     └─ 写 state['recalled_memories']
  │
  ├─ agent_node ↔ tools_node ↔ ...（详见维度 1）
  │     └─ recalled_memories 渲染进 system context
  │
  └─ END → memory_writeback_node
        ├─ 提取 (user_input, assistant_response) 对
        ├─ embedder.embed_documents([combined])
        ├─ memory_store.write(...) 用 ON CONFLICT DO NOTHING dedup
        └─ on failure → MemoryWritebackDLQ.enqueue(payload, next_retry_at=now+60)

后台 MemoryDLQWorker（lifespan task）
  │
  └─ loop:
        rows = SELECT * FROM memory_writeback_dlq
                WHERE status='pending' AND next_retry_at <= now()
        for row in rows:
            try:
                memory_store.write(row.payload)
                DELETE FROM memory_writeback_dlq WHERE id = row.id
            except:
                row.attempt += 1
                if row.attempt >= 5:
                    row.status = 'dead_letter'
                else:
                    row.next_retry_at = now + BACKOFF[row.attempt]

Admin UI 管理员手动操作
  │
  ├─ GET /v1/memory     → 列当前 user 的 memory（machine principal 403）
  ├─ PATCH /v1/memory/{id} → 改 content + 重 embed（embed 失败 503）
  └─ DELETE /v1/memory/{id} → soft delete (deleted_at = now)，幂等
```

## 3.6 局限与边界

- **没有 frozen snapshot**：跟 Hermes 的"加载时冻结 + 系统提示注入 + 中毒条目替换为占位符"不同，helix 的 memory 是 per-turn 动态召回，所以**前缀缓存命中率天然低**（每 turn 召回不同的 top-k）。
- **没有跨 turn 记忆汇总**：每 turn 独立 embed 查询，没有"短期记忆压缩成 1 条进长期"机制。M2 才有 archive 层。
- **没有 hybrid retrieval**：纯向量召回，没有"FTS5 + 向量 RRF rerank" —— RAG 子系统（J.5）有 RRF，但 memory 没有。
- **embedder 是同步 IO 阻塞点**：单次召回 / 写回都要等 embedder 服务响应（qwen `text-embedding-v4`），是 latency hot path。
- **跨 session 同一 user 共享**：所有 thread 共用同一个 `(tenant_id, user_id)` 维度的 memory，没有 per-thread / per-agent / per-skill 隔离粒度。
- **M2-C backlog**（`docs/ITERATION-PLAN.md:768-778`）：M0 是热数据；冷数据归档到 S3 + retrieve 路径在 M2 才有。
- **MEMORY.md 风格的"快照型记忆" / "USER.md 风格的角色档案" 不存在**：helix 没有这种"系统提示常驻的高密度文本块"概念，所有 memory 都是动态召回。

---

# 维度 4 — 上下文管理

## 4.1 现状评级

**生产级**（M0 Stream L.L2 ✅）。Hermes-derived "summarise the middle" 模式：preflight token estimate + head/tail 保留 + 中间 LLM 摘要 → `<context-summary>` SystemMessage + 多 pass 上限 + 失败显式 `ContextOverflowError`。Token usage 全链路追踪（G.9）。

ITERATION-PLAN 锚点：
- Stream L.L2：M0 ✅（PR #206）
- Stream E.3 `dynamic_context_middleware`：M0 ✅
- Stream G.9 `token_usage_middleware`：M0 ✅（PR #282）
- 跨 turn 迭代保留（"iterative summary preservation"）：**显式不做**（Mini-ADR L-2）

## 4.2 设计立场

`services/orchestrator/src/orchestrator/context/compressor.py:1-37` module docstring 直接给出四个核心设计选择（Mini-ADR L-2）：

```
* **One-shot per turn** — we don't keep a running summary across
  compressions (Hermes "iterative summary preservation"). Each
  compression starts fresh from the current message list; if the
  conversation grows large enough to need compression repeatedly the
  individual passes are cheap, and the result is easier to reason
  about than a self-feeding summary.
* **Independent summariser LLM call** — the compressor takes its own
  :class:`LLMCaller`. The agent's main router may go through the
  same caller, but the contract is "summarise this, return one
  message" rather than "act as the agent" so a future hop to a
  dedicated cheaper model is a one-field change.
* **Hard cap via ``max_passes``** — if successive summarisations
  cannot bring the estimated size below threshold the compressor
  raises :class:`ContextOverflowError`. Hiding the failure behind a
  silent fallback would let the run keep ballooning until the
  upstream rejects it; the explicit signal lets the orchestrator log
  a clean run-failed audit.
* **Rough char-based estimator** — ``estimate_tokens`` returns
  ``total_chars // 4``. Cheaper than tiktoken (no dependency, no
  per-message tokeniser call) and Hermes uses the same rule of
  thumb. ...
```

总结：**确定性优先于自适应** —— 不保留跨 turn summary（每次重新算）、超限直接 raise（不静默）、估算用 char 而不是 tokeniser（确定 + 便宜）。

跟 Hermes 的"最多 3 轮多 pass 压缩"对应 —— helix `max_passes` 默认也是 3（Hermes 经验值的直接移植，因为 Stream L 就是从 Hermes 学的）。

## 4.3 关键代码路径

| 角色 | 文件 | 关键符号 |
|------|------|---------|
| 模块 docstring | `services/orchestrator/src/orchestrator/context/compressor.py:1-38` | Mini-ADR L-2 4 条 |
| 常量 | `compressor.py:53-69` | `_CHARS_PER_TOKEN=4` / `_SUMMARY_TAG_OPEN/CLOSE` / `_SUMMARISER_SYSTEM_PROMPT` |
| Overflow 异常 | `compressor.py:72+` | `class ContextOverflowError` |
| ContextCompressor | `compressor.py:120+`（推断） | `@dataclass class ContextCompressor` |
| 主循环触发 | `services/orchestrator/src/orchestrator/graph_builder/builder.py` | agent_node 入口的 preflight |
| Token 估算 | `compressor.py` 内 | `estimate_tokens(messages) -> int` |
| Dynamic context middleware | `packages/helix-runtime/src/helix_agent/runtime/middleware/dynamic_context.py` | `DynamicContextMiddleware`（E.3） |
| Token usage middleware | `packages/helix-runtime/src/helix_agent/runtime/middleware/token_usage.py` | `TokenUsageMiddleware`（G.9） |
| Token usage store | `packages/helix-persistence/src/helix_agent/persistence/token_usage_store.py` | `SqlTokenUsageStore`（migration 0036） |

## 4.4 实现细节

### 4.4.1 摘要 prompt（`compressor.py:63-69`）

```python
_SUMMARISER_SYSTEM_PROMPT: str = (
    "You are a context compressor. Summarise the conversation excerpt "
    "below into 3-7 short bullet points capturing the essential facts, "
    "decisions, and pending work items. Preserve specific names, paths, "
    "and numerical values verbatim. Do not include any tool-call syntax "
    "or speculation about future steps."
)
```

vs Hermes 的 `SUMMARY_PREFIX`（300+ 字符的复杂控制提示），helix 的摘要 prompt **简短得多** —— 只给摘要器，不给主 agent。摘要本身落在 `<context-summary>` SystemMessage 里作为 reference。

### 4.4.2 ContextCompressor 字段（已确认）

```python
@dataclass(frozen=True)
class ContextCompressor:
    """Stream L.L2 — token-based context compression."""

    summariser: LLMCaller             # 独立的摘要 LLM 调用者（可与 agent 主 router 不同模型）
    context_window: int = 200000      # claude-opus 默认
    threshold_pct: float = 0.80       # 80% 时触发
    head_keep: int = 2                # 头部保留 2 条非 system
    tail_keep: int = 3                # 尾部保留 3 条非 system
    max_passes: int = 3               # 上限 3 轮，超过 raise ContextOverflowError

    async def compress(
        self, messages: Sequence[BaseMessage]
    ) -> Sequence[BaseMessage]:
        """Compress to fit under threshold."""
```

### 4.4.3 摘要落地的关键约束（Mini-ADR L-2）

- **summary 是 SystemMessage 不是 HumanMessage**：跟 Hermes 选择"summary HumanMessage 前置到 tail 首条 user msg"不同 —— helix 用独立 SystemMessage，保护 leading SystemMessage 的字节稳定性（L1 prompt caching 不变式）。
- **整个 compression 不再写 dynamic context**：L2 跟 E.3 `DynamicContextMiddleware` 都改 messages，但 L2 在 agent_node 入口先做，E.3 在 `before_llm_call` chain 里做。

### 4.4.4 `PolicySpec.context_compression` 配置

manifest 字段（`packages/helix-protocol/src/helix_agent/protocol/agent_spec.py` 推断）：

```yaml
spec:
  policies:
    context_compression:
      enabled: true
      threshold_pct: 0.80
      head_keep: 2
      tail_keep: 3
      max_passes: 3
      max_turns: null         # legacy（DynamicContextMiddleware 用）
      max_tokens: null        # legacy
```

`enabled=false` 时跳过 L2 路径，回退到 E.3 的 turn/token 截断式 DynamicContextMiddleware（更原始的策略）。

### 4.4.5 Token Usage 全链路（G.9，PR #282）

`packages/helix-runtime/src/helix_agent/runtime/middleware/token_usage.py` `TokenUsageMiddleware`：

- `after_llm_call` 中间件，从 `AIMessage.usage_metadata` 提取 input/output/cached tokens；
- emit Prometheus counter `helix_llm_token_usage_total{tenant, agent, model, type}`；
- 写持久表 `token_usage`（migration 0036）；
- per (tenant, agent, model) 维度累计，给 Grafana 大盘和成本视图喂数。

cache token 也单独分桶（L1 Anthropic prompt caching 把 `cache_creation_input_tokens` / `cache_read_input_tokens` 暴露在 `AIMessage.usage_metadata.input_token_details`）。

## 4.5 运行时行为

```
agent_node 入口
  │
  ├─ 如果 ContextCompressor 注入：
  │     ├─ for _pass in range(max_passes):
  │     │     est = estimate_tokens(messages)
  │     │     if est < context_window * threshold_pct: break
  │     │     compressed = await compressor.summarise_middle(messages)
  │     │     messages = compressed
  │     │
  │     └─ if est >= threshold and _pass == max_passes - 1:
  │           raise ContextOverflowError → run terminate (failed)
  │
  ├─ before_llm_call middleware chain
  │     ├─ E.3 DynamicContextMiddleware（manifest dynamic_context 注入）
  │     ├─ E.5 PII redact
  │     └─ E.13 cache lookup
  │
  ├─ LLMCaller.complete(messages, tools)
  │
  └─ after_llm_call middleware chain
        ├─ E.10.5 loop detection
        ├─ E.13 cache store
        ├─ G.9 TokenUsageMiddleware
        │     ├─ helix_llm_token_usage_total{tenant, agent, model, type=input}
        │     ├─ helix_llm_token_usage_total{tenant, agent, model, type=output}
        │     ├─ helix_llm_token_usage_total{tenant, agent, model, type=cached_input}
        │     └─ SqlTokenUsageStore.persist(...)
        └─ E.5 Langfuse trace
```

## 4.6 局限与边界

- **没有"iterative summary preservation"**（显式选择不做）：每次压缩都从当前 messages 重新算，N 次压缩 = N × 摘要器成本。
- **没有运行时 413 兜底**：API 拿到 413 不会触发再压缩 + 重试；ContextCompressor 是 preflight 唯一路径，超 `max_passes` 直接 fail run。
- **estimate 用 char/4 而非真实 tokenizer**：CJK 文本会偏激进，但 `threshold_pct=0.80` 留 20% headroom 兜底。
- **图像 token 估算缺失**：跟 Hermes 把每图算 1600 token 不同，helix 的 `estimate_tokens` 看起来不专门处理图像 message（J.6 多模态 message 进 messages 后估算可能偏低）。
- **summary 模型固定靠 manifest 配**：没有自动"按 turn 长度选 cheaper / better"路由。
- **没有"intelligent routing 按 query 路由模型"**：跟 Hermes 一样，主模型切换靠 manifest 显式配，不自动按任务类型决定。

---

# 维度 5 — 模型 / Provider 抽象

## 5.1 现状评级

**部分实现**。
- **核心 Protocol 抽象 + 9 个 provider + fallback tree + per-provider 限流 + stream stale + OAuth refresh**：M0 ✅（Stream E.11/E.11.5/E.12 + L.L3 + L.L8）。
- **国内 provider 套件**：M0 ✅（kimi / glm / deepseek / qwen / doubao）。
- **LiteLLM 集成**：不在 scope（设计选择 — 自研 Protocol 适配层）。
- **动态 provider 切换**（在 run 中换 provider）：不在 scope（需要 rebuild agent）。

ITERATION-PLAN 锚点：
- Stream E.11 LLM Provider Fallback Chain + E.11.5 国内 provider：M0 ✅
- Stream E.12 提供商层限流：M0 ✅
- Stream E.13 LLM response cache：M0 ✅
- Stream L.L3 Stream stale-detection：M0 ✅
- Stream L.L8 OAuth 401 refresh：M0 ✅

## 5.2 设计立场

`services/orchestrator/src/orchestrator/llm/router.py:82-95` 用 **`@runtime_checkable Protocol`** 而不是抽象基类：

```python
@runtime_checkable
class LLMProvider(Protocol):
    """Wire-level LLM caller — one provider, one model.

    Concrete adapters
    (:class:`~orchestrator.llm.providers.anthropic.AnthropicProvider`,
    :class:`~orchestrator.llm.providers.openai.OpenAIProvider`)
    translate :class:`BaseMessage` / :class:`ToolSpec` into the provider's
    wire format and back, raising :class:`LLMError` subclasses for
    transport / vendor failures. The router treats every
    :class:`LLMProvider` interchangeably; differences between Anthropic
    and OpenAI (system prompt placement, tool schemas, etc.) are an
    adapter concern.
    """

    async def complete(self, *, messages, ...) -> AIMessage: ...
```

这是个**鸭子类型 Protocol 而不是 dataclass profile**，跟 Hermes 的 `ProviderProfile`（声明式 dataclass）完全是两种思路。设计立场：

1. **行为契约优先**：provider 只需实现 `async complete()`，不需要声明 "我支持什么 auth / 我的 fixed_temperature 是什么"。
2. **错误分类驱动 fallback**：fallback 行为由 raise 哪个 `LLMError` 子类决定（client_error 不 fallback，server_error / rate_limit / network_error 才 fallback）。
3. **每个 provider 独立断路器 + 独立 langfuse span**（Mini-ADR E-13）：`around_llm_call` middleware chain 在 router 内部 **per-provider** 包装。
4. **声明式 Tree fallback**：`ModelSpec.fallback: list[ModelSpec]` 支持嵌套（`_flatten_chain()` 展平成有序链）。
5. **限流是 await 不是 raise**（Mini-ADR E-12）：`RateLimitedProvider` 用 `aiolimiter.AsyncLimiter` 漏桶，到限就 await 不抛 429，避免污染 E.4 断路器。

## 5.3 关键代码路径

| 角色 | 文件 | 关键符号 |
|------|------|---------|
| Router 模块 docstring | `services/orchestrator/src/orchestrator/llm/router.py:1-37` | fallback 语义 |
| Stream stale counter | `services/orchestrator/src/orchestrator/llm/router.py:66-70` | `_llm_stream_stale_total` (L.L3) |
| OAuth refresh counter | `services/orchestrator/src/orchestrator/llm/router.py:75-79` | `_llm_auth_refresh_total` (L.L8) |
| LLMProvider Protocol | `services/orchestrator/src/orchestrator/llm/router.py:82-95` | `@runtime_checkable class LLMProvider(Protocol)` |
| LLMRouter | `services/orchestrator/src/orchestrator/llm/router.py:100+`（推断） | `class LLMRouter` |
| OAuth 扩展 | `services/orchestrator/src/orchestrator/llm/oauth_provider.py` | `OAuthCapableProvider` Protocol（L.L8） |
| Anthropic adapter | `services/orchestrator/src/orchestrator/llm/providers/anthropic.py` | `HTTPAnthropicClient` |
| OpenAI adapter | `services/orchestrator/src/orchestrator/llm/providers/openai.py` | `OpenAIProvider` / `HTTPOpenAIClient` |
| OpenAI-compatible 工厂 | `services/orchestrator/src/orchestrator/llm/providers/openai_compatible.py` | `make_kimi_client` / `make_glm_client` / `make_deepseek_client` / `make_qwen_client` / `make_doubao_client` / `make_self_hosted_client` |
| Rate limiter | `services/orchestrator/src/orchestrator/llm/rate_limit.py` | `RateLimitedProvider`（`aiolimiter.AsyncLimiter`） |
| ModelSpec | `packages/helix-protocol/src/helix_agent/protocol/agent_spec.py:74-148` | `class ModelSpec` |
| Agent factory router 构建 | `services/orchestrator/src/orchestrator/agent_factory.py` | `build_llm_router(...)` / `_flatten_chain(...)` |

## 5.4 实现细节

### 5.4.1 LLMProvider Protocol 的"鸭子类型"语义

`router.py:82-105`（最末 `await self.complete` 签名推断）：

```python
@runtime_checkable
class LLMProvider(Protocol):
    async def complete(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec] | None = None,
        # ... provider-specific kwargs 不在 Protocol 上
    ) -> AIMessage: ...
```

`runtime_checkable` 让 `isinstance(provider, LLMProvider)` 在 runtime 可用 —— 比如 L.L8 的 OAuth 路径会：

```python
if isinstance(provider, OAuthCapableProvider):
    refreshed = await provider.refresh_credentials()
    if refreshed:
        ...
```

只有实现了 `refresh_credentials()` 的 provider 走 OAuth 路径，其他直接 401 → fallback。

### 5.4.2 Fallback 语义（来自 `router.py:8-17`）

| 异常 | 行为 |
|------|------|
| `LLMClientError` (4xx) | **不 fallback**，立即 re-raise（malformed request 下一个 provider 也会拒） |
| `LLMServerError` (5xx) | log + 继续下一个 provider |
| `LLMRateLimitError` (429 / 自身限流耗尽) | log + 继续 |
| `LLMNetworkError` | log + 继续 |
| `CircuitOpenError` | log + 继续（E.4 断路器跳） |
| `LLMStreamStaleError` (L.L3) | 继承 `LLMServerError` 走 fallback |
| `LLMAuthError` (L.L8 refresh 失败) | 继承 `LLMServerError` 走 fallback |
| 全部 provider 耗尽 | `AllProvidersExhaustedError` 包最后一次异常 |

**关键不变式**：router 自己 **不重试**（同一 provider 只跑一次）—— 重试是 `LLMErrorHandlingMiddleware`（E.4 `around_llm_call`）的事，per-provider 包装。这避免同 provider 反复打导致限流爆。

### 5.4.3 9 个 provider 的实现地图

| Provider | 协议 | 文件 | auth |
|----------|------|------|------|
| Anthropic | Messages API native | `providers/anthropic.py` | `x-api-key` + `anthropic-version` |
| OpenAI | Chat Completions | `providers/openai.py` | Bearer |
| Azure OpenAI | OpenAI 兼容 | （同上工厂） | Bearer + deployment URL |
| Self-hosted (vLLM / Ollama / llama.cpp) | OpenAI 兼容 | `providers/openai_compatible.py:make_self_hosted_client` | 可选 |
| Kimi (Moonshot) | OpenAI 兼容 | `make_kimi_client` | Bearer |
| GLM (Zhipu) | OpenAI 兼容 | `make_glm_client` | Bearer |
| DeepSeek | OpenAI 兼容 | `make_deepseek_client` | Bearer |
| Qwen (Alibaba) | OpenAI 兼容 | `make_qwen_client` | Bearer |
| Doubao (ByteDance) | OpenAI 兼容 | `make_doubao_client` | Bearer |

E.11.5 国内 provider 全部走 OpenAI 兼容协议 + 共享 `HTTPOpenAIClient` —— 每个工厂函数主要差异在 `base_url` 和默认 model 名。

### 5.4.4 ModelSpec 字段（来自之前探索 + STREAM-E-DESIGN）

```python
class ModelSpec(BaseModel):
    provider: Literal["anthropic", "openai", "azure", "self-hosted",
                       "kimi", "glm", "deepseek", "qwen", "doubao"]
    name: str                           # e.g. "claude-opus-4.7-20250515"
    base_url: str | None = None         # self-hosted 必填
    api_key_ref: str | None = None      # 经 SecretStore 解
    context_window_size: int = 200_000  # L.L2 用
    cache_enabled: bool = True          # L.L1 Anthropic prompt caching 开关
    supports_vision: bool = False       # J.6 决定 Path A vs Path B
    fallback: list[ModelSpec] = []      # tree-based fallback
    routing: RoutingRules | None = None # J.11 step-class routing
```

`fallback` 是**嵌套结构**（每个 fallback 自己也可以有 fallback），agent_factory 的 `_flatten_chain()` 把树展平：

```
primary
  └─ fallback[0]              # priority 1
       └─ fallback[0].fallback[0]   # priority 2
       └─ fallback[0].fallback[1]   # priority 3
  └─ fallback[1]              # priority 4
```

加上 extra_fallbacks（如 J.6 vision router 的 VL 替补），最终顺序 priority 是 `primary → primary.fallback... → extra_fallbacks[0]...`。

### 5.4.5 L.L3 Stream stale-detection（PR #199）

`router.py:66-70`：

```python
_llm_stream_stale_total = helix_counter(
    "helix_llm_stream_stale_total",
    "Provider calls that exceeded LLMRouter.stream_deadline_s (Stream L.L3).",
    ("provider_key",),
)
```

实现策略（`AgentSpecBody.stream_deadline_s: int = 90`）：每 provider `complete()` 内 `asyncio.wait_for(coro, timeout=stream_deadline_s)`，超时 raise `LLMStreamStaleError`（继承 `LLMServerError` 走 retryable → router 自动 fallback）。

放在 per-provider 层而不是 agent_node 外层（Mini-ADR L-3 明确）—— 外层 wrap 会让 hung primary 吃掉 fallback 预算。

### 5.4.6 L.L8 OAuth refresh（PR #201）

`router.py:75-79`：

```python
_llm_auth_refresh_total = helix_counter(
    "helix_llm_auth_refresh_total",
    "Credential refreshes triggered by OAuth-capable provider 401s (Stream L.L8).",
    ("provider_key", "result"),  # result ∈ {success, fail}
)
```

`OAuthCapableProvider` Protocol：

```python
@runtime_checkable
class OAuthCapableProvider(Protocol):
    async def refresh_credentials(self) -> bool: ...
```

行为：401 → check `isinstance(provider, OAuthCapableProvider)` → if True call `refresh_credentials()` → 重试 1 次；连续 401 / refresh False → `LLMAuthError` → fallback。

## 5.5 运行时行为

```
agent_factory.build_llm_router(ModelSpec, extra_fallbacks=[])
  │
  ├─ _flatten_chain(model_spec) → [primary, fb1, fb2, ...]
  │
  └─ for spec in chain:
        provider = _build_provider(spec)  # 按 spec.provider 路由到 9 个 adapter 之一
        provider = RateLimitedProvider.with_rpm(provider, rpm=...)
        handles.append(ProviderHandle(spec=spec, provider=provider))
  │
  └─ return LLMRouter(handles=handles, middleware_chain=chain, stream_deadline_s=90)

LLMRouter.complete(messages, tools)
  │
  └─ for handle in handles:
        try:
            # E.12.5: middleware chain per-provider
            response = await chain.invoke("around_llm_call", ctx,
                                           terminal=handle.provider.complete)
            # L.L3 wraps `complete` 内 asyncio.wait_for(stream_deadline_s)
            return response
        except LLMClientError:
            raise  # 4xx 不 fallback
        except LLMUnauthorizedError:
            # L.L8 OAuth refresh 路径
            if isinstance(handle.provider, OAuthCapableProvider):
                refreshed = await handle.provider.refresh_credentials()
                if refreshed:
                    try:
                        return await chain.invoke(...)  # 重试 1 次
                    except LLMUnauthorizedError:
                        raise LLMAuthError(...) from ...  # 走 fallback
            raise  # non-OAuth 直接 propagate
        except (LLMServerError, LLMRateLimitError, LLMNetworkError,
                CircuitOpenError, LLMStreamStaleError, LLMAuthError):
            logger.warning(...); continue  # 下一个 provider
  │
  └─ raise AllProvidersExhaustedError(last_exc)
```

## 5.6 局限与边界

- **fetch_models / 动态发现模型列表不存在**：跟 Hermes 的 `fetch_models()`（每 provider 自己探）不同，helix 不主动列模型 —— 模型名靠 manifest 写死。
- **provider 是配置驱动 + 工厂函数**：新 provider 需要写代码（不是 plugin discovery）。
- **没有用户级 `$HERMES_HOME/plugins/model-providers/` 覆盖**：所有 provider 都在 services/orchestrator 代码里。
- **Streaming delta 由各 adapter 处理**：HTTPAnthropicClient vs HTTPOpenAIClient 的流式 delta schema 差异在 adapter 内消化，router 只看到统一的 `AIMessage`。
- **rate limit 是 per-provider-key 内存**：不跨进程；多 orchestrator replica 各自一份桶。
- **fallback 是静态配置**：没有"根据 latency / cost / 历史 success rate 动态选 next"。
- **没有跨 provider 的智能 cost / latency 路由**：J.11 step-class routing 是按 step 名（planning / reflection / agent）选 router，不是按当前任务自适应。

---

# 维度 6 — 本地推理（Ollama / vLLM / llama.cpp）

## 6.1 现状评级

**部分实现**。
- **配置入口**：`provider="self-hosted"` + `base_url` 接 vLLM / Ollama / llama.cpp / LocalAI / Mistral 官方 OpenAI 兼容端点：M0 ✅
- **无 Ollama 专属支持**：没有像 Hermes 的 `query_ollama_num_ctx()` 那种通过 `/api/show` 拿真实 num_ctx 的探针
- **无本地端点自适应 timeout**：跟 Hermes 给本地端点放大 stream_read_timeout 不同，helix 全局用 `stream_deadline_s` 不分本地远程
- **无自动启动 / 模型下载**：用户负责 `ollama serve` / `vllm serve`

ITERATION-PLAN 锚点：
- 通过 Stream E.11.5 的 `self-hosted` provider 落地：M0 ✅
- 无专门的"本地推理 stream"

## 6.2 设计立场

helix 的策略是：**把本地推理当作一种特殊的 OpenAI-compatible provider** ——

- 没有"本地 vs 远程"的概念区分；
- 任何暴露 `/v1/chat/completions` 的服务都一视同仁；
- 唯一特殊点：`base_url` 必填（其他 provider 用默认）。

`packages/helix-protocol/tests/test_agent_spec.py:349-358` 是规范用法：

```python
model = ModelSpec.model_validate({
    "provider": "self-hosted",
    "name": "llama-3.1-70b",
    "base_url": "http://vllm.internal:8000",
})
assert model.provider == "self-hosted"
assert model.base_url == "http://vllm.internal:8000"
```

## 6.3 关键代码路径

| 角色 | 文件 | 关键符号 |
|------|------|---------|
| `self-hosted` 工厂 | `services/orchestrator/src/orchestrator/llm/providers/openai_compatible.py:155-200` | `make_self_hosted_client(api_key, base_url=...)` |
| Provider 路由 | `services/orchestrator/src/orchestrator/agent_factory.py:756-800`（推断 `_build_provider` 分支） | 按 `spec.provider == "self-hosted"` 进入 |
| ModelSpec | `packages/helix-protocol/src/helix_agent/protocol/agent_spec.py:115-120` | `base_url: str | None = None` |
| 测试规范 | `packages/helix-protocol/tests/test_agent_spec.py:349-358` | vLLM 配置示例 |

## 6.4 实现细节

### 6.4.1 配置示例

```yaml
spec:
  model:
    provider: "self-hosted"
    name: "llama-3.1-70b"
    base_url: "http://vllm.internal:8000"
    api_key_ref: "secret://local-key"  # 可选；Ollama 通常无需 auth
    context_window_size: 131072        # L.L2 用，需手填
```

也可以用 Ollama：

```yaml
spec:
  model:
    provider: "self-hosted"
    name: "qwen2.5-coder:32b"
    base_url: "http://localhost:11434/v1"
    api_key_ref: null  # Ollama 不验证
    context_window_size: 32768
```

### 6.4.2 `make_self_hosted_client` 实现策略（推断）

```python
def make_self_hosted_client(
    api_key: str | None,
    *,
    base_url: str,
    chat_completions_path: str = "/v1/chat/completions",
) -> HTTPOpenAIClient:
    """Return an OpenAI-compatible HTTPOpenAIClient pointed at base_url."""
    return HTTPOpenAIClient(
        api_key=api_key or "not-needed",
        base_url=base_url,
        chat_completions_path=chat_completions_path,
    )
```

跟其他 `make_kimi_client` / `make_glm_client` 共享同一 `HTTPOpenAIClient`，区别只在 `base_url`。

### 6.4.3 兼容性

任何暴露 OpenAI `/v1/chat/completions` 的服务都可以接：

- **vLLM**：`vllm serve --host 0.0.0.0 --port 8000 --model meta-llama/Meta-Llama-3.1-70B-Instruct`
- **Ollama**：`ollama serve`（11434 端口），但 model name 要带 tag（`qwen2.5-coder:32b`）
- **llama.cpp**：`llama-server --model ggml.gguf --port 8080`，OpenAI 兼容模式
- **LocalAI**：`local-ai run --address :8080`
- **HuggingFace TGI**：`text-generation-launcher --port 8080`

### 6.4.4 限流 / 错误处理共享

`self-hosted` provider 仍然走 `RateLimitedProvider.with_rpm()`，所有错误也走同一套 `LLMError` 分类 + 断路器 + L.L3 stream stale + L.L8 OAuth（如果实现了 refresh）。**完全平等**。

## 6.5 运行时行为

```
hermes 启动 / Agent init
  │
  ├─ AgentFactory.build_agent(manifest)
  │     ├─ build_llm_router(model_spec)
  │     │     ├─ _build_provider(spec):
  │     │     │     if spec.provider == "self-hosted":
  │     │     │         return make_self_hosted_client(
  │     │     │             api_key=secret_store.get(spec.api_key_ref),
  │     │     │             base_url=spec.base_url,
  │     │     │         )
  │     │     └─ wrap with RateLimitedProvider + chain handles
  │     │
  │     └─ build_react_graph + ContextCompressor(context_window=spec.context_window_size)
  │
  └─ 主循环
        └─ provider.complete(messages, tools)
              → httpx POST {base_url}/v1/chat/completions
                Authorization: Bearer {api_key}
              → JSON response → AIMessage
```

## 6.6 局限与边界

- **无本地端点 timeout 自适应**：本地模型可能比远程慢 10×，但 `stream_deadline_s=90` 是全局值。
- **无 num_ctx 自动探测**：vLLM 模型的实际 context window 写在 `/v1/models` 的 metadata，但 helix 不主动读 —— 必须 manifest 写对 `context_window_size`，写错会导致 L.L2 误判或 token 浪费。
- **无本地 embedding 集成**：J.3 memory 的 embedder 走 qwen `text-embedding-v4` （`/v1/embeddings` 兼容），可以接本地 vLLM embedding 模型，但没有"自动用本地"的优化。
- **无 vLLM-specific 优化**：LoRA adapter selection、prefix caching、speculative decoding 等 vLLM 高级特性都用不上（要靠 vLLM server side 配置，helix 不感知）。
- **无 llama.cpp grammar**：约束输出格式只能靠 LLM 自己（OpenAI function calling），不能用 GBNF。
- **无网络 / 内存检测**：Hermes 有 Tailscale CGNAT 网段识别，helix 没有这种"端点是不是局域网内"的判断。

---

# 维度 7 — 沙箱执行

## 7.1 现状评级

**生产级**（M0 Stream F ✅ + K.K5 / J.15 hardening）。`services/sandbox-supervisor/` 是**独立服务**（4452 行），通过 mTLS 跟 orchestrator 通信。Docker + gVisor (runsc) 双 runtime + 持久化 workspace + egress 网络隔离 + 完整资源 caps + 容器化（M1 sandbox warm pool 推迟）。F.8 自动化 harness 关 5/7 安全门，剩 2 个（CVE-2019-5736 / timing）推 M0→M1 Gate staging 跑。

ITERATION-PLAN 锚点：
- Stream F.1-F.11：M0 ✅
- K.K5 gVisor Gate Exit Criteria：M0 ✅（docs lock）
- J.15 持久化 workspace：M0 ✅
- M1-A Sandbox warm pool（P95 < 500ms）：M1 backlog

## 7.2 设计立场

`packages/helix-runtime/src/helix_agent/runtime/sandbox/runtime_provider.py:1-18` 给出核心拆分：

> "The Sandbox Supervisor (F.1) launches one container per ``exec_python`` call. *How* it is launched — the OCI runtime and the hardening flags — is owned here, so a single place enforces the Mini-ADR F-5 checklist and the dev (``runc``) vs prod (``runsc`` / gVisor) split is one config knob rather than branching scattered across the supervisor.
>
> subsystem 14 § 5.5: gVisor is Linux-only, so dev (incl. macOS) runs ``runc`` — it verifies sandbox *behaviour*, not isolation *strength*; the gVisor isolation gates run on a Linux CI runner under ``runsc``.
>
> The provider only *builds* the argv — it never calls Docker. That keeps it pure and unit-testable (test matrix #43) and leaves process execution to the supervisor."

设计立场：

1. **构造与执行分离**：`SandboxRuntimeProvider` 只**生成** docker argv（纯函数 + 单元可测），supervisor 才**执行**。
2. **OCI runtime 切换是一个 config 字段**：`runc`（dev / macOS）vs `runsc`（gVisor，Linux prod），不在代码里 if-else 散落。
3. **每次 exec_python 一个容器**：不复用（M1 才有 warm pool）；启动成本换隔离干净。
4. **Brain-Hands 物理隔离**：sandbox 内**无 hermes runtime / 无 LLM client / 无凭据**，凭据由 credential-proxy 注入出站请求。
5. **网络硬隔离**：`helix-sandbox-egress` 是 Docker `--internal` 网络 —— 沙箱**只能**访问同网络内的 credential-proxy，连不上外网 / 元数据服务 / 宿主。

## 7.3 关键代码路径

| 角色 | 文件 | 关键符号 |
|------|------|---------|
| Sandbox supervisor 服务 | `services/sandbox-supervisor/` (4452 行) | FastAPI service |
| Lifecycle | `services/sandbox-supervisor/src/sandbox_supervisor/lifecycle.py` | 容器生命周期 |
| Docker CLI 封装 | `services/sandbox-supervisor/src/sandbox_supervisor/docker_client.py` | `CliDockerClient` |
| Supervisor 主入口 | `services/sandbox-supervisor/src/sandbox_supervisor/supervisor.py` | 主逻辑 |
| Quota enforcer | `services/sandbox-supervisor/src/sandbox_supervisor/quota_enforcer.py` | 资源配额 |
| In-sandbox runner link | `services/sandbox-supervisor/src/sandbox_supervisor/runner_link.py` | stdio pipe protocol |
| Runtime provider | `packages/helix-runtime/src/helix_agent/runtime/sandbox/runtime_provider.py:51+` | `SandboxRuntimeProvider` |
| OCI runtime 字面量 | `runtime_provider.py:27` | `SandboxOciRuntime = Literal["runc", "runsc"]` |
| 默认 egress 网络 | `runtime_provider.py:32` | `DEFAULT_EGRESS_NETWORK = "helix-sandbox-egress"` |
| 默认资源限制 | `runtime_provider.py:35-47` | `SandboxResourceLimits` (1.0 CPU / 512MB / 128 PIDs / 64MB) |
| `exec_python` 工具 | `services/orchestrator/src/orchestrator/tools/sandbox.py` | sandbox 工具集成 |
| F.8 集成测试 | `services/sandbox-supervisor/tests/` | gate #1/#2/#4/#5/#8 自动化 |

## 7.4 实现细节

### 7.4.1 `SandboxRuntimeProvider.docker_run_argv` 完整 argv（`runtime_provider.py:61-106`）

```python
def docker_run_argv(
    self, *,
    image: str,
    container_name: str,
    limits: SandboxResourceLimits = DEFAULT_RESOURCE_LIMITS,
    workspace_volume: str | None = None,
) -> list[str]:
    argv = [
        "docker", "run",
        "--name", container_name,
        "--interactive",                 # 保 stdin 给 in-sandbox runner JSON-lines 协议
        "--read-only",                   # rootfs 只读
        *self._workspace_mount(limits, workspace_volume),
        "--cap-drop", "ALL",             # 删除所有 Linux capability
        "--security-opt", "no-new-privileges",  # 禁 setuid 提权
        "--pids-limit", str(limits.pids_limit),  # 默认 128
        "--memory", f"{limits.memory_mb}m",      # 默认 512MB
        "--cpus", str(limits.cpus),              # 默认 1.0
        "--network", self.egress_network,        # helix-sandbox-egress
    ]
    if self.oci_runtime == "runsc":
        argv += ["--runtime", "runsc"]   # gVisor
    argv.append(image)
    return argv
```

这跟 Hermes 的 docker backend 默认基本一致（cap-drop ALL / no-new-privileges / pids-limit / memory / cpus），但 helix 额外强制 `--read-only` rootfs 和 `--network helix-sandbox-egress`，**比 Hermes 默认更紧**。

### 7.4.2 workspace 两种模式（`runtime_provider.py:108-122`）

```python
@staticmethod
def _workspace_mount(limits, workspace_volume: str | None) -> list[str]:
    """The ``/workspace`` mount flags — tmpfs or a persistent volume."""
    if workspace_volume is None:
        # Ephemeral tmpfs. mode=1777: the tmpfs root mounts root-owned,
        # so without it the image's non-root ``agent`` user cannot
        # create files (F.8 gate #1).
        return [
            "--tmpfs",
            f"/workspace:rw,size={limits.workspace_size_mb}m,mode=1777",
        ]
    # Stream J.15 — a per-user docker named volume. A fresh volume
    # inherits the image's ``/workspace`` ownership (``agent:agent``),
    # so unlike tmpfs it needs no mode override.
    return ["--volume", f"{workspace_volume}:/workspace"]
```

- **Ephemeral**：`--tmpfs /workspace:rw,size=64m,mode=1777`，容器销毁即删。
- **Persistent**（J.15）：`--volume {workspace_volume}:/workspace`，per-user named volume，跨容器保留。

### 7.4.3 OCI runtime 验证（`runtime_provider.py:125-144`）

```python
def make_sandbox_runtime_provider(
    oci_runtime: str,
    *,
    egress_network: str = DEFAULT_EGRESS_NETWORK,
) -> SandboxRuntimeProvider:
    """..."""
    valid: tuple[str, ...] = get_args(SandboxOciRuntime)
    if oci_runtime not in valid:
        msg = f"unknown sandbox OCI runtime: {oci_runtime!r} (expected one of {valid})"
        raise ValueError(msg)
    return SandboxRuntimeProvider(
        oci_runtime=oci_runtime,
        egress_network=egress_network,
    )
```

`oci_runtime` 是 `str` 接收（不是 Literal），因为来自 `environments/{env}.yaml`；未知值 raise `ValueError` 早 fail。

### 7.4.4 In-sandbox runner stdio 协议（`runner_link.py`）

```
Supervisor
  │
  ├─ docker run -i {sandbox image}
  │     │
  │     └─ in-sandbox python runner 启动:
  │           - sys.stdin loop:
  │               for line in sys.stdin:
  │                   request = json.loads(line)
  │                   if request["op"] == "exec_python":
  │                       result = exec_code(request["code"])
  │                       sys.stdout.write(json.dumps(result) + "\n")
  │
  └─ PipeRunnerLink:
        - write JSON line to container's stdin
        - read JSON line from container's stdout
        - timeout via asyncio.wait_for
```

### 7.4.5 F.8 集成 harness 关的 5/7 安全门

`docs/ITERATION-PLAN.md:294-302`：

- ✅ **gate #1** 文件系统隔离（F.8 harness 自动）
- ✅ **gate #2** 进程隔离（F.8 harness 自动）
- ✅ **gate #3** 网络隔离（F.9 `--internal` 网络，测试矩阵 #49，实测连 `169.254.169.254` 被拒）
- ✅ **gate #4** secret 不可见（F.8 harness 自动）
- ✅ **gate #5** fork bomb PID limit（F.8 harness 自动）
- ⏳ **gate #6** timing 测试（需真实 runsc → M0→M1 Gate 人工渗透）
- ⏳ **gate #7** CVE-2019-5736 PoC（需真实 runsc → M0→M1 Gate 人工渗透）
- ✅ **gate #8** 取消信号 1s 内 kill 干净（F.8 harness 自动）

剩 2 个推到 staging Linux + 真实 runsc 跑（**K.K5 锁定不允许"软推迟"豁免**，`docs/ITERATION-PLAN.md:388`）。

## 7.5 运行时行为

```
orchestrator agent_node 决定调 exec_python
  │
  └─ tools_node._dispatch_tool(SandboxTool, args={"code": "..."})
        │
        ├─ HTTP POST {sandbox_supervisor_url}/v1/sandbox/exec_python
        │     {"thread_id": ..., "code": ..., "timeout": 60}
        │
        └─ supervisor.lifecycle.acquire(thread_id):
              │
              ├─ resolve workspace_volume (J.15 per-user) or None (ephemeral)
              ├─ runtime_provider.docker_run_argv(image, container_name, limits, volume)
              ├─ CliDockerClient.run(argv)
              │     └─ subprocess: docker run -i --read-only --cap-drop ALL ...
              │
              ├─ PipeRunnerLink(stdin, stdout)
              ├─ link.send_request({"op": "exec_python", "code": ...})
              ├─ link.recv_result()  ← async with timeout
              │
              ├─ on cancellation_token.is_set():
              │     docker.kill(container_name)  ← 1s 内 (F.8 gate #8)
              │
              ├─ on success:
              │     docker.rm(container_name)
              │     return ToolResult(content=stdout, meta={...})
              │
              └─ on timeout:
                    docker.kill + rm
                    return ToolResult(content="[timeout]")

容器内（沙箱内）
  │
  ├─ 没有 helix runtime / 没有 LLM client / 没有 API key
  ├─ rootfs read-only，只能写 /workspace
  ├─ 出站只能到 helix-sandbox-egress 网络内的 credential-proxy
  │     └─ credential-proxy 拉 secret 后代理到外部 API
  └─ 元数据服务（169.254.169.254）连接被拒
```

## 7.6 局限与边界

- **每 exec 一容器，无 warm pool**：冷启动几百 ms 到 s 级，M0 接受；M1-A 目标 P95 < 500ms。
- **runsc 仅 Linux**：dev / macOS 走 runc，对应"测行为不测隔离强度"。CVE-2019-5736 / timing isolation **必须 staging Linux 跑真实 runsc**（K.K5 锁）。
- **docker-out-of-docker**：sandbox-supervisor 容器化后挂宿主 `/var/run/docker.sock`（Mini-ADR I-2），多 supervisor replica 之间不能共享。
- **没有 Firecracker / Kata Containers**：跟 Hermes 一样不在 scope。
- **没有 Modal / E2B / Daytona 等 SaaS sandbox**：跟 Hermes 不同 —— helix 设计上**只**支持 Docker + gVisor，避免外部 SaaS 依赖（多租户企业不接受）。
- **每容器一次 exec_python**：跟 Hermes 的 "Modal Sandbox.exec 复用 environment" 不同，没有"在同一容器多次执行不同 code" 的能力（M1 warm pool 才有）。
- **没有像 Hermes 那样的"快照 source"模式**：每次执行 Python 都是新解释器，环境变量 / state 不跨调用持久化（只有 /workspace 文件持久化）。

---

# 维度 8 — 子 Agent / 并行

## 8.1 现状评级

**生产级**（M0 Stream J.4 + J.4-补强 + J.4-补强-2 ✅ + L.L6 并发 ✅）。**递归 sub-agent delegation + 真正的并行 fan-out + 深度结构性 cap + cycle detection（构建期 DFS）+ deadline 继承 + sub-agent trajectory 单独记录**。

ITERATION-PLAN 锚点：
- Stream J.4：M0 ✅（PR #151/152/154）
- J.4-补强 (Mini-ADR J-21 trajectory + budget telemetry)：M0 ✅（PR #220）
- J.4-补强-2 (M0 内并行 fan-out，原 M2-B 取消推迟)：M0 ✅（PR #222/223/224 + 收尾）
- L.L6 工具阶段化并行：M0 ✅
- M2-B 子 SSE 进度流回父：M2 backlog

## 8.2 设计立场

`services/orchestrator/src/orchestrator/tools/subagent.py:1-19` 的 module docstring：

> "A manifest's ``spec.subagents`` block declares deployed agents the parent may delegate to. The assembler (Stream J.4 PR4) wraps each entry into a named :class:`SubAgentTool` so the parent's LLM sees delegation as an ordinary tool call."

设计立场：

1. **agent-as-tool**：子 agent 对父 LLM 暴露为普通 tool call，签名跟其他 tool 一样。
2. **构建期声明，不是运行期 spawn**：`spec.subagents` 在 manifest 里写死，每个 entry 是 `(name, agent_ref, description)`，构建期 cycle detection 把所有引用图扫一遍。
3. **结构性深度终止**（`subagent.py:74-80`）：

> "An agent built at this depth gets **no** ``SubAgentTool`` registered — structural recursion termination, so a cross-manifest cycle (A->B->A) can never run away (Mini-ADR J-12). This replaces a token-budget guard: helix has no runtime token budget, so cost is bounded structurally by depth times each agent's ``max_iterations``."

—— helix 没有运行时 token budget，所以靠"depth × max_iterations" 限。

4. **取消链穿透**：父 cancellation token 通过 `RunnableConfig.configurable` 传给子，所有子 / 孙都被同一 token 触发 cancel。
5. **L.L6 阶段化并发就是 sub-agent fan-out 的载体**：`SubAgentTool.spec.is_parallel_safe = True`，同 stage 多个 sub-agent 通过 `asyncio.gather + Semaphore(MAX_TOOL_WORKERS=8)` 并发。

## 8.3 关键代码路径

| 角色 | 文件 | 关键符号 |
|------|------|---------|
| 模块 docstring | `services/orchestrator/src/orchestrator/tools/subagent.py:1-20` | J.4 / Mini-ADR J-12 引用 |
| 后台 trajectory task set | `subagent.py:60-66` | `_BACKGROUND_TRAJECTORY_TASKS` |
| Trajectory dispatch timeout | `subagent.py:68-72` | `_TRAJECTORY_DISPATCH_TIMEOUT_S = 5.0` |
| 深度 cap | `subagent.py:74-80` | `MAX_SUBAGENT_DEPTH` 文档 |
| SubagentStatus 6 态 | `packages/helix-protocol/src/helix_agent/protocol/subagent.py` | `PENDING/RUNNING/COMPLETED/FAILED/CANCELLED/TIMED_OUT` |
| SubAgentInvocation DTO | 同上 | `class SubAgentInvocation` |
| ChildAgentBuilder Protocol | `subagent.py` (后段) | callback 由 control-plane 注入 |
| Scheduling | `services/orchestrator/src/orchestrator/tools/scheduling.py` | `plan_stages()` / `MAX_TOOL_WORKERS = 8` / 冲突检测 |
| Agent factory cycle detect | `services/orchestrator/src/orchestrator/agent_factory.py` | `detect_subagent_cycle()` DFS |
| Eval baseline | `tools/eval/sub_agent.py` | 8 case + parallel_fanout + cycle_detection |

## 8.4 实现细节

### 8.4.1 SubAgentTool 调用流（`subagent.py:14-17` docstring）

> "the :class:`~orchestrator.tools.registry.Tool` adapter: ``call()`` builds the child agent, runs it to completion on its own thread, and returns the child's final answer to the parent."

子 agent 完整流：

1. **resolve agent_ref**：通过 `ChildAgentBuilder` callback（control-plane 注入，因为 AgentSpecStore 在 control-plane）
2. **build BuiltAgent**：用子 manifest + 父继承的 ToolEnv（含 SandboxClient / SecretStore / LLMRouter / ...）
3. **新 thread_id + run_id**：每次 delegation 一个独立 thread
4. **运行子 graph**：父 LLM 等子 final answer
5. **记 SubAgentInvocation**：写父 `AgentState.subagent_invocations` channel
6. **可选 trajectory 单独写**：J.4-补强 Mini-ADR J-21，子 trajectory 走独立 ObjectStore key

### 8.4.2 SubagentStatus 6 态机（J.4-补强-2 PR #223）

```python
class SubagentStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
```

每个终态 path emit 一个 `SubAgentInvocation` 进父 state（包含 `iteration_used` / `llm_call_count` / `wall_clock_ms` 进 ToolResult.meta）。

### 8.4.3 L.L6 阶段化并发 + sub-agent fan-out

`scheduling.py` 的 `plan_stages()` 算法（Mini-ADR L-6 + J-40）：

```python
# ToolSpec 字段
class ToolSpec:
    is_read_only: bool = False
    path_args: tuple[str, ...] = ()
    is_parallel_safe: bool = False  # J.4-补强-2 Mini-ADR J-40 加

# 冲突规则（plan_stages 内）
def conflicts(a: ToolCall, b: ToolCall) -> bool:
    a_spec = registry[a.name]
    b_spec = registry[b.name]
    # 两个 read_only 永不冲突
    if a_spec.is_read_only and b_spec.is_read_only:
        return False
    # subagent 之间 is_parallel_safe → 永远可以并发
    if a_spec.is_parallel_safe and b_spec.is_parallel_safe:
        return False
    # 路径相交才冲突
    return _paths_overlap(a, b, a_spec.path_args, b_spec.path_args)
```

效果：父 LLM 一次返回 5 个 `subagent` tool_call → 全部进同一 stage 并发，`asyncio.gather` 包装，最多 `MAX_TOOL_WORKERS=8` 并发。

### 8.4.4 Cycle detection（构建期 DFS，J.4-补强-2 PR #224）

`agent_factory.detect_subagent_cycle()`：

```python
def detect_subagent_cycle(
    spec_store: AgentSpecStore,
    tenant_id: UUID,
    root_agent_ref: str,
) -> None:
    """DFS through subagent references; raise AgentFactoryError on cycle."""
    visiting: set[str] = set()
    visited: set[str] = set()

    def dfs(agent_ref: str) -> None:
        if agent_ref in visiting:
            raise AgentFactoryError(f"subagent cycle detected: {agent_ref}")
        if agent_ref in visited:
            return
        visiting.add(agent_ref)
        spec = spec_store.get_active(tenant_id, agent_ref)
        for sub_ref in [s.agent_ref for s in spec.subagents or []]:
            dfs(sub_ref)
        visiting.remove(agent_ref)
        visited.add(agent_ref)

    dfs(root_agent_ref)
```

构建期早 fail（不是运行期），并且支持菱形拓扑（A→B→C + A→D→C 不报错，但 A→B→A 报错）。

### 8.4.5 Deadline 继承（J.4-补强-2 PR #224）

`ToolContext.deadline_at: float | None` 进 sub-agent run config：

```python
# parent run_agent in sse.py
config = {
    "configurable": {
        ...,
        "cancellation_token": parent_token,
        # 父的 deadline 是 time.monotonic() + parent_timeout
        # 子继承同一个 deadline，子超时 = 父超时
    }
}
```

子 agent 内的工具调用同样收到 `ToolContext.deadline_at`，超过 deadline 立即 raise `RunCancelledError`。

### 8.4.6 Sub-agent trajectory 单独记录（J.4-补强 Mini-ADR J-21）

`subagent.py:61-72`：

```python
_BACKGROUND_TRAJECTORY_TASKS: set[asyncio.Task[None]] = set()
_TRAJECTORY_DISPATCH_TIMEOUT_S: float = 5.0
```

子 agent 终态时：
- 单独写到 ObjectStore：`trajectories/{tenant_id}/{outcome}/{date}/{sub_thread_id}.jsonl`（跟父分开）
- 通过 `asyncio.create_task` + 5s outer deadline + `_BACKGROUND_TRAJECTORY_TASKS` 防 GC fire-and-forget
- 3 outcome 全 dispatch (`success` / `failed` / `cancelled`)

## 8.5 运行时行为

```
父 agent_node LLM 决定 delegate
  │
  └─ tool_calls = [SubAgentCall(name="researcher"), SubAgentCall(name="reviewer")]
        │
        └─ tools_node:
              ├─ plan_stages(tool_calls) → 同 stage 收集 is_parallel_safe=True 的
              │     stage 1: [researcher, reviewer]   ← 同一 stage 并发
              │
              └─ for stage in stages:
                    await asyncio.gather(
                        *[_dispatch_tool(call, ctx) for call in stage],
                        return_exceptions=True
                    )
                    │
                    └─ _dispatch_tool(SubAgentCall):
                          │
                          ├─ SubAgentTool.call(args, ctx):
                          │     ├─ child_builder(agent_ref, tenant_id) → BuiltAgent
                          │     ├─ child_thread_id = uuid4()
                          │     ├─ child_config = {
                          │     │     "configurable": {
                          │     │         "thread_id": child_thread_id,
                          │     │         "tenant_id": ctx.tenant_id,
                          │     │         "user_id": ctx.user_id,
                          │     │         "cancellation_token": ctx.cancellation_token,
                          │     │         "deadline_at": ctx.deadline_at,
                          │     │     }
                          │     │ }
                          │     │
                          │     ├─ 子 graph.astream(input, config=child_config)
                          │     │     ├─ 子 agent_node ↔ 子 tools_node ↔ ...
                          │     │     │   （没有 SubAgentTool 注册 if depth==MAX，结构性终止）
                          │     │     ├─ 子可能再 fan-out 到 sub-sub-agent
                          │     │     └─ deadline 继承 → 整棵子树同 deadline
                          │     │
                          │     ├─ on child success:
                          │     │     SubAgentInvocation(status=COMPLETED, iteration_used=N, ...)
                          │     │     trajectory_record(child_thread_id, outcome="success")
                          │     │       ← fire-and-forget, 5s deadline
                          │     │     return ToolResult(content=child_final_answer,
                          │     │                       state_updates={
                          │     │                           "subagent_invocations": [invocation]
                          │     │                       })
                          │     │
                          │     ├─ on MaxStepsExceededError:
                          │     │     invocation = SubAgentInvocation(status=FAILED, ...)
                          │     │     trajectory_record(outcome="max_steps")
                          │     │     return ToolResult(content="[child max_steps]", ...)
                          │     │
                          │     └─ on RunCancelledError:
                          │           invocation = SubAgentInvocation(status=CANCELLED, ...)
                          │           trajectory_record(outcome="cancelled")
                          │           raise  # 传播给父
                          │
                          └─ classify_mutation(result) → 父 failed_mutations 通道
```

## 8.6 局限与边界

- **子 SSE 不流回父**：子的中间 LLM token 不流给父；父只看到 final answer。M2-B 后续。
- **子 manifest 必须 deploy**：跟 Hermes 的 "spawn arbitrary subagent with custom system_prompt" 不同 —— helix 子 agent 必须是 control-plane 已注册的 AgentSpec，构建期 cycle detection 要能 resolve。
- **没有运行时 token budget**：靠"depth × max_iterations" 结构性 cap（深度默认 3，每层 20 步 → 最坏 60 步 / agent）。
- **没有动态 spawn**：父不能在 run 中决定"我要新创一个 sub-agent"；只能在 manifest 中预声明。
- **没有审批自动化分级**：跟 Hermes 的 `_subagent_auto_deny` / `_subagent_auto_approve` 不同，helix 的 sub-agent 直接继承父的审批策略（J.8 PolicySpec），没有"子默认安全"或"子可以 YOLO" 的区分。
- **没有 LangGraph supervisor pattern**：自研 stage-based 调度，不用 langgraph-supervisor 这种通用 supervisor 模板。

---

# 维度 9 — Cron 调度（trigger / scheduled task）

## 9.1 现状评级

**生产级**（M0 Stream J.10 ✅ + B.7 触发器 API + H.4 Triggers 治理面 + N system_admin 接入）。**自研 scheduler + DLQ retry + per-tenant quota + cron + webhook 双 kind + audit + 跨租户 system_admin**。

ITERATION-PLAN 锚点：
- Stream J.10 Triggers：M0 ✅（Mini-ADR J-26 / J-42）
- B.7 Run trigger API：M0 ✅
- N.4 list API 加 `tenant_id=⋆`：M0 ✅
- H.4 Triggers 治理面：M0 ✅（PR #302）
- M1+：webhook secret rotate（M0 走删-重建，M1+ in-place rotate）

## 9.2 设计立场

跟 Hermes 把 cron 当作"独立 daemon 把 prompt 注入新 session"不同，helix 的 trigger 是**完全数据库驱动的状态机**：

- 所有 trigger 配置 / 执行状态都在 Postgres（`agent_trigger` / `trigger_run` 表），不存文件；
- Scheduler 是 **control-plane 内嵌**的 background task（不是独立 daemon、不用 APScheduler / Celery），靠 SELECT FOR UPDATE / next_run_at 索引；
- 每次触发都创建独立 thread / run，跟普通 agent run 走同一管道（`fire_trigger()` 复用 `run_agent`）；
- **DLQ + retry**：失败有指数退避，5 次后 dead_letter。

设计文档（`docs/streams/STREAM-J-DESIGN.md` Mini-ADR J-26 / J-42 推断）核心：

1. **Single-replica control-plane worker**：M0 不分布式（M1 才考虑 PG NOTIFY 跨 replica）。
2. **三阶段 cycle**：fire → reconcile → retry。
3. **Per-tenant quota 集成**：trigger 触发计入 tenant quota。

## 9.3 关键代码路径

| 角色 | 文件 | 关键符号 |
|------|------|---------|
| TriggerKind | `packages/helix-protocol/src/helix_agent/protocol/trigger.py:34` | `Literal["cron", "webhook"]` |
| TriggerSpec / TriggerRecord / TriggerRunRecord | `packages/helix-protocol/src/helix_agent/protocol/trigger.py:42-143` | DTOs |
| Scheduler | `services/control-plane/src/control_plane/scheduler.py:128-250` | `TriggerScheduler` |
| fire_trigger | `services/control-plane/src/control_plane/trigger_firing.py:48-150` | shared by cron + webhook |
| Trigger API | `services/control-plane/src/control_plane/api/triggers.py` | CRUD + webhook 端点 |
| Webhook 端点 | `services/control-plane/src/control_plane/api/triggers.py:335-412`（推断） | `POST /v1/webhooks/{trigger_id}` HMAC 验证 |
| TriggerRunStore | `packages/helix-persistence/src/helix_agent/persistence/trigger_store.py` | SQL + in-memory impl |
| Migration | `packages/helix-persistence/migrations/versions/0033_agent_trigger.py` | `agent_trigger` + `trigger_run` 表 |
| Admin UI Triggers | `apps/admin-ui/src/triggers/` | H.4 PR #302 |

## 9.4 实现细节

### 9.4.1 TriggerKind 与 config

```python
TriggerKind = Literal["cron", "webhook"]

# Cron config
{"expr": "0 9 * * *", "seed_input": "Generate today's report"}

# Webhook config
{"seed_input": "Process incoming webhook"}
```

- `expr` 用 `croniter` 库解析（5-field UNIX cron）；
- `seed_input` 进首条用户消息；
- webhook secret 创建时返回明文一次，DB 存 HMAC hash。

### 9.4.2 Scheduler 三阶段 cycle

`scheduler.py:128-250` 的 `run_once()`：

```
Phase 1: fire
  └─ SELECT * FROM agent_trigger
        WHERE enabled = true AND kind = 'cron'
        AND tenant_id IN (allowed_tenants)
     for each trigger:
        next_run_at = croniter(expr, last_run_at).get_next()
        if next_run_at <= now():
            run_id = fire_trigger(trigger, ...)
            UPDATE agent_trigger SET last_run_at = now()
            INSERT INTO trigger_run (trigger_id, run_id, status=FIRED, ...)

Phase 2: reconcile
  └─ for each trigger_run WHERE status = FIRED:
        agent_run_status = SELECT status FROM agent_run WHERE id = run_id
        if agent_run_status == "completed":
            UPDATE trigger_run SET status = SUCCEEDED
        elif agent_run_status == "failed":
            UPDATE trigger_run SET status = FAILED, attempt += 1
            if attempt >= 5:
                UPDATE trigger_run SET status = DEAD_LETTER
            else:
                UPDATE trigger_run SET status = RETRYING,
                       next_retry_at = now() + BACKOFF[attempt]

Phase 3: retry
  └─ for each trigger_run WHERE status = RETRYING AND next_retry_at <= now():
        run_id = fire_trigger(trigger, ...)
        UPDATE trigger_run SET status = FIRED, run_id = new_run_id
```

`BACKOFF = (60, 5*60, 30*60, 2*3600, 6*3600)` — 1m → 5m → 30m → 2h → 6h。`_MAX_ATTEMPTS = 5`。

### 9.4.3 `fire_trigger` 跟普通 run 共享 manifold

`trigger_firing.py:48-150`：

```python
async def fire_trigger(
    trigger: TriggerRecord,
    *,
    now: datetime,
    agent_spec_store: AgentSpecStore,
    runtime: AgentRuntime,
    ...,
) -> UUID | None:
    # 1. Resolve agent spec
    record = await agent_spec_store.get(trigger.tenant_id, trigger.agent_name, trigger.agent_version)
    if record.status != AgentSpecStatus.ACTIVE:
        return None  # skip, no trigger_run row

    # 2. Build agent
    built = await runtime.get_agent(record)

    # 3. Create fresh thread + run
    thread_id = uuid4()
    run_id = uuid4()

    # 4. Build messages from seed_input
    messages = [SystemMessage(...), HumanMessage(trigger.config["seed_input"])]

    # 5. Fire-and-forget background run_agent (no SSE consumer)
    asyncio.create_task(_background_run(built, messages, config=...))
    return run_id
```

跟用户面的 `run_agent` 走**完全一样的**管道（context compression / memory recall / tool dispatch / trajectory recording），只是没有 SSE 消费者。

### 9.4.4 Webhook 端点（推断）

`api/triggers.py:335-412`：

```python
@router.post("/v1/webhooks/{trigger_id}")
async def receive_webhook(
    trigger_id: UUID,
    request: Request,
    x_helix_webhook_secret: str = Header(...),
):
    trigger = await trigger_store.get(trigger_id)
    if not trigger or trigger.kind != "webhook":
        raise HTTPException(404)
    if not hmac.compare_digest(
        hashlib.sha256(x_helix_webhook_secret.encode()).hexdigest(),
        trigger.config["secret_hash"],
    ):
        raise HTTPException(401)
    payload = await request.body()
    run_id = await fire_trigger(trigger, now=datetime.now(UTC), ...)
    return {"run_id": run_id}
```

每个 webhook trigger 独立 secret（per-trigger，不是 per-tenant）。

### 9.4.5 Per-tenant quota 集成

scheduler 内：

```python
with _tenant_scope(tenant_id, user_id):
    # Tenant 作用域内执行所有操作
    quota.check_admission(tenant_id, dimension="run_count")
    await fire_trigger(trigger, ...)
```

超 quota 时跳过本次触发，等下一个 next_run_at（不阻塞 scheduler 也不进 DLQ）。

## 9.5 运行时行为

```
control-plane lifespan 启动
  │
  └─ asyncio.create_task(TriggerScheduler.run_loop())

TriggerScheduler.run_loop (每 N 秒一次)
  │
  └─ for tenant_id in all_tenants:
        with tenant_scope(tenant_id):
            await run_once()
                ├─ Phase 1: fire 到点的 cron triggers
                ├─ Phase 2: reconcile FIRED trigger_run 看 agent_run 状态
                └─ Phase 3: retry RETRYING 且 next_retry_at <= now 的

POST /v1/webhooks/{trigger_id}（外部业务系统触发）
  │
  ├─ HMAC 验证 secret
  ├─ fire_trigger(webhook_trigger, seed_input=payload)
  │     │
  │     ├─ 创建 thread + run
  │     ├─ 写 trigger_run (status=FIRED)
  │     └─ asyncio.create_task(background_run_agent(...))
  │           ├─ 走完整 agent loop（含 memory / context compression / tools）
  │           ├─ 写 trajectory
  │           └─ 完成 → agent_run.status = completed | failed
  │
  └─ return {"run_id": uuid}

下一轮 scheduler reconcile：
  ├─ trigger_run.status = FIRED, agent_run.status = completed
  └─ UPDATE trigger_run SET status = SUCCEEDED

Admin UI H.4 Triggers
  │
  └─ GET /v1/triggers?status=...        ← 列 + filter
        POST /v1/triggers                ← 创建 cron 或 webhook
        PATCH /v1/triggers/{id}          ← 启用/禁用
        DELETE /v1/triggers/{id}         ← 删
        GET /v1/triggers/{id}/runs       ← 看执行历史
        POST /v1/triggers/{id}/run-now   ← 手动触发
```

## 9.6 局限与边界

- **Single-replica scheduler**：M0 control-plane 不能多 replica 跑 scheduler（会 double-fire），需要 leader election 或 PG NOTIFY-based 分布式。M1+。
- **Tick 间隔默认偏粗**：不像 Hermes 60s 固定，helix 是 N 秒一轮（具体值看配置），但下限受 DB 查询成本约束。
- **Cron 5-field UNIX**：不支持 Quartz syntax（秒 / 时区）。
- **基于 UTC**：没有 per-trigger timezone 支持。
- **No trigger dependencies**：trigger A 完成不能自动触发 B；要靠 A 自己显式 fire B。
- **Webhook secret rotate 走删-重建**（M0），M1+ 才支持 in-place rotate（H.4 PR 收尾时显式推迟）。
- **No per-trigger rate limit**：per-tenant quota 是上限，单 trigger 不能再细分。
- **错过不补**：control-plane 宕机期间到期的 cron job，下次启动只 advance next_run_at，**不会 catch-up 跑**（跟 Hermes 一样）。

---

# 维度 10 — 消息平台

## 10.1 现状评级

**不在 scope（设计决策）**。这是 helix 与 Hermes **最大的产品定位差异**：helix 不内置任何末端消息平台 adapter（无 Slack / Telegram / 飞书 / 钉钉 / Signal / Discord / ...），由业务系统通过 webhook 接入。

ITERATION-PLAN 锚点：
- `docs/ITERATION-PLAN.md:328`（H Stream 范围澄清原文）：
  > "Business 系统通过 API 消费 helix 的 per-user 持久 agent；helix **不自带末端用户对话 UI**（末端用户通过 business 系统自己的 UI 与 agent 对话）。Admin UI 仅服务操作人群（平台 admin / agent 开发者 / 运营 / SRE）"

## 10.2 设计立场

helix 的产品哲学是 **backend agent platform**：

- **业务系统是 helix 的客户**：客户的产品（聊天软件、SaaS、移动端 app）负责与末端用户交互；
- **helix 提供**：通用 webhook trigger（HMAC 验证）+ SSE stream API + manifest 管理 + Audit / Quota / IAM；
- **客户业务系统**：自己接 Slack / Telegram / WhatsApp / 微信 / 飞书 / 钉钉 / 自家 IM / 邮件 / SMS / 移动端 SDK；把入站消息转成 `POST /v1/webhooks/{trigger_id}` 或 `POST /v1/sessions/{id}/runs`，再把响应自己 push 回末端。

这跟 Hermes "一个 CLI / 一个用户 / 直接挂在 Slack 上当 bot 用" 是完全不同的产品形态。

## 10.3 关键代码路径

| 角色 | 文件 | 关键符号 |
|------|------|---------|
| Webhook trigger 端点 | `services/control-plane/src/control_plane/api/triggers.py:335-412`（推断） | `POST /v1/webhooks/{trigger_id}` HMAC |
| Trigger DTO | `packages/helix-protocol/src/helix_agent/protocol/trigger.py:34` | `TriggerKind = Literal["cron", "webhook"]` |
| Webhook secret 验证 | 同上 | HMAC-SHA256 |

**搜不到任何 Slack / Telegram / WhatsApp / Discord / 飞书 / 企微 / 钉钉 SDK 集成代码**（之前 explorer 三组都 grep 过空），确认这是设计决策。

## 10.4 实现细节

### 10.4.1 webhook trigger 是唯一的入站通道

`docs/ITERATION-PLAN.md:340`：

> "依赖：H.1b+ 任何代码 PR 上线**前提 = Stream N 合入**（系统管理员跨租户能力)"

helix 把"如何让消息进来"的责任完全外推给业务系统：

- **方式 1（推荐，async）**：业务系统接到末端用户消息 → `POST /v1/webhooks/{trigger_id}` + `X-Helix-Webhook-Secret` header → 返回 `run_id` → 用 `GET /v1/sessions/{thread_id}/runs/{run_id}/events` (SSE) 拉响应或轮询 `GET /v1/runs/{run_id}`。
- **方式 2（直接 SSE）**：业务系统对每个 user request 直接 `POST /v1/sessions/{id}/runs` 开 SSE stream，拿到响应往末端推。

### 10.4.2 SSE 跨租户隔离（K.K2）

`docs/ITERATION-PLAN.md:385`：

> "K2 SSE 跨租户隔离（补 G3）— 安全模型由 thread 归属校验保证（误判更正），补 `test_runs_cross_tenant_sse_rejected` 锁住 invariant"

webhook + SSE 必须自动做 tenant 隔离 —— 不允许 tenant A 的 webhook 触发 / 订阅 tenant B 的 thread。

### 10.4.3 系统提示 SDK 客户端

`apps/admin-ui/src/` 有 6 个 SDK clients（H.1b PR3，`docs/ITERATION-PLAN.md:333`），都是 admin / 运维用，**没有"用户聊天界面" SDK**。

## 10.5 运行时行为

```
End-user → Business System (Slack/Telegram/...) → 业务后端
                                                       │
                                                       └─ HTTP POST /v1/webhooks/{trigger_id}
                                                                     X-Helix-Webhook-Secret: ...
                                                                     {payload}
                                                                     │
                                                                     ▼
                                                              helix control-plane
                                                                  │
                                                                  ├─ HMAC 验证
                                                                  ├─ fire_trigger(webhook)
                                                                  │     ├─ 创建 thread + run
                                                                  │     └─ asyncio.create_task(background_run_agent)
                                                                  │
                                                                  └─ 返回 {"run_id": uuid}

业务系统再调（取响应）：
  GET /v1/sessions/{thread_id}/runs/{run_id}/events (SSE)
    ├─ 流式接收事件
    └─ 把响应自己 push 回末端用户
```

## 10.6 局限与边界

- **业务系统必须自己实现 multi-platform adapter**：Slack Block Kit / Telegram InlineKeyboard / 飞书卡片 等富文本组件 helix 不提供。
- **业务系统必须自己实现 rate limit / 反垃圾**：helix 只在 tenant 维度做 quota，不在 platform / chat / user 维度。
- **业务系统必须自己实现媒体上传**：J.6 多模态 upload 端点（`POST /v1/sessions/{thread_id}/uploads`）的 caller 是业务系统，不是末端用户。
- **会话路由（哪个 chat → 哪个 thread）由业务系统决策**：helix 不知道 "telegram:chat_id:12345" 对应哪个 thread，业务系统自己维护这个映射。
- **没有 SessionSource 抽象**：跟 Hermes 的 `SessionSource(platform, chat_id, ...)` 不同，helix 的 thread / session 只有 `tenant_id` + `user_id`，没有 platform 维度。
- **没有 inbound 平台特有的 voice / image / file 协议适配**：业务系统负责把语音转文字、把图片上传到 J.6 endpoint。

> ⚠️ 这个维度的"不在 scope"**不是 backlog**，是 helix 一直会保持的设计选择。M1/M2/M3 都不会内置消息平台 adapter。

---

# 维度 11 — MCP 支持（Client / Server）

## 11.1 现状评级

**部分实现**。
- **MCP Client（stdio transport）**：M0 ✅（Stream E.9）。每 tenant 上限 5 server，per-server stdio 子进程 + ClientSession。
- **HTTP / SSE transport**：❌ M1 backlog（Mini-ADR E-5 明确）。
- **MCP Server（helix 暴露能力给 Claude Code / Cursor）**：❌ **不在 scope**（搜不到任何 `FastMCP` / `MCPServer` / `mcp.server` 引用）。
- **MCP Sampling**（server 反向请求 LLM）：❌ 不在 scope。

ITERATION-PLAN 锚点：
- Stream E.9 MCP client：M0 ✅
- HTTP / SSE transport：M1 backlog（Mini-ADR E-5）
- MCP Server：未在 ITERATION-PLAN 出现

## 11.2 设计立场

跟 Hermes 双向 MCP（Client + Server）不同，helix 的 MCP 是**单向消费式** —— 只接外部 MCP server 当工具源，不把自己暴露给其他 IDE / agent。

`services/orchestrator/src/orchestrator/tools/mcp.py:1-36` module docstring：

> "Wraps Anthropic's official ``mcp`` Python SDK so per-tenant MCP servers configured via ``tenant_config.mcp_servers`` (E.8 migration 0011) get exposed to the orchestrator's :class:`ToolRegistry`. Per Mini-ADR E-5, M0 ships **stdio transport only** — each MCP server is a local subprocess. HTTP / SSE transports land in M1."

设计立场：

1. **per-tenant 配置**：MCP server 列表存 `tenant_config.mcp_servers` JSONB 字段，per-tenant 隔离。
2. **stdio 子进程 = 一对一**：每个 tenant 启动自己的 MCP server 子进程（不跨 tenant 共享）。
3. **N=5 server 上限**：防 stdio 子进程泄漏（`mcp.py:58` `DEFAULT_MAX_SERVERS = 5`）。
4. **输出中间截断 20K**：每次 `call_tool` 结果超过 20000 char 时**中间裁剪**（head 50% + `[N chars truncated]` + tail 50%）—— 不是 head-only 也不是 tail-only。

## 11.3 关键代码路径

| 角色 | 文件 | 关键符号 |
|------|------|---------|
| 模块 docstring | `services/orchestrator/src/orchestrator/tools/mcp.py:1-36` | Stream E.9 / Mini-ADR E-5 / E-10 |
| 常量 | `mcp.py:57-61` | `DEFAULT_MCP_CHAR_CAP = 20_000` / `DEFAULT_MAX_SERVERS = 5` / `DEFAULT_TIMEOUT_S = 30.0` |
| MCPServerConfig | `mcp.py:69-87` | `@dataclass(frozen=True) class MCPServerConfig` |
| MCPToolDef | `mcp.py:90-97` | `name / description / input_schema` |
| MCPCallResult | `mcp.py:99+` | dataclass |
| MCPClient Protocol | `mcp.py:110+`（推断） | `@runtime_checkable class MCPClient(Protocol)` |
| StdioMCPClient | `mcp.py:195+`（推断） | 生产 adapter 包 `mcp.client.stdio.stdio_client` + `ClientSession` |
| MCPTool | `mcp.py:300+`（推断） | `Tool` adapter，name 是 `mcp:<server>.<tool>` |
| MCPServerPool | `mcp.py:380+`（推断） | lifecycle owner + N=5 cap |
| register_mcp_tools | `mcp.py:411+`（推断） | `list_tools` + 注册进 ToolRegistry |
| 配置 schema | `packages/helix-protocol/src/helix_agent/protocol/tenant_config.py` | `TenantConfigPatch.mcp_servers` |
| 迁移 | `packages/helix-persistence/migrations/versions/0011_tool_config.py` | `tenant_config.mcp_servers` JSONB |

## 11.4 实现细节

### 11.4.1 MCPServerConfig（`mcp.py:69-87`）

```python
@dataclass(frozen=True)
class MCPServerConfig:
    """Per-server launch config from ``tenant_config.mcp_servers``.

    Mirrors the JSONB row shape: ``{"name", "command": [...], "env": {...}}``.
    ...
    """

    name: str
    command: Sequence[str]
    env: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.command:
            msg = f"mcp server {self.name!r} has empty command"
            raise ValueError(msg)
```

JSONB 行格式：

```json
{
  "mcp_servers": [
    {
      "name": "filesystem",
      "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/workspace"],
      "env": {}
    },
    {
      "name": "github",
      "command": ["npx", "-y", "@modelcontextprotocol/server-github"],
      "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_..."}
    }
  ]
}
```

跟 Hermes 配置基本一致，但 **helix 没有 `timeout` / `connect_timeout` / `supports_parallel_tool_calls` / `transport` / `url` / `sampling` 字段**（这些都不在 M0 scope）。

### 11.4.2 输出 20K 中间截断（Mini-ADR E-10）

`mcp.py:30-35` docstring：

> "Output truncation per Mini-ADR E-10 / § 1.1 E.9: each ``call_tool`` result is rendered to text (TextContent blocks concatenated) and, if over 20 000 chars, **middle-trimmed** with the head 50 % + a ``[N chars truncated]`` placeholder + the tail 50 %. Middle trim (not head or tail) lets the LLM see both the start of the response (typically status / preamble) and the end (often a conclusion)."

为什么中间裁剪：保留 status / preamble + conclusion，比单纯 head-truncate 或 tail-truncate 信息密度高。

### 11.4.3 工具命名 `mcp:<server>.<tool>`

`mcp.py:17-19`：

> ":class:`MCPTool` — one **Helix** :class:`Tool` per MCP-exposed tool, namespaced as ``mcp:<server>.<tool>`` so the LLM sees them as first-class entries in the spec list."

跟内置工具（`web_search` / `exec_python`）混合在同一 ToolRegistry，但通过前缀 `mcp:` 易区分。

### 11.4.4 MCPServerPool 生命周期（N=5 cap）

`mcp.py:21-25`：

> ":class:`MCPServerPool` — lifecycle owner. Enforces an N=5 server cap per § 6 "MCP stdio 子进程泄漏" of STREAM-E-DESIGN, and runs each server inside an ``AsyncExitStack`` so subprocess exits propagate cleanly on shutdown."

为什么 N=5：每个 MCP server 是子进程（占 FD、Python 解释器、Node 进程等），太多会饿死宿主。设计预算 = "5 个高价值 server 足够，多了价值递减 + 风险递增"。

### 11.4.5 build_agent 注入流程

```python
# agent_factory.build_agent(manifest)
for tenant_config in tenant_config_store.get(tenant_id):
    for server_cfg in tenant_config.mcp_servers[:DEFAULT_MAX_SERVERS]:
        client = await mcp_server_pool.get_or_create(server_cfg)
        await register_mcp_tools(server_name=server_cfg.name,
                                  client=client,
                                  registry=tool_registry)
```

启动时一次性 `list_tools`，运行时不动态 refresh。

## 11.5 运行时行为

```
orchestrator 启动 / 首次 agent build
  │
  └─ MCPServerPool.initialize(tenant_id):
        ├─ 读 tenant_config.mcp_servers JSONB
        ├─ 截断到 max=5
        └─ for each MCPServerConfig:
              async with AsyncExitStack() as stack:
                  read, write = await stack.enter_async_context(
                      stdio_client(StdioServerParameters(
                          command=cfg.command[0],
                          args=list(cfg.command[1:]),
                          env=cfg.env,
                      ))
                  )
                  session = await stack.enter_async_context(
                      ClientSession(read, write)
                  )
                  await session.initialize()
                  tools = await session.list_tools()
                  for tool_def in tools:
                      registry.register(MCPTool(
                          name=f"mcp:{cfg.name}.{tool_def.name}",
                          client=client,
                          ...
                      ))

主循环 tool_call
  │
  └─ tools_node._dispatch_tool(MCPTool, args):
        result = await session.call_tool(tool_def.name, args)
                       # asyncio.wait_for(timeout=30s)
        rendered = "\n".join([b.text for b in result.content])
        if len(rendered) > 20000:
            rendered = rendered[:10000] + f"...[{trunc} chars truncated]..." + rendered[-10000:]
        return ToolResult(content=rendered, meta={...})
```

## 11.6 局限与边界

- **stdio only**：HTTP / StreamableHTTP / SSE transport 在 M1+（Mini-ADR E-5 明示）。
- **server 列表启动时一次性**：MCP `notifications/tools/list_changed` 不消费。
- **5 server 上限硬编码**：超过的 server 直接被截断（不警告）。
- **无 OAuth manager**：跟 Hermes 的 `tools/mcp_oauth.py` 不同，helix 没有 MCP server OAuth 流程支持。
- **无 sampling**：MCP server 不能反向请求 helix 跑一次 LLM 补全。
- **不暴露 MCP Server**：helix 的 tools / sessions / messages 不能被 Claude Code / Cursor 通过 MCP 反向访问。
- **subprocess stderr 处理推断不严**：源码 docstring 没特别说，需要看具体实现是否重定向到 log（避免污染主进程 stderr）。
- **per-tenant 子进程不共享**：tenant A 和 tenant B 的 `github` server 是两个独立子进程，资源 / 启动成本 × N tenants。

---

# 维度 12 — 扩展机制（工具 + 技能 + MCP + 钩子）

## 12.1 现状评级

**部分实现**。
- **Tool registry + Skill 库 + MCP**：M0 ✅（Stream E.6 / J.7a / E.9）。
- **声明式 manifest 扩展点**：M0 ✅（YAML 写 skill / tool / subagent / trigger 等）。
- **hook / plugin 链**：❌ **不在 scope**（中间件链是**硬编码 + manifest opt-in**，不开放外部 plugin 注册）。
- **agent 自创建 skill**：❌ M1-K backlog（8 项推迟，见维度 2）。
- **`code` 字段执行**：❌ M1-K backlog（J.7b-2，需要 Python 插槽 + sandbox + AST 校验）。

ITERATION-PLAN 锚点：
- Stream E.2-E.10 中间件链：M0 ✅
- Stream J.7a Skill 库：M0 ✅
- M1-K J.7b 8 项：M1 backlog
- M1-F2 Python 插槽：M1 backlog（依赖 M1-A cosign 供应链）

## 12.2 设计立场

helix 的扩展机制设计哲学完全不同于 Hermes：

| | Hermes | helix |
|---|--------|-------|
| 工具发现 | AST 扫 `tools/*.py` + `registry.register()` 自我注册 | 静态导入 + `agent_factory.build_tool_registry()` 显式装配 |
| 技能 | 文件系统（`~/.hermes/skills/`）+ SKILL.md | Postgres 表（skill / skill_version）+ name@version 引用 |
| 插件钩子 | 17 个 hook + 入口点 + per-用户 plugin 目录 | **无**（中间件链硬编码 + manifest opt-in） |
| 用户扩展能力 | bring-your-own plugin | 提 PR 改 ToolRegistry 装配代码 |

设计立场可从 `docs/ITERATION-PLAN.md:692` M1-K backlog 反推：

> "M1-K J.7b 8 项推迟到 M1，**包含 agent 进化工具、code 字段执行、progressive loading、LLM moderation、public skill 库**..."

即 helix M0 故意把"扩展"做得**保守而显式**：不支持 plugin / hook 热加载，所有扩展能力（tool / skill / MCP / sub-agent）都必须通过 manifest 声明 + 数据库存储 + 代码装配。这跟"业务无关多租户企业引擎"的定位一致：**安全审计第一**，agent autonomy 永远是 opt-in。

## 12.3 关键代码路径

| 角色 | 文件 | 关键符号 |
|------|------|---------|
| ToolRegistry | `services/orchestrator/src/orchestrator/tools/registry.py` | `ToolRegistry` + `ToolSpec` |
| Tool 装配 | `services/orchestrator/src/orchestrator/tools/assembly.py` | `build_tool_registry(...)` |
| 中间件链装配 | `services/orchestrator/src/orchestrator/middleware_assembly.py` | 硬编码 8 个 middleware 顺序 |
| Skill 解析 | `packages/helix-protocol/src/helix_agent/protocol/skill.py` | `parse_skill_ref` / `SkillRef` / `SkillStatus` |
| Skill 加载 | `services/orchestrator/src/orchestrator/agent_factory.py:200+`（推断 `_load_skills_phase`） | resolve + tool 注入 + `<skill>` XML 包裹 |
| Skill API | `services/control-plane/src/control_plane/api/skills.py` | 9 个 endpoint + ZIP import/export |
| Manifest validator | `packages/helix-protocol/src/helix_agent/protocol/agent_spec.py` | `_check_skills` validator |
| Hook（不存在）| 无 | （搜不到 plugin hook 注册机制） |

## 12.4 实现细节

### 12.4.1 ToolSpec 6 字段（已实现 + L.L6 扩展）

```python
@dataclass(frozen=True)
class ToolSpec:
    name: str                              # tool 调用名
    description: str                       # LLM 看到的描述
    parameters: Mapping[str, Any] = {}     # JSON Schema
    is_read_only: bool = False             # L.L6 — 只读，可并发
    path_args: tuple[str, ...] = ()        # L.L6 — 路径冲突检测
    is_parallel_safe: bool = False         # J.4-补强-2 Mini-ADR J-40 — subagent 并发安全
    from_skill: str | None = None          # J.7a — metrics 标签（哪个 skill 提供）
```

跟 Hermes 的 `ToolEntry`（含 `check_fn` / `requires_env` / `emoji` / `dynamic_schema_overrides` 等）相比，helix 字段更少 + 更聚焦"调度行为"。

### 12.4.2 内置 Tool 标注表

| Tool | is_read_only | path_args | is_parallel_safe | 说明 |
|------|--------------|-----------|------------------|------|
| `web_search` | ✅ | () | ✅ | E.7 Tavily |
| `ask_image` | ✅ | () | ✅ | J.6 多模态 |
| `knowledge_search` | ✅ | () | ✅ | J.5 RAG |
| `list_artifacts` | ✅ | () | ✅ | artifact 管理 |
| `save_artifact` | ❌ | ("name",) | ❌ | 同名冲突 |
| `http` | ❌（保守） | () | ❌ | E.8（POST/PUT/PATCH 实际允许） |
| `update_plan` | ❌ | () | ❌ | K.K8，写 AgentState.plan |
| `exec_python` | ❌ | () | ❌ | F sandbox，stateful |
| `subagent` | ❌ | () | ✅ | J.4，subagent fan-out 安全 |
| `mcp:*` | ❌（保守） | () | ❌ | E.9 外部 MCP |

### 12.4.3 Skill 引用 grammar

`SKILL_REF_PATTERN = r"^[a-z][a-z0-9_-]{0,63}(@[1-9][0-9]*)?$"`

`parse_skill_ref(raw) -> SkillRef`：
- bare name → `SkillRef(name="foo", version=None)` → 解 latest ACTIVE
- 显式版本 → `SkillRef(name="foo", version=3)` → 解特定版本（draft / active / archived 都可）

### 12.4.4 Skill 加载流（agent_factory）

```
manifest.spec.skills = ["reporting@2", "sentiment"]
  │
  └─ for raw in spec.skills:
        ref = parse_skill_ref(raw)
        skill_version = SkillStore.get_version(tenant_id, ref.name, ref.version)
        # 检查 status
        if ref.version is None and skill_version.status != ACTIVE:
            raise SkillNotActiveError(...)
        # 检查 required_models（必须包含主 model name）
        if main_model.name not in skill_version.required_models:
            raise SkillModelMismatchError(...)
        # 检查 tool conflict（两 skill 不能 own 同名 tool）
        for tool_name in skill_version.tool_names:
            if tool_name in already_owned:
                raise SkillToolConflictError(...)
        # 拼 prompt fragment
        merged_system_prompt += f"<skill name=\"{ref.name}\" version=\"{ref.version}\">\n{skill_version.prompt_fragment}\n</skill>\n"
        # 注册 tool subset（only skill's tool_names）
        for tool_name in skill_version.tool_names:
            registry.register(build_tool(tool_name, from_skill=ref.name))
```

### 12.4.5 Skill API（9 endpoint，J.7a Step 3）

```
POST   /v1/skills                           # 创建 skill（status=DRAFT, version=0）
POST   /v1/skills/{id}/versions             # 发布新版本（version=1, 2, 3 ...）
PATCH  /v1/skills/{id}                      # status patch（DRAFT → ACTIVE / ACTIVE → ARCHIVED）
DELETE /v1/skills/{id}                      # 删除（仅 DRAFT + 没引用）
GET    /v1/skills                           # 列表 + filter
GET    /v1/skills/{id}                      # 详情
GET    /v1/skills/{id}/versions/{version}   # 特定版本内容
POST   /v1/skills/import                    # ZIP import
GET    /v1/skills/{id}/export               # ZIP export
```

ZIP import 防护：path traversal（ZIP slip）+ size cap + regex deny-list（防 `import os; os.system(...)` 这类）。

audit action：`SKILL_CREATE` / `SKILL_VERSION_CREATE` / `SKILL_STATUS_CHANGE`。

### 12.4.6 Middleware 链是硬编码的

`services/orchestrator/src/orchestrator/middleware_assembly.py`（基于 J.7a 注入 ToolSpec.from_skill 等 hint 推断）：

```python
# Built-in middleware order (always wired):
- ObservabilityMiddleware (A.7/A.8/A.9)
- PIIRedactor (D.2)
- DynamicContextMiddleware (E.3)
- LLMErrorHandlingMiddleware (E.4)         # 断路器 + 重试
- LLMResponseCacheMiddleware (E.13)        # manifest opt-out via cache.enabled
- LangfuseMiddleware (E.5)                  # if env.langfuse_enabled
- TokenUsageMiddleware (G.9)
- SandboxAuditMiddleware (E.10)
- LoopDetectionMiddleware (E.10.5)
```

**没有 user-registerable hook 入口** —— 加新 middleware 必须改 `middleware_assembly.py` + 提 PR + 过 ADR。

### 12.4.7 Hook / plugin 缺失现状

之前 explorer agent 在 packages/ 和 services/ 内 grep `hook` / `plugin` / `register_hook` 等关键词 → **无匹配**（除了 middleware chain 相关）。这跟 `docs/ITERATION-PLAN.md:692-700` 的 M1-K backlog 表达一致：

> "M1-K J.7b 8 项推迟见 § M1-K Agent skill 进化（agent author/refine tool / code 字段执行 / progressive loading / LLM moderation / public 内置库 / supporting files / per-agent 启停细化 / UI 元数据）"

—— 8 项里没有"plugin hook 系统"。helix 显然认为这条线（hook）不是产品方向。

## 12.5 运行时行为

```
control-plane 启动
  │
  ├─ 加载 manifest（YAML + Pydantic 校验）
  ├─ skill_resolver.resolve(spec.skills) → 查 skill_version 表
  ├─ tool_assembler.build_tool_registry():
  │     ├─ 注册 builtin tools
  │     ├─ 注册 MCP tools (from tenant_config.mcp_servers)
  │     ├─ 注册 subagent tools (from spec.subagents)
  │     ├─ 注册 skill tools (subset of builtin + override prompt)
  │     └─ 校验 conflict
  ├─ middleware_assembler.build_chains():
  │     ├─ before_llm_call chain
  │     ├─ around_llm_call chain (per-provider)
  │     ├─ after_llm_call chain
  │     └─ before_tool_dispatch chain
  │
  └─ runtime.get_agent(...) → BuiltAgent (frozen)

主循环 tool_call
  │
  └─ tools_node._dispatch_tool(tool, args, ctx):
        before_tool_dispatch chain.invoke(ctx)
          ├─ SandboxAuditMiddleware：检查 args 含黑名单命令？raise ToolBlockedError
          └─ ...
        tool.call(args, ctx)  ← 实际 tool 调用
```

## 12.6 局限与边界

- **无热加载**：tool / skill / middleware 改动都需要 build agent 重新装配（manifest deploy 或 process restart）。
- **无 plugin entry point**：第三方 pip 包不能通过 `setup.py entry_points` 注入 tool（Hermes 有）。
- **Middleware 顺序硬编码**：用户不能改 middleware 顺序，只能改 manifest opt-out 单个（如 `cache.enabled=False`）。
- **Skill `code` 字段不可执行**（M0）：M1-K J.7b-2 才解禁。
- **无 supporting files**（M0）：跟 Hermes 的 `references/` / `templates/` / `scripts/` 不同，M0 helix Skill 是单一 prompt fragment + tool name list。M1-K J.7b-6 backlog。
- **Agent 不能在 run 中改 skill**：M1-K J.7b-1 才有 `author_skill` / `refine_skill`。
- **无 public skill 内置库**：M1-K J.7b-5（参考 deer-flow `/skills/public`）。
- **跨租户共享**：所有 skill 严格 per-tenant（`UNIQUE(tenant_id, name)`），不允许跨租户引用（marketplace 在 M3）。

---

# 维度 13 — UI 形态

## 13.1 现状评级

**部分实现**。
- **Admin UI**（React 19 + Vite + Antd 5）：M0 ✅（Stream H.1-H.4，2026-05-26 收官）。完整 IA：登录 / Agents / Sessions / Runs / Playground / Approval / Curation / Skills / Triggers / Memory / Audit / Settings IAM+Ops。
- **CLI**：❌ **不在 M0 scope**。M1-I `helix lint` / `helix run`（manifest 本地跑 + lint）。
- **末端用户对话 UI**：❌ **不在 scope**（设计决策，业务系统责任，见维度 10）。
- **VS Code / IntelliJ JSON Schema**：❌ M1-I backlog（manifest 自动补全）。

ITERATION-PLAN 锚点：
- Stream H.1a/b/2/3/4：M0 ✅（2026-05-26 整体收官）
- Stream N：M0 ✅（H 上线前提）
- M1-I CLI + Admin UI 升级：M1 backlog（`docs/ITERATION-PLAN.md:713-717`）

## 13.2 设计立场

来自 `docs/ITERATION-PLAN.md:328` H Stream 范围澄清原文：

> "Admin UI 仅服务操作人群（平台 admin / agent 开发者 / 运营 / SRE）—— **单面 SPA**。debug 能力作为 per-agent **Playground tab** 嵌入 Agent 详情页。原 H.4（用户面）取消"

锁定 10 条设计基线（来自 `docs/ITERATION-PLAN.md:332` H.1a 决策记忆 [project_admin_ui_design_baseline]）：

- 单面操作端（不混末端用户）
- Linear + Console taste（生产级 UI/UX，不堆 Antd 默认组件）
- Dark-first（dark/light theme toggle）
- Cyan + violet brand
- Agent-中心 7 IA
- Inter + JBMono 字体
- zh-CN + en
- WCAG AA 承诺
- 响应式 ≥ 1280px
- Lighthouse ≥ 90，首屏 < 2s

## 13.3 关键代码路径

| 角色 | 文件 | 关键符号 |
|------|------|---------|
| Admin UI 工程 | `apps/admin-ui/` | Vite + TS + Antd 5 |
| 路由 | `apps/admin-ui/src/router.tsx` | react-router-dom v7 |
| 认证 | `apps/admin-ui/src/auth/`（推断） | OIDC code-flow PKCE + silent renew |
| Shell | `apps/admin-ui/src/shell/`（推断） | 瘦左导航 + 薄顶 bar |
| CommandPalette | `apps/admin-ui/src/command-palette/`（推断） | Cmd+K real routes |
| Agents page | `apps/admin-ui/src/agents/`（推断） | 列表 / 详情 / Playground tab |
| Runs page | `apps/admin-ui/src/runs/`（推断） | 跨 thread 索引 / 事件流 |
| Curation page | `apps/admin-ui/src/curation/`（推断） | H.4 PR #298 |
| Skills page | `apps/admin-ui/src/skills/`（推断） | H.4 PR #299/300 |
| Triggers page | `apps/admin-ui/src/triggers/`（推断） | H.4 PR #302 |
| Settings page | `apps/admin-ui/src/settings/`（推断） | H.4 PR #303/304 |
| Storybook | `apps/admin-ui/.storybook/`（推断） | 组件文档 |
| E2E 测试 | `apps/admin-ui/e2e/`（推断） | Playwright + axe a11y |

## 13.4 实现细节

### 13.4.1 H Stream 5 子项

| 子项 | PR | 范围 |
|------|----|------|
| H.1a 设计基线 | #262/263 | 6 原则 + Agent-中心 IA + WCAG AA + tokens + Antd override + mockups + brand glyph |
| H.1b React 19 + Vite + Antd 5 骨架 | #264/272/274/277-281（8 PR 链） | Vite + Antd ConfigProvider + i18n + OIDC PKCE + CommandPalette + Storybook + Playwright + axe a11y |
| H.2 Agent / Manifest 管理 + Playground | #284/285/286（3 PR 链） | Monaco YAML 编辑器 + Create Agent flow + Playground tab（SSE + tool calls 日志） |
| H.3 Runs + Trace + Approval | #289-294（6 PR 链） | 跨 thread Runs 索引 + run_event 表 + EventStreamPanel + ApprovalCard Monaco UX + TraceToolbar（Langfuse 外链） |
| H.4 治理面 | #296-304 + 收尾（10 PR 链） | Memory / Curation+Eval / Skills / Triggers / Settings IAM+Ops / Audit 跨 agent 跨 user 治理视图 |

### 13.4.2 N Stream 跨租户能力（H 上线前提）

`docs/ITERATION-PLAN.md:359-374`：4 决策 + 6 子项 + 38 集成测试矩阵，引入 `Role.SYSTEM_ADMIN` enum + `role_binding.platform_scope` 字段 + 默认 "All tenants" 聚合视图。

UI 侧（`useAuth() → { isSystemAdmin, currentTenantScope }`）让 system_admin 默认看跨租户视图，操作切租户全留 audit。

### 13.4.3 OIDC 认证

H.1b PR #278（PR 2b）：OIDC code-flow PKCE + silent renew + API Key / JWT 旁路（机器 principal）。生产用 Keycloak（C.1 Mini-ADR 还在 `docker-compose Keycloak service + dev realm 配置待补`，`docs/ITERATION-PLAN.md:221`）。

### 13.4.4 国际化

i18next + zh-CN + en，全 UI 文案双语支持（H.1b PR #277）。

### 13.4.5 CLI 现状

之前 explorer 在 `apps/` / `tools/` / `services/` 下 grep `typer` / `click` / `rich` 等关键词 → 只找到 `tools/deploy/deploy.py` / `tools/persistence/restore_*` 这类**内部运维脚本**，**没有用户面 CLI**。

`docs/ITERATION-PLAN.md:713-717` M1-I：

> "M1-I CLI + Admin UI 升级（~3 周）
> - [ ] `helix lint` + `helix run`（本地跑 manifest）
> - [ ] Admin UI：版本对比、灰度面板、Vault secret 管理
> - [ ] JSON Schema 发布（VS Code/IntelliJ 自动补全）"

M0 故意不做用户 CLI —— 操作员 / SRE 用 Admin UI 或 curl 直接 hit REST API。

## 13.5 运行时行为

```
Browser → https://helix.example.com → nginx (TLS 终止)
                                          │
                                          └─ Admin UI 静态资源 + /api/* 反代到 control-plane
                                                │
                                                └─ OIDC code-flow:
                                                      ├─ 跳 Keycloak login
                                                      ├─ 拿 code → 后端换 token
                                                      ├─ silent renew loop
                                                      └─ axios interceptor 加 Bearer header

Admin UI 主要视图调 control-plane API：
  GET    /v1/me                          → 当前用户 + isSystemAdmin + currentTenantScope
  GET    /v1/agents                      → Agent 列表（system_admin 见跨租户）
  POST   /v1/agents                      → 创建 Agent（manifest YAML）
  PUT    /v1/agents/{id}                 → 更新 manifest（Monaco YAML 编辑器）
  GET    /v1/sessions                    → Session 列表
  POST   /v1/sessions/{id}/runs (SSE)    → Playground 跑 agent
  GET    /v1/runs                        → 跨 thread Runs 列表
  GET    /v1/runs/{id}/events            → 事件流（SSE，replay + live）
  POST   /v1/runs/{id}/approve           → 批准 pending approval
  GET    /v1/curation?status=pending     → Curation 队列
  POST   /v1/curation/{id}/promote       → 升 eval dataset
  GET    /v1/skills                      → Skill 列表
  POST   /v1/skills/{id}/versions        → 发布新版本
  GET    /v1/triggers                    → Trigger 列表
  GET    /v1/memory                      → Memory CRUD
  GET    /v1/audit?cross_tenant=true     → Audit log（system_admin）
  GET    /v1/admin/tenant-config         → 租户配置
  GET    /v1/admin/role-bindings         → IAM
```

## 13.6 局限与边界

- **无 CLI**：M1-I 才有 `helix lint` / `helix run`；M0 没有本地跑 manifest 工具，全靠 control-plane API。
- **无终端 TUI**：跟 Hermes 的 prompt_toolkit + Rich 不同，helix 完全是 web UI。
- **无末端用户对话 UI**：这是设计决策（业务系统负责），M3 也不会做。
- **无 IDE 集成**：M1-I 才有 manifest JSON Schema 发布。
- **Admin UI 单 SPA**：M1+ 灰度面板 / Vault secret 管理 / 版本对比 是 backlog。
- **Storybook / E2E / a11y 测试在**（H.1b PR #281）：但完整的 design system 文档还在持续补。
- **响应式仅 ≥ 1280px**：手机 / 平板不优化（操作员是 desktop 用户）。
- **末端用户的 helix-side persistence 不可见**：用户在业务系统里看到的不是 helix 的 Admin UI，业务系统自己渲染 helix 返回的 trajectory 给末端用户。

---

# 维度 14 — 技能可移植（agentskills.io 风格）

## 14.1 现状评级

**部分实现**。
- **Skill 协议 + 持久化 + 版本管理**：M0 ✅（Stream J.7a，2026-05-21 完成 5 PR 链）。
- **`name@version` 引用 + DRAFT/ACTIVE/ARCHIVED 状态机**：M0 ✅。
- **ZIP import/export + admin moderation（regex deny-list）+ audit**：M0 ✅。
- **跟 agentskills.io 标准的对齐**：❌ **不一致**。helix Skill 是 `(prompt_fragment + tool_names + required_models)` 三元组存 Postgres，不是 agentskills.io 的 SKILL.md frontmatter + supporting files。
- **跨租户共享 / Marketplace**：❌ **M3 backlog**（`docs/ITERATION-PLAN.md:32`：`内部 marketplace`）。

ITERATION-PLAN 锚点：
- Stream J.7a：M0 ✅
- M1-K J.7b（8 项）：M1 backlog
- M3 内部 marketplace：M3 backlog

## 14.2 设计立场

helix 的 Skill 设计**故意不对齐 agentskills.io 标准**，原因是：

1. **多租户隔离强于跨工具可移植性**：每个租户的 skill 严格 per-tenant（`UNIQUE(tenant_id, name)`），不能跨租户引用；agentskills.io 设计上是公共 hub，跨用户跨工具共享，跟 helix 多租户企业定位冲突。
2. **强 governance 模型**：DRAFT → ACTIVE → ARCHIVED 状态机 + admin moderation + audit，每次发版都有审计记录；agentskills.io 是 git-based 自由 publish。
3. **Postgres 单一真值源**：Skill 内容在 DB 不在文件系统，便于 RLS / backup / migration；agentskills.io 是文件系统（SKILL.md + references/ / templates/ / scripts/）。
4. **Tool subset 显式声明**：Skill 必须列出它启用的 tool_names，构建期 conflict detection；agentskills.io 是 prompt 自由提到 tool 名。
5. **`<skill>` XML 包裹 prompt injection 防护**：J.7a Mini-ADR J-23 修订要求 skill prompt fragment 加 `<skill name="..." version="...">...</skill>` 包裹，防止 skill 内容 jailbreak 主 system prompt。

## 14.3 关键代码路径

| 角色 | 文件 | 关键符号 |
|------|------|---------|
| Skill 协议 | `packages/helix-protocol/src/helix_agent/protocol/skill.py` | `Skill` / `SkillVersion` / `SkillRef` / `SkillStatus` / `SKILL_REF_PATTERN` / `parse_skill_ref` |
| Skill ORM | `packages/helix-persistence/src/helix_agent/persistence/models/skill.py:31-66` | `SkillRow` / `SkillVersionRow` |
| Skill Store | `packages/helix-persistence/src/helix_agent/persistence/skill_store.py` | `SkillStore` ABC + In-Memory + SQL impl |
| 迁移 | `packages/helix-persistence/migrations/versions/0029_skill.py` | `skill` + `skill_version` 表 |
| Skill API | `services/control-plane/src/control_plane/api/skills.py` | 9 endpoint + ZIP import/export |
| Moderation | `services/control-plane/src/control_plane/skill_moderation.py`（推断） | regex deny-list |
| Skill 加载 | `services/orchestrator/src/orchestrator/agent_factory.py:200+`（`_load_skills_phase`） | resolve + tool inject + `<skill>` 包裹 |
| AgentFactoryError 子类 | `services/orchestrator/src/orchestrator/errors.py` | `SkillNotFoundError` / `SkillNotActiveError` / `SkillModelMismatchError` / `SkillToolConflictError` 等 5 类 |
| Eval | `tools/eval/skill.py` | 12 case 覆盖 resolve / error / moderation / zip |
| Admin UI | `apps/admin-ui/src/skills/` | H.4 PR #299/300 |

## 14.4 实现细节

### 14.4.1 Skill DTO（`packages/helix-protocol/src/helix_agent/protocol/skill.py`）

```python
class SkillStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"

@dataclass(frozen=True)
class Skill:
    id: UUID
    tenant_id: UUID
    name: str                            # SKILL_REF_PATTERN
    status: SkillStatus
    latest_version: int                  # 0 = 没发过版
    description: str
    category: str | None                 # tool_use | code | retrieval | ...
    created_at: datetime

@dataclass(frozen=True)
class SkillVersion:
    id: UUID
    skill_id: UUID
    tenant_id: UUID
    version: int                         # 1-based，不可变
    prompt_fragment: str                 # Markdown，构建期 `<skill>` 包裹
    tool_names: tuple[str, ...]          # 此版本激活的 tools
    required_models: tuple[str, ...]     # 模型兼容性约束
    authored_by: Literal["human", "agent"]  # J.19 learning loop 标记（M0 都是 human）
    created_at: datetime

@dataclass(frozen=True)
class SkillRef:
    name: str
    version: int | None                  # None = latest ACTIVE
```

### 14.4.2 Skill 引用 grammar

`SKILL_REF_PATTERN = r"^[a-z][a-z0-9_-]{0,63}(@[1-9][0-9]*)?$"`

- 合法：`reporting` / `reporting@2` / `code-executor` / `web-search@10`
- 非法：`Reporting`（大写）/ `_foo` / `1foo` / `foo@0`（版本必 ≥1）

### 14.4.3 `<skill>` XML 包裹（Mini-ADR J-23 修订）

构建期把每个 skill 的 prompt_fragment 拼成：

```text
<skill name="reporting" version="2">
{prompt_fragment 内容}
</skill>

<skill name="sentiment" version="1">
...
</skill>
```

进 system message。LLM 能区分 "skill instruction" vs "user input"，防止 skill 内容里的 `# Ignore previous instructions` 这种 jailbreak。

### 14.4.4 错误类型

`services/orchestrator/src/orchestrator/errors.py`（J.7a Step 2 PR #234 添加 5 个新子类）：

- `SkillNotFoundError`：skill_id / name 不存在
- `SkillVersionNotFoundError`：name 存在但 version 不存在
- `SkillNotActiveError`：bare name 但 latest_version 不是 ACTIVE
- `SkillModelMismatchError`：skill `required_models` 不含主 model
- `SkillToolConflictError`：两 skill 声明同名 tool

构建期 fail，运行期不会发生。

### 14.4.5 ZIP import/export

- **export**：`GET /v1/skills/{id}/export` → ZIP（含 latest_version 的 SKILL.md + metadata.json）
- **import**：`POST /v1/skills/import` 接 ZIP →
  - 防 ZIP slip（路径含 `..` 拒）
  - 防 size cap 超限（默认 1MB）
  - regex deny-list moderation（M0 用 regex，M1-K J.7b-4 升级到 LLM judge）
  - 创建 DRAFT skill，需要后续 admin promote 到 ACTIVE

audit：`SKILL_CREATE` / `SKILL_VERSION_CREATE` / `SKILL_STATUS_CHANGE`。

### 14.4.6 跟 agentskills.io 对照

| 维度 | agentskills.io | helix |
|------|---------------|-------|
| 载体 | SKILL.md frontmatter + body + subdirs | Postgres row（prompt_fragment + tool_names + required_models） |
| 版本 | semver | integer（1, 2, 3 ...） |
| 状态 | publish / archive | DRAFT / ACTIVE / ARCHIVED |
| 跨用户共享 | git-based 公共 hub | per-tenant 严格隔离（M3 才有 marketplace） |
| 引用 | `@hermes/web-search` | `web-search` 或 `web-search@2`（无作者命名空间） |
| Supporting files | `references/` / `templates/` / `scripts/` | ❌ 不支持（M1-K J.7b-6 backlog） |
| 平台限制 | `platforms: [macos, linux]` | ❌ 不支持（multi-tenant 服务端无 OS 概念） |
| Inline shell | `!`cmd`` 预处理 | ❌ 不支持 |
| Template vars | `${HERMES_SKILL_DIR}` | ❌ 不支持 |
| Agent 自创建 | 不支持 | ❌ M1-K J.7b-1 backlog |

## 14.5 运行时行为

```
admin 创建新 skill via Admin UI
  │
  └─ POST /v1/skills {name, description, category}
        ├─ SkillStore.create(...) → DRAFT, latest_version=0
        ├─ audit log: SKILL_CREATE
        └─ return Skill

admin 发布新版本
  │
  └─ POST /v1/skills/{id}/versions {prompt_fragment, tool_names, required_models}
        ├─ SkillStore.add_version(skill_id, version=latest_version+1, ...)
        ├─ skill.latest_version = version
        ├─ audit log: SKILL_VERSION_CREATE
        └─ return SkillVersion

admin promote 到 ACTIVE
  │
  └─ PATCH /v1/skills/{id} {status: "active"}
        ├─ skill.status = ACTIVE
        ├─ audit log: SKILL_STATUS_CHANGE
        └─ return Skill

agent_factory.build_agent(manifest)
  │
  ├─ for raw_ref in manifest.spec.skills:
  │     ref = parse_skill_ref(raw_ref)
  │     ↓
  │     SkillStore.get_version(tenant_id, name, version=ref.version)
  │       ├─ bare ref (version=None) → 查 latest_version where status=ACTIVE
  │       └─ pinned ref → 查特定 version（draft/active/archived 都可）
  │     ↓
  │     conflict check (tool_names 跟其他 skill 不重叠)
  │     ↓
  │     required_models check (主 model 在 list 里)
  │     ↓
  │     merged_system_prompt += "<skill name='...' version='N'>...</skill>"
  │     tool_registry.register(builtin_tool, from_skill=ref.name)
  │
  └─ build_react_graph + 主 loop
```

## 14.6 局限与边界

- **不兼容 agentskills.io 公共 hub**：M0 helix Skill 不能直接 import agentskills.io 上的 SKILL.md。
- **无 supporting files**：M1-K J.7b-6 backlog。
- **无 agent autonomy**：agent 不能在 run 中创建 / 修改 skill；M1-K J.7b-1 backlog。
- **无 progressive / lazy loading**：所有 skill 在构建期都拼进 system prompt；M1-K J.7b-3 backlog。
- **moderation 是 regex deny-list**：M1-K J.7b-4 升级 LLM judge。
- **无 public skill 内置库**：M1-K J.7b-5 backlog。
- **跨租户共享需要 M3**：内部 marketplace 在 M3，跨企业 marketplace 在 scope 外。
- **`code` 字段执行不开放**：M0 SkillVersion 没有 `code` 字段；M1-K J.7b-2 才解禁（依赖 Python 插槽 + sandbox + AST 校验）。
- **无 UI 元数据**（icon / color / display_name）：M1-K J.7b-8 backlog。

---

# 维度 15 — RL 训练 / 轨迹收集

## 15.1 现状评级

**部分实现**。
- **Trajectory 采集**（ShareGPT JSONL）：M0 ✅（Stream L.L7）。
- **Curation Candidate + Eval Dataset 三层**：M0 ✅（J.12 + J.13）。
- **Eval Gate**（CI 卡 PASS / FAIL + baseline）：M0 ✅（G.4）。
- **Feedback 收集**：M0 ✅（G.6）。
- **轨迹压缩**：❌ 不在 scope（Hermes 有专门的 trajectory_compressor，helix 没有；ShareGPT 直接喂训练流水线）。
- **Reward model / RL / SFT 训练闭环**：❌ **不在 scope**（导出 ShareGPT JSONL 后由用户自己 LlamaFactory / Axolotl / TRL 训）。

ITERATION-PLAN 锚点：
- Stream L.L7 Trajectory recording：M0 ✅
- Stream J.12 Curation Worker：M0 ✅
- Stream J.13 Eval Dataset：M0 ✅
- Stream G.4 Eval Gate：M0 ✅
- Stream G.6 Feedback：M0 ✅
- M2-D Eval Gate + 持续改进 pipeline：M2（合并 J.12 + J.13b/c）

## 15.2 设计立场

`services/orchestrator/src/orchestrator/trajectory/recorder.py:1-34` module docstring：

```
Hermes ``agent/trajectory.py:30-56`` splits success vs failure into
two files; we add a ``max_steps`` and ``cancelled`` split because our
durable-resume / max_steps paths are distinct events the eval gate
will want to weigh differently.

Mini-ADR L-7 highlights:

* **Plain ObjectStore, not WORM.** ``audit_log`` is the compliance
  source of truth (Stream D.1 sends it to S3 Object Lock); trajectory
  is LLM-trainable side data. A lost JSONL line is acceptable; a lost
  audit row is not.
* **Best-effort.** :meth:`TrajectoryRecorder.record` swallows
  ``ObjectStoreError`` after emitting a counter so it cannot stall
  the run's terminal path. Callers schedule it via
  ``asyncio.create_task`` with their own deadline.
* **Per-tenant prefix.** Per-tenant scan stays cheap; no
  cross-tenant trajectory mixing in the bucket layout.
```

设计立场：

1. **Trajectory ≠ audit**：audit 是合规 WORM source of truth；trajectory 是 LLM-trainable 训练数据，丢一条不致命。
2. **Best-effort + fire-and-forget**：`record()` swallow 错误 + emit counter，5s 外层 deadline，主 run terminal path 永不被它阻塞。
3. **4 outcome 分流**（多 Hermes 2 个）：`success` / `failed` / `max_steps` / `cancelled` 各自独立 prefix，eval gate 可分别 weigh。
4. **per-tenant prefix**：bucket 不跨租户混。
5. **直接 ShareGPT 格式**：跟 LlamaFactory / Axolotl / TRL 等开源训练框架兼容，不引入 helix 专属格式。

## 15.3 关键代码路径

| 角色 | 文件 | 关键符号 |
|------|------|---------|
| 模块 docstring | `services/orchestrator/src/orchestrator/trajectory/recorder.py:1-34` | Mini-ADR L-7 |
| Outcome 类型 | `recorder.py:57-59` | `TrajectoryOutcome = Literal["success", "failed", "max_steps", "cancelled"]` |
| Counter | `recorder.py:61-71` | `_trajectory_recorded_total` / `_trajectory_record_errors_total` |
| TrajectoryRecord | `recorder.py:74-92` | `@dataclass(frozen=True)` |
| ShareGPT 序列化 | `recorder.py:95-150+`（推断） | `serialize_messages_sharegpt(messages)` |
| TrajectoryRecorder | `recorder.py:200+`（推断） | `class TrajectoryRecorder` |
| SSE 集成 | `services/orchestrator/src/orchestrator/sse.py` | `_dispatch_trajectory` fire-and-forget |
| Eval Dataset DTO | `packages/helix-protocol/src/helix_agent/protocol/eval_dataset.py` | `CurationCandidateRecord` / `EvalDatasetRecord` |
| Curation Worker | `services/control-plane/src/control_plane/curation_worker.py` | `_classify()` |
| Eval Gate | `tools/eval/helix_eval.py` | YAML loader + agent runner + baseline 卡 |
| Export tool | `tools/eval/export_dataset.py` | `EvalDatasetRecord → YAML` |
| Baseline YAML | `tools/eval/baselines/m0_gate_baseline.yaml` | 7 PASS + 7 DEFERRED |
| 迁移 | `packages/helix-persistence/migrations/versions/0034_eval_dataset.py` | `eval_dataset` + `curation_candidate` 表 |

## 15.4 实现细节

### 15.4.1 ObjectStore key 布局（`recorder.py:7-15`）

```
{prefix}/{tenant_id}/{outcome}/{YYYY}/{MM}/{DD}/{thread_id}.jsonl
```

- `prefix` 默认 `trajectories`（`DEFAULT_PREFIX = "trajectories"` @ `:55`）
- `tenant_id` UUID 字符串
- `outcome` ∈ `{success, failed, max_steps, cancelled}`
- 日期分区
- 一个 thread 一个文件（同 thread 多 run 累加到同一 jsonl）

示例：`trajectories/abc-123/success/2026/05/27/def-456.jsonl`

### 15.4.2 TrajectoryRecord 字段（`recorder.py:74-92`）

```python
@dataclass(frozen=True)
class TrajectoryRecord:
    thread_id: UUID
    tenant_id: UUID
    outcome: TrajectoryOutcome
    messages: Sequence[BaseMessage]      # AgentState.messages
    user_id: UUID | None = None
    run_id: UUID | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    step_count: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

`messages` 是 LangChain `BaseMessage` 列表（系统、用户、助手、tool 4 类），`serialize_messages_sharegpt` 转 ShareGPT JSON。

### 15.4.3 ShareGPT 输出（`recorder.py:95-100`）

> "The output shape — one ``{role, content, ...}`` dict per message — matches the format Hermes saves to ``trajectory_samples.jsonl`` (``agent/trajectory.py``) and the loader our future J.13 eval gate ..."

显式跟 Hermes ShareGPT 兼容。每行一个 record：

```json
{
  "thread_id": "...",
  "outcome": "success",
  "started_at": "...",
  "finished_at": "...",
  "step_count": 5,
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "...", "tool_calls": [...]},
    {"role": "tool", "content": "...", "tool_call_id": "..."},
    {"role": "assistant", "content": "Final answer"}
  ],
  "metadata": {...}
}
```

### 15.4.4 J.13 Eval Dataset 三 source

```python
class EvalDatasetRecord:
    source: Literal["golden", "trajectory", "regression"]
    source_trajectory_key: str | None    # provenance to L7
```

- **golden**：人工编写的标准答案
- **trajectory**：从 curation candidate promote 来的
- **regression**：bug 修复后的回归用例

### 15.4.5 Eval Gate baseline（`tools/eval/baselines/m0_gate_baseline.yaml`）

`docs/ITERATION-PLAN.md:603`：

> "J.13a 逐能力 eval 场景集 baseline 锁定（`tools/eval/baselines/m0_gate_baseline.yaml`，2026-05-21 落地；7 PASS + 7 DEFERRED）"

每个能力（J.3 memory / J.4 sub-agent / J.5 RAG / J.6 multimodal / J.7 skill / 等）一个 case set，跑完后对照 baseline 看 pass_rate。

### 15.4.6 K.K12 Memory recall eval harness

`tools/eval/memory_recall.py` + `tools/eval/datasets/memory_recall/zh_en_seed.yaml`（4 zh + 4 en）+ embedder-agnostic runner + recall@k / mrr@k metric。

### 15.4.7 不在 scope 的部分

- **Trajectory compression**：Hermes 有 `trajectory_compressor.py` 用 OpenRouter gemini-3-flash-preview 压缩超 token 的轨迹；helix 没有这一步（ShareGPT 原始 jsonl 直接喂训练流水线）。
- **Reward model**：完全没有；评估靠 user feedback（up/down）+ outcome（completed / failed / max_steps）。
- **训练闭环**：helix 出 ShareGPT JSONL；后续微调（LlamaFactory / Axolotl / TRL / unsloth）由用户自己跑。
- **Atropos 集成**：搜不到 `import atropos` / `Atropos` 相关代码。

## 15.5 运行时行为

```
agent run 终态 (sse.py:run_agent 终结)
  │
  ├─ 确定 outcome：
  │     finish_reason == "stop" → success
  │     MaxStepsExceededError → max_steps
  │     RunCancelledError → cancelled
  │     其他异常 → failed
  │
  ├─ snapshot = await graph.aget_state(config)
  ├─ messages = snapshot.values.get("messages") or []
  │
  ├─ record = TrajectoryRecord(
  │     thread_id=..., tenant_id=..., outcome=...,
  │     messages=messages, step_count=...,
  │     metadata={"agent_name": ..., "model": ...},
  │ )
  │
  └─ asyncio.create_task(
        asyncio.wait_for(
            trajectory_recorder.record(record),
            timeout=_TRAJECTORY_DISPATCH_TIMEOUT_S,  # 5.0s 外层 deadline
        )
      )
        ├─ recorder.record 内部：
        │     try:
        │         data = serialize_messages_sharegpt(record.messages)
        │         line = json.dumps({...}) + "\n"
        │         key = f"{prefix}/{tenant_id}/{outcome}/{date}/{thread_id}.jsonl"
        │         await object_store.append(key, line)
        │         _trajectory_recorded_total.labels(outcome=outcome).inc()
        │     except ObjectStoreError as e:
        │         _trajectory_record_errors_total.labels(outcome=outcome, reason=...).inc()
        │         logger.warning(...)
        │
        └─ run terminal path 不被阻塞

后台 CurationWorker (周期 task)
  │
  └─ for new trajectory write:
        feedback = await feedback_store.get(thread_id, run_id)
        signal, rating = _classify(outcome, has_down, has_up)
        if signal:
            CurationCandidateStore.upsert(...)  # status=PENDING

admin 通过 Admin UI 审 candidate
  │
  ├─ promote → status=PROMOTED + 创建 EvalDatasetRecord
  └─ dismiss → status=DISMISSED

CI / 手动 eval
  │
  └─ python tools/eval/helix_eval.py --baseline m0_gate_baseline.yaml
        ├─ load datasets/*.yaml
        ├─ for each case: run agent → compare to expected
        ├─ compute pass_rate / recall@k / etc.
        └─ assert >= baseline，否则 CI 红

数据导出（用户准备训练数据）
  │
  └─ python tools/eval/export_dataset.py --tenant ... --agent ... --name ... --out ...
        └─ 生成 YAML（cases 数组）/ JSONL（ShareGPT）
              └─ 用户喂自己的 LlamaFactory / Axolotl / TRL pipeline
```

## 15.6 局限与边界

- **没有轨迹压缩**：超大 trajectory 直接进 jsonl 文件，下游训练时如果超 model context，用户自己处理（不像 Hermes 内置 trajectory_compressor）。
- **没有训练流水线**：M0 不闭环 RL / SFT，纯 data emit。M2-D "持续改进 pipeline" 才正式做 Eval Gate + 数据迭代闭环。
- **没有 reward model**：完全没有自动质量评估，全靠 user up/down 和 outcome label。
- **没有 LLM-as-judge**：M1-K J.7b-4 才用 LLM 升级 admin moderation；trajectory 评估没有 LLM judge 路径。
- **没有 trajectory 跨 thread 聚合**：每 thread 独立 jsonl，没有 user-level / agent-level 跨 thread 训练样本。
- **`completed` outcome 是粗粒度**：success 内还有"用户满意 vs 用户最终未满意"差异，helix 用 G.6 feedback 区分但不进 outcome 字段。
- **Curation 是规则驱动**：信号分类是硬编码的 negative > failed > positive，没有 ML / LLM-based candidate scoring。
- **数据导出仍要用户自己处理 PII**：trajectory 已经过 D.2 PII redactor（写入前），但客户在自训前可能要再过一遍合规扫。

---

# 附录 A — helix monorepo 目录速查

```
helix-agent/                                # 6e0e9ed @ 2026-05-27
├── CLAUDE.md                               # 项目级 LLM 协作规范
├── README.md
├── conftest.py                             # pytest 跨包 fixtures
├── pyproject.toml                          # uv workspace
├── uv.lock
│
├── packages/                               # 4 个共享 Python SDK
│   ├── helix-common/                       # observability / metrics validator
│   ├── helix-persistence/                  # SQLAlchemy 2.0 + Alembic
│   │   └── migrations/versions/0001..0038  # 38 个迁移
│   ├── helix-protocol/                     # Pydantic DTO 协议层
│   └── helix-runtime/                      # middleware / sandbox provider / secret
│
├── services/                               # 7 个独立部署的服务
│   ├── control-plane/                      # 主 FastAPI service
│   │   ├── src/control_plane/
│   │   │   ├── api/                        # 各资源的 REST + SSE 端点
│   │   │   ├── scheduler.py                # Trigger scheduler
│   │   │   ├── trigger_firing.py
│   │   │   ├── curation_worker.py
│   │   │   ├── subagent_runtime.py         # ChildAgentBuilder
│   │   │   └── tenant_scope.py             # system_admin RLS
│   │   └── tests/
│   │
│   ├── orchestrator/                       # LangGraph 库形态（in-process 与 control-plane 共进程）
│   │   └── src/orchestrator/
│   │       ├── state.py                    # AgentState
│   │       ├── runner.py                   # GraphRunner + sanitize_thread
│   │       ├── sse.py                      # run_agent + sse_consumer
│   │       ├── agent_factory.py            # build_agent / build_llm_router / detect_subagent_cycle
│   │       ├── middleware_assembly.py
│   │       ├── errors.py                   # MaxStepsExceededError 等
│   │       ├── graph_builder/
│   │       │   ├── builder.py (754 行)     # build_react_graph
│   │       │   ├── memory.py               # memory_recall + writeback node
│   │       │   ├── planner.py              # plan_execute 节点（J.1）
│   │       │   ├── reflect.py              # 反思节点（J.2）
│   │       │   ├── _approval.py            # 审批 helper（J.8）
│   │       │   └── _config.py              # cancellation_token 取值
│   │       ├── context/
│   │       │   └── compressor.py           # L.L2 ContextCompressor
│   │       ├── llm/
│   │       │   ├── router.py               # LLMRouter + fallback tree
│   │       │   ├── caller.py               # LLMCaller Protocol
│   │       │   ├── oauth_provider.py       # L.L8 OAuthCapableProvider
│   │       │   ├── rate_limit.py           # RateLimitedProvider
│   │       │   └── providers/
│   │       │       ├── anthropic.py        # HTTPAnthropicClient + cache_control
│   │       │       ├── openai.py
│   │       │       └── openai_compatible.py # kimi/glm/deepseek/qwen/doubao/self-hosted
│   │       ├── tools/
│   │       │   ├── registry.py             # ToolRegistry / ToolSpec / ToolResult / ToolContext
│   │       │   ├── assembly.py             # build_tool_registry
│   │       │   ├── scheduling.py           # plan_stages + MAX_TOOL_WORKERS=8
│   │       │   ├── mcp.py                  # MCP Client (stdio)
│   │       │   ├── subagent.py             # SubAgentTool
│   │       │   ├── sandbox.py              # exec_python 工具
│   │       │   ├── mutation_classifier.py  # L.L4 mutation classification
│   │       │   └── http.py                 # E.8
│   │       ├── trajectory/
│   │       │   └── recorder.py             # L.L7 TrajectoryRecorder
│   │       └── resume.py                   # sanitize_dangling_tool_calls
│   │
│   ├── credential-proxy/                   # aiohttp 出站凭证注入
│   ├── sandbox-supervisor/                 # FastAPI Docker / gVisor 沙箱
│   │   └── src/sandbox_supervisor/         # 4452 行
│   │       ├── supervisor.py
│   │       ├── docker_client.py            # CliDockerClient
│   │       ├── lifecycle.py
│   │       ├── runner_link.py              # stdio JSON-lines 协议
│   │       └── quota_enforcer.py
│   ├── audit-backup-worker/                # audit_log → S3 WORM
│   ├── event-log-archive-job/              # event_log 半年归档
│   └── retention-cleanup-job/              # TTL 清理 + image lifecycle
│
├── apps/                                   # 前端
│   ├── admin-ui/                           # React 19 + Vite + Antd 5（17.5K TS+TSX）
│   │   ├── src/
│   │   │   ├── shell/                      # 瘦左导航 + 顶 bar
│   │   │   ├── command-palette/            # Cmd+K
│   │   │   ├── auth/                       # OIDC PKCE
│   │   │   ├── agents/                     # Agent 列表 / 详情 / Playground
│   │   │   ├── runs/                       # Runs 跨 thread 索引 / EventStreamPanel
│   │   │   ├── sessions/                   # Session CRUD
│   │   │   ├── curation/                   # Curation 队列
│   │   │   ├── skills/                     # Skill 库
│   │   │   ├── triggers/                   # Trigger 管理
│   │   │   ├── memory/                     # Memory CRUD
│   │   │   ├── settings/                   # IAM / Ops
│   │   │   └── router.tsx                  # react-router-dom v7
│   │   ├── .storybook/
│   │   └── e2e/                            # Playwright + axe
│   └── admin-ui-demo/                      # H.1a 设计阶段 mockup
│
├── tools/                                  # 工具脚本
│   ├── eval/                               # 9.6K LOC
│   │   ├── helix_eval.py                   # 主 runner
│   │   ├── export_dataset.py               # EvalDatasetRecord → YAML
│   │   ├── memory_recall.py                # K.K12
│   │   ├── sub_agent.py                    # J.4 baseline
│   │   ├── rag.py                          # J.5 baseline
│   │   ├── skill.py                        # J.7a baseline
│   │   ├── platform_admin.py               # N.6 跨租户场景
│   │   ├── datasets/                       # YAML test cases
│   │   └── baselines/
│   │       └── m0_gate_baseline.yaml       # 7 PASS + 7 DEFERRED
│   ├── deploy/
│   │   ├── deploy.py                       # I.2 蓝绿 + canary
│   │   └── rollback.py                     # I.3 回滚
│   ├── observability/
│   │   └── rules/sli.yml                   # Prometheus recording rules
│   ├── dev-certs/ / tls/                   # 本地开发 mTLS
│   └── persistence/
│       ├── restore_audit.py                # K14 WORM restore drill
│       └── test_pg_restore_drill.py        # K15 testcontainers
│
├── environments/                           # dev.yaml / staging.yaml / prod.yaml
├── infra/                                  # docker-compose 全栈 + observability profile
│
└── docs/
    ├── CLAUDE.md
    ├── ITERATION-PLAN.md                   # 921 行主路线图
    ├── architecture/
    │   ├── 00-OVERVIEW.md
    │   ├── 01-SYSTEM-ARCHITECTURE.md
    │   ├── 02-AGENT-MANIFEST.md
    │   ├── 03-MONOREPO-LAYOUT.md
    │   ├── 04-ROADMAP.md
    │   ├── 05-RISKS.md
    │   ├── 06-OPEN-SOURCE-DEPS.md
    │   ├── 07-INFRASTRUCTURE-GAPS.md       # 24 项 P0
    │   ├── 08-AGENT-CAPABILITY-ASSESSMENT.md # 26 维 agent 能力评估
    │   └── subsystems/                     # 13-memory-store / 14-sandbox / ...
    ├── streams/
    │   └── STREAM-A..N-DESIGN.md           # 13 份 stream 设计
    ├── adr/                                # Architecture Decision Records
    ├── decisions/                          # Phase 启动决策
    ├── design/                             # admin-ui-philosophy / admin-ui-language
    ├── dev/ / dr/ / runbooks/ / security/
    └── research/
        ├── 01-orchestration-engines.md
        ├── 02-sandbox-isolation.md
        ├── 03-managed-agents-platforms.md
        ├── 04-deerflow-source-analysis.md
        ├── 05-deerflow-deeper-scan.md
        ├── hermes-deep-dive.md             # Hermes 15 维度事实底稿
        └── helix-current-state.md          # 本报告
```

# 附录 B — 关键文件索引（按维度）

| 维度 | 主要文件 |
|------|---------|
| 1. Agent 循环 | `services/orchestrator/src/orchestrator/{state.py, runner.py, sse.py, graph_builder/builder.py, tools/scheduling.py}` |
| 2. 自我改进 | `services/control-plane/src/control_plane/{curation_worker.py, api/feedback.py}`、`packages/helix-protocol/src/helix_agent/protocol/eval_dataset.py` |
| 3. 记忆 | `packages/helix-persistence/src/helix_agent/persistence/memory/{base.py, sql.py, dlq.py}`、`services/orchestrator/src/orchestrator/graph_builder/memory.py`、`migrations/0013/0017/0024/0025` |
| 4. 上下文管理 | `services/orchestrator/src/orchestrator/context/compressor.py`、`packages/helix-runtime/.../middleware/{dynamic_context.py, token_usage.py}` |
| 5. Provider | `services/orchestrator/src/orchestrator/llm/{router.py, caller.py, oauth_provider.py, rate_limit.py, providers/}` |
| 6. 本地推理 | `services/orchestrator/src/orchestrator/llm/providers/openai_compatible.py:make_self_hosted_client` |
| 7. 沙箱 | `services/sandbox-supervisor/`（整服务 4452 行）、`packages/helix-runtime/src/helix_agent/runtime/sandbox/runtime_provider.py` |
| 8. 子 Agent | `services/orchestrator/src/orchestrator/tools/subagent.py`、`services/orchestrator/src/orchestrator/agent_factory.py:detect_subagent_cycle` |
| 9. Cron / Trigger | `packages/helix-protocol/.../trigger.py`、`services/control-plane/src/control_plane/{scheduler.py, trigger_firing.py, api/triggers.py}`、`migration 0033` |
| 10. 消息平台 | **不内置**；`services/control-plane/src/control_plane/api/triggers.py:335+` webhook 端点 |
| 11. MCP | `services/orchestrator/src/orchestrator/tools/mcp.py`、`migration 0011`、`packages/helix-protocol/.../tenant_config.py` |
| 12. 扩展机制 | `services/orchestrator/src/orchestrator/{tools/registry.py, tools/assembly.py, middleware_assembly.py}`、`packages/helix-protocol/.../skill.py` |
| 13. UI | `apps/admin-ui/`（17.5K TS+TSX） |
| 14. 技能可移植 | `packages/helix-protocol/.../skill.py`、`packages/helix-persistence/src/helix_agent/persistence/models/skill.py`、`services/control-plane/src/control_plane/api/skills.py`、`migration 0029` |
| 15. RL / Trajectory | `services/orchestrator/src/orchestrator/trajectory/recorder.py`、`tools/eval/{helix_eval.py, export_dataset.py, baselines/m0_gate_baseline.yaml}`、`migration 0034` |

# 附录 C — ITERATION-PLAN 阶段对应表

| 维度 | M0 已交付 | M1 backlog | M2 backlog | M3 backlog |
|------|----------|-----------|-----------|-----------|
| 1. Agent 循环 | E.6 + L.L1-L8（8 条 Hermes-derived） | — | — | — |
| 2. 自我改进 | J.12 curation + G.6 feedback + L.L7 trajectory + J.13 eval | M1-K J.7b-1 `author_skill`/`refine_skill` | — | — |
| 3. 记忆 | J.3 + K.K6 CRUD + K.K7 DLQ + K.K12 eval gate | — | M2-C Memory archive 层（冷迁移） | — |
| 4. 上下文管理 | L.L2 + E.3 dynamic context + G.9 token usage | — | — | — |
| 5. Provider | E.11/E.11.5 9 provider + E.12 限流 + E.13 cache + L.L3 stale + L.L8 OAuth | — | — | — |
| 6. 本地推理 | self-hosted provider + base_url | — | — | — |
| 7. 沙箱 | F.1-F.11 + K.K5 gVisor gate | M1-A warm pool（P95 < 500ms）+ 镜像供应链 + cosign | M2-F Chaos 工程 | M3 K8s 沙盒 |
| 8. 子 Agent | J.4 + J.4-补强 + J.4-补强-2 + L.L6 并行 | — | M2-B Multi-Agent Orchestration（子 SSE 流回父） | — |
| 9. Cron / Trigger | J.10 + B.7 + N.4 + H.4 | M1-G 灰度 + Canary | M2-D Eval Gate 持续改进 | — |
| 10. 消息平台 | webhook trigger（HMAC） | — | — | — |
| 11. MCP Client | E.9 stdio + 20K 中间截断 + N=5 cap | HTTP / SSE transport | — | — |
| 11. MCP Server | — | — | — | — |
| 12. 扩展机制 | J.7a Skill 库（DRAFT/ACTIVE/ARCHIVED + ZIP import/export）+ Tool registry + 中间件链硬编码 | M1-K J.7b（8 项）+ M1-F2 Python 插槽 | — | M3 内部 marketplace |
| 13. UI - Admin | H.1-H.4（5 子项 24 PR）+ N system_admin | M1-I Admin UI 升级（版本对比、灰度面板、Vault） | — | — |
| 13. UI - CLI | — | M1-I `helix lint` / `helix run` + JSON Schema | — | — |
| 13. UI - 末端用户 | — | — | — | — |
| 14. 技能可移植 | J.7a name@version + DRAFT/ACTIVE/ARCHIVED + audit | M1-K 8 项 | — | M3 内部 marketplace |
| 15. RL / Trajectory | L.L7 + J.12 + J.13 + G.4 eval + G.6 feedback | — | M2-D Eval Gate + 持续改进 pipeline | — |

# 附录 D — 跨维度复用的设计模式总结

1. **Declarative manifest + DB 装配**（Skill / Subagent / Trigger / MCP / Hook 全用同一思路）
   - YAML manifest 声明 → Pydantic 校验 → control-plane 持久化 → agent_factory build 期装配
   - 跟 Hermes "filesystem + AST 扫描" 完全不同的哲学

2. **Per-tenant RLS + system_admin 跨租户开关**（所有存储一致）
   - 默认严格 per-tenant；`Role.SYSTEM_ADMIN` + `role_binding.platform_scope` 升级到跨租户
   - 所有跨租户访问留 audit

3. **Best-effort fire-and-forget + counter + outer deadline**（trajectory / subagent trajectory / 后台 task 通用）
   - `asyncio.create_task` + `asyncio.wait_for(deadline)` + swallow 内部异常
   - 主 run terminal path 永不被旁路阻塞
   - 3-档 counter（success / store_error / unexpected）

4. **PostgresSaver checkpoint + cancellation_token in RunnableConfig**（LangGraph runtime 约束）
   - 所有可序列化状态进 AgentState；不可序列化 runtime object 走 configurable
   - cancellation token 是全链路 `asyncio.Event`，每个 phase 协作式 polling

5. **Window protection + summarise the middle + max_passes + hard fail**（context compression）
   - 头尾保留 + 中间摘要 + 限轮 + 失败显式 raise
   - 从 Hermes 直接学（L.L2 Mini-ADR L-2 显式 acknowledge）

6. **`<skill>` / `<context-summary>` / `<mutation-advisory>` XML 包裹**（防 prompt injection）
   - 任何动态注入到 system 的 LLM-readable 文本都用 XML 包裹
   - 让 LLM 能区分 "动态注入" vs "用户指令"

7. **Zero-tech-debt 6 条 + 设计先行**（所有 stream 都遵循）
   - 每 stream 编码前必须先做架构设计 + 更新文档 + self-review
   - 每 stream 完成前必须 6 条核验（无 TODO / 测试达标 / 文档同步 / 可观测齐全 / CI 全绿 / bug 不遗留）

8. **Webhook + SSE + cron + sub-agent fan-out 共享 fire_trigger / run_agent 管道**
   - 所有触发模式都进同一 LangGraph 主循环
   - 同样的 middleware / context compression / memory recall / trajectory recording
   - "怎么触发"是边缘，"如何执行"是核心

— EOF —
