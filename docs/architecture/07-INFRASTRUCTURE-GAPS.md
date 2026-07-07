# 07 基础设施 Gap Analysis — 产品级清单审视

> 本文档系统盘点：要把 Expert Work 做成"产品级 Agent 工作引擎"（不只是"能跑通的引擎"），现有方案还有哪些基础设施层面的设计缺失。
>
> **结论先行**：核心运行时层（编排/沙盒/事件日志/中间件）已扎实，但**横切关注点（认证/审计/PII/SRE/DX/合规）的设计深度不足**——这些是 Dify、DeerFlow 这类"半成品"产品级演进时最容易翻船的地方。

---

## 评估维度（13 个维度）

我按"产品级工程系统"的标准维度盘点，每项给：
- ✅ **已覆盖**（现有方案明确包含且足够细致）
- 🟡 **部分覆盖**（提到了但深度不够 / 缺细节）
- ❌ **缺失**（完全没考虑或一笔带过）

---

## 1. 核心运行时层 ✅ 整体扎实

| 子项 | 状态 | 说明 |
|------|------|------|
| 编排引擎 | ✅ | LangGraph |
| Sandbox 隔离 | ✅ | Docker + gVisor，未来 Kata |
| Event Log + Checkpoint | ✅ | append-only Postgres + LangGraph PostgresSaver |
| 凭证代理 | ✅ | M0 自研 / M1+ Envoy |
| MCP 集成 | ✅ | 多 server + transport + OAuth |
| Stream Bridge | ✅ | last-event-id 重连 + 心跳 |
| 中间件系统 | ✅ | 含 dynamic_context、llm_error_handling、sandbox_audit、@Next/@Prev 锚点（第三次扫描后补齐）|

**结论**：这一层是方案最强的部分。没有明显 gap。

---

## 2. 认证、授权与身份 🟡 设计粒度不足

### 现状
- 提到"Auth/RBAC（JWT）"，但只是个 placeholder

### 缺失项

| Gap | 严重性 | 说明 |
|-----|--------|------|
| **用户认证方式选型** | 🔴 P0 | OAuth2 / OIDC / SSO（企业 SSO 集成）/ 自建 JWT，哪个？多种并存？ |
| **服务间认证** | 🔴 P0 | Control Plane ↔ Orchestrator ↔ Sandbox-Supervisor 之间是否要 mTLS？SPIFFE/SPIRE？ |
| **API Key 管理**（外部业务系统调用 Expert Work）| 🔴 P0 | 创建/吊销/轮换/限流，按 key 维度计费 |
| **Agent 身份**（每个 Agent 实例自己的 workload identity）| 🟠 P1 | 用 Agent 调外部 API 时谁在调用——Agent 自己还是用户？这影响审计 |
| **细粒度权限模型** | 🟠 P1 | RBAC vs ABAC？资源粒度（agent/session/tool/secret）×操作粒度（create/read/update/delete/run）的权限矩阵 |
| **会话授权**（用户 A 能不能看用户 B 的 session）| 🔴 P0 | 多租户基础（部分被 thread_meta 的 check_access 覆盖，但不完整） |
| **越权检测** | 🟠 P1 | 异常行为检测（一个 agent 突然访问大量资源、跨租户读取） |

### 建议
- M0：选定 OIDC（兼容 Okta/Auth0/Keycloak）+ JWT for service-to-service + 简单 RBAC 矩阵
- M1：API Key 管理 + 细粒度 ABAC
- 文档化资源-操作权限矩阵

---

## 3. 审计与合规 🟡 与 event_log 混淆

### 现状
- event_log 用于 session 内事件追溯
- 没有"操作审计"层（who did what when from where）

### 缺失项

