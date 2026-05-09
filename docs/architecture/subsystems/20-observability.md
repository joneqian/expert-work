# 20 Observability — span/metric/log 命名规范、关键 dashboard、SLO

> 让"agent 跑通"升级为"产品级可运维"：所有跨服务调用走 W3C Trace Context、metric 命名统一前缀 `helix_*`、结构化日志强制字段、SLO/错误预算驱动发布冻结。

---

## 1. 职责 & 边界

### ✅ 做
- **三大支柱**：metric (Prometheus) / trace (OTel + Tempo) / log (Loki)
- **命名规范**：span / metric / log 字段的统一标准
- **强制 schema**：日志必填字段（含 redaction 中间件保证不带 PII）
- **W3C Trace Context 传播**：跨 Control Plane → Orchestrator → Sandbox → MCP → LLM Gateway 全链路
- **SLO/SLI 定义**：可用性、TTFT、冷启动、API 延迟
- **错误预算策略**：耗尽 → 自动冻结新 manifest 发布 24h
- **Dashboard 模板**：每个核心子系统一份 Grafana JSON
- **告警分级**（P0/P1/P2）+ 路由到 PagerDuty / 飞书

### ❌ 不做
- 不做业务可视化（agent 调用链、token 大盘） → M2 引入 Langfuse 或自建
- 不做用户反馈、A/B 报表 → [26 Eval Framework](./26-eval-framework.md)
- 不做应用层 unhandled exception 监控 → Sentry / GlitchTip 集成（M1 落地）
- 不做计费 chargeback → 业务运营层
- 不做日志全文搜索（明文 prompt/response 存 event_log，按需读）

---

## 2. 上下游依赖

```
所有服务 ──▶ OTel SDK ──▶ OTel Collector ──▶ ┬─▶ Tempo（trace）
                                                ├─▶ Prometheus（metric remote-write）
                                                └─▶ Loki（log）
                                                          │
                                                          ▼
                                                   Grafana（统一展示）
                                                          │
                                                          ▼
                                                   Alertmanager ──▶ PagerDuty / 飞书
```

| 依赖方向 | 子系统 | 关系 |
|---------|--------|------|
| 上游 | 所有服务 | 强制注入 OTel SDK + structured logger |
| 下游 | OTel Collector / Prometheus / Loki / Tempo / Grafana | 自托管或托管服务 |
| 横切 | [17 Audit Log](./17-audit-log.md) | audit 事件部分进 Loki（合规视角不同：audit 偏 who/what，log 偏 how） |
| 横切 | PII Redactor 中间件 | 写 log/trace 前过 redactor，按 `tenant_config.pii_fields` 脱敏 |

---

## 3. 数据模型 / 状态机

不是有状态服务，本子系统主要是**规范**。下面是**强制约束**的物化形式。

### 3.1 命名前缀

| 类型 | 前缀 | 例 |
|------|------|-----|
| span name | `helix.{component}.{action}` | `helix.orchestrator.session_run` |
| metric name | `helix_{component}_{noun}_{unit}` | `helix_llm_latency_seconds` |
| log logger | `helix.{component}` | `helix.sandbox.pool` |
| label key | snake_case | `tenant`, `agent`, `trace_id` |

**关键决策**：**所有 metric 严格走 `helix_*` 前缀**，与 vendor 中间件库的命名隔离；防止 LangGraph / langchain 自带 metric 与我们混淆。

### 3.2 SLO 表（M1 目标）

```sql
CREATE TABLE slo_definition (
    id            BIGSERIAL PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,             -- 'session_ttft_p95'
    component     TEXT NOT NULL,                    -- orchestrator / sandbox / control_plane / global
    metric        TEXT NOT NULL,                    -- 'helix_session_ttft_seconds'
    target_pct    NUMERIC(5,2) NOT NULL,            -- 99.0 表示 99% 时间满足
    threshold     NUMERIC(10,3) NOT NULL,           -- 1.5（秒）
    window_days   INT NOT NULL DEFAULT 30,
    alert_burn_rate NUMERIC(4,2) NOT NULL DEFAULT 14.4,  -- 错误预算燃烧率告警阈值
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## 4. 关键接口

### 4.1 OTel 初始化（每个服务统一）

```python
# packages/helix-common/src/helix/common/otel.py
from opentelemetry import trace, metrics
from opentelemetry.sdk.resources import Resource

