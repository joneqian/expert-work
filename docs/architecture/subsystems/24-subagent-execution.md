# 24 Sub-Agent 执行 — lead spawn specialists、状态机、parent-child trace、超时级联、quota 继承

> 把"单 agent 单线程跑完"升级为"lead 分解任务 → 并行 spawn specialists → 隔离失败 → 合并结果"。核心：清晰的 6 状态机、严格的失败隔离边界、parent-child trace 全链路、token/时间预算继承不击穿。

---

## 1. 职责 & 边界

### ✅ 做
- subagent 6 状态机（PENDING / RUNNING / COMPLETED / FAILED / CANCELLED / TIMED_OUT）
- 通过 `tools.subagent` 工具声明式 spawn child agent
- parent-child session 关系链与 trace 链关联
- **失败隔离**：subagent 崩溃 / 超时 / 拒绝 不影响 lead 继续推进
- **超时级联**：subagent 超时 → executor 强制 cancel + 抛 typed exception 给 lead
- **quota 继承**：subagent 的 token / cost 计入 lead session；不允许子树超 lead 总预算
- 并发控制：每 lead 同时活跃 subagent ≤ N（manifest 声明，per-tenant 可调）
- 递归层级控制：默认 max_depth=2，manifest 可调
- 资源隔离：每 subagent 独立 sandbox

### ❌ 不做
- 不做工作流编排（plan-execute / DAG 是 [orchestrator/workflow] 的事）
- 不做 sandbox 池调度 → [14 Sandbox Pool](./14-sandbox-pool.md)
- 不做跨租户 subagent 调用（绝对禁止）
- 不做 A2A 跨集群协议 → M3 议题
- 不做 lead 与 subagent 之间的 streaming UI（M3 议题）

---

## 2. 上下游依赖

| 依赖方向 | 子系统 | 关系 |
|---------|--------|------|
| 上游 | Orchestrator | lead agent 通过 `tool_call` 触发 subagent |
| 同级 | LangGraph subgraph | subagent 本身是 child compiled graph |
| 横切 | [14 Sandbox Pool](./14-sandbox-pool.md) | 每 subagent 独立 sandbox |
| 横切 | [16 Quota / Rate Limit](./16-quota-rate-limit.md) | quota 继承 |
| 横切 | [17 Audit Log](./17-audit-log.md) | **仅控制层事件写 audit**（spawn_denied / depth_limit_violation / cross_tenant_attempt）；常规 spawn / cancel / 超时是 agent 行为，写 [19] event_log |
| 横切 | [19 Durable Execution](./19-durable-execution.md) | subagent session 同样需要 checkpoint；parent pause 时级联 |
| 横切 | [20 Observability](./20-observability.md) | parent_trace_id 联接 |
| 横切 | [25 HITL](./25-hitl.md) | subagent 内部触发 HITL 时 lead 接收 PAUSED 信号 |

---

## 3. 数据模型 / 状态机

### 3.1 状态机

```
                  ┌──────────┐
                  │ PENDING  │  spawn 已写日志，sandbox 未起
                  └────┬─────┘
                       │ acquire sandbox + start graph
                       ▼
                  ┌──────────┐
                  │ RUNNING  │
                  └────┬─────┘
                       │
       ┌───────────────┼─────────────────┬──────────────────┐
       │               │                 │                  │
   complete         error             timeout           parent.cancel
       │               │                 │                  │
       ▼               ▼                 ▼                  ▼
  COMPLETED         FAILED          TIMED_OUT          CANCELLED
```

- **PENDING → RUNNING**：sandbox acquire 成功；首条 message 已写
- **RUNNING → COMPLETED**：graph END 节点 + result 已写 event_log
- **RUNNING → FAILED**：unhandled exception / sandbox crash / 工具不可用
- **RUNNING → TIMED_OUT**：到达 `tool.subagent.timeout_s`；executor 强制 cancel
- **RUNNING → CANCELLED**：parent cancel / parent failed / parent quota exhausted

**关键决策**：**所有终态都不可逆**；同一 subagent_id 不可重启，重试由 lead 决定（生成新 subagent_id）。

### 3.2 Postgres DDL