| Gap | 严重性 | 说明 |
|-----|--------|------|
| **操作审计日志** | 🔴 P0 | manifest 修改、secret 访问、agent 启停、用户登录等管理动作；与 event_log 分表 |
| **审计日志不可篡改** | 🔴 P0 | append-only + WORM 存储 / S3 Object Lock |
| **PII 处理框架** | 🔴 P0 | **通用 redactor 中间件**（业务无关）+ **per-tenant `pii_fields` 配置**驱动；不同租户列不同字段（医疗：patient.id_card；HR：employee.salary；客服：customer.phone）|
| **数据保留策略** | 🔴 P0 | event_log / audit_log / memory 各自保留多久？冷归档到 S3？|
| **租户数据导出**（GDPR Article 20）| 🟠 P1 | 一键导出某租户全部数据（manifest + sessions + memory + uploads）|
| **租户数据删除**（GDPR Article 17）| 🟠 P1 | 软删除 + 30 天 grace period + 硬删除；级联到 vector DB / 对象存储 |
| **合规可插拔架构** | 🔴 P0 | `tenant_config.compliance_pack` 字段 + 引擎根据 pack 自动注入（PII/加密/保留/隔离/驻留）；M0 必备基础设施层 |
| **合规认证就绪**（M2 可选）| 🟡 P2 | HIPAA / SOC2 / ISO27001 / 等保三级**正式审计**推到 M2 业务需要时再启动；M0/M1 提供前置基础设施 |
| **数据驻留**（data residency）| 🟡 P2 | 某些客户要求数据不出特定 region |

### 建议
- M0 必做：audit_log 表 + 中央 PII redactor + 数据保留 SLA 文档
- M1：HIPAA 技术控制项落地（加密、访问日志、数据隔离）
- M2：导出/删除 API + 合规审计报表

---

## 4. 安全（基础设施层）🟡 大部分有但分散

### 现状
- gVisor 隔离 ✅
- 凭证代理 ✅
- sandbox_audit middleware ✅
- 运维提到 secrets 轮换、镜像扫描

### 缺失项

| Gap | 严重性 | 说明 |
|-----|--------|------|
| **数据加密**（at-rest）| 🔴 P0 | Postgres TDE、S3 SSE-KMS、Vault 后端 KMS；密钥管理统一规划 |
| **数据加密**（in-transit）| 🔴 P0 | 全链路 TLS（含 service mesh）|
| **Secrets 轮换自动化** | 🟠 P1 | Vault dynamic secrets + 短 TTL；自动轮换 DB/API key |
| **Container 镜像扫描** | 🟠 P1 | Trivy/Grype + SBOM；CI 阻断高危 CVE；私有 registry |
| **Supply chain 安全** | 🟠 P1 | sigstore/cosign 签名 manifest 和 image；SLSA 等级 |
| **依赖漏洞扫描** | 🟠 P1 | pip-audit / Dependabot / Snyk |
| **WAF / DDoS 防护** | 🟡 P2 | 如果暴露公网入口 |
| **运行时安全监控** | 🟠 P1 | Falco / 检测异常 syscall（gVisor 之外的运行时检测）|
| **Prompt Injection 运行时防护** | 🟠 P1 | M0 测试用 garak，但运行时呢？需 input filter middleware（已有 placeholder）|
| **越狱检测** | 🟠 P1 | 输出层检测 LLM 是否泄漏 system prompt / 工具描述 |
| **Egress 防护** | 🟠 P1 | sandbox 出站除了 Credential Proxy 还要 DNS 过滤、域名 allowlist 强制 |
| **Pod Security Standards**（K8s 阶段）| 🟠 P1 | 强制 restricted profile；OPA/Gatekeeper 策略 |

### 建议
- M0 必做：全链路 TLS + Postgres TDE + 镜像扫描 CI gate
- M1：Vault dynamic + cosign 签名 + Falco
- M2：完整 prompt injection 运行时防护

---

## 5. 可观测性 🟡 提到但缺规范

### 现状
- OpenTelemetry + Prometheus + Loki + Grafana 提到
- 没有具体规范

### 缺失项

