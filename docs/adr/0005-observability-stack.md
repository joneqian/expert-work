# ADR-0005：可观测栈 — 自托管 Langfuse + Prometheus + Grafana + Loki + Tempo

- **状态**：✅ 已决策
- **日期**：2026-05-11
- **决策依据**：LangSmith 在国内不可用（[phase-0-launch 决策 3 备注](../decisions/phase-0-launch.md)）；Langfuse 开源 + 自托管 + 数据不出境 + 与 LangGraph 集成成熟
- **背景**：M0 Stream A.9（指标）+ E.5（Langfuse middleware）+ G.7（Grafana 大盘）依赖此选型

---

## TL;DR

| 关注点 | 选择 | 部署 |
|--------|------|------|
| **Agent-specific trace**（LLM call、tool call、token usage） | **Langfuse** | 自托管 |
| **基础设施 trace**（HTTP / DB / Redis 跨服务）| **Tempo** + OpenTelemetry | 自托管 |
| **Metrics**（业务 + 技术）| **Prometheus** | 自托管 |
| **Logs**（结构化日志聚合） | **Loki** | 自托管 |
| **Dashboard / 告警 UI** | **Grafana** | 自托管 |
| **Error tracking**（M1 加） | **Sentry / GlitchTip** | M1 评估 |

---

## 1. 上下文

### Agent 工程的特殊可观测需求

普通后端的 trace 只到 HTTP 请求级别，但 Agent 需要：
- **每步 LLM 调用**：prompt / completion / token / latency / cost
- **每步 tool call**：tool name / args / result / duration
- **多步 reasoning chain 可视化**：能时间线展开整个 graph 执行
- **prefix cache 命中率**（参考 [research/05](../research/05-deerflow-deeper-scan.md)）
- **per-tenant / per-agent 成本聚合**

这些超出 Prometheus / Tempo 的能力范围，需要专门的 Agent 可观测平台。

### P0 关联

- P0 #10 结构化日志规范 — Loki 是消费方
- P0 #11 W3C Trace Context — Tempo + OpenTelemetry SDK
- P0 #12 指标体系 — Prometheus
- P0 #13 SLO/SLI 定义 — Grafana 大盘 + Prometheus alertmanager
- P0 #14 告警体系 — Grafana / alertmanager
- P0 #15 Agent-specific 可观测 — **Langfuse**（本 ADR 核心）

### 决策约束

- **LangSmith ❌**：国内不可用（Phase 0.1 决策 3 明确）
- **数据不出境**：合规要求，所有 trace / log / metric 必须留在内网
- **开源 / 自托管优先**：避免商业 SaaS 锁定
- 单人项目，运维负担需可控（首选有 Helm chart / docker-compose 的方案）

---

## 2. 决策

### 2.1 Langfuse 自托管

**版本**：Langfuse v3+（含原生 prefix cache 监控 + LangChain/LangGraph callback handler）

**部署**：
- M0：docker-compose（与开发栈一起起）
- M1+：Helm chart 部署到 K8s
- 后端：阿里云 RDS PostgreSQL（独立 schema 或独立实例，避开主库竞争）
- 对象存储（如启用持久化 trace blob）：阿里云 OSS（[ADR-0004](./0004-object-storage.md)）

**接入**：
- 在 `expert_work.runtime.middleware` 加 Langfuse `CallbackHandler`，挂到 LangGraph 的 `@Next` 锚点
- 每次 LLM 调用 / tool 调用自动上报到 Langfuse
- Langfuse SDK：`langfuse-python`

**关键能力**：
- Trace 可视化（时间线 + tree 视图）
- Per-trace token / cost 统计
- 用户反馈关联（👍/👎 → trace ID）
- Eval dataset 管理（与 Stream G.5 集成）

### 2.2 三件套 — Prometheus + Loki + Tempo + Grafana

标准 cloud-native 可观测栈：
- **Prometheus**：scrape 各服务 `/metrics`；按 retention 90 天保留
- **Loki**：结构化日志聚合；按 retention 30 天 + 冷归档到 OSS
- **Tempo**：分布式 trace 存储；trace_id 由 OpenTelemetry SDK 注入
- **Grafana**：统一 UI；M0 准备第一版"系统健康 + Agent 性能"大盘

