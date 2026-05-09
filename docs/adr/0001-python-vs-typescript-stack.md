# ADR-0001：Python vs TypeScript 全栈选型

- **状态**：✅ 已决策 — **采用纯 Python 方案**
- **日期**：2026-05-09
- **决策时间**：2026-05-09
- **决策依据**：LangGraph.js 持久化层多个未修 bug（直接威胁事件溯源核心）+ DeerFlow vendor 4000 行无法移植（重写 4-6 周）+ 国内 LangGraph.js 熟手稀缺。TS 真实优势（Bun 内存、tRPC schema 共享）对企业内部 Agent 引擎场景不关键，且 FastAPI + OpenAPI + openapi-typescript 可平替 schema 共享方案。
- **背景**：原方案选 Python（FastAPI + LangGraph + 自研 + vendor DeerFlow harness）；用户提出"是否改用 TS 全栈（LangChain.js + LangGraph.js + Node.js/Bun）"，需评估风险

---

## TL;DR（≤100 字）

**保持 Python 方案，不建议切换到纯 TS。**

主要风险：①LangGraph.js Postgres 持久化已知严重 bug（直接威胁我们的事件溯源核心）；②DeerFlow vendor 4000 行重写成本 4-6 周；③国内 LangGraph.js 熟手极少，招聘 + 培养周期长。如果公司未来要统一 TS 栈，**混合架构**（Python 控制平面 + TS API Gateway 与 React 前端共栈）是唯一值得考虑的折中方案，但收益可被 OpenAPI 中间层平替，并非必须。

---

## 1. 评估结论速查表

| 维度 | 纯 Python | 纯 TS | 混合栈 |
|------|----------|-------|--------|
| **LangGraph 持久化稳定性** | ✅ 生产可用（v0.2.64+） | ❌ 多个 PostgresSaver 故障未修 | ✅ Python 侧稳定 |
| **DeerFlow vendor 复用（4000 行）** | ✅ 直接 vendor | ❌ 全部失效，重写 4-6 周 | ✅ Python 侧可 vendor |
| **TS 端基础设施栈成熟度** | N/A | ✅ Fastify/Drizzle/BullMQ 都不输 Python | ✅ TS 网关层用 |
| **数据处理 / 本地 ML** | ✅ pandas/numpy/scikit-learn | ❌ 无完全等价物 | ✅ Python 侧 |
| **MCP 生态** | ✅ Python server 多 | ⚠️ TS SDK 等价但社区 server 少 | ✅ Python 直连 |
| **与 React UI schema 共享** | 走 OpenAPI 中间层（成熟） | ✅ tRPC 直连 | ✅ tRPC 直连（TS 网关）|
| **国内招聘人才池** | ✅ Python AI 工程师多 | ❌ LangGraph.js 熟手极少 | ⚠️ 需双栈 |
| **冷启动 / 内存（容器化）** | 中等（FastAPI ~250MB load）| ✅ Bun 比 Node ↓40% 内存 | 中等 |
| **M0 时间** | 基线 | **+4-6 周** | +2 周 |
| **运维复杂度** | 单运行时 | 单运行时 | **双运行时** |
| **长期维护风险** | 低 | 中（生态萎缩可能性）| 中（双栈耦合）|

---

## 2. 关键硬性风险（详）

### H1. LangGraph.js 持久化层不稳定（严重性 5/5，概率 4/5）

我们的核心架构依赖 **PostgresSaver（事件溯源 + checkpoint 恢复）**——这是 Brain-Hands-**Session** 三层范式中"Session"的根。LangGraph.js 当前公开未修的关键 issue：

- **Issue #6104**：PostgresSaver 完全不工作
- **Issue #5769**：JSON 序列化错误（AIMessage）
- **Issue #6125**：消息历史未保留
- **Issue #1692**：Cloudflare Workers 环境中 Postgres/Redis checkpointer 断裂
- **Issue #2142**：Subgraph 持久化状态未插入数据库

**影响**：事件日志和 checkpoint 是 M0 必交付的核心能力。如果 PostgresSaver 不可用：
- 选项 A：用 MemorySaver 过渡 → 进程重启丢状态，违反 durable execution
- 选项 B：自建 PostgresSaver 替代 → 又回到"自写 4000 行基础设施"问题
- 选项 C：等社区修 → 不可控

**Python 侧对比**：LangGraph Python 0.2.64+ 的 `langgraph-checkpoint-postgres` 是生产可用的，且已被多家公司采纳（Klarna、Elastic 等）。

---

### H2. DeerFlow vendor 失效，4000 行重写（严重性 4/5，概率 5/5 — 确定）

