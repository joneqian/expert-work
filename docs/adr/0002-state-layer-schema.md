# ADR-0002：状态层 schema — event_log 与 audit_log 分表

- **状态**：✅ 已决策
- **日期**：2026-05-11
- **决策依据**：会话事件与运维审计的访问模式、保留周期、安全姿态差异大；分表是产品级最低成本选择
- **背景**：M0 Stream A.1（Postgres schema）+ A.4（audit_log）需要落地状态层表设计

---

## TL;DR

**event_log（会话事件）与 audit_log（运维审计）使用两张分离的表，不合并为单一日志表。**

两者在 5 个维度差异显著：写入频率、查询模式、保留周期、加密 / WORM 要求、消费者。合并会导致索引膨胀、归档策略冲突、合规审计困难。

---

## 1. 上下文

### 两类事件的本质差异

| 维度 | event_log | audit_log |
|------|-----------|-----------|
| **来源** | Orchestrator harness loop / LLM 调用 / 工具调用 / 沙盒事件 | 控制面 admin 动作（manifest 修改、secret 访问、用户登录、agent 启停）|
| **写入频率** | 高（每会话 10-1000 条；prod 总量 ≥5000 evt/s）| 低（每秒数条到数十条）|
| **查询模式** | 按 thread_id / session_id 时间序回放（replay）；多为最近窗口 | 按时间 + actor + resource 检索；为合规审计 / 取证 |
| **保留周期** | 默认 90 天 + 半年后归档 S3 | 7 年 WORM（合规要求）|
| **不可篡改** | 弱要求（append-only 即可）| 强要求（append-only + WORM 桶 + tamper detection）|
| **加密 / PII** | 经过 PII redactor 中间件处理 | 不含用户内容，但可能含 secret reference / IP / user identity |
| **消费者** | LangGraph PostgresSaver replay / Trace UI / Eval 重放 | 合规 / 安全 / 法务 / 内审 |

### P0 关联

- P0 #5（操作审计日志，与 event_log 分表）— **本 ADR 直接落实**
- P0 #6（审计日志不可篡改）— 由 audit_log 表 + WORM 桶共同实现
- P0 #18（event_log 冷归档）— 仅适用于 event_log

---

## 2. 决策

**event_log 与 audit_log 设计为两张分离的 Postgres 表，schema 如下。**

### event_log（位于 Stream A.1）

```
TABLE event_log (
  id                BIGSERIAL PRIMARY KEY,
  thread_id         UUID NOT NULL,
  session_id        UUID,
  tenant_id         UUID NOT NULL,         -- 多租户 RLS 字段
  seq               BIGINT NOT NULL,       -- 单调递增（vendor DeerFlow 模式）
  event_type        TEXT NOT NULL,         -- e.g. llm_call / tool_call / sandbox_event
  payload           JSONB NOT NULL,        -- 已经 PII redactor 处理
  trace_id          TEXT,                  -- W3C Trace Context
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

  UNIQUE (thread_id, seq)
);

CREATE INDEX event_log_thread_id_idx ON event_log (thread_id, seq);
CREATE INDEX event_log_tenant_created_idx ON event_log (tenant_id, created_at DESC);

-- RLS 策略：仅本 tenant 可读
ALTER TABLE event_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY event_log_tenant_isolation ON event_log
  USING (tenant_id = current_setting('app.current_tenant_id')::uuid);
```

补充 vendor DeerFlow 既有字段：参考 `packages/helix-runtime/src/helix_agent/runtime/event_log/` 实现时按需扩展。

### audit_log（位于 Stream A.4）

