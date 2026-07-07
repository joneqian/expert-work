# 08 — Agent 能力评估

> 在做 canonical 能力 agent 之前,对 expert-work 当前真实 agent 能力做的一次全面评估。
> 配套:[07-INFRASTRUCTURE-GAPS](./07-INFRASTRUCTURE-GAPS.md)(基础设施缺口)、[ITERATION-PLAN § Stream J](../ITERATION-PLAN.md)。

## 1. 评估方法

- **框架**:5 层 26 维能力模型(下表)。框架经两次复盘扩充 —— 初版 21 维,补 model 路由 / 产物 / 调度触发 / 学习闭环 → 25 维,再补"有状态执行环境"→ 26 维。
- **对标**:两个成熟开源 agent 项目 —— deer-flow、hermes-agent —— 作能力基线。**对标是为校准"成熟长什么样"+ 找差距,结论是独立分析,非照抄。**
- **取证**:只评 `main` 上**实际落地的源码**;设计文档 / ITERATION-PLAN 里的计划不算能力。
- **成熟度**:生产级(已实现 + 接入 live agent 路径 + 有测试)/ 骨架(部分结构或桩)/ 缺失(无此概念)。

## 2. 能力矩阵

| # | 维度 | expert-work | deer-flow | hermes |
|---|------|-------------|-----------|--------|
| L1.1 | 推理循环 / 迭代控制 | 生产级 | 生产级 | 生产级 |
| L1.2 | 规划 / 任务分解 | **缺失** | 生产级 | 生产级 |
| L1.3 | 反思 / 自我修正 | **骨架** | 骨架 | 生产级 |
| L1.4 | Sub-agent / 多智能体 | **缺失** | 生产级 | 生产级 |
| L1.5 | Model 路由 / 按任务&模态选型 | **骨架** | 骨架 | 生产级 |
| L2.6 | 上下文管理 | 生产级 | 生产级 | 生产级 |
| L2.7 | 记忆 | 短期生产级 / **长期缺失** | 生产级 | 生产级 |
| L2.8 | 知识 / 检索 / RAG | **缺失** | 骨架 | 骨架 |
| L2.9 | 多模态输入 | **骨架** | 生产级 | 生产级 |
| L3.10 | 工具 | 生产级 | 生产级 | 生产级 |
| L3.11 | MCP | 生产级 | 生产级 | 生产级 |
| L3.12 | 代码执行 / 沙盒(临时无状态) | 生产级 | 生产级 | 生产级 |
| L3.13 | Skill + skill 进化 | **缺失** | 生产级 | 生产级 |
| L3.14 | 产物 / Artifact 管理 | **缺失** | 骨架 | 骨架 |
| L3.15 | 有状态 per-user 执行环境 / 持久工作区 | **缺失** | 缺失 | 骨架 |
| L4.16 | 流式输出 | 生产级 | 生产级 | 生产级 |
| L4.17 | 人在回路 / 审批 | **缺失** | 骨架 | 生产级 |
| L4.18 | 取消 / 超时 / 生命周期 | 生产级(强) | 生产级 | 骨架 |
| L4.19 | 调度 / 触发(非请求-响应) | **缺失** | 缺失 | 生产级 |
| L5.20 | 持久化执行 / 崩溃恢复 | 生产级(强) | 生产级 | 骨架 |
| L5.21 | 弹性 / 错误恢复 | 生产级 | 生产级 | 生产级 |
| L5.22 | 成本 / token 治理 | 生产级 | 生产级 | 生产级 |
| L5.23 | 安全护栏 / guardrails(含多租户隔离) | 生产级 | 生产级 | 生产级 |
| L5.24 | 可观测 / 推理可追溯 | 生产级(强) | 生产级 | 骨架 |
| L5.25 | 质量度量 / eval | 骨架 | 骨架 | 骨架 |
| L5.26 | 学习 / 反馈闭环 | **缺失** | 骨架 | 骨架 |

Expert Work 计:**生产级 13 / 骨架 4 / 缺失 9**。

## 3. 分析

### 3.1 Expert Work 是优秀的 agent *执行底座*,还不是有认知能力的 *agent 平台*

13 个生产级维度几乎全在 **L3 行动 / L4 控制 / L5 可靠** —— 即 Stream A–I 建的企业基础设施。缺口集中在 **L1 推理 + L2 知识 + skill / 产物 / 有状态环境 / 人在回路 / 调度 / 学习** 这些"认知 / harness"维度。

Expert Work M0 是一个有意的下注:基础设施先行,认知能力推后。这个下注执行得很扎实,但也意味着"通用 agent 平台"目前只兑现了一半 —— 通用平台级的*基础设施*,极简的*agent 认知能力*。

### 3.2 Expert Work 与参考项目是互补画像,不是同类

deer-flow / hermes 恰好相反:认知层强(规划、长期记忆、sub-agent、skill 进化都生产级),但企业基础设施弱 —— 单用户 / SaaS-lite,多租户隔离基本没有,hermes 连回合内 checkpoint、结构化可观测都没有。

Expert Work 在以下维度**反超**两个参考项目,是企业级 agent 平台的硬通货:

- **持久化执行**:LangGraph checkpointer + 崩溃恢复时的悬挂 `tool_call` 修复(`runner.sanitize_thread`)。hermes 无回合内 checkpoint。
- **协作式取消链**:`CancellationToken` 穿透 LLM / tool / sandbox,端到端协作式取消。
- **多租户隔离**:Postgres RLS + reservation-based quota 引擎。参考项目基本是单用户。
- **可观测**:Langfuse + 结构化 audit log + 自托管 OTel/Prometheus/Grafana 栈。
- **沙盒**:gVisor + 沙盒审计中间件 —— 三者中隔离最强。