def init_otel(service_name: str) -> None:
    resource = Resource.create({
        "service.name": service_name,
        "service.namespace": "helix",
        "service.version": __version__,
        "deployment.environment": os.environ["ENV"],
    })
    # exporter 走 OTel Collector（同 pod sidecar）
    ...

def with_agent_span(name: str, **attrs):
    """统一的业务 span 装饰器，强制注入 tenant/agent/trace_id。"""
```

### 4.2 结构化日志

```python
# packages/helix-common/src/helix/common/log.py
logger = get_logger("helix.orchestrator")

logger.info("session.start", extra={
    "tenant": ctx.tenant,
    "session_id": ctx.session_id,
    "agent": ctx.agent_name,
    "agent_version": ctx.agent_version,
    "trace_id": ctx.trace_id,
    "span_id": ctx.span_id,
})
```

底层 formatter 强制：
- JSON 格式
- ISO 8601 时间戳
- 必填字段缺失 → 写 `WARNING helix.log_schema_violation` 但不阻断业务
- 经过 redactor 中间件（按 `tenant_config.pii_fields`）

---

## 5. 算法 / 关键决策

### 5.1 Span 命名规范

`helix.{component}.{action}`，component 必须来自固定枚举：

| component | 说明 |
|-----------|------|
| `control_plane` | manifest CRUD、user actions |
| `orchestrator` | graph 调度、LLM/tool 调用 |
| `sandbox` | 容器生命周期、命令执行 |
| `credential_proxy` | secret 注入、出站 HTTP |
| `mcp_gateway` | MCP server 路由 |
| `llm_gateway` | LLM provider 调用 |
| `memory` | 短期/长期记忆读写 |
| `subagent` | 子 agent spawn / lifecycle |
| `hitl` | 暂停 / 审批流程 |
| `eval` | 评测运行 |

例：

```
helix.orchestrator.session_run
  ├─ helix.orchestrator.llm_call
  │    └─ helix.llm_gateway.provider_request
  ├─ helix.orchestrator.tool_call
  │    ├─ helix.credential_proxy.inject
  │    └─ helix.sandbox.exec
  └─ helix.orchestrator.checkpoint
```

每个 span 必有 attrs：`tenant`, `agent`, `agent_version`, `session_id`。

### 5.2 关键 metric

**业务指标**

```
helix_session_duration_seconds{tenant,agent,version,outcome}        histogram
   # outcome = success / failed / cancelled / timeout
helix_session_ttft_seconds{tenant,agent}                            histogram
helix_llm_tokens_total{tenant,agent,provider,model,direction}       counter
   # direction = input | output | cache_creation | cache_read
helix_llm_latency_seconds{tenant,provider,model}                    histogram
helix_tool_call_duration_seconds{tenant,tool_type,outcome}          histogram
   # tool_type = builtin | http | mcp | sandbox | subagent
helix_subagent_total{tenant,parent_agent,status}                    counter   # 24
helix_subagent_depth{tenant,parent_agent}                           histogram # 24
helix_quota_exceeded_total{tenant,dimension,reason}                 counter   # 16；统一用 _exceeded_，含 reason label
helix_auth_decisions_total{result,reason}                           counter
   # result = grant | deny