| Gap | 严重性 | 说明 |
|-----|--------|------|
| **结构化日志规范** | 🔴 P0 | 字段标准（trace_id/tenant_id/agent/version/session/run/user）+ 强制 schema + redaction 中间层 |
| **Trace 传播规范** | 🔴 P0 | W3C Trace Context；跨 Control Plane → Orchestrator → Sandbox → MCP server 全链路 |
| **指标体系** | 🔴 P0 | 业务指标（agent 调用量、成功率、TTFT、token/$、tool 调用图）+ 技术指标（QPS、延迟、错误率、资源） |
| **SLO/SLI 定义** | 🔴 P0 | 可用性、TTFT P95、durable execution 恢复时间——没数字就没产品级 |
| **告警体系** | 🔴 P0 | 告警分级（P0/P1/P2）、告警通道（飞书/PagerDuty）、告警去重抑制、值班排班 |
| **Runbook** | 🟠 P1 | 每个告警有标准处理 SOP |
| **错误追踪** | 🟠 P1 | Sentry / GlitchTip 集成（应用层 unhandled exception）|
| **Agent-specific 可观测** | 🔴 P0 | LangSmith / Langfuse 选一个还是自建？token 分布、tool 调用图、reasoning 可视化、session 时间线 |
| **Session 回放**（time travel）| 🟠 P1 | 基于 event_log 重放，研发调试关键 |
| **业务大盘** | 🟠 P1 | 给业务/产品/客户成功看的（不是给 SRE 看的）|
| **慢查询与火焰图** | 🟠 P1 | py-spy / Postgres slow query log |
| **成本可视化** | 🟠 P1 | token usage middleware 数据 → 租户/agent/session 维度成本大盘 |

### 建议
- M0 必做：日志 schema + Trace Context 规范 + SLO 定义 + 第一版 Grafana 大盘
- M1：Langfuse 集成（开源免费）+ 业务大盘 + Runbook
- 持续：完善告警库

---

## 6. 数据存储与生命周期 🟡 主存提及，生命周期缺失

### 现状
- Postgres + pgvector + Redis + Vault 提及
- 对象存储一笔带过

### 缺失项

| Gap | 严重性 | 说明 |
|-----|--------|------|
| **对象存储正式选型** | 🔴 P0 | uploads / sandbox snapshots / event_log 冷归档 / agent artifacts → S3-compatible（MinIO 自建？阿里云 OSS？）+ 接口抽象 |
| **数据备份策略** | 🔴 P0 | Postgres 全量 + 增量 WAL；RPO/RTO 目标；演练频率 |
| **跨 AZ / 跨 region 复制** | 🟠 P1 | DR 计划 |
| **数据归档** | 🔴 P0 | event_log 半年后冷归档 S3；归档后查询路径 |
| **数据保留 TTL** | 🔴 P0 | session / memory / upload / audit 各自 TTL；自动清理 job |
| **数据库迁移策略** | 🟠 P1 | zero-downtime migration（expand-contract pattern）；Alembic 流程 |
| **读写分离** | 🟡 P2 | 主从复制；读副本路由 |
| **分片策略** | 🟡 P2 | event_log 按 tenant 分区（已提）+ 大租户独立 schema/库 |
| **Redis 用途分隔** | 🟠 P1 | 限流缓存 / 会话缓存 / job queue 各用独立 DB 或独立实例（避免互相影响）|
| **连接池规划** | 🟠 P1 | PgBouncer 必上；连接数预算（每 orchestrator worker × N）|

### 建议
- M0 必做：对象存储抽象 + Postgres 自动备份 + 保留 TTL 文档 + PgBouncer
- M1：跨 AZ DR 演练 + 数据归档 pipeline

---

## 7. 多租户深度 🟡 隔离机制有但运营层缺

### 现状
- tenant_id 贯通 ✅
- Postgres RLS ✅
- sandbox 进程隔离 ✅

### 缺失项

