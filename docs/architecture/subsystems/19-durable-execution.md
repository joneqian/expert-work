# 19 Durable Execution / Resume — checkpoint、幂等、replay 去重、长会话恢复

> 把"调用一次 LLM 就跑完一整轮"升级为"任意时刻断电、节点漂移、HITL 暂停后都能从断点恢复且**不重复触发副作用**"。核心机制：LangGraph PostgresSaver checkpoint + server 端 idempotency_key 协议 + replay 去重。

---

## 1. 职责 & 边界

### ✅ 做
- **LangGraph checkpoint 编排**：周期化写 checkpoint、决定 N（每多少 event/turn 一次）、控制 state 大小
- **副作用工具幂等协议**：服务端生成 `idempotency_key`、写 event_log 双阶段记录、replay 去重
- **会话 pause / resume API**：HITL 触发的暂停、外部审批后的恢复
- **版本兼容判定**：resume 时检查 manifest version / model version 是否变更，不兼容时拒绝
- **state 大小管控**：超阈值（默认 1MB）的字段外存到对象存储，state 仅留引用
- **HTTP 工具透传 `Idempotency-Key` header**：Stripe/PayPal 业界惯例
- **故障恢复路径**：orchestrator 进程崩溃后另一个 worker 接管同一 thread 的 graph 执行

### ❌ 不做
- 不做 checkpoint 物理存储 → [23 Postgres Scalability](./23-postgres-scalability.md)
- 不做副作用工具的语义判定 → 由 manifest `idempotent: false` 显式声明
- 不做 HITL 审批 UI → [25 HITL](./25-hitl.md)
- 不做容器进程层面的 checkpoint（CRIU 类） → 不在范围；sandbox 视为可重建
- 不做跨 region 故障转移 → [22 Disaster Recovery](./22-disaster-recovery.md)

---

## 2. 上下游依赖

| 依赖方向 | 子系统 | 关系 |
|---------|--------|------|
| 上游 | Orchestrator (LangGraph) | 调度图执行；本子系统是 graph 中间件 |
| 上游 | Control Plane API | 暴露 `pause` / `resume` HTTP |
| 下游 | Postgres | checkpoint 表 + event_log 表 |
| 下游 | 对象存储 (S3/MinIO) | 大 state 字段外存 |
| 横切 | [10 LLM Gateway](./10-llm-gateway.md) | LLM 调用是 idempotent 的（不需要 key），但响应缓存命中协同 |
| 横切 | [12 MCP Gateway](./12-mcp-gateway.md) | MCP 工具调用透传 Idempotency-Key |
| 横切 | [17 Audit Log](./17-audit-log.md) | resume / 强制 unblock 写审计 |
| 横切 | [20 Observability](./20-observability.md) | checkpoint 写入耗时、replay 命中率 metric |
| 横切 | [25 HITL](./25-hitl.md) | interrupt 触发 pause；approve 触发 resume |

---

## 3. 数据模型 / 状态机

### 3.1 Session 持久状态机

```
RUNNING ──checkpoint──▶ RUNNING                    （正常推进）
RUNNING ──interrupt(HITL)──▶ PAUSED ──resume──▶ RUNNING
RUNNING ──crash──▶ (无状态，由其他 worker 接管) ──replay──▶ RUNNING
RUNNING ──user_cancel──▶ CANCELLED
RUNNING ──complete──▶ COMPLETED
RUNNING ──unrecoverable_error──▶ FAILED
PAUSED  ──timeout──▶ FAILED|CANCELLED|COMPLETED   （按 HITL default_action）
```

### 3.2 Postgres DDL