```sql
CREATE TABLE subagent_invocation (
    id                 UUID PRIMARY KEY,             -- = child session_id（uuid7）
    tenant_id          TEXT NOT NULL,
    parent_session_id  UUID NOT NULL,
    parent_agent       TEXT NOT NULL,
    parent_agent_ver   TEXT NOT NULL,
    parent_trace_id    TEXT NOT NULL,                -- W3C trace id
    child_agent        TEXT NOT NULL,                -- e.g. 'security-auditor-agent'
    child_agent_ver    TEXT NOT NULL,
    depth              SMALLINT NOT NULL,            -- 0 = lead 直接子；最大值由 manifest 控制
    state              TEXT NOT NULL,                -- PENDING/RUNNING/COMPLETED/FAILED/CANCELLED/TIMED_OUT
    timeout_s          INT NOT NULL,
    max_tokens         INT,                          -- 子树 token 上限（继承 lead 剩余）
    started_at         TIMESTAMPTZ,
    finished_at        TIMESTAMPTZ,
    input_hash         TEXT NOT NULL,                -- args 摘要
    result_summary     TEXT,                         -- 摘要（result 明文进 event_log）
    error_code         TEXT,                         -- 'timeout' / 'tool_unavailable' / 'quota_exceeded' / ...
    error_message      TEXT,
    tokens_input       INT NOT NULL DEFAULT 0,
    tokens_output      INT NOT NULL DEFAULT 0,
    cost_usd_micro     BIGINT NOT NULL DEFAULT 0,    -- 微美元，整数避免浮点
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON subagent_invocation (tenant_id, parent_session_id);
CREATE INDEX ON subagent_invocation (parent_trace_id);
CREATE INDEX ON subagent_invocation (state, created_at);
```

### 3.3 Pydantic schema

```python
class SubagentInvocation(BaseModel):
    id: UUID
    tenant: str
    parent_session_id: UUID
    parent_trace_id: str
    child_agent: str
    child_agent_ver: str
    depth: int
    state: Literal["PENDING","RUNNING","COMPLETED","FAILED","CANCELLED","TIMED_OUT"]
    timeout_s: int
    max_tokens: int | None

class SubagentResult(BaseModel):
    """返回给 lead 的标准结果信封；失败也是 SubagentResult，lead 可正常处理。"""
    invocation_id: UUID
    state: Literal["COMPLETED","FAILED","CANCELLED","TIMED_OUT"]
    output: dict | None                              # state=COMPLETED 时有
    error: SubagentError | None
    tokens: TokenUsage
    duration_ms: int
```

---

## 4. 关键接口

### 4.1 Manifest（lead 侧声明）

```yaml
spec:
  tools:
    - subagent:
        name: security_auditor                       # 在 lead prompt 中暴露的工具名
        ref: security-auditor-agent@^2.0             # 引用其他 manifest，semver
        timeout_s: 60                                # 强制超时
        max_tokens: 8000                             # 子树 token 预算
        max_concurrent: 2                            # lead 内并发上限（同名 subagent）
```

### 4.2 Python（executor 接口）

```python
class SubagentExecutor:
    async def spawn(self, req: SpawnRequest, *, parent_ctx: AgentContext) -> SubagentResult:
        """同步语义：返回时已是终态。内部异步 spawn graph + 等待结果。"""

    async def cancel(self, invocation_id: UUID, reason: str) -> None: ...

    async def list_active(self, parent_session_id: UUID) -> list[SubagentInvocation]: ...


class SpawnRequest(BaseModel):
    parent_ctx: AgentContextRef
    child_agent: str
    child_agent_ver: str
    input: dict
    timeout_s: int
    max_tokens: int
    depth: int
```

### 4.3 Lead 侧调用样式（声明式 → 引擎自动生成 ToolNode）

```python
# 引擎按 manifest 自动生成；lead 的 LLM 看到的 tool schema：
{
  "name": "security_auditor",
  "description": "Run security audit on given code diff. Returns issues list.",
  "input_schema": {...},     # 来自 child agent 的入参 schema
}
# LLM tool_call → executor.spawn → SubagentResult → 注入 lead messages
```

---

## 5. 算法 / 关键决策

### 5.1 Spawn 流程