原方案的 vendor 计划（详见 [research/04-deerflow-source-analysis.md](../research/04-deerflow-source-analysis.md)）：
- 🔴 P0：~2500 行（event_log + persistence + checkpointer/store 工厂 + stream_bridge + run manager）
- 🟠 P1：~1500 行（5 个核心 middleware + subagent executor + guardrails + mcp client）

**TS 化后全部失效**（DeerFlow 是 Python 写的）。

TS 生态中可替代的开源项目：
| 项目 | 是否 vendor 友好 | 提供能力 |
|------|---------------|---------|
| **LangGraph.js Platform** | ❌ 闭源产品 | 不开放基础设施 |
| **Mastra** | ⚠️ 部分 | 有 workflow，无 event_log/persistence |
| **Vercel AI SDK** | ❌ | Agent loop，无持久化 |
| **Inngest** | ⚠️ 托管为主 | Durable execution 但厂商绑定 |
| **DBOS** | ✅ 开源 | Postgres-backed durability + workflow（**唯一值得评估的替代**）|

**重写成本预估**：
- event_log：100-150 行（Drizzle + SQL）
- persistence factory：200-300 行
- stream_bridge：150-200 行
- 5 middleware：400-600 行
- subagent_executor：300-500 行
- 持久化层兼容 LangGraph.js 的 bugs：额外 200-400 行 workaround

**总计**：~1300-2150 行 TS + 4-6 周工时 + 测试时间。

---

### H3. 国内 LangGraph.js 工程师人才池稀缺（严重性 3/5，概率 4/5）

**国内市场现实**：
- AI 工程师生态 = Python（PyTorch、TF、HF、LangGraph 教程几乎全 Python）
- TS 后端 = Web/Node 全栈，大多数没碰过 LangGraph
- 既懂 LangGraph.js 又懂多租户 backend 的人 = 稀缺

**影响**：
- 招 1 个直接上手的人需要 2-3 个月
- 培养（高级全栈 → LangGraph.js 熟练）需要 3-4 周 + 生产案例打磨
- 团队前 2 个月产能 -30%

**缓解**：可以接受培养周期，但要在 M0 时间预算中体现

---

### H4. 数据处理与本地 ML 缺失（严重性 2-4/5，**取决于业务 roadmap**）

如果未来 Agent 涉及：
- 本地 embedding（sentence-transformers 风格）
- ranking / re-ranking 模型
- 数据科学管道（pandas/numpy/scikit-learn）
- 自定义 fine-tuning / inference

→ **TS 完全无法自主**，必须调用 Python 微服务。

如果完全不涉及（只是 LLM 调用 + tool use + RAG with managed vector DB），TS 影响较小。

**对多业务线平台**：业务范围跨医疗/HR/客服/研发等，至少其中医疗租户可能涉及临床数据 ETL、规则引擎、特征工程——多业务线意味着至少有一条业务线需要 Python ML 生态，不能为了 TS 把它砍掉。

---

### H5. 混合栈（Python + TS）的隐藏成本（严重性 3/5，概率 4/5 — 如果选混合）

混合栈听起来美好，但真实成本：
- **跨语言 schema 同步**：tRPC 是 TS-only，无法跨 Python/TS。混合栈实际上要走 **OpenAPI / JSON Schema** 中间层——和"纯 Python + 自动生成 OpenAPI"路径**完全相同**
- **跨服务通信**：Fastify ↔ Python LangGraph 走 HTTP/gRPC，每次调用 +5-20ms 延迟
- **双运行时部署**：Node + Python 两套镜像、两套基础镜像、两套 GC 调优、两套监控
- **双团队招聘**：需要 Python AI 工程师 + TS 后端工程师两类人
- **类型断点**：schema 在中间层手动维护，drift 风险高

**残酷事实**：混合栈的"React 共享 schema"收益，纯 Python + FastAPI 自动 OpenAPI export + `openapi-typescript` 生成 TS 类型 ≈ 完全等价。

---

## 3. TS 真实优势（不要忽视）

公允起见，TS 方案有几个真实优势：

| 优势 | 价值 | 何时关键 |
|------|------|---------|
| Bun 内存 -40%、冷启动 1.75x 快 | 容器密度高、serverless 成本低 | 单机 200+ Agent 时显著 |
| 端到端类型系统（Zod / TypeBox） | 减少 schema 漂移 bug | 大团队多人协作 |
| 与 React 19 共栈 | 前端 + 后端工程师可互转 | 团队规模 < 10 人 |
| Edge / Cloudflare Workers 部署 | 全球低延迟 | 跨国业务 |
| Fastify / Hono 性能更高 | 高 QPS 网关 | 公开 API 大流量 |