| Gap | 严重性 | 说明 |
|-----|--------|------|
| **租户级 quota 管理** | 🔴 P0 | 配额模型：CPU 核时 / 内存 GB 时 / token 数 / API 调用数 / sandbox 实例数；准入控制 |
| **租户级配置** | 🔴 P0 | 每租户 model API key / Vault path / MCP 白名单 / 限流阈值 |
| **租户生命周期** | 🟠 P1 | 创建/暂停/恢复/删除；级联清理（agent/session/memory/upload/audit）|
| **租户级灰度** | 🟠 P1 | 新功能按租户发布（不只是按 manifest version）|
| **租户级监控视图** | 🟠 P1 | Admin UI 租户视角的资源使用、成本、错误率 |
| **租户隔离审计** | 🟠 P1 | 自动检测 cross-tenant 数据泄漏（定期跑测试）|
| **租户间公平性**（noisy neighbor）| 🟠 P1 | 一个租户大量调用不能拖垮其他租户：cgroup / Postgres connection limit per tenant / Redis quota |

### 建议
- M0：基础 quota（token/sandbox 实例数）+ 租户配置隔离
- M1：完整生命周期 API + 公平性保障

---

## 8. 弹性与可靠性 🟡 部分模式有

### 现状
- LLM 断路器 ✅
- M2 durable execution ✅

### 缺失项

| Gap | 严重性 | 说明 |
|-----|--------|------|
| **Health checks** | 🔴 P0 | liveness/readiness/startup probe；不只服务存活，还要依赖（DB/Redis/Vault）健康 |
| **Graceful shutdown** | 🔴 P0 | SIGTERM 处理：完成在途 session、转移连接、清 sandbox、checkpoint flush；超时强制 |
| **超时分层** | 🔴 P0 | request timeout > session timeout > tool timeout > LLM timeout；级联超时和取消传播 |
| **Backpressure** | 🟠 P1 | 当 orchestrator 队列堆积，怎么拒绝新请求？429 with Retry-After |
| **幂等性** | 🟠 P1 | 重复 POST /runs 怎么处理？Idempotency-Key header |
| **请求取消** | 🔴 P0 | 用户取消 session：信号要传到 orchestrator → sandbox → in-flight LLM call |
| **重试策略统一** | 🟠 P1 | 不仅 LLM，所有外部依赖（DB/Vault/MCP/HTTP tool）都要：指数退避 + jitter + 上限 |
| **故障演练**（Chaos）| 🟠 P1 | 工具化：toxiproxy / chaos-mesh；定期跑 |
| **故障预案文档** | 🔴 P0 | "Postgres 主挂了"、"Vault 不可达"、"Anthropic 全挂了"——每个有 runbook |
| **限流体系** | 🔴 P0 | 三层：网关层（per IP/key）+ 业务层（per tenant/agent）+ 提供商层（per LLM key）|

### 建议
- M0 必做：probes + graceful shutdown + 超时分层 + 三层限流
- M1：故障演练 + runbook 库
- M2：完整 chaos engineering

---

## 9. 性能与扩展 🟡 提及但无设计

### 现状
- Sandbox warm pool ✅

### 缺失项

| Gap | 严重性 | 说明 |
|-----|--------|------|
| **缓存层架构** | 🔴 P0 | LLM response cache（语义+精确）、embedding cache、tool result cache；TTL 策略；多租户 cache key 安全 |
| **批处理** | 🟠 P1 | Anthropic Batch API（成本 -50%）适用于异步场景 |
| **连接池** | 🔴 P0 | LLM provider HTTP client、PG、Redis、MCP server 都要池化 |
| **资源调度** | 🟠 P1 | sandbox supervisor 怎么决定哪台机器起 sandbox（亲和性 / 反亲和性）|
| **容量规划文档** | 🟠 P1 | 100 agent 需要多少资源 / 1000 agent / 10000 agent |
| **性能基准 baseline** | 🟠 P1 | 每个 release 对比上版的性能回归 |
| **GPU 资源池**（如果用本地推理）| 🟡 P2 | embedding / re-rank 模型 |