**OpenTelemetry collector** 作为唯一上报入口（push 模型），路由 trace → Tempo，metric → Prometheus（remote_write），log → Loki。

### 2.3 数据流

```
Expert Work service
  ├─ structured log → otel-collector → Loki ──→ Grafana
  ├─ metric (/metrics scrape) ──── Prometheus ──→ Grafana
  ├─ trace (OTLP) → otel-collector → Tempo ───→ Grafana
  └─ Agent callback (langfuse SDK) ────────────→ Langfuse UI
```

trace_id 在 Langfuse trace 与 Tempo trace 之间共享，可点击 Langfuse 跳转到 Tempo 看底层 HTTP 调用。

### 2.4 告警通道

- Prometheus alertmanager + Grafana 统一告警源
- 路由：飞书机器人（P0 即时）+ 邮件（P1）+ 工单系统（P2）
- 具体路由配置在 Stream G.2（告警体系）

---

## 3. 后果

### 正向

- **数据不出境**：全部自托管，符合国内合规
- **开源生态**：Langfuse 活跃维护（v3 系列）+ Prometheus/Grafana 是行业标准
- **Agent + 系统两层 trace 联动**：通过 trace_id 串起来
- **零商业 license 成本**

### 负向 / 风险

- **运维负担**：5 个组件（Langfuse、Prometheus、Loki、Tempo、Grafana）+ otel-collector，单人维护需要靠 Helm chart / Operator
- **Langfuse v3+ 是 Java 后端**：与 Python 主栈不一致；但只是部署 artifact，不影响应用代码
- **存储成本**：Loki / Tempo 大量数据需要冷归档策略（与 OSS 配合）
- **学习曲线**：单人需熟悉 PromQL、LogQL、OTel 概念

### 验证手段（Stream A Verification + Stream E Verification）

- [ ] Service emit 结构化日志 → Loki 可查（按 trace_id 过滤）
- [ ] Service emit metric → Prometheus 可抓 → Grafana 显示
- [ ] LangGraph 一次 LLM 调用 → Langfuse trace 出现 → 关联 trace_id 在 Tempo 可见
- [ ] Prefix cache 命中率指标在 Langfuse 可见（> 80% 是 Stream E Exit 标准）

---

## 4. 备选方案

| 方案 | 否决理由 |
|------|---------|
| **LangSmith** | 国内不可用（Phase 0.1 决策） |
| **Helicone**（自托管 LLM 代理 + UI） | 主要面向 OpenAI 调用拦截；与 LangGraph 集成不如 Langfuse 深 |
| **Phoenix (Arize)** | 偏 ML evaluation；Agent trace 没有 Langfuse 强 |
| **自建 trace 表 + 自己写 UI** | 时间成本太高；不是单人项目能负担的 |
| **Datadog APM** | 商业 SaaS；国内不可用 |
| **阿里云 ARMS** | 与「Langfuse 是 Agent-specific」需求不匹配；可作为补充用于基础设施 trace（M1 评估） |

---

## 5. 落地引用

- **Stream A.7/A.8/A.9** 日志 + trace + 指标规范：在 `packages/expert-work-common/src/expert_work/common/` 实现
- **Stream E.5** Langfuse middleware 接入：在 `packages/expert-work-runtime/src/expert_work/runtime/middleware/langfuse.py`
- **Stream G.1/G.2** SLO + 告警体系：基于 Prometheus / alertmanager
- **Stream G.7** 第一版 Grafana 大盘：模板存 `deploy/grafana/dashboards/`
- **environments/{env}.yaml** 已声明 `observability.{langfuse,prometheus,tempo,grafana}` 各 host 字段
- **本地 dev** docker-compose 包含上述 5 个容器（Stream A 实施时落地）

---

## 6. Mini-ADR OBS-L1 — Langfuse 激活 + 入库前 PII 脱敏