```
0. dispatcher 生成 idempotency_key = uuid7()           （server 端，lead/LLM 不可见）
1. lead LLM 输出 tool_call(name="security_auditor", input=...)
2. orchestrator 路由到 SubagentExecutor.spawn
3. 校验：
   - depth + 1 ≤ max_depth                          否则 → SubagentResult(state=FAILED, error=DepthExceeded)
   - 同 lead 当前活跃数 < max_concurrent             否则 → 等待 / FAILED(ConcurrencyExceeded)
   - 子树预算：lead 剩余 tokens ≥ max_tokens         否则 → FAILED(QuotaInsufficient)
4. 写 subagent_invocation state=PENDING
5. 写 event_log event_type=subagent_spawn_pre（含 parent_trace_id, idempotency_key, args_hash）
6. 启动 child graph：
   - acquire sandbox（与 lead sandbox 不共享）
   - 创建 child AgentContext：tenant 继承、actor_id 继承、quota_remaining 限制为 max_tokens
   - 启动 LangGraph subgraph（async task）
7. state=RUNNING；parent 阻塞等待结果（asyncio.wait_for(timeout_s)）
8. child END / error / timeout → 写 event_log event_type=subagent_result（**复用同一 idempotency_key**） + 写终态 + SubagentResult 返回 lead
```

**幂等性 / replay 协议（与 [19 § 5.3](./19-durable-execution.md) 协议对齐）**：

`subagent.spawn` 是副作用工具，必须遵循双阶段 idempotency：

```
replay 时（lead session 由其他 worker 接管）：
  dispatcher 在 spawn 前先查 event_log（必带 tenant_id 防越权 replay）：
    SELECT payload FROM event_log
     WHERE tenant_id = $1
       AND thread_id = $2                  -- lead session_id
       AND event_type = 'subagent_result'
       AND payload->>'idempotency_key' = $3;
  - 命中：直接构造 SubagentResult 返回 lead，**不重 spawn**（防止 child 被启动 N 次）
  - 未命中但 subagent_spawn_pre 命中：判定为崩溃中途，按 child timeout_s 等剩余时间或视作 FAILED（policy 配置）
  - 都未命中：执行新 spawn 流程（步骤 0–8）
```

**关键决策**：**spawn 同步语义**——lead 的 tool_call 调用要么返回终态结果要么超时，不暴露"异步 future"给 LLM。LLM 不擅长处理 pending 状态。

**关键决策**：**idempotency_key 由 dispatcher（server 端）生成**，不由 LLM 控制——LLM 不可信，复用同一 key 跨 spawn 会让两个不同 spawn 共用结果，污染 lead；server 端按 (lead_thread_id, lead_step_seq, tool_call_id) 派生唯一 key。

### 5.2 失败隔离

| child 失败类型 | lead 收到 | lead 默认行为 |
|---------------|----------|--------------|
| graph unhandled exception | `SubagentResult(state=FAILED, error.code="internal_error")` | LLM 自由决定（重试 / 改用其他 tool / 放弃） |
| sandbox 不可用 | `state=FAILED, error.code="sandbox_unavailable"` | LLM 决定 |
| 子工具不可用 | `state=FAILED, error.code="tool_unavailable", error.tool=...` | LLM 决定 |
| timeout | `state=TIMED_OUT, error.code="timeout"` | LLM 决定（通常不重试同名） |
| HITL（child 内部要求审批） | child → PAUSED；lead 收到 `state=PAUSED, hitl_ref=...` 特殊信号 → lead session 也 PAUSED 等待 | [25 HITL](./25-hitl.md) 接管 |
| quota 耗尽（子树超 max_tokens） | `state=FAILED, error.code="quota_exceeded"` | LLM 决定 |
| parent cancel | child → CANCELLED（不返回 lead） | n/a |

**关键决策**：**所有 child 失败都返回 `SubagentResult` 而非抛异常**——LLM 收到结构化结果可决策；引擎只在 lead 自身无法处理（如 LangGraph 内部异常）时才抛。

### 5.3 超时级联

```
spawn 时启动 asyncio.create_task(run_child_graph())
asyncio.wait_for(task, timeout=timeout_s)
  ↓ 超时
1. 标 state=TIMED_OUT（trans-aware 写库）
2. 取消 task：cancel sandbox + 释放资源 + 写 event_log subagent_cancelled
3. 返回 SubagentResult(state=TIMED_OUT)
4. 注：child 内部正在跑的 LLM/tool 调用接受 cancel signal 后立即终止；
   不可中断的副作用（已发出的 HTTP）依赖 [19 idempotency 协议] 兜底
```

