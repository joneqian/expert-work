# 10 LLM Gateway / Provider Router

> 所有 LLM 请求的**统一入口**：多 provider failover、token 预算预扣、prompt cache 命中策略、每租户 key 隔离、观测埋点。

---

## 1. 职责 & 边界

### ✅ 做
- **统一抽象** Anthropic / OpenAI / Azure / 自托管模型，对 orchestrator 暴露同一套 `chat(messages, tools, ...) -> ChatResponse` 协议
- **Provider failover**：主模型连续失败超阈值后自动切换到 fallback chain
- **重试与退避**：按错误码分类（4xx / 429 / 5xx）执行不同重试策略
- **Prompt cache 命中保护**：拒绝/告警 system prompt + tool schema 中含动态值的请求
- **Token 预算协同**：调用前向 [16 Quota](./16-quota-rate-limit.md) 预扣 reservation，调用后按真实 usage 补差
- **Per-tenant key 隔离**：每个租户的 model API key 来自 [11 Credential Proxy](./11-credential-proxy.md)，不混用
- **可观测性**：每次调用都有 OTel span + Prometheus metric + redacted log
- **断路器**：按 `(provider, key)` 维度，连续失败打开断路，避免雪崩

### ❌ 不做
- **不做**业务侧 prompt 工程（system_prompt 来自 manifest，gateway 不改写）
- **不做**多模型语义投票 / ensemble（M3 之后议题）
- **不做** LLM response cache（这是 [orchestrator 中间件层](../03-MONOREPO-LAYOUT.md) 的职责，gateway 只关心一次具体调用）
- **不引入** LiteLLM、langchain LLM wrappers——耦合过重，自实现 ~800 行可控

---

## 2. 上下游依赖

```
┌──────────────────────────────────────────────────────────┐
│ Orchestrator (LangGraph llm_node)                        │
│   └─ 通过 SDK: gateway_client.chat(spec, messages, ...)  │
└─────────────────────┬────────────────────────────────────┘
                      ▼
              ┌───────────────────┐
              │  LLM Gateway      │
              │  (此子系统)        │
              └───┬───────┬───────┘
                  │       │
                  │       └─→ [11 Credential Proxy]  取 provider API key
                  │       └─→ [16 Quota]             预扣/补差 token 预算
                  │       └─→ [17 Audit Log]         记录调用元数据（不含 body）
                  │       └─→ [20 Observability]     trace / metric / log
                  ▼
        ┌────────────────────────────┐
        │  Anthropic / OpenAI / ...  │
        └────────────────────────────┘
```

调用时机：仅由 **orchestrator 内部**调用；**业务代码（manifest 的 python 插槽）禁止**直接 import provider SDK——通过 `helix.sdk.llm` 客户端走 gateway。

---

## 3. 数据模型 / 状态机

### 3.1 Postgres DDL — 调用记录与断路器状态

```sql
-- 调用元数据表（不写 prompt/response 明文，明文走 event_log）
CREATE TABLE llm_call_log (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       TEXT        NOT NULL,
    agent_name      TEXT        NOT NULL,
    agent_version   TEXT        NOT NULL,
    session_id      UUID,
    run_id          UUID,
    provider        TEXT        NOT NULL,      -- anthropic / openai / azure / self_hosted
    model           TEXT        NOT NULL,      -- claude-sonnet-4-5 / gpt-4o / ...
    role            TEXT        NOT NULL,      -- primary / fallback_1 / fallback_2
    input_tokens    INT         NOT NULL DEFAULT 0,
    output_tokens   INT         NOT NULL DEFAULT 0,
    cache_read      INT         NOT NULL DEFAULT 0,
    cache_creation  INT         NOT NULL DEFAULT 0,
    latency_ms      INT         NOT NULL,
    status          TEXT        NOT NULL,      -- ok / retry_4xx / retry_429 / retry_5xx / failed / fallback_used
    error_code      TEXT,                       -- e.g. "anthropic.rate_limit_error"
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX llm_call_log_tenant_started ON llm_call_log (tenant_id, started_at DESC);
CREATE INDEX llm_call_log_session ON llm_call_log (session_id);

-- 断路器状态（共享存储，多 orchestrator 副本一致）
CREATE TABLE llm_circuit_state (
    provider        TEXT NOT NULL,
    key_ref         TEXT NOT NULL,             -- credential proxy 的 secret ref，不存明文
    state           TEXT NOT NULL,             -- closed / open / half_open
    failures        INT  NOT NULL DEFAULT 0,
    opened_at       TIMESTAMPTZ,
    next_probe_at   TIMESTAMPTZ,
    PRIMARY KEY (provider, key_ref)
);
```

