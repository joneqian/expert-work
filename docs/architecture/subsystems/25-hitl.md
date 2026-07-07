# 25 Human-in-the-Loop — interrupt、审批 UX、超时回退、外部审批通道

> 把"agent 自主跑完所有动作"升级为"敏感动作必须人工审批后才能执行"。核心：LangGraph `interrupt()` 暂停 → control plane 审批清单 → 多通道审批（Web/Slack/邮件）→ 超时按 default_action 回退 → 全程审计。

---

## 1. 职责 & 边界

### ✅ 做
- manifest 声明 `policies.require_approval` 触发模式（pattern: `tool:send_email` / `tool:delete_*` 等）
- LangGraph `interrupt()` 暂停 graph + 写 PAUSED 状态
- 暴露 pending list / approve / reject API（Control Plane）
- 多通道审批：内置 web UI（M2 起）、Slack bot（M2 plugin）、邮件回调（M2）、企业微信（M3）
- 超时回退：默认 24h，超时按 `default_action: reject | approve | escalate`
- 审批者识别：JWT 强制，actor_id 写 [17 Audit Log](./17-audit-log.md)
- 单次执行级粒度（不允许"信任此 agent 24h"）
- 紧急绕过 `force_resume`（admin 权限 + 审计标 `bypassed=true`）
- 一次性 token 防重放

### ❌ 不做
- 不做工作流编排（plan-execute / DAG）→ orchestrator/workflow
- 不做明文 prompt/response 显示策略（由 audit & observability 各自处理）
- 不做"批量信任规则"（M3+ 议题，需安全方案）
- 不做无 manifest 声明的隐式 HITL（manifest 不声明就不会触发）
- 不做审批结果的 LLM 解读（审批是结构化结果：approved/rejected）

---

## 2. 上下游依赖

| 依赖方向 | 子系统 | 关系 |
|---------|--------|------|
| 上游 | Orchestrator | 调用 `should_interrupt()` 判定；触发 LangGraph `interrupt()` |
| 上游 | Control Plane API | 暴露 pending list、approve/reject 接口 |
| 上游 | Admin UI / Slack bot / 邮件 callback | 审批入口 |
| 横切 | [15 AuthN/AuthZ](./15-authn-authz.md) | 审批者身份验证 + RBAC（`hitl.approve` 权限） |
| 横切 | [17 Audit Log](./17-audit-log.md) | 所有 pending / approve / reject / timeout / force_resume 写审计 |
| 横切 | [19 Durable Execution](./19-durable-execution.md) | interrupt → pause；approve → resume |
| 横切 | [20 Observability](./20-observability.md) | pending 数量、超时率、审批延迟分布 |
| 横切 | [24 Sub-Agent](./24-subagent-execution.md) | child HITL 触发后 lead 也 PAUSED 等待 |

---

## 3. 数据模型 / 状态机

### 3.1 状态机

```
                       ┌─────────┐
                       │ PENDING │   等待审批
                       └────┬────┘
                            │
        ┌──────────────────┬┴───────────────┬───────────────┐
        │                  │                │               │
     approve            reject           timeout         force_resume
        │                  │                │               │
        ▼                  ▼                ▼               ▼
   APPROVED            REJECTED         （按 default_action）  BYPASSED
                                       APPROVED|REJECTED|ESCALATED
```

- **PENDING**：interrupt 触发，等待人工
- **APPROVED**：审批通过，graph 从 interrupt 点继续
- **REJECTED**：审批拒绝，graph 走 fallback path 或终止
- **ESCALATED**：超时升级到上级审批人；进入新一轮 PENDING（生成新 hitl_request）
- **BYPASSED**：admin 强制绕过（审计高亮）

### 3.2 Postgres DDL

