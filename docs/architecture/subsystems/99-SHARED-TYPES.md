# 99 Shared Types — 跨子系统共享类型与枚举

> 所有 ≥ 2 个子系统共用的 Pydantic enum / SQLAlchemy enum / 状态机表 / 类型别名集中定义点。
> 子系统 doc **引用**本文件而非重定义。本文件不引入业务逻辑，纯 schema。

---

## 1. 使用约定

- Pydantic enum 统一用 `StrEnum` / `Literal`（不要混用 `IntEnum`，可读性差且易因排序变更）
- 任何 enum 变更走 PR review；**新增** enum 值不需要架构 review，**删除/重命名** 必须有迁移脚本 + 至少跨 2 个 minor version
- 子系统 doc 通过 `from expert_work.types import EventType` 等方式引用
- DDL 列类型 → 用 `TEXT + CHECK` 约束（而非 PG 原生 `enum` 类型；后者迁移困难）
- 跨子系统的 `Literal[...]` 不再各自重写，统一 `re-export` 自本模块
- 本文件维护权威列表；CI lint 校验所有子系统 doc 中提到的 enum 值都在本文件登记

代码导出位置（建议）：

```
packages/expert-work-protocol/src/Expert Work/types/
├── __init__.py
├── events.py          # EventType
├── audit.py           # ActorType / ResourceType / AuditAction
├── state.py           # ThreadState / SubagentState / HITLState / ...
├── isolation.py       # IsolationLevel / ComplianceLevel
└── aliases.py         # TenantId / SessionId / IdempotencyKey / ...
```

---

## 2. EventType（`event_log.event_type` 列值）

事件总线统一类型枚举。按贡献子系统分组（见 [23 Postgres Scalability § 3.2](./23-postgres-scalability.md) `event_log` 表定义）：

```python
# packages/expert-work-protocol/src/Expert Work/types/events.py
from enum import StrEnum

class EventType(StrEnum):
    # === 19 Durable Execution ===
    TOOL_PRE_CALL          = "tool_pre_call"
    TOOL_RESULT            = "tool_result"
    STATE_SNAPSHOT         = "state_snapshot"
    ERROR                  = "error"
    PAUSE                  = "pause"
    RESUME                 = "resume"
    FORCE_RESUME           = "force_resume"

    # === 24 Subagent Execution ===
    SUBAGENT_SPAWN_PRE     = "subagent_spawn_pre"
    SUBAGENT_RESULT        = "subagent_result"
    SUBAGENT_CANCELLED     = "subagent_cancelled"
    SUBAGENT_TIMEOUT       = "subagent_timeout"

    # === 25 HITL ===
    HITL_PENDING           = "hitl_pending"
    HITL_APPROVED          = "hitl_approved"
    HITL_REJECTED          = "hitl_rejected"
    HITL_TIMEOUT           = "hitl_timeout"

    # === 27 Context Compression ===
    CONTEXT_COMPRESSED     = "context_compressed"

    # === 10 LLM Gateway ===
    LLM_CALL_PRE             = "llm_call_pre"
    LLM_CALL_RESULT          = "llm_call_result"
    LLM_FALLBACK_USED        = "llm_fallback_used"
    LLM_PROVIDER_CIRCUIT_OPEN = "llm_provider_circuit_open"
```

DDL 用 CHECK 约束：

```sql
ALTER TABLE event_log
  ADD CONSTRAINT event_log_event_type_chk CHECK (event_type IN (
    -- 19 Durable Execution
    'tool_pre_call','tool_result','state_snapshot','error','pause','resume','force_resume',
    -- 24 Subagent
    'subagent_spawn_pre','subagent_result','subagent_cancelled','subagent_timeout',
    -- 25 HITL
    'hitl_pending','hitl_approved','hitl_rejected','hitl_timeout',
    -- 27 Context Compression
    'context_compressed',
    -- 10 LLM Gateway
    'llm_call_pre','llm_call_result','llm_fallback_used','llm_provider_circuit_open'
  ));
```

> 新增 event_type：1) 在贡献子系统 doc 里说明语义；2) 这里登记；3) 同一个 PR 改 CHECK 约束 + 部署迁移；4) 旧版本读取到未知 type 必须降级处理（log warning，不 crash）。

---

## 3. ActorType（`audit_log.actor_type` 列值）