### 3.3 真正的 14 个缺口

| 缺口 | Expert Work 现状 | 参考项目怎么做(对标基线) |
|------|-----------|--------------------------|
| **规划 / 任务分解** | 缺失 —— 纯单步 ReAct,无 planner、无 todo | deer-flow `TodoMiddleware`;hermes `todo` + Kanban |
| **反思 / 自我修正** | 骨架 —— 仅 `loop_detection` 病态退化保护 | hermes 后台 review loop(daemon 自评) |
| **Sub-agent / 多智能体** | 缺失 —— 单体 agent | deer-flow `subagents/executor.py`;hermes `delegate_tool` |
| **Model 路由** | 骨架 —— 有 fallback,但 manifest 一 agent 锁一模型 | hermes `image_routing.py`(vision 路由) |
| **长期记忆** | 缺失 —— 仅单 run 的 checkpointer | deer-flow 跨会话结构化记忆;hermes 持久 `MEMORY.md` |
| **知识 / 检索(RAG)** | 缺失 —— 无向量库/检索 | 三者皆弱(靠外部 search / FTS5)。非 table-stakes |
| **多模态输入** | 骨架 —— 留了消息槽,无 handler | deer-flow `view_image`;hermes 全 vision 路由 |
| **Skill + skill 进化** | 缺失 —— 无 skill 概念 | deer-flow skill installer + 进化;hermes 自主创建 loop |
| **产物 / Artifact 管理** | 缺失 —— run 只吐 SSE 文本流,sandbox 文件随沙盒销毁即丢 | deer-flow `present_file`;hermes `file_tools` |
| **有状态 per-user 执行环境** | 缺失 —— 沙盒 per-run 临时无状态(Mini-ADR F-2);无持久工作区、无沙盒会话生命周期 | hermes Daytona/Modal 持久后端;deer-flow 亦无 |
| **租户内 per-user 隔离** | 缺失为一等维度 —— 隔离做到 per-租户(RLS);租户内 per-user 靠 thread/session 授权,`user_id` 非一等隔离 scope key | 参考项目是单用户工具,无此语境 |
| **人在回路 / 审批** | 缺失 —— 运行中无法被人审批/纠偏 | hermes 中断 + 审批门;deer-flow `ask_clarification` |
| **调度 / 触发(非请求-响应)** | 缺失 —— 纯 `POST /runs` 同步驱动 | hermes 完整 cron 系统 |
| **学习 / 反馈闭环** | 缺失 —— G.6 采集了 👍/👎,但无迭代闭环 | hermes trajectory→dataset |

(eval = 骨架:Expert Work 有 `tools/eval` G.4 离线 harness + G.5 golden/regression 集,三者中最有意的 eval 故事,M0 为骨架级 —— 它是 canonical agent 的度量工具,见 Stream J.13。)

## 4. 目标产品形态:per-user 持久 agent

平台要支撑的目标产品形态(2026-05-18 与用户确认):

> **租户 = 公司,用户 = 公司的员工 / 客户。每个用户拥有自己的、持久的 agent 实例 —— 对话状态 + 长期记忆 + 持久工作区(各自的文件与中间产物)。用户静默一段时间自动释放算力,新消息进来快速还原。canonical 能力 agent 即此产品形态本身,不是另起的验证 agent。**

Expert Work 现有架构本身契合"无状态计算 + 持久状态"模型(checkpointer 即 per-user 实例的持久形态,空闲零算力成本)。但这个产品形态需要两个 Expert Work 当前没有的平台能力:

1. **租户内 per-user 隔离**(L5.23 的细化)—— `(tenant_id, user_id)` 复合 scope:thread 归属、长期记忆、工作区都按用户隔离。→ **Stream J.14**。
2. **有状态 per-user 执行环境**(L3.15)—— 推荐架构 **临时算力 + 持久卷**:每用户一个持久卷(工作区 / 产物);沙盒容器仍临时、启动挂载该用户的卷;生命周期 = 活沙盒复用 / 空闲 TTL 停容器(卷保留)/ 新消息起容器 + 挂卷还原。不走 container 快照(CRIU 复杂脆弱)。→ **Stream J.15**。

> 注:M1-A 的 sandbox warm pool 是预热的*空*沙盒、只为加速冷启动,**不是** per-user *有状态*沙盒 —— 两回事。

**Stream J 的验收锚定**:能端到端支撑这个 per-user 持久 agent 跑通。

## 5. 结论与决策

**结论**:Expert Work M0 把企业基础设施做到了生产级、部分领先参考项目;但 agent 认知 / harness 层有 **14 个缺口**,尚不是一个能支撑"per-user 持久 agent"产品形态的 harness 能力完整平台。

**决策**:先把 Expert Work 建成 harness 能力完整的 agent 平台 —— 把 14 个缺口补到生产级 —— 再做 canonical / 产品 agent。落地为 **[ITERATION-PLAN](../ITERATION-PLAN.md) Stream J — Agent Harness 能力补全**(J.1–J.15,设计先行见 `docs/streams/STREAM-J-DESIGN.md`)。

这是一个量级与 M0 若干 Stream 总和相当的大里程碑,是建成"通用 agent 平台"的必经投入。