```sql
CREATE TABLE hitl_request (
    id                UUID PRIMARY KEY,                  -- uuid7
    tenant_id         TEXT NOT NULL,
    session_id        UUID NOT NULL,
    agent_name        TEXT NOT NULL,
    agent_version     TEXT NOT NULL,
    trace_id          TEXT NOT NULL,
    interrupt_node    TEXT NOT NULL,                     -- LangGraph node name
    trigger_pattern   TEXT NOT NULL,                     -- 'tool:send_email' / 'cost:>10usd' / ...
    action_summary    TEXT NOT NULL,                     -- '即将向 user@x.com 发送邮件，主题：...'
    action_payload    JSONB NOT NULL,                    -- 完整动作参数（明文，受租户隔离）
    state             TEXT NOT NULL,                     -- PENDING/APPROVED/REJECTED/ESCALATED/BYPASSED
    requested_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    responded_at      TIMESTAMPTZ,
    timeout_at        TIMESTAMPTZ NOT NULL,
    timeout_action    TEXT NOT NULL,                     -- reject / approve / escalate
    approver_id       TEXT,                              -- JWT subject
    approver_role     TEXT,                              -- 审批时的角色快照
    approver_channel  TEXT,                              -- web | slack | email | force
    rejection_reason  TEXT,
    one_time_token    TEXT NOT NULL,                     -- 用于 email/slack 链接的一次性 token
    token_consumed_at TIMESTAMPTZ
);
CREATE INDEX ON hitl_request (tenant_id, state, requested_at);
CREATE INDEX ON hitl_request (session_id);
CREATE INDEX ON hitl_request (timeout_at) WHERE state = 'PENDING';
```

### 3.3 Pydantic schema

```python
class HITLRequest(BaseModel):
    id: UUID
    tenant: str
    session_id: UUID
    agent: str
    interrupt_node: str
    trigger_pattern: str
    action_summary: str                 # 短文本：审批 UI 一眼看懂
    action_payload: dict                # 完整参数；UI 折叠显示
    state: Literal["PENDING","APPROVED","REJECTED","ESCALATED","BYPASSED"]
    requested_at: datetime
    timeout_at: datetime
    timeout_action: Literal["reject","approve","escalate"]

class HITLDecision(BaseModel):
    request_id: UUID
    decision: Literal["approve","reject"]
    rejection_reason: str | None = None
    override_payload: dict | None = None    # 审批人可调整动作参数（如改收件人）
```

---

## 4. 关键接口

### 4.1 Manifest（声明触发条件）

```yaml
spec:
  policies:
    require_approval:
      - pattern: "tool:send_email"
        timeout_s: 86400                   # 24h 默认
        default_action: reject
        approvers:
          roles: ["operator", "admin"]    # 任一角色即可批
          channels: ["web", "slack"]
      - pattern: "tool:delete_*"           # 通配
        timeout_s: 3600
        default_action: reject
        approvers:
          roles: ["admin"]
      - pattern: "cost:>10usd"             # 单次工具调用 cost 超阈值
        timeout_s: 7200
        default_action: escalate
        escalate_to: ["security_lead"]
```

### 4.2 Python（orchestrator 集成）

```python
class HITLGate:
    async def maybe_interrupt(self, tool_call: ToolCall, ctx: AgentContext) -> InterruptDecision: ...
    async def wait_for_decision(self, request_id: UUID, timeout_s: int) -> HITLDecision: ...
    async def force_resume(self, request_id: UUID, *, actor_id: str, reason: str) -> None: ...
```

### 4.3 HTTP API（Control Plane）

```
GET  /v1/hitl/pending?tenant=...&agent=...
       → list[HITLRequest]                  # 当前用户可审批的列表

POST /v1/hitl/{request_id}:approve
       Body: HITLDecision
       Auth: JWT (role ∈ approvers.roles)
       → 200 {state: "APPROVED"}

POST /v1/hitl/{request_id}:reject
       Body: HITLDecision
       → 200

POST /v1/hitl/{request_id}:force_resume
       Body: {reason: "..."}
       Auth: JWT (admin)
       → 200 {bypassed: true}

GET  /v1/hitl/{request_id}/by_token?token=<one_time_token>
       → 渲染审批页面（邮件/Slack 链接的 landing）
```

### 4.4 Slack bot