### 3.2 Pydantic Schema — 请求/响应契约

```python
# packages/helix-protocol/src/helix/protocol/llm.py
from pydantic import BaseModel, Field
from typing import Literal, Any

class LLMMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[dict]              # 多模态时为 list
    cache_control: Literal["ephemeral"] | None = None

class LLMToolSchema(BaseModel):
    name: str
    description: str
    input_schema: dict                     # JSON Schema
    cache_control: Literal["ephemeral"] | None = None

class LLMRequest(BaseModel):
    tenant: str
    agent: str
    agent_version: str
    session_id: str | None = None          # 语义 ≡ thread_id；调 [16 Quota] reserve_tokens 时以 thread_id 字段名传入
    run_id: str | None = None

    model_spec: "ModelSpec"                # 来自 manifest，含 fallback chain
    messages: list[LLMMessage]
    tools: list[LLMToolSchema] = []
    tool_choice: Literal["auto", "any", "none"] | dict = "auto"
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool = True

    reservation_id: str                    # Quota 子系统返回的预扣 ID
    purpose: Literal["production", "summarization", "eval", "judge"] = "production"
    # M0/M1 默认 production；M2 起 eval 与 judge 走独立 quota / audit 采样
    # 用途分类影响 [16 Quota](./16-quota-rate-limit.md) 维度归集与 [17 Audit Log](./17-audit-log.md)
    # 采样策略；`summarization` 由 [27 上下文压缩](./27-context-compression.md) 设置

class LLMUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

class LLMResponse(BaseModel):
    provider: str
    model: str
    role_used: Literal["primary", "fallback_1", "fallback_2", "fallback_3"]
    message: LLMMessage
    tool_calls: list[dict] = []
    stop_reason: str
    usage: LLMUsage
    latency_ms: int
```

### 3.3 断路器状态机

```
        ┌────────────┐  failures >= threshold (默认 5/30s)
        │   CLOSED   │ ──────────────────────────────────► OPEN
        └────────────┘                                       │
              ▲                                              │
              │ probe success                                │ cooldown 到期
              │                                              ▼
        ┌─────┴────────┐                              ┌───────────────┐
        │ HALF_OPEN    │ ◄──── probe (1 req allowed) ─│  next_probe   │
        └──────────────┘                              └───────────────┘
              │ probe fail
              └──────────────────────────────────────► OPEN（重置 cooldown）
```

---

## 4. 关键接口

### 4.1 Python SDK（orchestrator 调用方）

```python
# packages/helix-sdk/src/helix/sdk/llm.py
from helix.protocol.llm import LLMRequest, LLMResponse

class LLMGatewayClient:
    async def chat(self, req: LLMRequest) -> LLMResponse: ...
    async def stream(self, req: LLMRequest) -> AsyncIterator[LLMChunk]: ...

# 使用
resp = await llm.chat(LLMRequest(
    tenant=ctx.tenant, agent=ctx.agent, agent_version=ctx.agent_version,
    session_id=ctx.session_id, run_id=ctx.run_id,
    model_spec=spec.model,
    messages=messages,
    tools=tool_schemas,
    reservation_id=reservation.id,
))
```

### 4.2 Provider 抽象基类

```python
# services/llm-gateway/src/llm_gateway/providers/base.py
from abc import ABC, abstractmethod

class BaseProvider(ABC):
    name: str

    @abstractmethod
    async def chat(self, req: LLMRequest, api_key: str) -> LLMResponse: ...

    @abstractmethod
    async def stream(self, req: LLMRequest, api_key: str) -> AsyncIterator[LLMChunk]: ...

    @abstractmethod
    def classify_error(self, exc: Exception) -> "ErrorClass": ...
    # ErrorClass = retryable_4xx | rate_limit_429 | retryable_5xx | non_retryable | network

# 已实现
class AnthropicProvider(BaseProvider): ...   # 直接用 anthropic SDK
class OpenAIProvider(BaseProvider): ...      # 直接用 openai SDK
# Azure 复用 OpenAIProvider，仅 base_url 不同
```

### 4.3 内部 HTTP API（与 orchestrator 同进程时为函数调用，跨进程时为内部 RPC）

| Method | Path | 说明 |
|---|---|---|
| POST | `/v1/chat` | 非流式 chat（用于 sync 工具调用） |
| POST | `/v1/chat/stream` | SSE 流式 |
| GET  | `/v1/circuit/{provider}/{key_ref}` | 调试用：查断路器状态 |