```sql
-- LangGraph 官方 checkpoint 表（PostgresSaver 自带迁移，仅列示）
-- 真实表名：checkpoints / checkpoint_writes / checkpoint_blobs
-- 我们扩展一张元数据表，记录每个 thread 的最新 checkpoint 信息
CREATE TABLE durable_thread_meta (
    thread_id          UUID PRIMARY KEY,
    tenant_id          TEXT        NOT NULL,
    agent_name         TEXT        NOT NULL,
    agent_version      TEXT        NOT NULL,            -- 启动时 pin
    model_provider     TEXT        NOT NULL,
    model_name         TEXT        NOT NULL,
    state              TEXT        NOT NULL,            -- RUNNING / PAUSED / CANCELLED / COMPLETED / FAILED
    pause_reason       TEXT,                            -- hitl_required / external_pause / null
    last_checkpoint_id TEXT,                            -- LangGraph checkpoint_id
    last_event_seq     BIGINT      NOT NULL DEFAULT 0,
    state_size_bytes   BIGINT      NOT NULL DEFAULT 0,
    blob_refs          JSONB       NOT NULL DEFAULT '[]'::jsonb,  -- [{"path": "s3://...", "field": "scratch"}]
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON durable_thread_meta (tenant_id, state, updated_at);

-- 副作用工具调用的"双阶段"记录：pre_call → result
-- 物理上复用 event_log（列名以 23 § 3.2 为准：event_type / tenant_id / session_id / seq）
-- event_type=tool_pre_call 与 event_type=tool_result 通过 idempotency_key 配对
-- payload 字段示例：
-- tool_pre_call: {"tool": "send_email", "idempotency_key": "01J9...", "args_hash": "sha256:..."}
-- tool_result:   {"tool": "send_email", "idempotency_key": "01J9...", "status": "ok", "result_hash": "..."}
--
-- subagent.spawn 同样视为副作用调用，走双阶段：
-- event_type=subagent_spawn_pre / event_type=subagent_result，配对 idempotency_key 同上

-- 强制约束：event_log 写入 pipeline 必须经过 redactor middleware（在 payload 落库前），
-- 按 tenant_config.pii_fields 自动 redact；该约束被 12 / 13 / 19 共同遵守。
```

### 3.3 Pydantic schema

```python
class DurableThreadMeta(BaseModel):
    thread_id: UUID
    tenant: str
    agent: str
    agent_version: str
    state: Literal["RUNNING", "PAUSED", "CANCELLED", "COMPLETED", "FAILED"]
    last_checkpoint_id: str | None
    last_event_seq: int
    state_size_bytes: int

class IdempotencyEnvelope(BaseModel):
    """工具调用前 server 生成，写 event_log；replay 时凭此 key 去重。"""
    idempotency_key: str       # uuid7（含时间戳，便于排查）
    tool: str
    args_hash: str             # sha256(canonical_json(args))，便于校验 args 一致性
    issued_at: datetime
```

---

## 4. 关键接口

### 4.1 Python（orchestrator 内部）

```python
class DurableExecutor:
    async def attach(self, thread_id: UUID) -> CompiledGraph:
        """加载或创建 thread 对应的 graph + checkpoint。"""

    async def step(self, thread_id: UUID, input: dict) -> AsyncIterator[Event]:
        """推进图；每 N 个 event 自动 checkpoint。"""

    async def pause(self, thread_id: UUID, reason: str) -> None:
        """写 PAUSED + 强制 flush checkpoint。"""

    async def resume(self, thread_id: UUID, *, actor_id: str) -> AsyncIterator[Event]:
        """
        幂等：server 端用 `pg_advisory_xact_lock(thread_id_hash)` 防并发；
        重复调用返回上次 resume 的结果（不二次推进），避免 25 HITL / control plane
        重试导致的 double-resume；幂等窗口与 thread 终态对齐。
        """
        ...

    async def cancel(self, thread_id: UUID, *, actor_id: str) -> None: ...


class IdempotentToolDispatcher:
    async def call(
        self, tool: ToolHandle, args: dict, *, idempotent: bool, ctx: AgentContext
    ) -> ToolResult:
        """idempotent=False 时：先查 event_log 是否已有同 key 的 tool_result → 命中直返。"""
```

### 4.2 HTTP（Control Plane）

```
POST /v1/sessions/{thread_id}:pause
     Body: {"reason": "manual"}
     → 200 {"state": "PAUSED", "checkpoint_id": "..."}

POST /v1/sessions/{thread_id}:resume
     Body: {}
     → 200 {"state": "RUNNING"} | 409 {"reason": "version_mismatch", ...}

POST /v1/sessions/{thread_id}:cancel
     Body: {"reason": "user_abort"}
     → 200

GET  /v1/sessions/{thread_id}/durability
     → DurableThreadMeta
```