```
/Expert Work approve <request_id>           # slash command
/Expert Work reject <request_id> reason="..."
/Expert Work list                           # 当前 oncall 待办
```

bot 内部调相同 HTTP API；Slack user → tenant user 映射来自 [15 AuthN/AuthZ](./15-authn-authz.md) 的 `external_identity` 表。

---

## 5. 算法 / 关键决策

### 5.1 触发判定

orchestrator 的 ToolNode wrapper 在每次工具调用前问 `HITLGate.maybe_interrupt`，按 manifest `require_approval` 列表 glob 匹配：`tool:*`、`cost:>NUSD`（估算本次调用 cost）、`data:contains_pii`（调 PII detector）；任一命中 → 触发 interrupt。

**关键决策**：**触发判定在工具调用之前**——副作用还没发生，审批才有意义；如果在结果上判定，伤害已造成。

### 5.2 暂停与恢复（与 19 集成）

interrupt 流：写 `hitl_request state=PENDING` → 写 event_log → LangGraph `interrupt()` → `durable_thread_meta.state=PAUSED, pause_reason=hitl_required` → flush checkpoint → 多通道通知（web SSE + Slack + email）。

approve 流：校验 JWT + role + tenant + state==PENDING → 写 state=APPROVED + approver_id → 写 audit → 触发 [19 durable.resume](./19-durable-execution.md)，LangGraph 从 interrupt 点继续；若 approver 给了 `override_payload`，用其覆盖原 tool args。

**resume 幂等性（与 [19 § 4.1](./19-durable-execution.md) 协议对齐）**：