### 建议
- M0：连接池规划 + 简单 LLM cache（精确）
- M1：批处理 + 语义 cache + 容量规划文档

---

## 10. 部署与发布 🟡 流水线缺失

### 现状
- docker-compose / K8s + Helm 提及

### 缺失项

| Gap | 严重性 | 说明 |
|-----|--------|------|
| **CI/CD pipeline** | 🔴 P0 | lint / test / 镜像构建 / 安全扫描 / 部署；GitHub Actions or GitLab CI 标准化 |
| **环境隔离** | 🔴 P0 | dev / staging / prod 的配置、数据库、密钥分离；无意混淆防护 |
| **服务发布策略** | 🔴 P0 | 蓝绿 / 金丝雀（不只是 manifest 灰度，服务本身也要）|
| **服务回滚** | 🔴 P0 | 一键回滚 + 数据库迁移兼容（向前/向后） |
| **DB 迁移 zero-downtime** | 🟠 P1 | expand-contract pattern；Alembic 规范 |
| **Sandbox image 发布流程** | 🟠 P1 | 镜像版本化 + 漏洞扫描 + 签名 + 内部 registry |
| **基础设施 IaC** | 🟠 P1 | Terraform / Pulumi 描述基础设施；防漂移 |
| **环境一致性**（dev = prod）| 🟠 P1 | docker-compose.dev = subset of helm prod |
| **Runtime 配置 vs 镜像配置** | 🟠 P1 | 12-factor compliance |
| **金丝雀分析自动化** | 🟡 P2 | 灰度过程中错误率/延迟自动决策回滚 |

### 建议
- M0 必做：完整 CI/CD + 三环境 + 镜像 registry
- M1：IaC + zero-downtime 迁移规范

---

## 11. 开发者体验 (DX) 🟡 CLI 起步

### 现状
- expert-work-cli（lint / run）✅

### 缺失项

| Gap | 严重性 | 说明 |
|-----|--------|------|
| **本地开发环境** | 🟠 P1 | 一行起所有依赖；manifest 热加载；断点 attach 到 sandbox 内代码 |
| **Agent 单元测试框架** | 🔴 P0 | pytest 风格写 agent 测试；fixture（mock LLM/tool）；assertion 库 |
| **Mock LLM** | 🔴 P0 | recordings replay（VCR 风格）+ 确定性输出 + 成本控制 |
| **Eval 框架** | 🔴 P0 | promptfoo / OpenAI Evals / 自建？数据集管理 + 自动跑 + diff 报表 |
| **A/B 评估** | 🟠 P1 | 两个 manifest version 在同一 eval 集上对比 |
| **YAML schema IDE 提示** | 🟠 P1 | JSON Schema 发布；VS Code / IntelliJ 自动补全 |
| **Agent debugger** | 🟠 P1 | 单步执行 graph、查看 state、time travel |
| **新人 onboarding 文档** | 🟠 P1 | 第一个 agent 30 分钟跑通教程 |
| **Cookbook** | 🟠 P1 | 常见模式：怎么做 RAG / 怎么做 plan-execute / 怎么做 HITL |
| **错误信息友好化** | 🟠 P1 | manifest lint 报错有位置、原因、建议 |

### 建议
- M0：测试框架 + Mock LLM + Eval 框架（promptfoo 集成）+ JSON Schema 发布
- M1：本地热加载 + cookbook 起步

---

## 12. 业务运营层 ❌ 几乎空白

### 现状
- Token usage middleware ✅（仅追踪，无运营）

### 缺失项

