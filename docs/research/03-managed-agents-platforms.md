# 托管 Agent 平台架构调研

> 调研日期：2026-05
> 调研对象：Claude Managed Agents、OpenAI Assistants、AWS Bedrock AgentCore、Cloudflare Durable Objects、Vertex AI Agent Builder、Devin、Manus、Replit Agent、Vercel AI SDK、LangGraph Platform
> 输出：自研引擎必须实现的 15 大核心组件清单

---

## 1. Claude Managed Agents（最重要的对标）

**资料**：
- https://www.anthropic.com/engineering/managed-agents
- https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- https://docs.anthropic.com/en/api/agent-sdk

### 架构核心：Brain-Hands 解耦（"Brain vs. Hands"）

```
┌────────────────────┐
│  Brain (控制平面)   │  无状态 harness loop
│  - LLM 推理         │  调用 Claude，路由 tool calls
│  - Tool 路由        │  可水平扩展
└──────────┬─────────┘
           │
   ┌───────┴────────┐
   ▼                ▼
┌──────────────┐  ┌──────────────┐
│  Hands #1    │  │  Hands #2    │  ... 多 sandbox
│  (数据平面)   │  │              │  独立 sandbox 环境
│  执行操作     │  │              │  执行具体动作
└──────────────┘  └──────────────┘
        │                │
        └────────┬───────┘
                 ▼
        ┌─────────────────┐
        │ Session         │  append-only event log
        │ (状态平面)       │  所有发生事件的源头
        │ append-only log │  支持 checkpoint / 恢复 / replay
        └─────────────────┘
```

**性能收益**：TTFT p50 ↓60%、p95 ↓90%+

### 文件系统隔离
- gVisor 拦截 syscalls
- 自定义 PID 1 进程管理，6 个 Linux namespaces 激活
- 默认无网络工具，所有数据挂载对 agent 可访问
- **凭证代理在 sandbox 外部**，credentials 永不进入容器

### 子进程隔离
- filesystem/network 限制在 OS 层强制，适用所有 subprocess（kubectl、terraform、npm 等）
- seccomp 禁用（gVisor 已在更高层级拦截）
- root 权限运行（设计考量）

### 长运行 Session 恢复
- 基于事件日志的 session checkpoint
- "Dreaming"：后台定时任务，review session transcripts 提取 patterns
- Memory filesystem 挂载，agent 用 bash 操作完成记忆管理
- 自动内存去重、合并、提炼跨 session 模式

### 多 Agent 编排（新功能）
- Lead agent 分解任务给 specialist agents（各自独立 context、prompt、tools）
- 并行执行，共享文件系统
- Event persistence 保证每个 agent 记住已做的工作

### 定价模型
$0.08/运行小时 + 标准 Claude 模型费用（24/7 运行约 $58/月）

---

## 2. OpenAI Assistants API v2

### 状态管理
- **Threads**：持久化、自动管理的对话 session，自动截断超长上下文
- **Runs**：单次 execution run，支持 max_prompt_tokens / max_completion_tokens
- **Thread Locking**：Run 处于 in_progress 时 Thread 被锁定，防止并发修改

### 执行架构
- Code Interpreter 沙盒：gVisor 容器 + Jupyter kernel subprocesses
- 默认 1GB 内存，支持持久化容器复用（auto 模式）
- 上传/生成文件通过 annotations 返回

### 工具
Function calling 原生 + File Search + Code Interpreter

---

## 3. AWS Bedrock AgentCore（2025-10 GA）

### 多租户隔离（核心创新）
- **MicroVM 隔离**：每个 session 获得专用虚拟机
- **AgentCore Identity**：集中身份管理，支持 SigV4、OAuth2.0、API Keys
- **AgentCore Runtime**：完全隔离的执行环境

### 多租户模式
- **Silo Pattern**（最高隔离）：每租户独立栈部署
- **Pool Pattern**（成本优化）：租户共享 OCU 资源

### Session Lifecycle
- 默认 idle 超时 15 分钟（900s）
- StopRuntimeSession 即时终止
- Code Interpreter session 默认超时 15 分钟

---

## 4. Cloudflare Durable Objects + Agents

### 单对象模式
- 每个 Agent = 一个 DurableObject 实例
- 全局唯一 ID 跨域路由
- 单线程、协作式多任务（浏览器 execution model）
- 内置存储，无需外部 DB
- DurableObject 作为"supervisor"拦截所有请求

### 优势
持久化内存、跨 workflow 协调、实时交互、无基础设施管理

---

## 5. Google Vertex AI Agent Builder

### 完整栈架构
- **Build 层**：Agent Development Kit + Agent Studio（低代码）+ Agent Garden（模板库）
- **Runtime 层**：Agent Runtime（亚秒级冷启动）+ Agent Engine（完全托管）
- **State**：Sessions（单次交互）+ Memory Bank（跨 session 持久化）
- **Security**：Agent Identity（每个 agent 唯一身份）+ Agent Gateway（中央策略执行）
- **Data**：RAG Engine + Vector Search

---

## 6. Devin / Cognition Labs