`durable.resume(thread_id)` 接口本身**幂等**——server 端 `pg_advisory_xact_lock(hash(thread_id))` 防并发，重复调用直接返回上次 resume 的结果（不二次推进 graph）；幂等窗口与 thread 终态对齐。HITL 这一层的"一次性 token + `state==PENDING` 条件 UPDATE"（[§ 5.5](#55-审批者识别--一次性-token) / [§ 5.3](#53-超时回退)）防的是**审批通道侧的 webhook 回放**（同一邮件链接被点两次、Slack 回调重投递），与 19 的 advisory lock 是**两层独立的防护**：

| 风险 | 防御层 |
|------|--------|
| 审批侧 webhook / 链接被重放 | HITL 一次性 token + `WHERE state='PENDING'` 条件 UPDATE（本子系统） |
| HITL → durable.resume 调用被重试（网络抖动 / control plane 重发） | 19 的 advisory lock + 终态短路（resume 子系统） |
| 两个 worker 同时接管同一 thread 推进 | 19 的 advisory lock（resume 子系统） |

**关键决策**：**HITL 不重新发明 resume 的幂等性**——把幂等性责任完全落在 19；HITL 只确保自己写入的 state 转换是原子的（CAS 模式）。

### 5.3 超时回退

每分钟跑一次 reaper：

```sql
UPDATE hitl_request
SET state = CASE timeout_action
              WHEN 'approve'  THEN 'APPROVED'
              WHEN 'reject'   THEN 'REJECTED'
              WHEN 'escalate' THEN 'ESCALATED'
            END,
    responded_at = now(),
    approver_channel = 'timeout'
WHERE state = 'PENDING' AND timeout_at <= now()
RETURNING id, session_id, state;
```

对每行：
- `APPROVED` / `REJECTED`：触发 graph resume
- `ESCALATED`：创建新 hitl_request（approver_role 升级为 escalate_to），重置 timeout

**关键决策**：**默认 default_action=reject**——失败安全（fail-secure）原则；不审批就不执行副作用。

### 5.4 审批粒度

**单次执行级**——每个工具调用都重新审批：

| 选项 | 选择 | 理由 |
|------|------|------|
| 单次执行 | ✅ 选 | 最安全，每次明确同意 |
| 该 session 内同名 tool 信任 | ❌ | 攻击者可在 LLM 输出诱导后续相同 tool 调用 |
| 该 agent 全局 24h 信任 | ❌ | 提权风险 |
| approver 可选"信任本 thread 后续"（M3） | 待评估 | 需 RBAC + 显式开关 |

### 5.5 审批者识别 + 一次性 token

邮件 / Slack 链接含一次性 token：

```
https://app.expert_work.io/hitl/{request_id}?token={one_time_token}
```

token：UUID7 + HMAC（key 来自 [11 Credential Proxy](./11-credential-proxy.md) 的 `hitl_signing_key`）；落地后访问 `/by_token` → 引导 SSO 登录 → 完成绑定 → 写 `token_consumed_at`，token 失效。

**关键决策**：**token 仅用于 routing**（点击链接快速跳转），**不替代 JWT 鉴权**——真正 approve 还是要 JWT；token 一次性消费防分享。

### 5.6 紧急绕过 force_resume

admin 角色专用，场景为审批人不在线 + 业务紧急。流程：admin POST `:force_resume` + 非空 reason → 校验 admin → 写 state=BYPASSED + approver_channel=force → [17 Audit Log] 高亮 + P1 告警 → 触发 graph resume。

**关键决策**：**force_resume 是异常路径而非常规路径**——监控告警 + 周报必出，对 admin 形成约束。

### 5.7 child agent 内部 HITL（与 24 联动）

child 触发 HITL → child PENDING + PAUSED；lead 收到 `SubagentResult(state="PAUSED", hitl_request_id=...)` 也 interrupt + pause。审批 child 后链式恢复：child 继续跑完 → 推 `state="COMPLETED"` 给 lead → lead 自动 resume。UI 把 parent + child 同一 hitl 入口聚合显示。

---

## 6. 失败模式 & 缓解

| 失败模式 | 影响 | 缓解 |
|---------|------|------|
| 审批人都不在线 → 反复 escalate 死循环 | 永远不结束 | escalate 链最深 3 层；最后一层 default_action 强制 reject |
| force_resume 滥用 | 审批形同虚设 | 单独 RBAC + 审计高亮 + 周报 + Slack 通知 admin 团队 |
| 一次性 token 泄漏（邮件转发） | 任意人可走 landing | token 仅用于路由，不替代 SSO；JWT 强制 |
| Slack bot 身份被冒用 | 假审批 | Slack signing secret 强校验；Slack user 必须有 `external_identity` 绑定 |
| timeout_at 漂移（reaper 卡死） | pending 永不超时 | reaper 健康检查；备份 SQL job（pg_cron） |
| LangGraph interrupt 丢失（崩溃） | resume 后跳过审批 | hitl_request 表是 source of truth；resume 前先查 state==APPROVED 才能继续 |
| 大量 PENDING 占库 | 表膨胀 | reaper 老化 BYPASSED/COMPLETED 记录到冷归档（按 `audit_retention_days`） |
| approver override_payload 注入恶意参数 | 提权 | override 仅允许预声明字段（manifest `approval.editable_fields`）；其他改动一律拒绝 |
| 审批通道延迟（Slack 不达） | 用户等不及 | 多通道并发通知（Slack + email），首达即可 |
| HITL 期间用户取消 session | 已发出审批请求悬挂 | session cancel 级联将 PENDING 改 REJECTED；触发审计 |
| race：approve 与 timeout 同时发生 | 状态错乱 | 状态变更走 SQL `WHERE state='PENDING'` 条件更新；只一方成功 |

---

## 7. 可观测性

> 命名规范、必填字段、cardinality 约束统一遵循 [20 § 5.1–5.3](./20-observability.md)；本节仅列本子系统专属。

### 7.1 Metric

```
expert_work_hitl_pending_total{tenant,agent}                          gauge
expert_work_hitl_decision_total{tenant,agent,decision,channel}        counter
   # decision = approve | reject | timeout | bypass
   # channel  = web | slack | email | force | timeout
expert_work_hitl_decision_duration_seconds{tenant,agent}              histogram
expert_work_hitl_timeout_total{tenant,timeout_action}                 counter
expert_work_hitl_force_resume_total{tenant,actor_id}                  counter
expert_work_hitl_escalation_total{tenant,from_role,to_role}           counter
```

**关键 SLO（M2 目标）**：
- `hitl_decision_duration_seconds` P95 < 1h（业务方 oncall 指标）
- `hitl_force_resume_total` 月度 < 5（次数太多说明审批流不健康）

### 7.2 OTel span

- `expert_work.hitl.interrupt`（attrs：rule, action_summary）
- `expert_work.hitl.decision`（attrs：decision, channel, approver_id, duration_s）
- `expert_work.hitl.timeout`（attrs：timeout_action）
- `expert_work.hitl.force_resume`（attrs：actor_id, reason）

### 7.3 关键日志

每个 state 变更写 INFO 级 `hitl.{state}`，必带 `request_id, session_id, agent, agent_version, approver_id, channel`。完整字段遵循 [20 § 5.3](./20-observability.md#53-日志必填字段)。

---

## 8. 安全考虑

| 攻击面 | 防御 |
|--------|------|
| 重放审批链接（邮件转发） | 一次性 token + JWT 双重；token_consumed_at 后失效 |
| 审批人身份伪造 | JWT 校验 + role 校验 + tenant 校验 |
| LLM 操纵 action_summary 误导审批 | summary 由引擎按 manifest 模板生成（不是 LLM 自由输出）；payload 折叠展示 |
| 跨租户审批（A 租户审批 B 租户的 request） | API 强制 JWT.tenant == request.tenant |
| force_resume 被滥用 | RBAC + 审计 + 频率告警 |
| approve 时 override_payload 越权改动 | manifest `approval.editable_fields` 白名单；不在白名单的字段被改→ 422 |
| timeout 配置异常长（如 30 天） | manifest lint 上限 7d；超出需 admin 审核 |
| 审批 UI XSS | action_summary / payload 显式转义；UI 框架默认 escape |
| Slack bot signing key 泄漏 | Vault 管理；定期轮换 |
| pending 列表枚举其他租户 request | API 按 tenant 强制过滤；DB 层 RLS |

**关键决策**：**`action_summary` 必须由引擎模板渲染**，不允许 LLM 直接写入；防止 prompt injection 让 LLM 写"看起来无害"的 summary 隐藏真实危险动作。

---

## 9. M0 / M1 / M2 演进

### M0 —— 不做
- manifest 中允许声明 `policies.require_approval`，lint 通过但运行时忽略（warning 日志）

### M1 —— 不做（核心是多租户 + 灰度）
- 表结构 + Pydantic schema 落地（为 M2 铺路）

### M2（6-8 周）—— 完整框架上线
- LangGraph interrupt 集成
- web UI 审批面板（pending list + approve/reject）
- 触发模式：`tool:*`, `tool:delete_*`, `cost:>NUSD`, `data:contains_pii`
- 超时回退（reject / approve / escalate）
- Slack bot（slash command）
- 邮件回调（一次性 token）
- force_resume + 审计高亮
- 与 [24 Subagent](./24-subagent-execution.md) 联动（child HITL）

### M3 —— 高级
- 企业微信通道
- 批量审批（同 session 多个 pending 一次决策）
- 智能审批建议（基于历史 + LLM-as-judge 提示）
- 跨集群路由（M3）

---

## 10. 开放问题

1. **是否允许 LLM 看见审批结果原因**：reject 时 `rejection_reason` 是否注入 LLM 让它改写？倾向是（结构化反馈对 ReAct 友好），但要防止 LLM 把审批员的话当上下文学习。
2. **审批粒度细化**：不止 approve/reject，还可"approve with edit"（修改 payload 后批准）——已加到 5.2，但 UI 复杂度高，M3 再优化。
3. **多审批人 quorum**：某些高敏感动作要求 2 人同意？manifest schema 加 `min_approvers: 2`；M2 末考虑。
4. **审批 SLA 报告**：每月给业务方发审批延迟报告（哪些 agent 频繁等待）；M2 末。
5. **审批历史可搜索**：审批理由的全文搜索如何做（Loki vs Postgres FTS）？倾向 Postgres FTS（数据小、合规存放需要）。