helix_hitl_pending_total{tenant,agent}                              gauge     # 25
helix_hitl_decision_duration_seconds{tenant,outcome}                histogram # 25
helix_eval_gate_decision_total{tenant,suite,decision}               counter   # 26
helix_eval_regression_total{tenant,suite}                           counter   # 26
```

**基础设施指标**

```
helix_sandbox_cold_start_seconds{image,runtime}                     histogram
helix_sandbox_pool_size{image,state}                                gauge     # 14
helix_sandbox_acquire_latency_seconds{result}                       histogram # 14；result=ok|cold_miss|timeout
helix_checkpoint_write_duration_seconds{tenant,result}              histogram
helix_event_log_append_duration_seconds                             histogram # 标准化命名（替代 audit_write_latency_seconds）
helix_resume_total{tenant,outcome}                                  counter   # 19；outcome=ok|version_mismatch|force|rejected
helix_pg_connection_pool_in_use{db}                                 gauge     # 23 共用同一命名
helix_redis_command_duration_seconds{cmd}                           histogram # 13/16 调 Redis 时 emit
helix_network_egress_meta_attempt_total{tenant}                     counter   # 21；安全 P0 信号，任何 > 0 都告警
helix_dr_backup_age_seconds{asset_type}                             gauge     # 22；核心 RPO SLI
```

**关键决策**：**label cardinality 严格管控**——`agent` 是 enum（manifest name），`session_id` / `trace_id` 不进 label（高基数会爆 Prometheus）。session_id 留在 trace 与 log。

### 5.3 日志必填字段

所有 INFO 及以上日志强制：

| 字段 | 类型 | 说明 |
|------|------|------|
| `timestamp` | ISO8601 | 由 logger 自动注入 |
| `level` | enum | DEBUG/INFO/WARNING/ERROR/CRITICAL |
| `logger` | str | `helix.{component}` |
| `message` | str | 事件名（snake_case，非自由文本） |
| `tenant` | str | 必填，contextvar 注入 |
| `trace_id` | str | W3C |
| `span_id` | str | W3C |
| `service` | str | service name |
| `env` | str | dev/staging/prod |

可选但常见：`session_id`, `agent`, `agent_version`, `actor_id`。

**关键决策**：**`message` 必须是 snake_case 的事件 ID**（如 `session.start`、`tool.call.start`），不允许 `f"started session {x}"`。事件 ID 是查询 key，自由文本进 `extra` 字段。

### 5.4 SLO 与错误预算

**M1 目标**：

| SLO | 目标 | 测量 |
|-----|------|------|
| 控制平面 API 可用性 | 99.9% / 30d | `1 - rate(http_5xx) / rate(http_total)` |
| 控制平面 API P99 延迟 | < 200ms | `helix_http_request_duration_seconds_bucket` |
| Session TTFT P95 | < 1.5s | `helix_session_ttft_seconds` |
| Sandbox 冷启动 P95 | < 3s (M0) / < 500ms (M1 warm pool) | `helix_sandbox_cold_start_seconds` |
| Durable resume 成功率 | > 99% | `helix_resume_total{outcome="ok"} / total` |

**错误预算策略**：

```
error_budget = 1 - SLO target
# 99.9% → 0.1% → 30d 内 ~43min 预算

burn_rate(1h)  > 14.4  → P0 告警（5min 烧完 1h 预算）
burn_rate(6h)  > 6     → P1 告警
burn_rate(24h) > 3     → P2 告警

错误预算耗尽 → CI 自动冻结新 manifest 发布 24h（写 audit）
```

### 5.5 Agent 生命周期完整追踪（E3）

> 解决"业务方想从 session_id 拉完整时间线 / 长会话 + subagent + HITL 全可见"的端到端追踪需求。
> 关键决策：**长会话不强行同 trace**——subagent / HITL pause / Durable resume 用**新 trace + Span Link**关联，
> 通过 `session_id` 在查询层聚合（理由：长 pause 期 OTel TTL 失效，必须新 trace；用 Link 维持拓扑可见）。

**session root span**

- 每个 session 对应一个根 span：`helix.session.run`
- TTL：7 天（足以覆盖跨节点漂移、HITL 多日 pause）
- 必填 attrs：`tenant`, `session_id`, `agent`, `agent_version`, `root_span_id`
- 起止：session 创建时 start，正常 complete / cancelled / failed / timeout 时 end + 设置 status

**子 trace 拆分规则**

| 触发 | 处理 | 关联方式 |
|------|------|---------|
| `subagent.spawn`（[24](./24-subagent-execution.md)） | child 用**新 trace** | parent span 持 child trace 的 `Span Link`；child root span attrs 含 `parent.session_id` / `parent.span_id` |
| HITL pause（[25](./25-hitl.md)） | resume 时用**新 trace** | resume span 持 pause span 的 `Span Link`；attrs `helix.hitl.pause_span_id` |
| Durable resume（[19](./19-durable-execution.md) crash 接管 / 长 pause 后） | 接管 worker 用**新 trace** | resume span 持 last checkpoint span 的 `Span Link`；attrs `helix.durable.resume_from_checkpoint_id` |

**跨 trace 拼接**：所有同 `session_id` 的 trace 通过 Tempo 索引聚合（按时间戳排序拼出完整时间线）。

**业务查询 API**

```
GET /v1/sessions/{session_id}/trace-tree
     → {
         "session_id": "...",
         "traces": [
           {"trace_id": "...", "kind": "root|subagent|hitl_resume|durable_resume",
            "parent_link": null | "<trace_id>", "started_at": "...", "ended_at": "...",
            "critical_events": [ { "span_id": "...", "name": "helix.hitl.pause", "attrs": {...} } ]
           }, ...
         ]
       }