- **状态**：✅ 已决策（2026-06-13）
- **背景**：HX-7 已把 Langfuse SDK adapter（`langfuse_sdk.py`）接线进 `control-plane/app.py`，但生产部署一直未启用——dev 跑的是 M0 内存桩 `RecordingLangfuseClient`，等于没开。本 Mini-ADR 把 Langfuse 从「代码就绪」推到「dev + 生产都真启用」，并补上启用前提：**trace 入库前的 PII 脱敏**。Langfuse 的 generation observation 存的是**完整 prompt + completion 明文**，生产流量里那就是全租户真实用户对话 —— 集中存进一个 ClickHouse 会绕过 Postgres 的 per-tenant RLS 治理，爆炸半径变成跨租户（呼应 HX-10 威胁模型：爆炸半径由跨租户决定，不由信任决定）。

### 决策（四项）

| # | 维度 | 选择 | 理由 |
|---|------|------|------|
| 1 | **范围** | dev + 生产**同 stack**，靠 mask 开关切，一并落地 | dev 跑测试数据本可不脱敏，但同一开关默认开省去「生产忘开」的风险面 |
| 2 | **PII 覆盖档** | **regex 扩展**：复用 `DefaultSecretRedactor`，新增 `PII_PATTERNS`（email / 手机 / 身份证 / 信用卡）；Presidio/NER 押后 | 平台内部调试场景，姓名/地址脱敏 ROI 低；业界警告「全脱毁调试值」（PII-Bench） |
| 3 | **注入路径** | **A 路：SDK `mask=` 构造参数** | 官方一等支持（已验 `langfuse>=3` 的 `Langfuse.__init__` 含 `mask`）；在 ingestion 层统一 mask input/output/metadata，**与中间件顺序无关**（catch-all），比 wrapper 双点接入少代码 |
| 4 | **默认开关** | `langfuse_pii_masking_enabled: bool = True`，**dev/生产都默认开** | fail-safe：漏配也是脱敏态，顶多调试值打折，绝不裸奔 PII |

### 机制

1. `make_langfuse_client(..., pii_masking_enabled=True)`：启用时构建一个 `DefaultSecretRedactor(patterns={**DEFAULT_PATTERNS, **PII_PATTERNS})`（secret + 对话 PII 全集），把 `mask=redactor.redact_tree` 传给 `Langfuse(...)`。
2. `redact_tree(data)` 复用既有 `_walk` 递归器（Mapping / list / tuple / str 全覆盖，非字符串叶子原样透传），是 `redact_text` 的结构化推广，丢弃 hit 计数以匹配 langfuse mask 的 `data→data` 契约。
3. ClickHouse 入库前，langfuse-worker 收到的每条 trace 的 input/output 都已是脱敏态。

### 非目标 / 边界

- **不改 A.7 audit 脱敏路径**：`PII_PATTERNS` 单独定义，audit 仍只跑 `DEFAULT_PATTERNS`（surgical）；audit 日志要不要一并加对话 PII 脱敏是独立后续项。
- **不引 NER / Presidio**：决策 2 明确押后；真遇到姓名/地址泄漏再立项。
- **服务端 Worker masking 回调**（自托管二道防线）不在本次范围 —— A 路 SDK mask 已在客户端统一脱敏，Worker 回调留作生产纵深加强的后续选项。

### 待验证（SE / 手动，CI 无真实例）

- [ ] langfuse v3 的 `mask` 调用时机 vs BaseMessage 序列化时机：确认 message-body 里的 PII **确实**被 mask（若 SDK 在序列化前用原始对象调 mask，`_walk` 会把 BaseMessage 整体透传而漏脱）。若漏，回退方案 = 在 `LangfuseSdkClient.start_span` 入口先把 messages 规整成 dict 再交给 SDK。
- [ ] 一次真 agent run → Langfuse UI 的 generation 里 prompt/completion 已脱敏。

### infra（dev 栈）

- 复用现有 `redis`（line 136）+ `minio`（S3 blob，ADR-0004）。
- 新增容器：`clickhouse` + `langfuse-web` + `langfuse-worker`（v3 自托管三件）。
- 生产部署（K8s/Helm）押到真要对生产开放可观测那天，本 Mini-ADR 只交付 dev 栈 + 应用层脱敏开关。