```python
# packages/expert-work-protocol/src/Expert Work/types/audit.py
from enum import StrEnum

class ActorType(StrEnum):
    USER             = "user"              # 真人；JWT.sub_type=user
    SERVICE_ACCOUNT  = "service_account"   # CLI / CI / cron 任务的虚拟身份
    SYSTEM           = "system"            # 引擎自身（如 timeout reaper、cleanup job、circuit breaker）
    AGENT            = "agent"             # agent 主动行为（agent_name@version 形式）
```

| Value | 语义 | actor_id 形式 |
|-------|------|--------------|
| `user` | 真人 | `user.id`（UUID） |
| `service_account` | 服务账号（CLI / CI） | `service_account.id`（UUID） |
| `system` | 引擎自身 | 固定字符串如 `"system:timeout_reaper"` / `"system:cleanup_job"` |
| `agent` | Agent 自主调用敏感 API | `<agent_name>@<version>` |

**关键约束**：`actor_type='agent'` 时必须同时填 `on_behalf_of`（驱动该 agent 的原始 user / sa），形成 attribution 链。

---

## 4. ResourceType（`audit_log.resource_type` 列值）

按 [17 § 5.1 audit 词表](./17-audit-log.md) + 各子系统贡献汇总。

```python
class ResourceType(StrEnum):
    MANIFEST       = "manifest"        # 18 Manifest 供应链
    SESSION        = "session"         # 19 Durable Execution / 25 HITL
    SANDBOX        = "sandbox"         # 14 Sandbox Pool
    SECRET         = "secret"          # 11 Credential Proxy
    AUDIT          = "audit"           # 17 自审计
    QUOTA          = "quota"           # 16 Quota / Rate Limit
    USER           = "user"            # 15 AuthN/AuthZ
    ROLE_BINDING   = "role_binding"    # 15 AuthN/AuthZ
    API_KEY        = "api_key"         # 15 AuthN/AuthZ
    DR             = "dr"              # 22 Disaster Recovery
    EVAL           = "eval"            # 26 Eval Framework
    SUBAGENT       = "subagent"        # 24 Subagent Execution
    ROLE           = "role"            # 15 AuthN/AuthZ（角色定义本身的 CRUD）
    AUTH           = "auth"            # 15 AuthN/AuthZ（登录/登出/refresh 这类不绑定具体 resource_id 的事件）
```

`resource_id` 形式约定：

| resource_type | resource_id 形式 |
|---------------|------------------|
| manifest | `<name>@<version>` |
| session | session UUID |
| sandbox | sandbox UUID |
| secret | secret 的 key 路径（`<scope>/<name>`，不含明文） |
| eval | `eval_set:<name>@<version>` 或 `eval_run:<UUID>` |
| subagent | `subagent_invocation.id`（UUID） |
| role_binding / api_key | UUID |
| dr | `restore:<job_id>` / `failover:<region>` / `drill:<plan_id>` |
| auth | NULL（事件挂在 actor 上） |

---

## 5. AuditAction（`audit_log.action` 列值，`<resource>:<verb>` 格式）

权威 action 词表。CI lint 校验所有 `audit.write(action=...)` 在此清单内。