**关键决策**：**timeout_s 必须小于 lead session 的 timeout** — manifest lint 阶段强制 `subagent.timeout_s ≤ workflow.session_timeout_s × 0.8`；防止 child 把 lead 自己的超时窗口吃光。

### 5.4 Quota 继承

```python
# spawn 时计算
remaining = lead_session.token_quota_remaining
allocated = min(req.max_tokens, remaining)
if allocated < req.max_tokens * 0.5:               # 至少给一半才有意义
    return SubagentResult(state=FAILED, error.code="quota_insufficient")

child_ctx.token_quota = allocated
# child 每次 LLM 调用扣 child_ctx；child 结束时把实际消耗回写 lead
lead_session.token_used += child.tokens_total
```

**关键决策**：**整个 subagent 树共享 lead 的 token 预算**；不允许 child 申请超过 lead 剩余的额度；防止递归 spawn 导致 token 爆炸。

#### Quota 计费（与 [16 Quota / Rate Limit](./16-quota-rate-limit.md) 协议对齐）

`subagent.spawn` 调 [10 LLM Gateway](./10-llm-gateway.md) 的语义不变（reserve → call → commit / release），但 **reservation / commit 在 child 层、按 child 维度独立计**：

| 维度 | 落点 | 说明 |
|------|------|------|
| `token_reservation.thread_id` | **child 的 thread_id** | 每个 child session 自己 reserve / commit；child 终态前不动 lead 的 ledger |
| `token_budget_ledger` 累加 | **commit 时 server 端按 `parent_thread_id` 自动级联**累加到 lead 的 ledger | child commit_tokens() 触发 server 端 trigger / 应用层级联写；防止"子树合计超 lead 预算"被绕过 |
| agent 维度 metric | `child_agent` 名独立桶（与 lead 不混） | `helix_quota_*{agent="security-auditor"}` 与 `agent="lead-agent"` 各自计；rate-limit QPS 独立计 |
| failure 时 release | 按 [16 § 5](./16-quota-rate-limit.md) `release_tokens(thread_id=child)` 标准流程 | child timeout / FAILED / sandbox 崩溃 都触发；避免预留 token 永久占用 |
| 子树总和强约束 | spawn 时 `allocated ≤ lead.remaining`（见上文 Python 片段） | 配 commit 时级联累加，双保险防爆 |

**关键决策**：**reservation 在 child 层、commit 时 server 端级联累加 lead**——既保留"agent 维度独立计 metric/QPS"的可观测性（[16 § 10](./16-quota-rate-limit.md)），又强制"子树总和不超 lead 预算"的硬约束。reservation 直接在 lead 层会让 child 的 metric/QPS 与 lead 混在一起，丢失粒度。

### 5.5 并发与递归层级

| 维度 | 默认 | 配置位 |
|------|------|--------|
| 同 lead 同时活跃 subagent 总数 | 3 | `tenant_config.subagent_max_concurrent`，可被 manifest 调低不调高 |
| 同 lead 同名 subagent 并发 | 1 | manifest `tools.subagent.max_concurrent` |
| 递归 max_depth | 2 | manifest `workflow.subagent_max_depth`，硬上限 5（lint 阶段拒绝更深） |
| 整 subagent 树节点总数 | 50 | `tenant_config.subagent_tree_max_nodes`，超出 → FAILED |

**关键决策**：**max_depth=2 是默认硬限**——A spawn B spawn C 不允许；理由是层级越深错误恢复越难、trace 噪声越大；少数业务（plan-execute 类）可申请 depth=3，需 admin 审核。

### 5.6 Trace / Session 链接

```
parent session:
  trace_id = T1
  events: [..., subagent_spawn(child_id=S2, child_trace=T2), ...]

child session:
  session_id = S2
  parent_session_id = S1
  parent_trace_id = T1
  trace_id = T2 (W3C trace 规范允许 child 用新 trace 但通过 link 关联)
  events: [session_start, ..., subagent_complete]
```

**关键决策**：**child 用新 trace_id（不是同 trace 的子 span）**——理由：child 自己内部可能很长（数千 span），混入 parent trace 让查询变重；通过 OTel `Link` API 关联，UI 层可"跳进 child trace"。