| Gap | 严重性 | 说明 |
|-----|--------|------|
| **使用统计** | 🟠 P1 | 每个 agent / 工具 / MCP server 的调用量、成功率、TTFT、token 分布 |
| **A/B 测试与流量分配** | 🟠 P1 | manifest version 之间按流量比例分配；统计显著性 |
| **用户反馈收集** | 🔴 P0 | 👍/👎、文本反馈、自动追溯到具体 turn 和 trace |
| **Eval 数据集管理** | 🔴 P0 | golden set、regression set、版本化、来源标注（人工/合成/真实） |
| **持续改进流水线** | 🟠 P1 | 用户反馈 → 自动加入 eval → 触发 prompt 改进 → 验证 → 上线 |
| **Cost optimization 建议** | 🟡 P2 | 检测高 token 调用、长上下文未用 cache、可优化的 prompt |
| **Quality dashboard** | 🟠 P1 | 每个 agent 当前质量得分（来自 eval + 用户反馈）|
| ~~**Chargeback 报表**~~ ✅ | ~~🟠 P1~~ | **已由 Stream Y/Z 关闭（2026-06-05）**：rate card 计量 + 成本派生 rollup（base/markup/billed 拆分）+ 按租户/agent/model 维度的 chargeback 报表与用量面。发票推迟 M2。详见 [ITERATION-PLAN](../ITERATION-PLAN.md) 平台中心化路线图 |

### 建议
- M0：feedback 表 + 简单 usage stats 大盘
- M1：eval 数据集管理 + ~~chargeback~~（chargeback 已由 Stream Y/Z 提前交付，2026-06-05）
- M2：持续改进流水线

---

## 13. Agent 生态 🟡 设计不足

### 现状
- Manifest schema ✅
- MCP 集成 ✅

### 缺失项

| Gap | 严重性 | 说明 |
|-----|--------|------|
| **Agent 内部市场** | 🟠 P1 | 团队 A 发布 agent 让团队 B 复用；版本/评分/文档 |
| **Tool 注册中心** | 🟠 P1 | 公司内部所有工具的目录；权限申请；用量统计 |
| ~~**MCP server 注册发现**~~ 🔄 | ~~🟠 P1~~ | **方向调整为 client-only（不再自建内部 MCP server registry）**：Stream W 交付的是**平台 MCP 连接器目录 + 租户实例化**（消费外部 MCP 生态，含 probe 健康检查 + 档位门控）。详见 [memory:project_mcp_direction_client_only] |
| ~~**Skill / Template 库**~~ ✅ | ~~🟠 P1~~ | **已由 Stream X 关闭（2026-06-05）**：平台精选 skill 库（`tenant_id NULL` + 档位门控）+ 租户自建（混合）+ resolver 双查接入 agent build。继承/mixin 机制仍 M1（见下条 Manifest 复用）|
| **Manifest 复用机制** | 🟠 P1 | YAML inherit / mixin（避免每个 agent 拷贝）|
| **跨 agent 通信协议**（A2A） | 🟡 P2 | Google A2A 或自定义 |
| **Plugin 机制** | 🟠 P1 | 第三方 middleware / tool / hook 怎么打包发布（@Next/@Prev 提供运行时机制，但分发流程缺）|

### 建议
- M1：Manifest 继承 + 内部 marketplace 第一版
- M2/M3：A2A 协议 + plugin 分发

---

## 总览：Gap 严重性矩阵

### 🔴 P0 — M0/M1 必须补齐（24 项）

不补齐**不算产品级**。罗列：

1. 用户认证方式选型（OIDC + JWT）
2. 服务间认证（mTLS）
3. API Key 管理
4. 会话授权完整化
5. 操作审计日志（与 event_log 分表）
6. 审计日志不可篡改
7. PII 脱敏框架
8. 数据保留策略
9. 数据加密（at-rest + in-transit）
10. 结构化日志规范
11. Trace Context 规范
12. 指标体系定义
13. SLO/SLI 定义
14. 告警体系
15. Agent-specific 可观测（Langfuse 等选型）
16. 对象存储正式选型
17. 数据备份策略
18. 数据归档 pipeline
19. 数据保留 TTL
20. 租户级 quota
21. 租户级配置
22. Health checks（多层）
23. Graceful shutdown
24. 超时分层
25. 请求取消传播
26. 故障预案文档
27. 限流体系（三层）
28. 缓存层架构
29. 连接池规划
30. CI/CD pipeline
31. 环境隔离（dev/staging/prod）
32. 服务发布策略
33. 服务回滚机制
34. Agent 单元测试框架
35. Mock LLM
36. Eval 框架
37. 用户反馈收集
38. Eval 数据集管理