> M0 阶段 LLM Gateway 与 orchestrator **同进程**部署（按 module 调用），M1+ 拆为独立 service 通过 mTLS 通信。

---

## 5. 算法 / 关键决策

### 5.1 调用主流程

```
1. 校验请求（tenant/agent/reservation_id 必填）
2. 检查 prompt cache 静态性（见 5.4）；不通过 → 拒绝或降级（按 manifest）
3. 选择 primary provider+model
4. 取该 (provider, key_ref) 断路器状态：
   - OPEN 且 now < next_probe_at → 直接走 fallback
   - HALF_OPEN → 仅放行 1 请求
   - CLOSED → 正常进入
5. 通过 [11 Credential Proxy] 取 API key
6. 调用 provider.chat()
7. 异常：分类 → 重试或 fallback（见 5.2）
8. 成功：写 llm_call_log + 上报 usage 给 [16 Quota] 补差
9. 返回 LLMResponse
```

### 5.2 重试与 fallback 策略（**关键决策**）

| 错误类型 | 行为 | 退避 |
|---|---|---|
| 4xx（参数错误、模型不存在） | **不重试**，立即抛 | — |
| 401/403（鉴权失败） | **不重试**，触发 secret 刷新告警 | — |
| 429（rate limit） | 重试最多 3 次 | 指数退避 + 抖动，0.5s/2s/8s |
| 5xx（服务端错误） | 重试最多 2 次 | 短退避 0.2s/1s |
| 网络/超时 | 重试最多 2 次 | 短退避 |
| Prompt cache miss（高频） | 不重试，但触发告警 | — |

**Fallback 策略**：primary 上面所有重试都失败后，**按 manifest 中 `model.fallback` 顺序**切换。
**核心约束**：fallback 链上的每一跳**不再做 4xx/5xx 重试**——失败立刻往下走，避免级联高延迟。

#### 两级降级语义（**关键决策**）

降级分两级，引擎默认为每个 manifest 自动注入 `[L1 同 provider 等价模型, L2 跨 provider 等价模型]` 二级 fallback chain：

| 层级 | 含义 | 切换成本 | 示例 |
|---|---|---|---|
| **L1 同 provider 内降级** | 同一 provider 同 key、同 region、同 prompt format、同 tool schema；零适配 | 秒级，无 prompt 改写 | Claude Sonnet → Claude Haiku |
| **L2 跨 provider 降级** | 不同 provider 不同 key；prompt/tool schema 需转换；stop reason 需映射 | 需要适配层（runtime 内置 converter） | Anthropic Claude → OpenAI GPT-4o |

**跨 provider 适配规则**（L2 切换时引擎自动执行）：

- **tool schema converter**：Anthropic `input_schema` ↔ OpenAI `function.parameters` 格式互转；不支持的特性（如 Anthropic computer use tool）在跨 provider 时降级为文本提示
- **system message 注入位置**：Anthropic 走顶层 `system` 参数 / OpenAI 走 `messages[0].role=system`；converter 屏蔽差异
- **stop reason 映射表**：`end_turn → stop` / `tool_use → tool_calls` / `max_tokens → length` / `stop_sequence → stop`

**降级感知**：fallback 成功（非 primary）后在 LLMResponse.role_used 字段标注 `fallback_*`；orchestrator 中间件可读此字段决定是否在响应中追加"已降级，输出质量可能下降"提示。

### 5.3 Token 预算协同（与 [16 Quota](./16-quota-rate-limit.md)）

API 名称以 [16 Quota](./16-quota-rate-limit.md) 为权威：`reserve_tokens` / `commit_tokens` / `release_tokens`。

```
请求路径：
  ① 调用前：reservation = quota.reserve_tokens(
         tenant=ctx.tenant, agent=ctx.agent, model=spec.model,
         thread_id=request.session_id,           # session_id 语义 ≡ thread_id
         est_tokens=N,
     )
     - est_tokens = sum(input msg tokens) + max_tokens
  ② 调用 provider.chat()
  ③ 调用后：quota.commit_tokens(
         reservation_id,
         actual_tokens=usage.input_tokens + usage.output_tokens,
     )
     - 口径固定：actual_tokens = input_tokens + output_tokens
     - cache_read_input_tokens / cache_creation_input_tokens 单独走 metric
       （helix_llm_gateway_tokens_total{kind=cache_read|cache_creation}），
       不参与 budget commit（避免在缓存命中时多扣业务侧预算）
     - 若 actual > est：补扣差额
     - 若 actual < est：返还差额
  ④ 异常路径：quota.release_tokens(reservation_id)（不计入用量）
```