```python
class AuditAction(StrEnum):
    # auth:* — 15 AuthN/AuthZ
    AUTH_LOGIN          = "auth:login"
    AUTH_LOGOUT         = "auth:logout"
    AUTH_LOGIN_FAILED   = "auth:login_failed"
    AUTH_TOKEN_REFRESH  = "auth:token_refresh"

    # manifest:* — 18 Supply Chain
    MANIFEST_READ       = "manifest:read"
    MANIFEST_WRITE      = "manifest:write"
    MANIFEST_DELETE     = "manifest:delete"
    MANIFEST_SIGN       = "manifest:sign"
    MANIFEST_VERIFY     = "manifest:verify"
    MANIFEST_PROMOTE    = "manifest:promote"
    MANIFEST_PUBLISH    = "manifest:publish"
    MANIFEST_REVOKE     = "manifest:revoke"

    # session:* — 19 Durable Execution / 25 HITL
    SESSION_READ          = "session:read"
    SESSION_WRITE         = "session:write"
    SESSION_CANCEL        = "session:cancel"
    SESSION_DEBUG         = "session:debug"
    SESSION_RESUME        = "session:resume"
    SESSION_PAUSE         = "session:pause"
    SESSION_FORCE_RESUME  = "session:force_resume"

    # sandbox:* — 14 Sandbox Pool
    SANDBOX_ACQUIRE        = "sandbox:acquire"
    SANDBOX_RELEASE        = "sandbox:release"
    SANDBOX_DEBUG          = "sandbox:debug"
    SANDBOX_FORCE_DESTROY  = "sandbox:force_destroy"
    SANDBOX_QUOTA_DENIED   = "sandbox:quota_denied"

    # secret:* — 11 Credential Proxy
    SECRET_READ    = "secret:read"
    SECRET_WRITE   = "secret:write"
    SECRET_INJECT  = "secret:inject"
    SECRET_ROTATE  = "secret:rotate"
    SECRET_DELETE  = "secret:delete"

    # audit:* — 17 自审计
    AUDIT_READ     = "audit:read"
    AUDIT_EXPORT   = "audit:export"

    # quota:* — 16 Quota / Rate Limit
    QUOTA_READ                = "quota:read"
    QUOTA_WRITE               = "quota:write"
    QUOTA_EXCEEDED_LOG        = "quota:exceeded_log"
    QUOTA_RATE_LIMIT_DENIED   = "quota:rate_limit_denied"

    # user / role_binding / api_key — 15 AuthN/AuthZ
    USER_CREATE          = "user:create"
    USER_UPDATE          = "user:update"
    USER_DISABLE         = "user:disable"
    ROLE_BINDING_GRANT   = "role_binding:grant"
    ROLE_BINDING_DENY    = "role_binding:deny"
    ROLE_BINDING_REVOKE  = "role_binding:revoke"
    ROLE_BINDING_CREATE  = "role_binding:create"
    ROLE_BINDING_DELETE  = "role_binding:delete"
    API_KEY_ISSUE        = "api_key:issue"
    API_KEY_CREATE       = "api_key:create"
    API_KEY_REVOKE       = "api_key:revoke"
    API_KEY_USED         = "api_key:used"

    # dr:* — 22 Disaster Recovery
    DR_RESTORE      = "dr:restore"
    DR_FAILOVER     = "dr:failover"
    DR_DRILL        = "dr:drill"
    DR_BACKUP_RUN   = "dr:backup_run"

    # eval:* — 26 Eval Framework
    EVAL_FORCE_PROMOTE   = "eval:force_promote"
    EVAL_DATASET_UPLOAD  = "eval:dataset_upload"

    # subagent:* — 24 Subagent Execution
    SUBAGENT_SPAWN_DENIED         = "subagent:spawn_denied"
    SUBAGENT_DEPTH_LIMIT_VIOLATION = "subagent:depth_limit_violation"
    SUBAGENT_CROSS_TENANT_ATTEMPT = "subagent:cross_tenant_attempt"
```

DDL 校验（在 audit_log 表上）：

```sql
ALTER TABLE audit_log
  ADD CONSTRAINT audit_log_action_format_chk
  CHECK (action ~ '^[a-z_]+:[a-z_]+$');     -- 形态校验，词表完整性由 CI lint 保证
```

> 完整词表与 CI 校验脚本同步，详见 [17 § 5.1](./17-audit-log.md)。

---

## 6. IsolationLevel

```python
# packages/expert-work-protocol/src/Expert Work/types/isolation.py
from enum import StrEnum

class IsolationLevel(StrEnum):
    SHARED             = "shared"               # 与同租户其他 agent 共享 sandbox warm pool
    DEDICATED_SANDBOX  = "dedicated_sandbox"    # 每 session 独占 sandbox 实例，acquire 后不回池
    DEDICATED_NODE     = "dedicated_node"       # 节点级亲和，绝不与他租户共节点
```

| compliance_pack | 强制最低 IsolationLevel |
|-----------------|------------------------|
| `null`          | `shared`（用户可选高） |
| `gdpr`          | `shared`（数据驻留通过 region 控制） |
| `sox`           | `dedicated_sandbox` |
| `hipaa`         | `dedicated_sandbox`（M2 起 `dedicated_node`） |

校验逻辑写在 [02 AGENT MANIFEST](../02-AGENT-MANIFEST.md) 静态校验阶段；运行时由 [14 Sandbox Pool](./14-sandbox-pool.md) 二次校验。

---

## 7. ComplianceLevel（即 `compliance_pack`）

```python
class ComplianceLevel(StrEnum):
    HIPAA  = "hipaa"
    GDPR   = "gdpr"
    SOX    = "sox"
    # null（即 None）= 无合规约束，普通业务
```

引擎根据该字段自动注入：
- 加密策略（at-rest / in-transit）
- PII redactor 启用
- audit_retention_days 默认值
- IsolationLevel 强制下限
- 部分 manifest 字段可选性变化（如 HIPAA 强制启用 `output_filter`）

