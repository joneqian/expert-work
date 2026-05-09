# Helix — 自研企业级 Agent 工作引擎方案

> **状态**：架构调研完成，待用户决策启动
> **更新时间**：2026-05-09

## 阅读指南

如果你只想看结论，按这个顺序读：

1. **[architecture/00-OVERVIEW.md](./architecture/00-OVERVIEW.md)** — 项目背景、关键决策、核心范式（10 分钟）
2. **[architecture/01-SYSTEM-ARCHITECTURE.md](./architecture/01-SYSTEM-ARCHITECTURE.md)** — 系统架构图、组件清单
3. **[architecture/02-AGENT-MANIFEST.md](./architecture/02-AGENT-MANIFEST.md)** — Agent 配置机制（YAML + Python 插槽）
4. **[architecture/04-ROADMAP.md](./architecture/04-ROADMAP.md)** — M0/M1/M2/M3 实施路线 + 验证方案

如果你想深挖技术依据，按这个顺序读调研附录：

5. **[research/01-orchestration-engines.md](./research/01-orchestration-engines.md)** — 14 个开源编排引擎对比
6. **[research/02-sandbox-isolation.md](./research/02-sandbox-isolation.md)** — 沙盒技术对比 + 主流 AI 公司实践
7. **[research/03-managed-agents-platforms.md](./research/03-managed-agents-platforms.md)** — Claude/OpenAI/AWS/Cloudflare 等托管平台架构
8. **[research/04-deerflow-source-analysis.md](./research/04-deerflow-source-analysis.md)** — DeerFlow 源码深度分析 + Vendor 文件清单
9. **[research/05-deerflow-deeper-scan.md](./research/05-deerflow-deeper-scan.md)** — 🆕 第三次源码深扫修正（2026-05-09，发现 6 个被误判的通用中间件 + 锚点系统，影响 API 成本 10x）

剩余架构补充：
- [architecture/03-MONOREPO-LAYOUT.md](./architecture/03-MONOREPO-LAYOUT.md) — 仓库目录结构
- [architecture/05-RISKS.md](./architecture/05-RISKS.md) — 风险与替代方案
- [architecture/06-OPEN-SOURCE-DEPS.md](./architecture/06-OPEN-SOURCE-DEPS.md) — 第三方依赖与 Vendor 清单
- **[architecture/07-INFRASTRUCTURE-GAPS.md](./architecture/07-INFRASTRUCTURE-GAPS.md)** — 🆕 产品级基础设施 Gap Analysis（13 维度盘点 P0/P1/P2 缺失项）

---

## 核心结论速览

### 为什么自研

Dify 三大痛点：版本维护困难（端到端产品深度定制后无法升级）、能力受限（DAG 不支持复杂编排）、Agent 间无沙盒隔离。

### 不取的开源方案

- **Dify / DeerFlow / Flowise** — 端到端产品形态，自研定制 = 重蹈覆辙
- **AutoGen** — 已维护模式
- **CrewAI** — 可控性差
- **OpenAI Agents SDK / Swarm** — 强绑 OpenAI

### 选定方案

| 层 | 选型 | 理由 |
|----|------|------|
| 编排引擎 | **LangGraph** (Python, MIT) | Graph State Machine 最灵活、生态最成熟、不强依赖 LangChain |
| 沙盒 | **Docker + gVisor (runsc)** 起步，K8s 阶段升级 Kata | OpenAI/Claude 同款半可信场景标准 |
| 状态层 | **Postgres append-only event_log + LangGraph PostgresSaver** | 事件溯源 + checkpoint |
| 配置 | **声明式 YAML manifest（80%）+ Python 插槽包（20%）** | K8s CRD + Helm + Anthropic Skills 思路；不做拖拽 UI |
| 凭证 | **Credential Proxy 网关注入**（M0 自研 aiohttp，M1+ Envoy + Vault）| 凭证永不进 sandbox |
| API | **FastAPI + Pydantic v2 + SQLAlchemy 2.0** | 控制平面标准栈 |
| 部署 | **Docker 单机起步**（M0/M1）→ K8s（M3）| 减少初期运维 |

### 对标范式

完整复刻 Anthropic Claude Managed Agents 的 **Brain-Hands-Session 三层解耦**：
- **Brain**（无状态 harness loop）= Orchestrator（LangGraph runtime）
- **Hands**（独立 sandbox）= 每个 session 一个 gVisor 容器
- **Session**（append-only event log）= Postgres `event_log` 表，唯一真相源

### Vendor 策略（关键创新）

不依赖 `deerflow-harness` PyPI 包（会拉进它的 14 中间件 + 应用层 + community 集成），但**手工 vendor 它的基础设施层**：
- 🔴 P0：event_log / persistence / checkpointer/store 工厂 / stream_bridge / run_manager（~2500 行）
- 🟠 P1：AgentMiddleware 基类 + 5 个核心中间件 + subagent executor + guardrails + mcp client（~1500 行）

**净节省 4-5 周开发**（自己写到生产级要踩 SQL 并发坑、seq 单调坑、批量锁坑）。

### 自研规模

总自研 ~12K 行 Python（含 vendor + 借鉴重写 + 真自研控制平面）。

### 实施路线

| 阶段 | 时长 | 关键交付 |
|------|------|---------|
| M0 — MVP | 4-6 周 | 1 个内部业务 Agent 跑通（取代 1 个 Dify 应用），与 Dify 平行运行对比 |
| M1 — 生产化 | 6-8 周 | 多租户 + Python 插槽 + 灰度发布 + 完整可观测性 |
| M2 — Durable + 多 Agent | 6-8 周 | 长会话恢复 + Plan-Execute + HITL + Eval gate |
| M3 — K8s + A2A | 持续 | Helm + Operator + 跨集群协议 |

---

## 架构决策记录（ADR）

- **[adr/0001-python-vs-typescript-stack.md](./adr/0001-python-vs-typescript-stack.md)** — Python vs TypeScript 全栈选型评估（2026-05-09）

---

## 等待用户决策的事项

1. **是否启动 M0**？人力 3 人（1 后端 lead + 1 平台 + 1 前端兼职）
2. **首个 dogfood 业务**？沿用现有 Dify 上线业务迁移（具体业务由用户告知；优先选流量适中、工具简单、合规简单）
3. **Linux 部署服务器**是否就绪？（gVisor 不支持 macOS prod）
4. **Vault / Postgres / 监控基础设施**是否申请到位？
5. **项目仓库命名**：`Helix` 还是其他？

---

## 反馈渠道

- 架构方案有疑问 → 标注在对应文档的 PR 中
- Vendor 文件选择有异议 → 在 [research/04-deerflow-source-analysis.md](./research/04-deerflow-source-analysis.md) 表格中评注
- 路线优先级调整 → 在 [architecture/04-ROADMAP.md](./architecture/04-ROADMAP.md) 标注