调用方必须带 JWT，actor 由 [15 AuthN/AuthZ](./15-authn-authz.md) 解出后写入审计。

---

## 5. 算法 / 关键决策

### 5.1 Checkpoint 节奏

**关键决策**：**每 N=20 个 event 一次 checkpoint，且 LLM 调用前后必 checkpoint**。

理由：
- LLM 是 graph 中最耗时与最贵的步骤；放在 LLM 之前 checkpoint 让重试不丢已完成的 reasoning
- LLM 之后立即 checkpoint，防止下一个工具调用前进程崩溃导致重复扣 token
- N=20 在工具密集场景下 ~每 1-2 turn 一次，对 Postgres 写压力可控（单 checkpoint < 50ms）

可调：manifest 中 `workflow.checkpoint_every_n_events` 覆盖默认值。

### 5.2 副作用工具幂等协议（核心）

```
1. orchestrator 决定调用 tool T
2. dispatcher 查 manifest：T.idempotent ?
   - True  → 直接调用（不写 envelope）
   - False → 走幂等流程
3. server 生成 idempotency_key = uuid7()
4. 写 event_log event_type=tool_pre_call, payload={tool, key, args_hash}
   （写 event 立即 commit，相当于"我打算做这件事"的契约）
5. flush checkpoint
6. 真正调用 T：
   - HTTP 工具：在请求 header 加 Idempotency-Key: <key>
   - MCP 工具：在 tool_call 元信息中加 idempotency_key
   - python 工具：通过 ctx.idempotency_key 注入，由工具实现侧自行透传
7. 收到结果 → 写 event_log event_type=tool_result, payload={key, status, result_hash, body_ref}
8. flush checkpoint，进入下一步
```

**`subagent.spawn` 也是副作用调用**：dispatcher 在执行 spawn 前为其生成 `idempotency_key = uuid7()`，写
`event_log event_type=subagent_spawn_pre, payload={parent_thread_id, child_agent, key, args_hash}`；
child 完成后 lead 收到回调，写 `event_log event_type=subagent_result, payload={key, child_thread_id, status, result_hash}`，
key 与 spawn_pre 同。replay 时若 dispatcher 命中已有 `subagent_result`，直接把 child 结果返回给 lead，**不再重复 spawn**。
对应到 [24 Subagent](./24-subagent-execution.md)：spawn pipeline 必须接受外部传入的 idempotency_key，
而不是在 child 侧自生成。

**关键决策**：**`idempotency_key` 由 server 生成，绝不由 manifest / LLM / 用户提供**。LLM 不感知这个 key 存在。

理由：
- 防 replay 攻击：LLM 若控制 key 可故意重用造成混淆
- 可审计：key 与 trace_id 绑定，事后可重放精确请求

### 5.3 Replay 去重

进程崩溃 / pod 重启后，新 worker 接管 thread：

```
1. attach(thread_id) → 加载最近 checkpoint_id
2. LangGraph 自动从 checkpoint resume，重放尚未持久化的步骤
3. 每个工具调用前，dispatcher 查 event_log（必须带 tenant_id 防越权 replay）:
   SELECT payload->>'status' FROM event_log
    WHERE tenant_id = $1
      AND thread_id = $2
      AND event_type = 'tool_result'
      AND payload->>'idempotency_key' = $3;
   - 命中 status=ok：直接返回上次 result（不重复 execute）
   - 命中 status=failed：按工具 retry_policy 决定重试或放弃
   - 未命中：执行调用，写 result

   subagent.spawn 同理（replay 命中 subagent_result 时直接返回 child 结果给 lead）：
   SELECT payload FROM event_log
    WHERE tenant_id = $1
      AND thread_id = $2
      AND event_type = 'subagent_result'
      AND payload->>'idempotency_key' = $3;
4. graph 推进到正常 RUNNING
```

**关键决策**：**replay 一定要先查 event_log 而不是先调工具**——否则 send_email 可能发两次。

### 5.4 长会话状态膨胀

