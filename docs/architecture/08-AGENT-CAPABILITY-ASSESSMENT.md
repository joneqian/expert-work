# 08 — Agent 能力评估

> 在做 canonical 能力 agent(能力验证 + dogfood 载体)之前,对 helix-agent 当前真实 agent 能力做的一次全面评估。
> 配套:[07-INFRASTRUCTURE-GAPS](./07-INFRASTRUCTURE-GAPS.md)(基础设施缺口)、[ITERATION-PLAN § Stream J](../ITERATION-PLAN.md)。

## 1. 评估方法

- **框架**:5 层 21 维能力模型(下表)。
- **对标**:两个成熟开源 agent 项目 —— deer-flow、hermes-agent —— 作能力基线。**对标是为校准"成熟长什么样"+ 找差距,结论是独立分析,非照抄。**
- **取证**:只评 `main` 上**实际落地的源码**;设计文档 / ITERATION-PLAN 里的计划不算能力。
- **成熟度**:生产级(已实现 + 接入 live agent 路径 + 有测试)/ 骨架(部分结构或桩)/ 缺失(无此概念)。

## 2. 能力矩阵

| # | 维度 | helix-agent | deer-flow | hermes |
|---|------|-------------|-----------|--------|
| L1.1 | 推理循环 / 迭代控制 | 生产级 | 生产级 | 生产级 |
| L1.2 | 规划 / 任务分解 | **缺失** | 生产级 | 生产级 |
| L1.3 | 反思 / 自我修正 | **骨架** | 骨架 | 生产级 |
| L1.4 | Sub-agent / 多智能体 | **缺失** | 生产级 | 生产级 |
| L2.5 | 上下文管理 | 生产级 | 生产级 | 生产级 |
| L2.6 | 记忆 | 短期生产级 / **长期缺失** | 生产级 | 生产级 |
| L2.7 | 知识 / 检索 / RAG | **缺失** | 骨架 | 骨架 |
| L2.8 | 多模态输入 | **骨架** | 生产级 | 生产级 |
| L3.9 | 工具 | 生产级 | 生产级 | 生产级 |
| L3.10 | MCP | 生产级 | 生产级 | 生产级 |
| L3.11 | 代码执行 / 沙盒 | 生产级 | 生产级 | 生产级 |
| L3.12 | Skill + skill 进化 | **缺失** | 生产级 | 生产级 |
| L4.13 | 流式输出 | 生产级 | 生产级 | 生产级 |
| L4.14 | 人在回路 / 审批 | **缺失** | 骨架 | 生产级 |
| L4.15 | 取消 / 超时 / 生命周期 | 生产级(强) | 生产级 | 骨架 |
| L5.16 | 持久化执行 / 崩溃恢复 | 生产级(强) | 生产级 | 骨架 |
| L5.17 | 弹性 / 错误恢复 | 生产级 | 生产级 | 生产级 |
| L5.18 | 成本 / token 治理 | 生产级 | 生产级 | 生产级 |
| L5.19 | 安全护栏 / guardrails | 生产级 | 生产级 | 生产级 |
| L5.20 | 可观测 / 推理可追溯 | 生产级(强) | 生产级 | 骨架 |
| L5.21 | 质量度量 / eval | 骨架 | 骨架 | 骨架 |

helix 计:**生产级 13 / 骨架 3 / 缺失 5**。

## 3. 分析

### 3.1 helix 是优秀的 agent *执行底座*,还不是有认知能力的 *agent 平台*

13 个生产级维度几乎全在 **L3 行动 / L4 控制 / L5 可靠** —— 即 Stream A–I 建的企业基础设施。5 个缺失 + 反思/多模态骨架,**全部集中在 L1 推理 + L2 知识 + skill + 人在回路**,即 agent 的"认知层"。

helix M0 是一个有意的下注:基础设施先行,认知能力推后。这个下注执行得很扎实,但也意味着"通用 agent 平台"目前只兑现了一半 —— 通用平台级的*基础设施*,极简的*agent 认知能力*。

### 3.2 helix 与参考项目是互补画像,不是同类