#### Provider 级断路器（**关键决策，与 model 级独立**）

为支撑 L2 跨 provider 降级语义，新增 provider 级断路器，与 § 3.3 的 `(provider, key_ref)` model 级断路器**独立运行**：

```
provider 整体故障率（5min 滑窗，跨该 provider 所有 model 聚合）> 50% 持续 30s
  → ProviderCircuitBreaker(provider) = OPEN
  → 跳过该 provider 的全部 model（即便它在 fallback chain 中前置）
  → 直接进入下一个不同 provider 的 fallback 候选

cooldown 60s 后 → HALF_OPEN：单请求探测；成功 → CLOSED；失败 → OPEN（重置 cooldown）
```

> 设计意图：当 Anthropic 全局故障时，model 级断路器要逐 model 打开很慢；provider 级断路器一次性熔断该 provider 的所有 model，直接走 L2 跨 provider 降级。

### 5.4 Prompt Cache 命中保护（**关键决策**）

Anthropic prompt cache 命中要求 prefix（system prompt + tool schema）**完全相同**。Gateway 在请求入口做静态性检查：

```python
def assert_static_prefix(req: LLMRequest) -> None:
    # 1. system message 不能含动态时间戳/UUID（正则识别 ISO datetime, UUIDv4）
    # 2. tool schema 的 description 必须可哈希且与上一次相同（agent_version 维度缓存）
    # 3. 如果检查失败：
    #    - manifest.observability.prompt_cache.strict_mode == true → 拒绝（Pydantic ValidationError）
    #    - 否则：metric llm_gateway_cache_unstable_total +1，记 warn log
```

> 设计源自 vendor 的 `dynamic_context_middleware`：动态值通过独立 HumanMessage 注入，**永不进 system_prompt**。

### 5.5 流式实现要点

- 使用 SSE，每个 chunk 形如 `event: delta\ndata: {"text": "..."}\n\n`
- 心跳：每 15s 发 `event: ping`（避免中间代理断开）
- 断点续传：Gateway 不做（属于 [stream_bridge](../03-MONOREPO-LAYOUT.md)），但**必须传 last-event-id**

---

## 6. 失败模式 & 缓解

| 失败模式 | 触发场景 | 影响 | 缓解 |
|---|---|---|---|
| Provider 雪崩 | 主 provider 全局故障 | 所有 agent 不可用 | 断路器 30s 内打开 → 走 fallback；fallback 不再重试 |
| Provider 级雪崩 | 整个 provider 全局故障（跨该 provider 所有 model） | 该 provider 所有 model 失败 | provider 断路器 OPEN → 跳过该 provider 全部 model（即使在 chain 中前置），直接走 L2 跨 provider 等价模型 |
| API key 被吊销 | 厂商运营动作 | 该 key 全部 401 | 检测到 401 → secret 刷新告警 + 立即走 fallback |
| Token 预扣不足 | 实际 output >> 预估 | 补差扣超额 quota | quota.commit 返回 over_budget=true → 标记 session warn，不阻断当前调用 |
| Prompt cache 抖动 | system_prompt 含动态值 | 缓存命中率 < 50%，成本 ↑ 10x | 静态性校验 + metric 告警 + manifest strict_mode |
| 流式中途断开 | 网络抖动 | 用户看到一半 | stream_bridge 重连机制（last-event-id），gateway 不重试 |
| 长 context > model 上限 | 历史消息过长 | 400 context_length_exceeded | 立即抛回 orchestrator，触发 [27 上下文压缩](./27-context-compression.md) 后重试 |
| 限流 (429) 持续 | 突发流量 | 大量 retry | 退避到 8s 后仍 429 → 标记降级 → fallback；同时给 [16 Quota] 上报"provider 侧限流"信号 |
| 单租户打爆 key | 一个租户消耗全部 RPM | 其他租户 429 | per-tenant key（M1）+ Quota 子系统强制 per-tenant rate limit |

---

## 7. 可观测性

> 日志完整字段遵循 [20 § 5.3](./20-observability.md)，此处仅展示子系统专属字段。
> Metric / span 命名遵循 [20 § 5.x 命名规范](./20-observability.md)：metric 统一 `helix_*` 前缀（snake_case），span 统一 `helix.{component}.{action}`。

### Prometheus metrics