| 阶段 | 阈值 | 措施 |
|------|------|------|
| state < 256KB | 默认 | 全部存 checkpoint blob |
| 256KB ≤ state < 1MB | warn | 监控告警，不阻塞 |
| state ≥ 1MB | 强制 | 拣出 top-N 大字段（messages、scratch、tool_results）外存到对象存储；state 内只留 `{"$ref": "s3://bucket/<key>"}` |
| messages / token 触发上下文压缩 | 触发条件、算法、失败处理 | 详见 [27 上下文压缩](./27-context-compression.md)；本子系统不重定义触发阈值 |

外存路径命名：`s3://expert-work-state/{tenant}/{thread_id}/{checkpoint_id}/{field}.json`，由 [22 DR](./22-disaster-recovery.md) 接管备份。

### 5.5 Resume 兼容性判定

resume 时强制校验：

| 项 | 不兼容时行为 |
|----|--------------|
| `manifest.agent_version` 已变更 | 拒绝 resume，返回 409；admin 须 force_resume 写审计 |
| `model.name` 已变更 | 警告，可继续（模型升级一般兼容）；记录 warning event |
| `tools` 列表新增/删除 | 删除工具且 state 中有 pending 调用 → 拒绝；仅新增则允许 |
| `system_prompt` 变更 | 允许（不破坏 messages） |
| checkpoint schema 版本不兼容 | 拒绝；提示需迁移 |

**关键决策**：**`agent_version` pin 到 thread**——thread 启动时记录的版本，全生命周期不变；新 manifest 上线只影响新 thread。

### 5.6 进程崩溃接管

- Postgres advisory lock：`pg_try_advisory_lock(hash(thread_id))` 保证同一 thread 仅一个 worker 持有
- worker 心跳：每 10s 续 lock；丢失 lock 即停止推进、释放资源
- 崩溃 worker 的 lock 在 connection close 后自动释放（30s 内由 Postgres 回收），其他 worker 抢锁接管

---

## 6. 失败模式 & 缓解

| 失败模式 | 影响 | 缓解 |
|---------|------|------|
| checkpoint 写失败（Postgres 短暂故障） | 进程崩溃丢部分进度 | 写 checkpoint 失败 → 同步重试 3 次 → 失败则 `state=FAILED` 不再推进；事后由 reaper 清理 |
| replay 时找不到 idempotency_key 对应 result（pre_call 已写但 result 未写） | 不确定上次是否真执行 | 视为"未执行"重试，并依赖工具自身 Idempotency-Key 防重 |
| state 膨胀超 1MB 但外存写失败 | checkpoint 阻塞 | 同步 retry → 失败则降级（截断 messages 到最近 N 条 + 写 warning） |
| 同 thread 两个 worker 同时推进（lock 失效） | 副作用重复 | advisory lock + 工具 Idempotency-Key 双保险 |
| HITL 超时（24h 默认）后用户来 resume | 业务期望已变 | 24h 后自动按 default_action 处理（详见 [25 HITL](./25-hitl.md)），thread 进终态；用户 resume 收到 410 |
| manifest hot-swap 期间 thread 仍在跑 | 版本切换混乱 | thread 启动时 pin agent_version；新版只接新 thread |
| 工具实现不接受 Idempotency-Key（旧系统） | 可能重复 | manifest 标 `idempotent: false` 仍触发幂等流程；工具侧未透传时由 event_log 兜底（去重发生在 dispatcher） |
| 大量 PAUSED thread 占空间 | Postgres 表膨胀 | reaper job：PAUSED > audit_retention_days 转冷归档 |
| checkpoint 表分区切换时崩溃 | 写入失败 | 分区由 [23](./23-postgres-scalability.md) 提前预创建；分区不存在时降级写默认分区 + 告警 |

---

## 7. 可观测性

> 命名规范、日志必填字段、span attrs 强制约定遵循 [20 Observability § 5.1 / § 5.3](./20-observability.md)；
> 本节只列本子系统特有的 metric / span / 日志事件。

### 7.1 Metric

```
expert_work_checkpoint_write_duration_seconds{tenant,result}        histogram
expert_work_checkpoint_size_bytes{tenant}                           histogram
expert_work_thread_state{tenant,state}                              gauge
expert_work_thread_pause_duration_seconds{tenant,reason}            histogram
expert_work_idempotent_replay_total{tenant,tool,result}             counter
   # result = hit_ok | hit_failed | miss
expert_work_resume_total{tenant,outcome}                            counter
   # outcome = ok | version_mismatch | force | rejected
expert_work_state_blob_externalize_total{tenant,field}              counter
```