**资料**：https://cognition.ai/blog

- 自动索引 repo 生成 architecture wiki（deepwiki.com）
- 与 engineering teams 集成（GitHub、Slack）
- 已 merged 数十万 PR（Goldman Sachs、Santander、Nubank）
- Devin 2.0：agent-native IDE 体验，并行规划执行

**缺陷**：内部架构细节公开较少，主要聚焦产品功能 demo

---

## 7. Lindy / Manus AI

### 自主 Agent 设计
- **Loop 结构**：Analyze → Plan → Execute → Observe（迭代）
- **多 Agent**：Planner / Execution / Verification sub-agents 在 cloud sandbox
- **CodeAct 模式**：用可执行 Python 代码作为 action（vs 传统 tool calling）
- **零预定义工作流**：全部委托给 foundation model（Claude 3.5 / Qwen）

---

## 8. LangChain LangGraph Platform（产品化版）

### State Persistence
- **Checkpoints**：每个 super-step 保存 graph state 快照
- **Threads**：每个 checkpoint 关联 thread_id
- **Super-steps**：单个"tick"，所有 scheduled nodes 并行执行
- **Failed Node Recovery**：失败节点的 sibling 已成功的 checkpoint 被保存，恢复时不重跑

### 存储后端
- MemorySaver（开发）
- PostgresSaver（生产）

---

## 9. Replit Agent 3

- Router layer 选择最优模型（GPT-5、Claude 3.5 Opus、Replit Code v3-33B）
- 基于 Nix 的确定性环境
- 24/7 运行支持（infrastructure upgrade 2025）
- 自测：自动在真实浏览器测试，200 分钟自主工作能力
- 安全：SOC 2 Type II（zero exceptions）、Bitsight Advanced（780 score）

---

## 10. Vercel AI SDK

### Agent 框架（非托管，是个 SDK）
- 统一 LLM 调用 API
- Tool 定义：描述 + Zod schema + execute 函数
- 自动 orchestration：append response → history，execute tool calls，循环到 text response 或 max steps
- AI Elements：20+ 生产级 React 组件

---

## 横向技术对标

### 隔离边界

| 平台 | 隔离级别 | 实现 |
|------|---------|------|
| Claude Managed Agents | 容器级 | gVisor syscall 拦截 |
| OpenAI Assistants | 容器级 | gVisor + Jupyter kernels |
| AWS Bedrock AgentCore | 虚拟机级 | MicroVM |
| Cloudflare DO | 进程级 | 全局单线程 |
| LangGraph | 逻辑隔离 | Thread-based state 分组 |

### 状态持久化

| 方案 | 平台 | 特点 |
|------|------|------|
| **事件溯源** | Claude MA、LangGraph | Append-only log，完整历史，time-travel |
| **快照+增量日志** | LangGraph、AWS Bedrock | 快照恢复，仅重放增量 |
| **自动 Thread 管理** | OpenAI Assistants | 内置截断，无显式持久化层 |
| **Durable Objects 内置** | Cloudflare | Object 本身即持久化 |

**共性**：所有托管方案都支持 "checkpoint recovery" — 失败后从最后稳定 state 恢复，无重复执行。

### 工具调用

| 方案 | 协议 |
|------|------|
| Claude Managed Agents | MCP（Model Context Protocol）|
| OpenAI Assistants | Function Calling |
| AWS Bedrock | Function Calling + Knowledge Bases |
| Vercel AI SDK | Tool definition + Zod |
| LangGraph | Tool nodes |

### 多租户

| 方案 | 方法 |
|------|------|
| Claude Managed Agents | API key + session ID 粒度（逻辑）|
| AWS Bedrock AgentCore | MicroVM per session（资源级）|
| Cloudflare DO | Per-tenant DurableObject（对象级）|
| OpenAI Assistants | Thread + Organization key（逻辑）|
| LangGraph Platform | Tenant context injection（应用层）|

**关键**：共享模型基础设施需应用层 metering（logging tenant IDs 并异步聚合），供应商发票无法按 tenant 拆分。

### 计费 / 限流

**Rate Limiting 模式**（关键发现）：
- ❌ Per-user limits → 单用户合规但租户总额失控
- ✅ "Family data plan"模式：每用户独立 queue，但汇总到 tenant+agent 共享配额

**成本控制**：
- Budgets：累积经济 exposure（$ 或 token）阈值
- Quotas：操作边界（req/h、并发 agent 数）
- 两者结合才能成本可预测

### 长运行与容错

**Durable Execution Pattern**（新兴标准）：
失败时外部持久化 execution state → 健康 VM 上自动 resume → 最后 checkpoint 后继续

**实现方案**：
- **Temporal**（业界标准）：event logging + replay
- **DBOS**：Postgres backed durability
- **Supervision Trees**：Erlang/OTP 模型

### 公开故障案例（SLA 教训）

**2025 关键 outages**：
1. AWS us-east-1（10/20）：DynamoDB DNS race → 15h downtime → Netflix/Slack/Snapchat/3500+ 公司
2. Cloudflare（11/18）：Bot Management 配置 oversized → 2h 全球
3. Google Cloud（6/12）：null pointer bug → 50+ services 7h