```
helix_llm_gateway_requests_total{tenant,agent,provider,model,role,status}
helix_llm_gateway_request_duration_seconds{tenant,agent,provider,model,role}    histogram
helix_llm_gateway_tokens_total{tenant,agent,provider,model,kind}
  # kind ∈ input/output/cache_read/cache_creation
helix_llm_gateway_retries_total{provider,error_class}
helix_llm_gateway_fallback_used_total{tenant,agent,from_provider,to_provider}
helix_llm_gateway_circuit_state{provider,key_ref}                       gauge 0/1/2
helix_llm_gateway_cache_unstable_total{tenant,agent}                    counter
helix_llm_provider_circuit_state{provider,state}                         gauge
  # state ∈ closed/open/half_open；与 model 级断路器独立
helix_quota_exceeded_total{tenant,agent,reason}                          counter
  # 与 [20 § 5] 共享命名；reason ∈ over_budget/over_rate/...（与 16 一致）
```

### OTel spans

```
helix.llm_gateway.chat
├── attrs: tenant, agent, agent_version, model, role, stream, msg_count
├── span: helix.llm_gateway.cache_check
├── span: helix.credential_proxy.fetch (link to [11])
├── span: helix.llm_provider.chat (provider span)
└── span: helix.quota.commit (link to [16])
```

### 日志字段（结构化 JSON，敏感值已 redact；通用字段见 [20 § 5.3]）

```
{
  "trace_id": "...", "tenant": "...", "agent": "...",
  "provider": "anthropic", "model": "claude-sonnet-4-5", "role_used": "primary",
  "input_tokens": 1234, "output_tokens": 256, "cache_read": 1100,
  "duration_seconds": 0.98, "status": "ok",
  "request_msg_hash": "sha256:...", "response_msg_hash": "sha256:..."
}
```

---

## 8. 安全考虑

| 攻击面 | 防御 |
|---|---|
| API key 泄漏 | key 永远从 [11 Credential Proxy](./11-credential-proxy.md) 取，不缓存到磁盘；进程内 LRU TTL ≤ 60s |
| 跨租户串号 | 请求强制带 `tenant`，Provider 调用前断言 `tenant == reservation.tenant` |
| Prompt 注入污染日志 | 日志只写 `request_msg_hash`，明文走 event_log（受 PII redactor 处理） |
| LLM 响应含 system prompt 反吐 | 由 [orchestrator output filter middleware](./20-observability.md) 在 gateway 之外检测，gateway 不参与内容审查 |
| 调试 API 滥用 | `/v1/circuit/...` 仅 admin 角色可访问，由 [15 AuthN/AuthZ](./15-authn-authz.md) 强制 |
| 大 body DoS | 入口校验 `messages` 总长 ≤ 1MB（按 model context 上限的 1.2x） |

---

## 9. M0 / M1 / M2 演进

### M0 — MVP（必须有）
- AnthropicProvider + OpenAIProvider 双实现
- 简单 fallback（顺序切换）
- 重试分类（4xx/429/5xx）
- 进程内断路器（不持久化）
- Token 预扣对接 [16 Quota] 简版（仅总量限制）
- Prompt cache 静态性 warn 模式
- Prometheus + OTel 埋点
- llm_call_log 表（明文 prompt 不写）

### M1 — 多租户生产化
- 断路器状态持久化到 Postgres（`llm_circuit_state`）
- Per-tenant API key（来自 [11 Credential Proxy] dynamic）
- 多 fallback 链（最多 3 跳）
- Prompt cache strict_mode（manifest 可启用）
- 模型质量回归告警（同 prompt 输出 token 分布漂移）

### M2 — 模型质量 A/B
- 同 prompt 双 provider 并跑（影子流量）
- A/B gate 集成 [26 Eval Framework](./26-eval-framework.md)：新模型 eval 不下降才放量
- 自托管模型支持（vLLM / TGI）

### M3 — 跨集群
- Gateway 集群化部署，断路器走 Redis
- Region-local fallback（保 data residency）

---

## 10. 开放问题

1. **是否引入 LLM response cache？** 倾向：放在 orchestrator middleware 层而不是 gateway，因为缓存 key 涉及业务语义（同义改写算不算命中？）。
2. **断路器 cooldown 自适应？** 当前固定 30s，是否按错误类型动态（429 比 5xx 长）？
3. **Streaming 错误中途的处理：** 已经发了一半 chunk 后 provider 报错，是否截断 + 再启 fallback？目前选择**直接报错给上层**，由用户重试整段。
4. **Per-tenant 模型白名单：** 是否在 gateway 层强校验 manifest.model 是否在租户允许列表？倾向放在 [02 Manifest 静态校验] + [16 Quota] 两道，gateway 不重复。
5. **Anthropic Batch API 集成：** 异步场景成本 -50%，但与 streaming 模型不兼容。M1 末决定是否加 batch 通道。
