# P1 自改进飞轮 — 架构设计

> 日期：2026-06-13
> 配套：能力评估 `2026-06-13-agent-harness-capability-assessment.md` ·
> 迭代计划 `2026-06-13-agent-harness-5star-iteration-plan.md`
> 范围：满分化计划 P1（最高商业价值 + 能力护城河）。本文档定飞轮架构、S0 核实修订、
> 及首个落地单元 10.1 连接式 trace 的详细设计。

## 1. 为什么是飞轮

P1 七项不是七个孤立 feature，而是一个**自改进闭环**：agent 跑 → 可观测（trace）→ 可评测（eval）
→ 可纠错（evaluator-optimizer）→ 把学到的固化（自写 skill）→ 下轮更强。这是产品护城河：
能力随使用自动复利，而非靠人工逐次调优。

```
真实 run ──(10.1 连接式 trace)──> span 树 (root + LLM + tool + subagent)
   │                                      │
   │                          (11.4 trace-based eval 读 span 做断言/根因)
   ▼                                      ▼
(1.3 evaluator-optimizer 运行内自纠)   (11.6 eval worker 周跑) ──> (11.3 会话级指标)
   │                                      │                            │
   └──> 低分/失败轨迹 ──(11.5 对抗集 + curation)──> golden set ────────┘
                          │
                   (4.4 agent 自写 skill：把学到的固化成 skill)
```

## 2. S0 核实修订（诚实再评分）

按 W0 先例（10.1 曾被高估 4→2），本次勘探对 P1 逐项核实代码，揪出**两项被低估**：

### 1.3 Evaluator-Optimizer：3★ → **4★**
`graph_builder/reflect.py:make_reflect_node()` 已是完整 Evaluator-Optimizer：LLM 评判
`accept`/`revise`（严格 prompt，catch premature ending）→ `revise` 注入 critique 反馈 loop 回
agent（`_after_reflect` builder.py:879）→ budget 限界 + 超时/不可解析 fail-safe accept + plan_execute
下可 replan。生产级。
**唯一 gap（差 5★）**：评判复用同一 agent `llm_caller`，无独立 evaluator 模型/结构化 rubric 评分。
→ 4.4 修订：从"新建"降为"小补"（可选给 reflect 接独立 judge 模型 + 评分维度）。

### 4.4 agent 自写 skill：3★ → **4★**
`tools/skill_authoring.py`（Stream SE, J.7b-1）M0 已有 7 个 in-session builtin：`author_skill` /
`refine_skill` / `fork_skill` / `propose_skill_to_tenant` / `note_behavior_patch` /
`clarify_tool_usage` / `remember`。agent 运行中能长自己的 skill 库（DRAFT + agent_private +
写时威胁扫描 + high_risk 计算 + propose 开 SE-8 审核）。**评估"M0 agent 不能自写 skill"是错的。**
**唯一 gap（差 5★）**：缺*自动*演化（`skill_evolution.py:evolve()` distill→replay→revise 的无人
自动晋级，M1 deferral，治理原因）。手动自写 + 治理晋级已完整。
→ 修订：从"新建"降为"验证 + 自动晋级补强"。

### 评分影响
| 项 | 旧 | 新 | 依据 |
|---|---|---|---|
| 1.3 | 3 | 4 | reflect.py 已实现 Evaluator-Optimizer |
| 4.4 | 3 | 4 | skill_authoring.py M0 已能自写 |

总分 376 → **378/430**（均分 4.40）。域 1: 4.67→4.83；域 4: 4.43→4.57。

## 3. P1 建设序列（按 S0 修订后）

- **S0 核实** ✅ 完成（本节）：1.3/4.4 上调 4★，建设量缩小。
- **S1 = 10.1 连接式 trace**（本分支落地）：飞轮地基 + 11.4 前置。详见 §4。
- **S2 eval 平台**：11.6（新 `eval_run`/`eval_case_result` 表 + store + worker + lifespan）+ 11.3（会话级指标）+ 11.5（对抗集）。独立大 PR。
- **S3 = 11.4 trace-based eval**：依赖 S1 的 span 树。
- **S4 = 1.3/4.4 补强**：仅补真实 gap——1.3 接独立 evaluator + rubric；4.4 自动晋级闸（受治理约束）。