但是——**这些优势对我们企业内部 Agent 引擎场景大多不关键**。我们不是公开 SaaS（没有跨国低延迟需求），单机 200+ Agent 是 M3 才考虑的规模，团队当前是 3 人（schema 漂移问题不大）。

---

## 4. 决策推荐

### 主推：纯 Python（保留原方案）

适用条件：
- 公司未强制 TS 栈
- 团队当前 ≤5 人，可专注一种语言
- 业务可能涉及数据处理 / 本地 ML
- 想最快交付（M0 = 4-6 周）

落地：
- 100% 按 [docs/architecture/](../architecture/) 执行
- React UI 通过 FastAPI 自动生成的 OpenAPI + `openapi-typescript` 共享类型
- 不需要任何架构调整

### 次推：分阶段混合栈（M2+ 引入）

适用条件：
- 公司未来明确要统一 TS 栈
- 当前已有大量 TS 业务系统
- M0/M1 可接受先纯 Python

落地：
- M0/M1：纯 Python（按原方案）
- M2：在 Python 控制平面前面加一层 TS Fastify API Gateway，专门服务 React UI
- M2：用 OpenAPI（不是 tRPC，因为 Python 不支持 tRPC）+ generated TS client 共享 schema
- 优势：享受 Python 稳定性 + TS 前端集成，零 vendor 重写

### 不推荐：纯 TS（除非非走不可）

仅在以下条件全部满足时考虑：
- 公司是 100% TS 全栈
- 已有 1-2 名能驾驭 LangGraph.js 的高级 Node 工程师
- 业务确定不涉及本地 ML / 数据处理
- 可接受 M0 +4-6 周延期
- 可接受 PostgresSaver bugs 自修风险

落地：
- 编排：LangGraph.js v0.3+（**慎选 PostgresSaver，建议先用 MemorySaver + Redis**）
- 后端：Fastify 或 Hono（如果上 Cloudflare Workers）
- ORM：Drizzle
- Queue：BullMQ + Redis
- Vendor 替代：评估 DBOS 或自写 4000 行 TS 基础设施
- Runtime：Bun（内存 / 冷启动优势）
- Sandbox：E2B SDK（TS 客户端）
- **必做**：Phase 0 用 1-2 周复现 LangGraph.js issues 并决定 PostgresSaver 替代方案

---

## 5. 关键参考资料

- [LangGraph.js Issue #6104 — PostgresSaver 完全不工作](https://github.com/langchain-ai/langgraph/issues/6104)
- [LangGraph.js Issue #5769 — AIMessage JSON 序列化错误](https://github.com/langchain-ai/langgraph/issues/5769)
- [LangGraph.js Issue #6125 — 消息历史未保留](https://github.com/langchain-ai/langgraph/issues/6125)
- [LangGraph.js Issue #1692 — Cloudflare Workers checkpointer 断裂](https://github.com/langchain-ai/langgraphjs/issues/1692)
- [LangGraph.js Issue #2142 — Subgraph 持久化状态丢失](https://github.com/langchain-ai/langgraph/issues/2142)
- [Anthropic SDK TypeScript Issue #553 — Batch + Caching 限制](https://github.com/anthropics/anthropic-sdk-typescript/issues/553)
- [DBOS Documentation — Durable Execution for AI Agents](https://www.dbos.dev/blog/durable-execution-crashproof-ai-agents)
- [BullMQ 5.0 Performance Benchmark](https://johal.in/opinion-bullmq-50-is-best-background-job-library-opinion/)
- [Bun vs Node.js Memory Benchmark 2026](https://strapi.io/blog/bun-vs-nodejs-performance-comparison-guide)
- [Mastra Framework Observational Memory](https://mastra.ai/blog/changelog-2026-02-26)

---

## 6. 待用户决策

请回答以下问题决定方向：

1. **公司当前其他业务系统主栈**？（决定混合栈是否有协作价值）
   - [ ] 主要是 Python / Java
   - [ ] 主要是 TS / Node
   - [ ] 混合，没强制
2. **是否计划未来 1-2 年统一 TS 栈**？
   - [ ] 是 → 考虑分阶段混合
   - [ ] 否 → 直接 Python
3. **业务路线是否涉及本地 ML / 数据处理**（embedding、ranking、医疗数据 ETL）？
   - [ ] 是 → Python 不可替代
   - [ ] 否 → 两种栈都行
4. **可接受的 M0 时间**？
   - [ ] 4-6 周（Python 基线）
   - [ ] 8-12 周（TS 选项含重写 vendor）
5. **团队是否已有 LangGraph.js 熟手**？
   - [ ] 有 ≥1 人 → TS 选项可考虑
   - [ ] 没有 → 强烈建议 Python