详见 [02 AGENT MANIFEST § tenant_config](../02-AGENT-MANIFEST.md)。

---

## 8. 状态机汇总表

一表看清所有子系统的状态空间与终态：

| 子系统 | 状态机名 | 状态值 | 终态 |
|--------|---------|--------|------|
| [19 Durable Execution](./19-durable-execution.md) | ThreadState | `RUNNING` / `PAUSED` / `COMPLETED` / `FAILED` / `CANCELLED` | `COMPLETED` / `FAILED` / `CANCELLED` |
| [24 Subagent Execution](./24-subagent-execution.md) | SubagentState | `PENDING` / `RUNNING` / `COMPLETED` / `FAILED` / `CANCELLED` / `TIMED_OUT` | 后 4 个 |
| [25 HITL](./25-hitl.md) | HITLState | `PENDING` / `APPROVED` / `REJECTED` / `ESCALATED` / `BYPASSED` / `TIMEOUT` | 除 `PENDING` 外全部 |
| [14 Sandbox Pool](./14-sandbox-pool.md) | SandboxInstanceState | `CREATING` / `READY` / `IN_USE` / `CLEANING` / `DESTROYED` / `FAILED` | `DESTROYED` / `FAILED` |
| [18 Manifest 供应链](./18-manifest-supply-chain.md) | ManifestStatus | `draft` / `signed` / `promoted` / `revoked` | `revoked` |
| [27 Context Compression](./27-context-compression.md) | CompressionState | `NORMAL` / `TRIGGERED` / `SUMMARIZING` / `COMPLETED` / `FAILED_FALLBACK` / `FAILED_HARD` | `COMPLETED` / `FAILED_FALLBACK` / `FAILED_HARD` |
| [26 Eval Framework](./26-eval-framework.md) | EvalRunState | `QUEUED` / `RUNNING` / `PASSED` / `FAILED` / `ERROR` / `CANCELLED` | 后 4 个 |
| [10 LLM Gateway](./10-llm-gateway.md) | CircuitState | `closed` / `open` / `half_open` | — （非业务终态）|

```python
# packages/expert-work-protocol/src/Expert Work/types/state.py
from enum import StrEnum

class ThreadState(StrEnum):
    RUNNING   = "RUNNING"
    PAUSED    = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED    = "FAILED"
    CANCELLED = "CANCELLED"

class SubagentState(StrEnum):
    PENDING   = "PENDING"
    RUNNING   = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED    = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"

class HITLState(StrEnum):
    PENDING   = "PENDING"
    APPROVED  = "APPROVED"
    REJECTED  = "REJECTED"
    ESCALATED = "ESCALATED"
    BYPASSED  = "BYPASSED"
    TIMEOUT   = "TIMEOUT"

class SandboxInstanceState(StrEnum):
    CREATING  = "CREATING"
    READY     = "READY"
    IN_USE    = "IN_USE"
    CLEANING  = "CLEANING"
    DESTROYED = "DESTROYED"
    FAILED    = "FAILED"

class CompressionState(StrEnum):
    NORMAL          = "NORMAL"
    TRIGGERED       = "TRIGGERED"
    SUMMARIZING     = "SUMMARIZING"
    COMPLETED       = "COMPLETED"
    FAILED_FALLBACK = "FAILED_FALLBACK"
    FAILED_HARD     = "FAILED_HARD"

class EvalRunState(StrEnum):
    QUEUED    = "QUEUED"
    RUNNING   = "RUNNING"
    PASSED    = "PASSED"
    FAILED    = "FAILED"
    ERROR     = "ERROR"
    CANCELLED = "CANCELLED"
```

> 终态判断统一通过 `state.is_terminal()` helper 提供（包内实现），避免每个子系统重写。

---

## 9. 公共类型别名

```python
# packages/expert-work-protocol/src/Expert Work/types/aliases.py
from typing import NewType
from uuid import UUID

# tenant 标识：UUID 或人类可读子域名都允许，TEXT 列存储
# 不强制 UUID 因运维写 manifest 时人类可读更直观
TenantId      = NewType("TenantId", str)

# session / thread 是同一个标识的不同语义视角
SessionId     = NewType("SessionId", UUID)
ThreadId      = SessionId                            # 语义别名，LangGraph 视角

# 副作用工具的幂等键，uuid7（含时间戳便于排查）
IdempotencyKey = NewType("IdempotencyKey", UUID)

# W3C trace context 格式（32 hex char）
TraceId       = NewType("TraceId", str)

# agent 引用：name@version（version 支持 semver 范围如 ^2.0）
AgentRef      = NewType("AgentRef", str)

# actor 标识，TEXT 形态见 § 3
ActorId       = NewType("ActorId", str)

# secret 引用：scope/name（不含明文，由 Credential Proxy 解析）
SecretRef     = NewType("SecretRef", str)
```

