# 编排引擎对比调研

> 调研日期：2026-05
> 调研范围：14 个开源 Agent 编排框架 / SuperAgent 平台
> 结论：选 **LangGraph** 作为核心编排引擎

---

## 候选项目对比矩阵

| 候选 | 编排模型 | Durable Exec | 多 Agent | 隔离 | UI | Stars | 许可 |
|------|----------|--------------|----------|------|-----|-------|------|
| **LangGraph** | Graph State Machine ✅✅ | Persistence API（PG/Redis） ✅ | Subgraph ✅✅ | ❌（需自己包）| ❌ | 24.8k | MIT |
| **Temporal** | Durable Workflow | ✅✅✅ 业界最成熟 | ✅ | Worker 进程级 | ❌ | 20k | 商业/源可用 |
| **Eino**（字节）| Component + ADK | Goroutine 长跑 | Supervisor/Plan-Execute | ⚠️ | ❌ | 7k | Apache 2.0 |
| **Mastra** | Workflow + Agent | 弱（靠 Inngest） | ✅ | ❌ | ❌ | 3k | MIT |
| **OpenAI Agents SDK** | Handoff | ❌ | ✅ | ❌ | ❌ | 19k | MIT |
| **OpenAI Swarm** | 教育版（已停止）| ❌ | ✅ | ❌ | ❌ | 5k | MIT |
| **CrewAI** | Role / Crew | ❌ | ✅✅ | ❌ | ❌ | 44.3k | Apache 2.0 |
| **AutoGen**（已维护模式） | 对话 | ❌ | ✅ | ❌ | ❌ | 30k | MIT |
| **Microsoft Agent Framework 1.0** | AutoGen + SK 融合 | ✅ | ✅✅ | ❌ | ❌ | - | MIT |
| **LlamaIndex Workflows** | 事件驱动 | ⚠️ | ✅ | ❌ | ❌ | ~10k | MIT |
| **Inngest AgentKit** | Networks + Router | ✅ | ✅ | ✅（serverless） | ❌ | 3k | MIT |
| **Restack** | 事件驱动 + K8s | ⚠️ | ✅ | K8s pod | ❌ | - | 商业 |
| **Dify**（对照基线） | DAG + UI | ❌ | ⚠️ | ❌ | ✅✅ | 111k | AGPL |
| **Flowise** | DAG + UI | ⚠️ | ✅ | ❌ | ✅ | 30k | MIT |
| **PydanticAI** | Agent-as-function | ❌ | ⚠️ | ❌ | ❌ | 3k | MIT |
| **DeerFlow**（字节）| 单 lead_agent + 14 middleware | LangGraph checkpoint | 串行 agent_name | Docker/K8s | ✅ | 45k | MIT |

---

## 详细分析

### 1. LangGraph（推荐）

**项目地址**：https://github.com/langchain-ai/langgraph

**架构特点**：
- **Graph State Machine**：State（共享数据） + Nodes（Python 函数） + Edges（条件转移）
- 三元组设计，明确控制流，每个节点持有局部 state，通过 edges 驱动转移
- 子图（Subgraph）支持层次化 Agent 组织
- Persistence API：Memory / Postgres / Redis 多后端
- HITL：interrupt/resume 原生支持
- Streaming：`graph.astream()` 自带 token-level 流

**优势**：
1. 编排灵活性最高（循环、条件、并行、HITL 都原生）
2. MIT 许可、API 稳定
3. 生态成熟（24.8k Stars、34.5M 月下载）
4. 不强依赖 LangChain（可独立用）

**劣势**：
1. 无内置 UI（需配套 LangSmith 或自建）
2. 学习曲线相对陡（Graph/State/Nodes 概念）
3. 中文社区资源较少

**对我们的契合度**：⭐⭐⭐⭐⭐
- Graph 模型可表达任意工作流（react / plan-execute / 复杂自定义）
- Persistence + Subgraph 直接对接 Brain-Hands-Session 范式
- MIT 让我们可以 vendor 必要部分