### 5.7 event_log vs audit_log 写入分工

subagent 相关事件按"agent 行为" vs "控制层事件"严格分流：

| 事件 | 写 [19] event_log | 写 [17] audit_log | 说明 |
|------|:-:|:-:|------|
| spawn 成功（常规执行） | ✅（`event_type=subagent_spawn_pre` + `subagent_result`） | ❌ | agent 行为，由 graph runtime 自然 emit |
| cancel（parent cancel child） | ✅（`event_type=subagent_cancelled`） | ❌ | agent 行为 |
| timeout | ✅（`event_type=subagent_timeout`） | ❌ | agent 行为 |
| FAILED（child 内部错误） | ✅（`event_type=subagent_result` with state=FAILED） | ❌ | agent 行为 |
| **spawn_denied**（quota / depth / concurrent 拒绝） | ✅ | ✅（`action=subagent:spawn_denied`） | 控制层判定结果，需要审计追溯 |
| **depth_limit_violation**（manifest lint 时已挡，运行时被绕过的兜底拒绝） | ✅ | ✅（`action=subagent:depth_limit_violation`） | 异常路径，重点关注 |
| **cross_tenant_attempt**（child.tenant ≠ parent.tenant） | ✅ | ✅（`action=subagent:cross_tenant_attempt`） | 安全事件，**P1 告警** |
| force_resume on child（HITL 联动 admin 强制恢复） | ✅ | ✅（[25 HITL](./25-hitl.md) 已规范） | 高风险，由 25 写 |