> `NewType` 仅在静态检查时区分；运行时等同 base type，不引入额外开销。

---

## 10. Postgres 约定

跨所有 tenant-scoped 表的统一约定，新表必须遵守：

- **RLS session 变量**：统一 `app.tenant_id`（不要 `current_setting('app.tenant')` 等变体）
- **tenant 列名**：统一 `tenant_id TEXT NOT NULL`（旧表用 `tenant` 的逐步迁移）
- **actor 字段**：统一 `actor_id TEXT NOT NULL`（兼容 user UUID / `service_account` UUID / `"system:*"` / `<agent>@<version>`）
- **时间戳**：`TIMESTAMPTZ NOT NULL DEFAULT now()`，列名 `created_at` / `updated_at` / `<verb>ed_at`
- **ID 主键**：业务表 `UUID DEFAULT uuid_generate_v7()`（含时间戳，索引友好）；append-only 高吞吐表（如 `event_log`）可用 `BIGSERIAL`
- **JSONB**：默认 `'{}'::jsonb`；不允许 `NULL` JSONB（避免 `IS NULL` vs `= '{}'` 双查）
- **RLS 策略**：写入用 `WITH CHECK`，读取用 `USING`，统一模式：

```sql
CREATE POLICY tenant_isolation_<table> ON <table>
  USING (tenant_id = current_setting('app.tenant_id', true))
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
ALTER TABLE <table> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <table> FORCE ROW LEVEL SECURITY;       -- 表 owner 也要受 RLS 约束
```

- **跨租户读取**：必须切换到专用 reader role（如 `audit_reader`），不在应用代码层 if-admin-skip-filter（参考 [15 § 4.3](./15-authn-authz.md)）

---

## 11. 维护与扩展

### 新增 enum 值的流程

1. 在贡献子系统的 doc 里说明语义、何时触发、payload 例子
2. 同一 PR 在本文件登记（含 docstring）
3. 同一 PR 改 DDL CHECK 约束 + 部署迁移脚本
4. CI lint 校验本文件所列与代码实际使用一致
5. 旧版本读取到未知值必须降级处理（log warning，不 raise；写入侧严格校验）

### 删除 / 重命名 enum 值

1. 至少跨 2 个 minor version 的 deprecation 期
2. 第 1 个 version：标 deprecated，新写入禁止，旧值仍可读
3. 第 2 个 version：清理代码 + 迁移脚本批量改写历史数据 + 移除 CHECK 约束中的旧值
4. 必须有 audit 行说明本次迁移（`action='audit:enum_migration'`，需先在词表登记）

### CI lint 规则（`.github/workflows/types-lint.yml`）

- 扫描所有子系统 doc 中的 `EventType.*` / `AuditAction.*` / `*State.*` 引用，与本文件字典比对
- 扫描所有 `audit.write(action=...)` 实际字面量，与 `AuditAction` 枚举比对
- 不一致 → fail PR

### 与 02 manifest 字段的关系

manifest YAML 里的字符串值（`compliance_pack`、`isolation_level`）必须取值于本文件枚举；Pydantic 模型直接 import：

```python
from expert_work.types import ComplianceLevel, IsolationLevel

class TenantConfig(BaseModel):
    compliance_pack: ComplianceLevel | None = None
    isolation_level: IsolationLevel = IsolationLevel.SHARED
    ...
```

---

## 12. 与上层 doc 的关系

| 上层 doc | 关系 |
|---------|------|
| [02 AGENT MANIFEST](../02-AGENT-MANIFEST.md) | manifest 字段值取自本文件枚举 |
| [00-INDEX](./00-INDEX.md) | 本文件作为附录索引登记 |
| [17 Audit Log](./17-audit-log.md) | action 词表权威定义；本文件 mirror 之 |
| [23 Postgres Scalability](./23-postgres-scalability.md) | event_log / audit_log DDL；本文件提供 CHECK 约束清单 |

> 本文件是横切性质的"词典"；不引入业务逻辑，只承担"避免散落重复定义" + "CI 可机器校验"两项责任。