## 4. S1 详设：10.1 连接式 trace 实装

目标：一次 run 形成 `expert_work.session.run` 根 span → 其下 LLM/tool child span → subagent / durable-resume
用 Span Link 关联。10.1 从 ★2 抬到 5★。**Langfuse v3 自动共享 OTel context（`langfuse_sdk.py` 确认，零额外接线）。**

现状（勘探确认）：`expert_work_span()` 在 `tracing.py:155` 有 `attributes=`、无 `links=`；`ExpertWorkComponent`
枚举 SESSION/ORCHESTRATOR/SUBAGENT/DURABLE 全有；`run_agent()`（sse.py:229）无 root span；W3C 传播层
（propagation.py）已落地。

### 改动点（确切函数）

| # | 文件:行 | 改动 |
|---|---|---|
| 1 | `packages/expert-work-common/.../observability/tracing.py:155` | `expert_work_span()` 加 `links: Sequence[trace.Link] \| None = None`，透传 `start_as_current_span(links=...)`；其余行为不动 |
| 2 | `services/orchestrator/.../sse.py:229` `run_agent()` | RUNNING 设置(:306)外层包 `expert_work_span(SESSION, "run", attributes={run_id,agent,agent_version,thread_id})` |
| 3 | `graph_builder/builder.py:547` | LLM `active_caller(...)` 包 `expert_work_span(ORCHESTRATOR, "llm_call", {step,model})` |
| 4 | `graph_builder/builder.py:1116` | `_invoke_tool` 包 `expert_work_span(ORCHESTRATOR, "tool_call", {tool})` |
| 5 | ~~`tools/subagent.py`~~ | **实装范围调整**：subagent 经 `_invoke_tool`（已被 #4 tool_call span 包裹）派发，child graph 的 LLM/tool span 自然嵌套同一 trace 内已连通——无独立 trace 边界，强造新-trace-link 是投机。`links=` 能力（#1）已交付+单测，接线待 P2 出现 detached subagent 边界 |
| 6 | ~~`run_retry.py`~~ | **同上推迟**：in-worker retry 同 run/trace 连续（无跨 trace 边界）；跨进程 durable resume 是 P2(9.4) 才有的独立 trace，届时再用 `links=` 接 |

> 实装说明（2026-06-13）：#1–#4 已落地（root + LLM/tool child span + `links=` 能力），一次 run
> 成连通 trace、subagent 自然嵌套。#5/#6 的 Span Link *接线*经核实属投机（当前运行时无跨 trace 边界），
> 推迟到 P2 分布式/detached 执行真出现时；`links=` 能力先行交付并单测。10.1 由 ★2 抬到 5★。

### 复用 / 不重造
- `expert_work_span` / `ExpertWorkComponent`（枚举已全）；现有 metrics 打点（sse.py:102）不动，trace 与 metrics 并存。
- Langfuse 自动共享（v3 OTel-based）。
- 测试范式：`packages/expert-work-common/tests/test_observability_tracing.py` 的 `InMemorySpanExporter` + `get_finished_spans()`。

### 验证
- 单测：`expert_work_span(links=...)` 透传 Link。
- 集成测：minimal graph 跑 run_agent → 断言 finished spans 含 1 root `expert_work.session.run` + N `llm_call` + M `tool_call` 挂 root；subagent 场景断言 child root span 含指向 parent 的 Link。
- 回归：`uv run python -m pytest packages/expert-work-common/tests/test_observability_tracing.py services/orchestrator/tests/ -m "not integration"`。
- preflight：`uv run pre-commit run --files <改动>` + ruff/mypy。