**关键决策**：**event_log 是 agent 行为流（高频，可能采样、可能 redact）**，**audit_log 是控制层判定 / 高风险动作 / 合规事件流（低频，不采样、WORM）**；混写会让 audit_log 被 spawn 噪声淹没，失去合规价值。新增 audit action：`subagent:spawn_denied` / `subagent:depth_limit_violation` / `subagent:cross_tenant_attempt`（需补到 [17 § 5.1 词表](./17-audit-log.md#51-action-词表强-schema不允许自由文本)）。

---

## 6. 失败模式 & 缓解

| 失败模式 | 影响 | 缓解 |
|---------|------|------|
| child sandbox 起不来 | spawn 失败 | 状态机 PENDING→FAILED；error.code=sandbox_unavailable；lead 决策 |
| child 死循环 | timeout 兜底 | timeout_s 强制 |
| child 内部 OOM | sandbox 自杀 | sandbox supervisor 探测 → state=FAILED |
| 同 thread 内 spawn 死锁（depth 深 + 锁循环） | session 卡死 | max_depth + tree_max_nodes + lead session 总 timeout 三重保护 |
| token 预算被多 subagent 抢光 | 后续 spawn 全失败 | 串行扣减 + 错误明确 + lead 收到 quota_insufficient 后决策 |
| child 内部 HITL 让 lead 卡死 | parent 也 PAUSED | UI/API 暴露 parent 与 child 状态联动；admin 可触发 force_resume on child |
| parent crash 但 child 仍在跑 | 悬挂 child | parent crash → reaper 扫 30s 内未续 lock 的 parent → 级联 cancel children |
| child 共享 lead 的 sandbox 导致跨 agent 污染 | 串数据 | 强制每 subagent 独立 sandbox（`isolation_level=dedicated_sandbox`）|
| 同名 subagent 并发竞争同一外部资源 | 副作用冲突 | `tools.subagent.max_concurrent=1` 默认；可显式上调 |
| 跨租户调用（漏洞） | 数据泄漏 | spawn 时强制 child.tenant == parent.tenant；manifest lint 也校验 |

---

## 7. 可观测性

> 命名规范、必填字段、cardinality 约束统一遵循 [20 § 5.1–5.3](./20-observability.md)；本节仅列本子系统专属。

### 7.1 Metric

```
helix_subagent_total{tenant,parent_agent,child_agent,state}                 counter
helix_subagent_duration_seconds{tenant,child_agent,state}                    histogram
helix_subagent_depth{tenant,parent_agent}                                    histogram
helix_subagent_concurrent{tenant,parent_agent}                               gauge
helix_subagent_token_usage{tenant,parent_agent,child_agent,direction}        counter
helix_subagent_quota_exceeded_total{tenant,reason}                           counter
   # reason = depth | tree_size | concurrent | tokens
```

### 7.2 OTel span

- `helix.subagent.spawn`（attrs：parent_session_id, parent_agent, parent_agent_version, child_agent, child_agent_version, depth, timeout_s）
- `helix.subagent.execute`（child trace 入口；与 parent 通过 Link 关联；attrs：child_agent, child_agent_version）
- `helix.subagent.cancel`（attrs：reason, child_agent_version）
- `helix.subagent.timeout`（attrs：elapsed_s, child_state_at_timeout, child_agent_version）

所有 span 必带 `agent_version`（[C8](./REVIEW-NOTES.md) 规范）；child span 的 `agent_version` 指 child 的，parent attrs 用 `parent_agent_version` 区分。

### 7.3 关键日志

每次 spawn / state change 写 INFO：`subagent.{state}`，必带 `parent_session_id, child_session_id, parent_trace_id, child_agent, child_agent_version, depth`。完整字段遵循 [20 § 5.3](./20-observability.md#53-日志必填字段)。

---

## 8. 安全考虑

| 攻击面 | 防御 |
|--------|------|
| 跨租户 spawn | spawn 强制 child.tenant == parent.tenant；manifest lint + 运行时双重校验 |
| 递归炸弹（A spawn A spawn A） | max_depth + tree_max_nodes 硬限；spawn 前递归调用图静态检查 |
| LLM 注入构造大量 spawn 耗资源 | 同 lead 并发 ≤ 3 + tree_max_nodes=50 + lead session 总 timeout |
| child 滥用 lead 的 token 配额 | 子树预算独立扣；超出 child 预算不影响 lead 剩余 |
| child 调用 lead 不该调用的工具（提权） | child 工具集来自 child manifest（独立校验），与 lead 解耦 |
| child sandbox 复用 lead sandbox 残留数据 | 强制独立 sandbox + 独立 workspace |
| HITL 期间 child 状态被绕过 | child PAUSED 状态写库 + lead 等待联动；force_resume 走 [25] 单独审计 |
| trace 链接被伪造 | `parent_trace_id` 由 server 设置；child manifest 不能覆盖 |

**关键决策**：**child 的工具权限是 child manifest 自己声明的子集**，绝不继承 lead 的工具权限——防止 lead "借" child 提权。

---

## 9. M0 / M1 / M2 演进

### M0 —— 不做
- manifest lint 阶段允许声明 `subagent` 工具但运行时禁用（返回 `tool_unavailable`）
- 仅留表结构 + Pydantic schema 占位（为 M1 铺路）

### M1（包含在 6-8 周内）—— 基础 spawn + 失败隔离
- 6 状态机上线
- 同步语义 spawn / 同步等待结果
- 失败隔离 + 超时级联
- max_depth=2 / max_concurrent=3 默认
- quota 继承基础（按 token）
- parent-child trace 链接（OTel Link）
- 监控 metric + dashboard

### M2 —— 高级编排
- HITL 跨 parent-child 联动（child 暂停 → lead 接收 PAUSED）
- subagent 结果缓存（同 input_hash 命中复用，配 manifest 选项）
- depth=3 实验性放开（白名单 tenant）
- 子树预算细分（按 cost 而非纯 token）

### M3 —— 跨集群 A2A
- A2A 协议适配（Google A2A 或自定义）
- 跨集群 subagent invocation（远程 spawn）
- 长 child（小时级）durable resume

---

## 10. 开放问题

1. **child failure 是否回写 lead messages**：FAILED 时把 error 喂给 LLM 让它决策？倾向是（默认 inject 简短 error；明文走 event_log）。
2. **同名 subagent 并发结果合并**：M1 串行；M2 是否支持 fan-out N → fan-in by lead？倾向 M2 引入 `parallel: N` 字段。
3. **child 中途追加预算**：child 跑到一半发现需要更多 token，能不能向 lead 申请？倾向不允许（复杂度高、攻击面大）；M3 议题。
4. **跨租户 marketplace subagent**：未来内部 marketplace 上的 subagent 可被多租户调用怎么计费？延后到 M3+。
5. **同步 spawn 是否阻塞 lead 主循环**：当前实现 lead 等待；高并发时是否影响 TTFT？需 benchmark；可考虑 future-style API 但要解决 LLM 状态表达问题。
