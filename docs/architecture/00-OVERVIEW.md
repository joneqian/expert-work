# Helix — 自研企业级 Agent 工作引擎架构方案

> **项目代号**：Helix（暂定，后续可改）
> **目标**：取代 Dify，对标 Anthropic Claude Managed Agents
> **场景**：**业务无关的多业务线 Agent 引擎**——医疗 / HR / 客服 / 研发等多个业务共享一套引擎，半可信代码 + 多租户强隔离；**领域知识、合规要求（HIPAA/GDPR/SOX）、PII 字段全部为可插拔的租户级模块**，引擎本体不绑定任何业务语义
> **文档状态**：已完成深度调研，待用户决策启动

---

## 1. Context — 为什么做这件事

公司当前用 Dify 作为 Agent 工作引擎，业务发展中暴露三个核心痛点：

1. **版本维护困难**：Dify 是端到端产品而非引擎，深度定制后升级几乎不可能
2. **能力受限**：DAG 模型对循环、条件分支、多 Agent 协作、长时执行支持不足
3. **沙盒隔离缺失**：所有 Agent 共享同一进程/容器，状态/资源/副作用互相污染

**目标**：从 0 自研一个**业务无关的通用 Agent 工作引擎**，最大化复用成熟开源积木（不重蹈 Dify 那种"端到端产品"覆辙），核心范式对标 Claude Managed Agents 的 **Brain-Hands 解耦 + 事件溯源 + 沙盒隔离**。

**多业务线特性**（关键设计原则）：
- 引擎本体不预设业务领域（不内嵌医疗/客服/HR 等业务概念）
- 通过 `tenant_config.compliance_pack` 字段租户级声明合规级别（hipaa/gdpr/sox/null），引擎自动启用对应中间件
- 通过 `templates/` 领域模板包提供医疗/客服/研发等场景的 prompt+tools+guardrails 预设，业务团队按需 `extends` 引用
- 隔离强度可配置（`isolation_level: shared / dedicated_sandbox / dedicated_node`）以满足不同合规等级

---

## 2. 已确认的关键决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 后端语言 | **Python** | 跟随最成熟开源项目（LangGraph 24.8k⭐、Claude Agent SDK 也是 Python/TS）|
| 代码信任度 | **半可信**（内部业务 Agent + 租户隔离）| 不需要执行用户上传脚本/LLM 任意代码，OpenAI/Claude 同款场景 |
| 沙盒方案 | **Docker + gVisor (runsc)** 起步，K8s 阶段升级 Kata | OCI 兼容、半可信场景业界标准、单机 50-150 实例 |
| 部署目标 | **Docker 单机起步**，未来扩 K8s | 减少初期运维成本，关键边界用 gRPC/HTTP 抽象，平滑迁移 |
| 编排核心 | **LangGraph (Python)** | Graph State Machine 最灵活、生态最成熟、MIT、Persistence/Subgraph/Streaming/HITL 都原生 |
| 配置形态 | **声明式 YAML manifest（80%）+ Python 插槽包（20%）** | 类比 K8s CRD + Helm + Anthropic Skills；不做拖拽 UI |
| Vendor 策略 | **不依赖 deerflow-harness 包**，但**手工 vendor 其中 P0/P1 模块** | 复用基础设施层（event log/persistence/factories）≈ 节省 4-5 周；不引入端到端架构耦合 |

---

## 3. 核心范式（来自 Claude Managed Agents 的工程博客）

### Brain-Hands-Session 三层解耦

```
┌────────────────┐    ┌────────────────┐    ┌────────────────┐
│  Brain         │    │  Hands         │    │  Session       │
│ 控制平面        │◀──▶│ 数据平面        │    │ 状态平面        │
│                │    │                │    │                │
│ 无状态         │    │ 独立 sandbox    │    │ append-only    │
│ harness loop   │    │ 每 session 一个 │    │ event log      │
│ 可水平扩展      │    │ gVisor 隔离     │    │ 唯一真相源      │
│ LLM 推理        │    │ filesystem/    │    │ 支撑 checkpoint │
│ tool 路由       │    │ network/       │    │ 恢复/replay    │
│                │    │ subprocess 隔离 │    │ time-travel    │
└────────────────┘    └────────────────┘    └────────────────┘
        │                    │                    │
        └────────────────────┴────────────────────┘
                             │
                ┌────────────▼────────────┐
                │  Credential Proxy       │
                │  凭证永不进 sandbox      │
                │  HTTP proxy 注入 secret │
                └─────────────────────────┘
```

性能收益（Anthropic 公开数据）：TTFT p50 ↓60%、p95 ↓90%。

### 必采的子原则

- **Event Log as Source of Truth**：所有 agent 行为追加到 append-only 日志，DB 角色禁 UPDATE/DELETE
- **Credential Proxy**：sandbox 内永远拿不到真实 secret，由网关在出站链路注入
- **Sub-Agent 协作**：Lead agent 分解任务给 specialists（参考 Claude Agent SDK 的 subagents 机制）
- **Dreaming**（M2 引入）：后台异步从 transcript 提炼跨 session 模式

---

## 4. 顶层设计原则

| 原则 | 含义 | 实现 |
|------|------|------|
| **Brain-Hands 解耦** | LLM 推理与代码执行物理隔离 | Orchestrator (Python) + Sandbox (gVisor 容器) |
| **凭证零落地** | secrets 永不进 sandbox | Credential Proxy 在出站链路注入 |
| **Event Log 唯一真相** | 状态可重放、可回滚、可审计 | append-only Postgres `event_log` + LangGraph PostgresSaver |
| **声明优先** | 80% 场景写 YAML，不写 Python | YAML Schema (Pydantic) + Jinja2 模板 + 可选 Python 插槽 |
| **不造轮子** | 编排=LangGraph、隔离=gVisor、协议=MCP | 自研只做"粘合层 + 控制平面" |
| **渐进可演进** | Docker 单机 → K8s 不重写 | 关键边界用 gRPC/HTTP，调度器是可替换组件 |

---

## 5. 文档导航

- [01-SYSTEM-ARCHITECTURE.md](./01-SYSTEM-ARCHITECTURE.md) — 系统架构图、组件矩阵、event log 表结构
- [02-AGENT-MANIFEST.md](./02-AGENT-MANIFEST.md) — Agent 配置机制（YAML + Python 插槽）
- [03-MONOREPO-LAYOUT.md](./03-MONOREPO-LAYOUT.md) — 仓库目录结构
- [04-ROADMAP.md](./04-ROADMAP.md) — M0/M1/M2/M3 路线 + 验证方案
- [05-RISKS.md](./05-RISKS.md) — 风险与替代方案
- [06-OPEN-SOURCE-DEPS.md](./06-OPEN-SOURCE-DEPS.md) — 第三方依赖与 DeerFlow vendor 清单

调研附录（产生本方案的依据）：
- [../research/01-orchestration-engines.md](../research/01-orchestration-engines.md) — 14 个编排引擎对比
- [../research/02-sandbox-isolation.md](../research/02-sandbox-isolation.md) — 沙盒技术对比 + 主流 AI 公司实践
- [../research/03-managed-agents-platforms.md](../research/03-managed-agents-platforms.md) — Claude/OpenAI/AWS/Cloudflare 等托管平台架构
- [../research/04-deerflow-source-analysis.md](../research/04-deerflow-source-analysis.md) — DeerFlow 源码深度分析 + vendor 文件清单