### 🟠 P1 — M1/M2 重要（约 40 项，省略列举）

合规、SOC2/HIPAA、Vault dynamic、Falco、cosign、imageScan、Sentry、Runbook、跨 AZ DR、IaC、批处理、A/B 测试、Manifest 继承等。

### 🟡 P2 — M3 演进（约 15 项）

跨 region、CDN、Pod Security 高级、自动化金丝雀分析、A2A、GPU 池等。

---

## 工时估算修正

原方案 M0 估计 4-6 周（仅核心运行时），未充分考虑这些横切关注点。

修正后估算（产品级）：

| 阶段 | 原估计 | 包含 P0 横切关注点后 | 增量主要内容 |
|------|--------|---------------------|-------------|
| **M0 — MVP 可上线** | 4-6 周 | **8-10 周** | + 认证授权 + 操作审计 + PII + 加密 + 日志规范 + SLO + 备份 + 健康检查 + 超时 + 限流 + CI/CD + Mock LLM + Eval 框架 |
| **M1 — 多租户生产化** | 6-8 周 | **10-12 周** | + 租户 quota + Vault dynamic + 镜像扫描 + 用户反馈 + 业务大盘 + DR + IaC |
| **M2 — Durable + 多 Agent** | 6-8 周 | 8-10 周 | + 数据归档 + chaos + 持续改进 pipeline + cookbook |
| **M3 — K8s + 生态** | 持续 | 持续 | + 合规认证 + A2A + marketplace |

**总周期到 M2 产品级**：原 4-5 个月 → **修正后 6-8 个月**（3 人团队）

---

## 决策建议

### 三种选择

#### A. 严格产品级路线
按本文档 P0 全补齐再上线，M0 = 8-10 周。**适用**：直接对外卖、合规客户从第一天就接入、多租户严格隔离。

#### B. MVP 优先路线（推荐）
M0 阶段补 **必备 P0 子集**（约 12-15 项），其他 P0 推到 M1：
- 必备：认证基础、操作审计、PII redactor、TLS、健康检查、graceful shutdown、超时、基础限流、CI/CD、Mock LLM
- 推迟：Vault dynamic、镜像扫描、IaC、跨 AZ DR、批处理、Eval 数据集管理（先用 promptfoo 简版）

→ M0 = 6-7 周，M1 补齐剩余 P0 = 10 周

**适用**：先内部用、多业务线引擎首次落地、沿用现有 Dify 业务做 dogfood。

#### C. 拆分团队路线
1 名工程师全力做横切关注点（auth/audit/SRE/CICD），2 名工程师做核心运行时。两线并行。

→ 缩短整体时间 20-30%，但需要更多人力。

---

## 总结

**当前方案的产品级评分：约 65-70 / 100**
- 核心运行时层：90/100（很扎实）
- 安全与合规：50/100（有底线，缺纵深）
- 可观测性：50/100（有基础设施栈，缺规范和指标）
- 弹性可靠性：60/100（有概念，缺工程化）
- 数据生命周期：40/100（重大缺失）
- 部署发布：50/100（有目标态，缺流水线）
- 开发者体验：40/100（CLI 起步）
- 业务运营：20/100（几乎空白）
- 多租户深度：60/100（隔离够，运营缺）

**做完本文档 P0 后**：产品级评分可达 **85-90 / 100**，可对外卖。

**核心建议**：M0 不要只盯着"agent 跑通"，要把横切关注点的"骨架"立起来——后期补这些远比前期立起来贵。