```

权限：等同 session 读权限（按租户隔离 + RBAC `session:read`）。

**Tempo 必索引字段**

`session_id` / `agent_name` / `error.class` 强制索引（支持业务侧反查）。

**关键事件标注**

下列 span 必须设置 `helix.critical=true`，dashboard 高亮 + UI 时间线突出：

| 事件 | 来源 | span 名 |
|------|------|---------|
| HITL 触发 | 25 | `helix.hitl.interrupt` |
| 模型降级触发 | 10 | `helix.llm_gateway.fallback` |
| Quota 拒绝 | 16 | `helix.quota.deny` |
| Subagent spawn | 24 | `helix.subagent.spawn` |
| Sandbox 驱逐 | 14 | `helix.sandbox.evict` |
| Durable resume | 19 | `helix.durable.resume` |

**session 异常终止**

`helix.session.run` 根 span status **必须**置 ERROR + reason（`crash` / `oom` / `cancelled` / `timeout` / `unrecoverable_error`），
并写 attr `helix.session.exit_reason`。

**replay 时 trace**

副作用工具被 idempotency_key 命中、跳过实际 execute 时，仍 emit 新 span（不 silent），但 attribute 加 `helix.replayed=true`，
便于查询时区分"实际发生" vs "replay 占位"。

**UI（admin web）**

提供「session lifecycle 时间线视图」：
- 瀑布图：root trace + 子 trace 按时间排序，子 trace 用不同颜色标识 kind
- 关键事件标注：critical=true 的 span 在时间线上以图标标注
- 子 trace 跳转链接：点击切换到 Tempo 详情视图
- 异常终止徽章：root status=ERROR 时顶部显示 reason

### 5.6 Dashboard 矩阵

每个核心组件一份 Grafana JSON（存 `tools/observability/dashboards/`）：

| Dashboard | 关键 panel |
|-----------|----------|
| `01-overview` | 全局可用性、QPS、错误率、租户 top10 |
| `02-orchestrator` | session 生命周期分布、LLM tokens、tool 调用图 |
| `03-sandbox` | pool 水位、冷启动延迟、驱逐原因分布 |
| `04-llm-gateway` | provider 失败率、断路器状态、token / $ |
| `05-control-plane` | API 延迟分布、用户操作热区 |
| `06-tenant-{tenant_id}` | 单租户视图：QPS、cost、error |
| `07-slo` | 各 SLO 燃烧率、剩余预算 |

### 5.7 告警分级

| 级别 | 路由 | 响应 SLA | 例 |
|------|------|---------|-----|
| P0 | PagerDuty + 飞书 oncall | 5 min | 控制平面不可用、Postgres 主挂 |
| P1 | PagerDuty | 30 min | LLM provider 全部失败、SLO 14.4 burn rate |
| P2 | 飞书告警群 | 24h | 错误预算 24h burn、单 tenant 异常飙升 |
| P3 | 邮件周报 | 周 | 容量趋势、cost 异常 |

每个告警必须有 runbook 链接（`docs/runbooks/{alert_name}.md`）。

---

## 6. 失败模式 & 缓解

| 失败模式 | 影响 | 缓解 |
|---------|------|------|
| OTel Collector 不可达 | trace/metric/log 全断 | SDK 配 retry buffer（10MB）+ batch；buffer 满后丢弃最旧 + WARNING |
| Prometheus remote-write 拥堵 | metric 滞后 | 提高 batch、降低 push 频率（30s）；本地 ring buffer |
| Loki 写入失败 | 日志丢失 | sidecar 保留磁盘缓冲 24h；超出按 FIFO 丢 |
| 高基数 label 爆 Prometheus | 查询慢 / OOM | label cardinality 检查 CI gate（regex 检查 metric definition） |
| 日志泄漏 PII | 合规违规 | redactor 中间件强制；CI 跑 PII detector 扫描 fixture log；定期审计 |
| trace 丢失 parent span（context 不传播） | 调用链断 | 跨服务调用强制 `traceparent` header；CI 集成测试覆盖 |
| 告警风暴 | oncall 疲劳 | Alertmanager group_by + inhibit + silence；每周回顾删掉低价值告警 |
| dashboard 慢查询 | 查询超时 | 大 panel 用 recording rule 预聚合；不直接查原始 metric |
| dev 环境告警被生产看到 | 噪音 | 强制 `env` label；告警路由按 env 分组 |

---

## 7. 可观测性

（本子系统是 observability 自身，meta 层）

| 自监控 metric | 说明 |
|---------------|------|
| `helix_otel_export_failed_total{type}` | export 失败计数（type=trace/metric/log） |
| `helix_log_schema_violation_total` | 日志缺必填字段次数 |
| `helix_redaction_applied_total{tenant,field}` | redactor 命中次数（健康度） |
| `helix_slo_burn_rate{slo}` | recording rule 计算的燃烧率 |
| `helix_alert_active_total{severity}` | 当前活跃告警数 |

---

## 8. 安全考虑

| 攻击面 | 防御 |
|--------|------|
| 日志含 secret/PII | redactor 中间件默认拦截；secret 字段 `*` 完全替换；PII 按 `tenant_config.pii_fields` 哈希或截断 |
| 跨租户日志可读 | Loki 按 `tenant` label 强制 RBAC（Grafana datasource per tenant）；查询语句注入 `{tenant="..."}` |
| trace 中 prompt 内容泄漏 | span attrs 不存 prompt body；只存 hash 与长度；body 进 event_log（受租户隔离） |
| metric label 含 user 输入 | 严禁；CI 静态检查 `Counter().labels(...)` 中的 label 必须是常量 |
| Grafana 公网暴露 | 内网 only + SSO；公开 dashboard 走只读快照 |
| 告警内容含敏感数据 | 告警模板限制为 metric 值 + tenant + service，不带 prompt/PII |

**关键决策**：**redaction 在 SDK 层而非 Collector 层**——SDK 拦截源头，避免 Collector 故障时绕过；Collector 再做一遍兜底。

---

## 9. M0 / M1 / M2 演进

### M0（5-7 周）—— 基础 trace + 简单 dashboard
- OTel SDK 接入所有服务（trace + metric）
- Loki 接入 stdout JSON 日志
- 命名规范文档化 + CI lint（regex 检查 metric/span 命名）
- Grafana 基础 dashboard：overview + orchestrator + sandbox（3 个）
- 简单告警：5xx 率、Postgres 连接耗尽

### M1（6-8 周）—— 完整 SLO + 错误预算 + 告警体系
- 完整 6 个 dashboard 上线
- SLO 定义入库 + recording rule 自动计算 burn rate
- Alertmanager 分级路由（PagerDuty + 飞书）
- runbook 库初版（每个告警一篇）
- redactor 中间件正式上线 + CI PII 扫描
- 错误预算冻结发布机制

### M2（4-6 周）—— Agent 视角观测 + 业务大盘
- 集成 Langfuse（开源）做 agent 链路可视化（token 分布、tool 调用图）
- session 时间线 UI（基于 event_log + trace）
- 业务大盘（每租户 cost、quality、QPS）
- session 回放（time travel）：基于 event_log 重放
- 慢查询 / 火焰图（py-spy）

### M3 —— 跨集群 + 异常根因
- 多 region trace 联邦（Tempo federation）
- 自动异常根因（基于 trace + metric correlation）
- 容量预测模型

---

## 10. 开放问题

1. **Langfuse 自建 vs 托管**：自建 Postgres 多一份压力；托管 SaaS 涉及租户数据出境。倾向 M2 自建。
2. **trace 采样率**：100% 太贵，按 tenant 配置 vs 全局固定？倾向 99% 错误必采 + 业务 1% 采样 + 可配 override。
3. **业务 PII 审计**：redaction 命中率监控告警阈值多少？倾向 < 0.01%（命中说明业务侧没按 schema 写）。
4. **跨集群 trace 联邦**：M3 用 Tempo 还是切 Honeycomb？取决于规模。
5. **session 回放权限**：admin 才能看明文 vs 业务 owner 也可看？倾向 RBAC 细分 `session:replay:read`。