---

### 2. Temporal（M2 阶段考虑作为 durable 底座）

**项目地址**：https://temporal.io

**架构特点**：
- **Durable Workflow**：所有执行都能完成（故障透明重试，状态持久化）
- 自动重试、状态快照、无损恢复
- 与 AI 结合：用 Temporal 做编排层，LLM 做推理层

**优势**：
1. 业界最成熟的 durable execution（Codex / Replit 生产案例）
2. 故障恢复完美（自动重试、状态快照）
3. K8s native，分布式可扩展

**劣势**：
1. 学习曲线陡（Workflow / Activity / Signal 概念）
2. 过度设计（简单 Agent 场景过重）
3. 缺乏 Agent 抽象（自行包装）
4. 需独立部署 Temporal Server

**结论**：M2 阶段考虑作为底座（durable execution），与 LangGraph 组合使用。

---

### 3. Eino（字节跳动 Go 生态）

**项目地址**：https://github.com/cloudwego/eino

**架构特点**：
- Component-Based + Agent Development Kit (ADK)
- Agent 模式：ChatModelAgent (ReAct) / WorkflowAgents / Supervisor / Plan-Execute-Replan / DeepAgents
- Go 实现，性能强、并发好

**优势**：
1. 生产级稳定性（Doubao/TikTok 真实应用）
2. Go 生态（高性能、高并发）
3. 模块化程度高
4. 轻量级

**劣势**：
1. Go 社区相对小
2. 中文文档为主
3. 无可视化 UI
4. TypeScript 支持缺失

**结论**：如果团队是 Go 主力则强推，否则选 LangGraph。

---

### 4. Mastra（TypeScript 优先）

**项目地址**：https://github.com/mastra-ai/mastra

**架构特点**：
- Workflows（DAG）+ Agents（自主推理）+ RAG
- TypeScript-native，每个 API 都为 JS 开发者优化
- Zod schema 工具定义 + MCP 支持

**优势**：
1. 现代 TS 体验（声明式 + Zod）
2. MCP 友好
3. 与 Next.js/Vercel 生态契合
4. 社区增长快

**劣势**：
1. 社区规模小
2. durable execution 弱（依赖 Inngest）
3. Advanced scenarios 文档不足

**结论**：TS 全栈团队的优秀选择。

---

### 5. CrewAI（Role-Based 多 Agent）

**项目地址**：https://github.com/crewaiinc/crewai

**架构特点**：
- Crew（团队）+ Flow（流程）双层
- Agent 抽象：Role + Goal + Backstory + Memory
- 流程类型：Sequential / Hierarchical

**优势**：
1. 高层抽象直观（适合快速原型）
2. 与 LangChain 解耦
3. 社区活跃

**劣势**：
1. 状态管理原始（无显式 State）
2. 可控性差（高度自主难预测）
3. 无 durable execution
4. 流程灵活性有限

**结论**：可控性差，企业应用风险高。

---

### 6. Dify（对照基线 — 我们要替代的就是它）

**核心问题点**：
- 早期强耦合 LangChain → 升级 LangChain 时需大量适配
- 2025 年推 Beehive runtime 替代 LangChain → 部分缓解
- 但仍是产品形态而非引擎，深度定制后被升级反复打断
- DAG 模型对循环 / 条件 / 长时执行支持弱
- 沙盒：容器级隔离，多 Agent 在同一进程，状态污染
- 可视化编辑器是核心卖点（但我们不需要拖拽）

**总结**：版本维护痛点是它产品化路径的必然结果。要解这个痛点必须自建引擎。

---

### 7. DeerFlow（字节跳动 Deep Research）

**项目地址**：https://github.com/bytedance/deer-flow

