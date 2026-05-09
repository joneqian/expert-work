# Helix 子系统设计文档索引

本目录是 [docs/architecture/](../) 下高层架构（00–07）的**子系统级深度补完**。

每个子系统一篇独立设计文档，回答："**这一块怎么做？M0 必须有什么？M1/M2 演进什么？**"

---

## 阅读路径

新人三步走：

1. 先读 [00-OVERVIEW](../00-OVERVIEW.md) → [01-SYSTEM-ARCHITECTURE](../01-SYSTEM-ARCHITECTURE.md)（懂大图）
2. 读本目录 [10/14/15/20](#子系统索引)（M0 核心：LLM Gateway / Sandbox Pool / Auth / Observability）
3. 按需展开其他子系统

---

## 子系统索引

### 🟢 核心运行时层（Brain–Hands 路径上的关键组件）

| ID | 子系统 | 一句话职责 | M0 | M1 | M2 |
|----|--------|----------|----|----|----|
| 10 | [LLM Gateway / Provider Router](./10-llm-gateway.md) | 多 provider 失败重试、token 预算、prompt cache 命中 | ✅ MVP | 多 provider+缓存 | 模型质量 A/B |
| 11 | [Credential Proxy](./11-credential-proxy.md) | 凭证零落地：sandbox 永远拿不到真实 token | ✅ aiohttp 版 | Envoy + Vault dynamic | 短 TTL 旋转 |
| 12 | [MCP Gateway](./12-mcp-gateway.md) | 多租户 MCP server 连接池 + 工具 allow list | ✅ 单 server | 多 server pool | 第三方 marketplace |
| 13 | [Memory Store](./13-memory-store.md) | 短期/长期记忆分层、向量索引、写入队列 | ✅ pgvector | 分层语义 | 跨 session 知识 |
| 14 | [Sandbox Pool](./14-sandbox-pool.md) | gVisor 池化、warm pool、image cache、配额仲裁 | ✅ 冷启动 | warm pool | K8s + Kata |

### 🟡 控制 & 安全层（每租户独立 + 合规底层）

| ID | 子系统 | 一句话职责 | M0 | M1 | M2 |
|----|--------|----------|----|----|----|
| 15 | [AuthN / AuthZ](./15-authn-authz.md) | RBAC、SSO/OIDC、tenant scoping、API key | ✅ MVP | 完整 RBAC | SCIM 同步 |
| 16 | [Quota / Rate Limit](./16-quota-rate-limit.md) | 多维度（tenant×agent×user×model）令牌桶 | ✅ 静态限流 | 动态预算 | 跨集群 |
| 17 | [Audit Log](./17-audit-log.md) | 与 event_log 分离的 WORM 审计、合规保留 | ✅ 基础 | WORM | 合规导出 |
| 18 | [Manifest 供应链](./18-manifest-supply-chain.md) | cosign 签名、CI gate、镜像 attestation | ⚠️ 内审 | cosign | SLSA L3 |
| 21 | [Network Policy](./21-network-policy.md) | 默认拒绝、egress allow-list、SSRF 防护 | ✅ allow-list | DNS 防劫持 | 零信任 |

### 🔵 可靠性 & 运维层（生产化必备）

| ID | 子系统 | 一句话职责 | M0 | M1 | M2 |
|----|--------|----------|----|----|----|
| 19 | [Durable Execution / Resume](./19-durable-execution.md) | 副作用幂等、replay 去重、长会话恢复 | ⚠️ 短会话 | 小时级 | 天级 |
| 20 | [Observability](./20-observability.md) | OTel span/metric/log 命名 + 关键 dashboard | ✅ 基础 trace | 完整 SLO | 异常根因 |
| 22 | [Disaster Recovery](./22-disaster-recovery.md) | 备份、PITR、跨区域、RPO/RTO | ⚠️ 备份 | PITR | 跨区域 |
| 23 | [Postgres Scalability](./23-postgres-scalability.md) | event_log 分区、读写分离、pgvector 索引 | ✅ 单库 | 分区 | 读写分离 |

### 🟣 协作 & 质量层（高级特性）

| ID | 子系统 | 一句话职责 | M0 | M1 | M2 |
|----|--------|----------|----|----|----|
| 24 | [Sub-Agent 执行](./24-subagent-execution.md) | 失败隔离、parent-child trace、超时级联 | ❌ | ✅ 基础 | 高级编排 |
| 25 | [Human-in-the-Loop](./25-hitl.md) | LangGraph interrupt、审批 UX、超时回退 | ❌ | ❌ | ✅ |
| 26 | [Eval Framework](./26-eval-framework.md) | 数据集、指标、A/B gate、CI 集成 | ❌ | ❌ | ✅ |

### 🟠 上下文管理层（长会话与状态压缩）

| ID | 子系统 | 一句话职责 | M0 | M1 | M2 |
|----|--------|----------|----|----|----|
| 27 | [Context Compression](./27-context-compression.md) | 长会话 token 窗口管理、分层保留、prefix cache 协同 | ⚠️ 简单截断 | ✅ 摘要 LLM | history layer 入 13 |

### 📚 附录（横切定义）

| ID | 文档 | 一句话职责 |
|----|------|----------|
| 99 | [Shared Types](./99-SHARED-TYPES.md) | 跨子系统共享枚举 / 状态机 / 类型别名 / Postgres 约定的权威定义点 |

图例：✅ 必须有 / ⚠️ 简化版 / ❌ 不做

---

## 子系统依赖图

```
                        ┌─────────────────────────┐
                        │ 15 AuthN/AuthZ          │
                        │ 16 Quota                │
                        │ 17 Audit Log            │ 横切：所有请求都过这三个
                        │ 20 Observability        │
                        │ 21 Network Policy       │
                        └────────────┬────────────┘
                                     │
                  ┌──────────────────┼──────────────────┐
                  ▼                  ▼                  ▼
         ┌──────────────┐   ┌──────────────┐   ┌───────────────┐
         │ Control      │   │ Orchestrator │   │ Sandbox       │
         │ Plane        │   │              │   │ Supervisor    │
         └──────┬───────┘   └──────┬───────┘   └───────┬───────┘
                │                  │                   │
                ▼                  ▼                   ▼
         ┌─────────────┐    ┌─────────────┐    ┌──────────────┐
         │18 Supply    │    │10 LLM GW    │    │14 Sandbox    │
         │   Chain     │    │11 Cred Proxy│    │   Pool       │
         │             │    │12 MCP GW    │    │              │
         │             │    │13 Memory    │    │              │
         │             │    │24 SubAgent  │    │              │
         │             │    │25 HITL      │    │              │
         │             │    │19 Durable   │    │              │
         │             │    │27 CtxCompr. │    │              │
         └─────────────┘    └─────────────┘    └──────────────┘

         持久化层（所有子系统共享）：
         23 Postgres Scalability    │   22 DR / Backup

         横切定义（所有子系统引用）：
         99 Shared Types（枚举 / 状态机 / 类型别名 / Postgres 约定）
```

---

## 每篇 doc 的统一结构

```
1. 职责 & 边界          # 这是什么、不是什么
2. 上下游依赖           # 与哪些其他子系统交互
3. 数据模型 / 状态机    # 表结构、enum、状态转换
4. 关键接口             # 内部 API 契约
5. 算法 / 关键决策      # 调度、仲裁、重试等
6. 失败模式 & 缓解      # 哪里会坏、怎么挡
7. 可观测性             # 关键 metric / span / log
8. 安全考虑             # 攻击面 + 防御
9. M0 / M1 / M2 演进    # 每阶段交付什么
10. 开放问题            # 未决策项
```

---

## 与上层 docs 的关系

| 上层 doc | 关系 |
|---------|------|
| [00-OVERVIEW](../00-OVERVIEW.md) | 业务定位与本目录无关，纯产品视角 |
| [01-SYSTEM-ARCHITECTURE](../01-SYSTEM-ARCHITECTURE.md) | 给出大图，本目录补深度 |
| [02-AGENT-MANIFEST](../02-AGENT-MANIFEST.md) | manifest 字段语义；每个子系统会引用相关字段 |
| [03-MONOREPO-LAYOUT](../03-MONOREPO-LAYOUT.md) | 子系统的代码位置都标在那里 |
| [04-ROADMAP](../04-ROADMAP.md) | 时间线；本目录的 M0/M1/M2 都对齐 roadmap |
| [05-RISKS](../05-RISKS.md) | 高层风险；子系统 doc 的"失败模式"是细化 |
| [06-OPEN-SOURCE-DEPS](../06-OPEN-SOURCE-DEPS.md) | vendor 清单；子系统 doc 引用具体 vendor 模块 |
| [07-INFRASTRUCTURE-GAPS](../07-INFRASTRUCTURE-GAPS.md) | gap 列表；本目录是对 gap 的逐项补完 |

---

## 修订记录

- **2026-05-09**：初版，17 个子系统骨架。
- **2026-05-09**：新增 **27 Context Compression**（长会话上下文压缩）独立成篇；新增 **99 Shared Types** 附录作为跨子系统共享枚举 / 状态机 / 类型别名权威定义点；02 manifest 同步增加 `model.fallback` 自动注入规则与 `policies.context_compression` 字段。