```
TABLE audit_log (
  id                BIGSERIAL PRIMARY KEY,
  occurred_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  actor_type        TEXT NOT NULL,         -- user / service / system
  actor_id          TEXT NOT NULL,
  tenant_id         UUID NOT NULL,
  action            TEXT NOT NULL,         -- e.g. manifest.create / secret.read / agent.delete
  resource_type     TEXT NOT NULL,
  resource_id       TEXT,
  outcome           TEXT NOT NULL,         -- success / denied / error
  reason            TEXT,
  source_ip         INET,
  trace_id          TEXT,
  metadata          JSONB,                 -- 不含用户内容，仅元信息

  -- 不可篡改保证：禁止 UPDATE / DELETE
  CONSTRAINT audit_log_no_updates CHECK (true)
);

CREATE INDEX audit_log_tenant_time_idx ON audit_log (tenant_id, occurred_at DESC);
CREATE INDEX audit_log_actor_time_idx ON audit_log (actor_id, occurred_at DESC);
CREATE INDEX audit_log_resource_idx ON audit_log (resource_type, resource_id);

-- DB 级保护：撤销所有 update/delete 权限
REVOKE UPDATE, DELETE ON audit_log FROM PUBLIC;
-- 应用层 role 也不持有 UPDATE/DELETE 权限（只能 INSERT/SELECT）
```

`audit_log` 还会通过 D.1（WORM 备份）周期性导出到 阿里云 OSS Object Lock 桶，作为 7 年合规副本。

### checkpoint 表

LangGraph PostgresSaver 自己管理 `checkpoint_blobs` / `checkpoint_writes` 等表，遵循 vendor schema 不动。

### thread_meta 表

vendor 自 DeerFlow，包含 `thread_id / session_id / tenant_id / created_by / status`，schema 在 A.2 vendor 时落地。

---

## 3. 后果

### 正向

- **保留策略分离**：event_log 90 天 + 冷归档；audit_log 7 年 WORM，互不干扰
- **索引精简**：每张表索引只为自己的查询模式设计
- **合规边界清晰**：合规审计 / 取证只看 audit_log，不被 event 干扰
- **写入热点隔离**：event_log 高频写不会拖慢 audit_log
- **权限分离**：应用层 service account 对 audit_log 仅 INSERT/SELECT；对 event_log 可以 INSERT/SELECT/（受限的）UPDATE

### 负向 / 风险

- **应用代码需要分别写两张表**：通过 helix-runtime + helix-persistence 提供统一封装减轻
- **跨表关联查询稍贵**：但实际很少需要（trace_id 提供软关联即可）
- **schema migration 翻倍**：Alembic 处理；M1-H zero-downtime migration 规范覆盖

### 监控指标

- `event_log_write_throughput`（目标 ≥ 5000 evt/s，M1 性能基准）
- `audit_log_immutability_violations`（≥ 1 即 P0 告警）
- `audit_log_worm_backup_lag`（备份到 OSS Object Lock 桶的延迟，> 5min P1）

---

## 4. 备选方案

| 方案 | 否决理由 |
|------|---------|
| **单一 unified_log 表 + event_class 字段区分** | 索引膨胀（同时支持高频回放 + 慢速合规检索）；保留策略冲突；不易做权限隔离 |
| **event_log 同时承担审计** | event_log 经过 PII redactor，会丢失审计需要的原始信息（如 secret reference） |
| **audit_log 写第三方 SaaS（Datadog Audit Logs 等）** | 数据出境合规问题；国内无可靠选项；自托管更便宜 |
| **audit_log 用消息队列（Kafka） + S3** | M0 不引入额外基础设施；表 + 周期 WORM 备份足够；Kafka 留 M1+ 评估 |

---

## 5. 落地引用

- **Stream A.1** event_log + thread_meta + checkpoint schema 实现位置：`packages/helix-persistence/src/helix_agent/persistence/`
- **Stream A.4** audit_log schema + 应用层封装：同上
- **Stream D.1** audit_log WORM 备份到 OSS Object Lock 桶：`packages/helix-runtime/...` 或独立 archival job
- **Stream H.8（新 G.8）** event_log 冷归档 pipeline：独立 archival job
- **Stream C.4** Postgres RLS 策略下放 event_log + audit_log 的租户隔离