### 7.2 OTel span

- `expert_work.durable.checkpoint`（attrs：thread_id, size_bytes, event_count, duration_ms）
- `expert_work.durable.replay`（attrs：thread_id, replayed_events, hit_count）
- `expert_work.durable.pause`（attrs：thread_id, reason）
- `expert_work.durable.resume`（attrs：thread_id, actor_id, force）

### 7.3 关键日志

每次 pause / resume / force_resume 都写 INFO 级别结构化日志，必带 `tenant / thread_id / agent / agent_version / actor_id / trace_id`。

---

## 8. 安全考虑

| 攻击面 | 防御 |
|--------|------|
| 客户端伪造 idempotency_key 重放历史动作 | server 端生成，永不接受客户端传入 |
| pre_call 写入但 result 未写时被攻击者 force_resume 跳过 | force_resume 单独权限（admin 角色），且写审计 + 标 `bypassed=true` |
| state 外存到 S3 时跨租户读取 | 路径前缀含 `tenant/`，对象存储 IAM 按 tenant 划 prefix；KMS 按 tenant 派生密钥 |
| checkpoint 中含 PII | 写入前过 PII redactor 中间件（按 `tenant_config.pii_fields`） |
| resume 跨租户（A 租户用户 resume B 租户 thread） | resume API 强制 JWT 中 tenant == thread.tenant；审计记录 |
| 未授权 cancel | cancel 校验 `session.create` 的同 actor 或 admin |
| 重放攻击：复用过期的 idempotency_key | uuid7 含时间戳；event_log 仅匹配同 thread；跨 thread key 不复用 |

**关键决策**：**force_resume 与 cancel_with_state_keep 都视为高风险动作**，必须经审计 + 单独 RBAC permission `durable:force`。

---

## 9. M0 / M1 / M2 演进

### M0（5-7 周）—— 短会话基础
- 直接用 LangGraph PostgresSaver；checkpoint 默认 every_n=20
- 仅支持 idempotent 工具（manifest 不允许 `idempotent: false`，lint 阶段拒绝）
- pause / resume API 暂不开放
- 进程崩溃后 30 分钟内手工触发恢复脚本（无自动接管）

### M1（6-8 周）—— 副作用幂等 + 自动接管
- idempotency_key 协议上线；HTTP/MCP 工具透传 Idempotency-Key
- advisory lock + 自动 worker 接管
- pause / resume API 开放（仅 manual reason，HITL 推 M2）
- state size 监控 + 外存（>1MB 触发）

### M2（6-8 周）—— 长会话 + HITL
- 与 [25 HITL](./25-hitl.md) 集成，interrupt 自动 pause
- 长会话（小时级、跨节点）完整支持
- 版本兼容判定 + force_resume RBAC
- 上下文压缩触发（详见 [27 上下文压缩](./27-context-compression.md)）；冷归档 PAUSED thread

### M3 —— K8s 接管
- 改造为 K8s Job + Operator，进程接管由 controller 处理
- 跨集群 checkpoint 复制（基于 logical replication）

---

## 10. 开放问题

1. **N=20 是否合适**：不同业务工具密度不同；是否按 EWMA 自适应？倾向 manifest 可调 + 默认 20。
2. **state 外存的引用一致性**：blob 写完再写 checkpoint，还是先写 checkpoint 再写 blob？倾向"先 blob 后 checkpoint"，blob 失败回滚 checkpoint。
3. **Idempotency-Key TTL**：HTTP 工具侧 key 多久后释放？业界常见 24h；我们与 audit_retention 对齐还是固定？倾向固定 7 天。
4. **跨 region resume**：M3 多 region 时，PAUSED 在 region A、resume 请求落 region B 怎么处理？倾向 thread 绑 home region，路由层导流。
5. **HITL 期间用户取消是否退已发起的副作用**：不退（副作用语义不可逆）；写 audit 标"用户在副作用 X 之后取消"。