**SLA 限制**：
- 99.9% = 43 分钟/月
- 99.99% = 4.3 分钟/月
- SLA credit 仅是 monthly bill 小部分，且需 10%+ error rate threshold

**启示**：SLA 保障不足以保证运维心安，需配合 Durable Execution 达到真正 resilience。

---

## 自研引擎的核心组件清单（共 15 个，基于以上对标的共性）

### P0（关键路径）

1. **Session & Event Log Manager** — append-only event log + checkpoint 快照 = 完整 session replay 与故障恢复基础
   - 参考：Claude MA session、LangGraph checkpoints、OpenAI threads

2. **Agent Harness & Tool Router** — 无状态循环（LLM → tool routing → 结果），与 execution 环境解耦
   - 参考：Claude MA brain vs hands、Vercel AI orchestration loop

3. **Sandbox / Execution Environment** — gVisor 容器 + namespace 隔离，filesystem/network/process 限制
   - 参考：Claude MA / OpenAI gVisor、AWS MicroVM、Vercel sandbox-runtime

4. **Credential Proxy & Secrets Vault** — Agent 端无法直接访问 secrets，凭证在网关层注入
   - 参考：Claude MA credential proxy、Agent Vault、HashiCorp Vault patterns

5. **State Persistence Layer** — 多 backend、checkpoints + snapshots、enable HITL 与 time-travel
   - 参考：LangGraph PostgresSaver、Temporal event sourcing、DBOS Postgres-backed durability

### P1（功能完整性）

6. **Multi-Agent Orchestrator & Delegation** — Lead agent 分解任务 → parallel specialist execution → shared filesystem coordination
   - 参考：Claude MA multiagent、VeriMAP framework、VMAO Plan-Execute-Verify loop

7. **Rate Limiting & Cost Allocation Gateway** — Family data plan（per-user queues 汇总到 tenant 配额），budget+quota dual control
   - 参考：AWS Bedrock pooled agents、multi-tenant cost allocation patterns

8. **Long-Running Session Recovery & Durable Execution** — Automatic checkpoint + supervised process model
   - 参考：Temporal、DBOS、Supervision trees、Claude MA dreaming

9. **Observability & Distributed Tracing** — OpenTelemetry integration，追踪 LLM/tool/RAG/memory ops
   - 参考：AgentTrace、OpenTelemetry GenAI project、Jaeger/Zipkin

10. **Memory & Knowledge Management** — Persistent cross-session memory + dreaming（自动 pattern 提炼），filesystem mount
    - 参考：Claude MA dreaming、Vertex Agent Memory Bank、LangGraph thread-based state

### P2（运维 & 扩展）

11. **Lifecycle & Resource Cleanup Manager** — Session timeout、idle resource 释放、graceful shutdown
    - 参考：AWS AgentCore LifecycleConfiguration

12. **MCP Server Integration** — 标准化工具接口层，支持任意数据访问 + code execution sandbox
    - 参考：Claude Agent SDK MCP、Anthropic MCP spec

13. **Identity & RBAC for Agents** — Per-agent unique workload identity，支持 SigV4/OAuth/API keys
    - 参考：AWS AgentCore Identity、Vertex Agent Identity

14. **Failure Recovery & Supervision Framework** — Retry policies、exponential backoff、fallback strategies、agent restart
    - 参考：Erlang supervision trees、Temporal retry、LangGraph node recovery

15. **Workflow Versioning & Canary Deployment** — Prompt version control、model version pinning、gradual rollout、rollback
    - 参考：Temporal versioning、MLOps best practices

---

## 关键文献

### 官方 Engineering Blogs
- [Anthropic Engineering: Managed Agents](https://www.anthropic.com/engineering/managed-agents)
- [Anthropic Engineering: Effective Harnesses](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- [Anthropic Engineering: Claude Code Sandboxing](https://www.anthropic.com/engineering/claude-code-sandboxing)
- [Cognition Labs Blog](https://cognition.ai/blog)
- [Temporal Blog: Build Resilient Agentic AI](https://temporal.io/blog/build-resilient-agentic-ai-with-temporal)

### 学术论文
- [Dive into Claude Code: Design Space](https://arxiv.org/html/2604.14228v1)
- [VeriMAP: Verification-Aware Planning](https://arxiv.org/html/2510.17109v1)
- [VMAO: Verified Multi-Agent Orchestration](https://arxiv.org/html/2603.11445v1)
- [AgentTrace: Observability Framework](https://arxiv.org/html/2602.10133)

### 开源参考
- [Claude Agent SDK Python](https://github.com/anthropics/claude-agent-sdk-python)
- [Vercel AI SDK](https://github.com/vercel/ai)
- [LangGraph](https://github.com/langchain-ai/langgraph)
- [Infisical Agent Vault](https://github.com/Infisical/agent-vault) — credential proxy 开源参考
- [HashiCorp Vault AI Agent Identity](https://developer.hashicorp.com/validated-patterns/vault/ai-agent-identity-with-hashicorp-vault)