**关键源码事实**（详见 [04-deerflow-source-analysis.md](./04-deerflow-source-analysis.md)）：
- **真实架构**：单一 `lead_agent` + 14 个 middleware 顺序拼装（不是多图编排）
- `langgraph.json` 只注册一个图：`"lead_agent": "deerflow.agents:make_lead_agent"`
- 多 Agent 表现是"通过 RunnableConfig 的 `agent_name` 参数串行切换 Agent 配置"，不是真正的多图并行
- SubagentLimitMiddleware 硬编码 `MAX_CONCURRENT_SUBAGENTS = 3`，clamp 到 [2, 4]
- 多租户：仅 user_id，**全无 tenant_id / org_id / workspace_id**

**结论**：不能整体作为基础库（重蹈 Dify 覆辙），但可 vendor 它的 SDK 子模块（event log、persistence、checkpointer/store、stream_bridge、5 个核心 middleware、subagent executor）。

---

## 对比矩阵（关键能力）

| 维度 | LangGraph | Temporal | Eino | Mastra | CrewAI | Dify | DeerFlow |
|------|-----------|----------|------|--------|--------|------|----------|
| 可视化编辑器 | ❌ | ❌ | ❌ | ❌ | ❌ | ✅✅ | ✅ |
| 多 Agent 协作 | ✅✅ | ✅ | ✅ | ✅ | ✅✅ | ⚠️ | ⚠️串行 |
| 人在回路 | ✅✅ | ✅✅ | ✅ | ✅ | ⚠️ | ✅ | ✅ |
| Durable Execution | ✅ | ✅✅✅ | ❌ | ⚠️ | ❌ | ❌ | ⚠️ |
| Agent 级隔离 | ⚠️ | ✅ | ⚠️ | ❌ | ❌ | ❌ | ✅ |
| MCP 支持 | ⚠️ | ❌ | ❌ | ✅ | ❌ | ✅ | ✅ |
| 可嵌入性 | ✅✅ | ⚠️ | ✅✅ | ✅ | ✅ | ⚠️ | ⚠️ |
| 学习曲线 | 陡 | 陡 | 中 | 低 | 低 | 低 | 中 |
| 多租户原生 | ❌ | ✅ | ❌ | ❌ | ❌ | ⚠️ | ❌ |

---

## 最终选型理由（LangGraph）

1. **核心竞争力**：
   - Graph State Machine 是业界最灵活的编排模型
   - Persistence API 完整（Memory/Postgres/Redis）
   - 原生人在回路（interrupt/resume）
   - MIT 许可完全开源

2. **可嵌入性最优**：
   - 无外部依赖，可独立部署
   - Python/TypeScript 双语言
   - API 设计清晰，易于二次开发

3. **生态成熟**：
   - 24.8k Stars、34.5M 月下载
   - 案例丰富、问题易解
   - Agent/Tool/Memory 等高层抽象健全

4. **解决三个痛点**：
   - ✅ 版本维护：独立维护、不依赖 LangChain 具体版本
   - ✅ 流程灵活性：Graph 设计天然支持复杂编排
   - ⚠️ Agent 隔离：需自行包装 Docker/K8s（这是我们 sandbox-supervisor 的职责）

---

## 不选其他的理由

| 候选 | 不选理由 |
|------|---------|
| Temporal | 过重，作为 durable 底座 M2 再考虑 |
| Eino | 团队不是 Go 主力 |
| Mastra | 团队不是 TS 主力 + durable 弱 |
| OpenAI Agents SDK | 强绑 OpenAI，违背"自研引擎"诉求 |
| CrewAI | 可控性差，企业应用风险 |
| AutoGen | 已进维护模式 |
| Microsoft Agent Framework | .NET 生态主导，Python 滞后 |
| LlamaIndex Workflows | 社区小、durable 弱 |
| Inngest AgentKit | 强绑 Inngest serverless |
| Restack | 闭源核心 |
| Dify | 端到端产品（要解决的就是它的痛点）|
| Flowise | 本质是 LangChain UI，问题转移 |
| PydanticAI | 单 Agent 设计，功能受限 |
| DeerFlow | 端到端 SuperAgent（详见 04 文档）|