deer-flow / hermes 恰好相反:认知层强(规划、长期记忆、sub-agent、skill 进化都生产级),但企业基础设施弱 —— 单用户 / SaaS-lite,多租户隔离基本没有,hermes 连回合内 checkpoint、结构化可观测都没有。

helix 在以下维度**反超**两个参考项目,是企业级 agent 平台的硬通货:

- **持久化执行**:LangGraph checkpointer + 崩溃恢复时的悬挂 `tool_call` 修复(`runner.sanitize_thread`)。hermes 无回合内 checkpoint。
- **协作式取消链**:`CancellationToken` 穿透 LLM / tool / sandbox,端到端协作式取消。hermes 仅粗粒度超时。
- **多租户隔离**:Postgres RLS + reservation-based quota 引擎。参考项目基本是单用户。
- **可观测**:Langfuse + 结构化 audit log + 自托管 OTel/Prometheus/Grafana 栈。hermes 仅 logging。
- **沙盒**:gVisor + 沙盒审计中间件 —— 三者中隔离最强。

### 3.3 真正的 8 个缺口

| 缺口 | helix 现状 | 参考项目怎么做(对标基线) |
|------|-----------|--------------------------|
| **规划 / 任务分解** | 缺失 —— 纯单步 ReAct,无 planner、无 todo | deer-flow `TodoMiddleware` + `write_todos` 工具、plan-mode 门控;hermes `todo` 工具 + Kanban(支持依赖链) |
| **反思 / 自我修正** | 骨架 —— 仅 `loop_detection` 病态退化保护 | hermes 后台 review loop(daemon 线程回合后自评、更新记忆/skill);deer-flow 隐式(LLM 看历史) |
| **长期记忆** | 缺失 —— 仅 LangGraph checkpointer(单 run 执行态) | deer-flow 跨会话结构化记忆(file/SQL,workContext/facts…);hermes 持久 `MEMORY.md`/`USER.md` |
| **Sub-agent / 多智能体** | 缺失 —— 单体 agent | deer-flow `subagents/executor.py`(隔离事件循环 + 超时 + token);hermes `delegate_tool`(隔离子 agent + 工具白名单) |
| **知识 / 检索(RAG)** | 缺失 —— 无向量库/检索;web_search 是工具非 grounding | 三者皆弱 —— deer-flow/hermes 也无向量库,靠外部 search 工具 / FTS5。**非 table-stakes,但需明确替代方案** |
| **多模态输入** | 骨架 —— 消息结构留了多模态槽,无 handler | deer-flow `view_image` 工具 + 中间件自动注入;hermes 全 vision 路由(图像/PDF/截屏) |
| **Skill + skill 进化** | 缺失 —— 无 skill 概念 | deer-flow skill installer + 进化配置(agent 自创建);hermes 自主 skill 创建 loop(`SKILL.md` + 渐进披露) |
| **人在回路 / 审批** | 缺失 —— 运行中无法被人审批/纠偏 | hermes 中断标志 + 审批门(once/always/deny);deer-flow `ask_clarification` 工具 |

### 3.4 对 canonical agent 的直接含义

若今天就建 canonical 能力 agent,它本质只能是"一个会调工具的 ReAct agent" —— 能充分验证 L3/L4/L5,但 **L1/L2 认知层几乎无可验证之物**。canonical agent 是能力评估的载体;平台能力不完整,canonical agent 就评不出完整能力面。

## 4. 结论与决策

**结论**:helix M0 把企业基础设施做到了生产级、部分领先参考项目;但 agent 认知/harness 层有 8 个缺口,尚不是一个 harness 能力完整的通用 agent 平台。

**决策**:先把 helix 建成一个 **harness 能力完整**的 agent 平台 —— 把 8 个缺口补到生产级 —— 再做 canonical 能力 agent 与 dogfood。落地为 **[ITERATION-PLAN](../ITERATION-PLAN.md) Stream J — Agent Harness 能力补全**(8 子项,设计先行见 `docs/streams/STREAM-J-DESIGN.md`)。

这是一个量级与 M0 若干 Stream 总和相当的大里程碑,是建成"通用 agent 平台"的必经投入。
