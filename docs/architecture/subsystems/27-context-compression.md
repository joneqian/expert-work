# 27 上下文压缩 / Context Compression — 长会话 token 窗口管理、分层保留、prefix cache 协同

> 把"消息攒到上限就 400 报错"升级为"无感分层压缩 + prefix cache 友好 + 失败可降级"。核心：multi-signal 触发条件、三层保留策略（system_prompt + 最近 K 轮 + 中间摘要 + pinned）、稳定截断点保 prefix cache 命中率、摘要 LLM 失败回退简单截断。

---

## 1. 职责 & 边界

### ✅ 做
- 监听 LangGraph state 中的 messages 数 / token 数；触发条件命中时执行压缩
- 分层保留（system_prompt + 最近 K 轮原文 + 中间摘要 + pinned 工具结果 + 当前 in-flight tool_call/result 对）
- 摘要 LLM 选型（更便宜模型 + 独立 quota bucket + purpose=summarization 标签）
- 与 [10 LLM Gateway](./10-llm-gateway.md) prefix cache 协同（截断点稳定，避免 cache 全 miss → 成本爆 10×）
- 失败回退：摘要 LLM 失败 → 简单截断（保留最近 K 轮 + system）；触发 / 完成 / 失败可观测性埋点
- 摘要内容经 redactor；M2 沉淀片段送 [13 Memory Store](./13-memory-store.md) history layer

### ❌ 不做
- 不管长期记忆 / 跨 session 知识 → [13 Memory Store](./13-memory-store.md)；27 仅处理活跃 session 的 in-flight context
- 不管 LangGraph state 持久化 → [19 Durable Execution](./19-durable-execution.md)
- 不做摘要质量 LLM-as-judge 评估 → [26 Eval Framework](./26-eval-framework.md)（M2 接入）
- 不做 prompt 模板渲染 / prompt 自动改写优化 → 不在范围

---

## 2. 上下游依赖

| 依赖方向 | 子系统 | 关系 |
|---------|--------|------|
| 上游 | [19 Durable Execution](./19-durable-execution.md) | graph runtime 在每个 LLM-call 节点前调 27 middleware |
| 上游 | Orchestrator middleware 链 | 27 在 `dynamic_context` 之后、LLM 调用之前 |
| 下游 | [10 LLM Gateway](./10-llm-gateway.md) | 摘要本身是一次 LLM 调用；context_length_exceeded 由 10 抛回触发 27 兜底 |
| 下游 | [13 Memory Store](./13-memory-store.md) | M2：高价值压缩片段沉淀为 history layer 长期记忆 |
| 横切 | [16 Quota / Rate Limit](./16-quota-rate-limit.md) | 摘要 token 走独立 bucket（`purpose=summarization`），不挤占主对话预算 |
| 横切 | [17 Audit Log](./17-audit-log.md) | 仅"压缩失败导致 session 终止"等高风险事件写 audit；常规压缩属 agent 行为，写 [19] event_log |
| 横切 | [20 Observability](./20-observability.md) | 触发率、压缩前后 token 直方图、prefix cache 命中率联动 |
| 横切 | PII Redactor 中间件 | 摘要 LLM 输入 / 输出都过 redactor；摘要不能引入新 PII |
| 横切 | tenant_config.compliance_pack | 高敏感 pack（HIPAA / GDPR）可禁用摘要，详见 § 8 |

---

## 3. 数据模型 / 状态机

### 3.1 状态机（按 session 看）

```
              ┌────────┐
              │ NORMAL │  低于触发阈值
              └───┬────┘
                  │ trigger 命中
                  ▼
              ┌────────────┐
              │ TRIGGERED  │  写 event_log compression_pre
              └─────┬──────┘
                    │ acquire summarization slot
                    ▼
              ┌──────────────┐
              │ SUMMARIZING  │  调摘要 LLM
              └─────┬────────┘
                    │
        ┌───────────┼─────────────────┐
        │           │                 │
     success     summary fail     redactor reject
        │           │                 │
        ▼           ▼                 ▼
   COMPLETED   FAILED_FALLBACK     FAILED_HARD
              （简单截断兜底）    （session 终止）
```

- **NORMAL → TRIGGERED**：multi-signal 命中（详见 § 5.1）
- **TRIGGERED → SUMMARIZING**：拿到摘要 slot，开始调 LLM
- **SUMMARIZING → COMPLETED**：摘要写回 state；LangGraph state reducer 替换中间消息；下次 LLM 调用用新 prefix
- **SUMMARIZING → FAILED_FALLBACK**：摘要 LLM 失败 → 退化为简单截断（保留最近 K 轮 + system_prompt，丢弃中间），仍可继续推进
- **SUMMARIZING → FAILED_HARD**：摘要内容 redactor 拒绝（疑似 PII 泄漏）→ 不能继续，session 进 FAILED + 写 audit

### 3.2 Postgres DDL

```sql
CREATE TABLE compression_event (
    id                 UUID PRIMARY KEY,                  -- uuid7
    tenant_id          TEXT NOT NULL,
    thread_id          UUID NOT NULL,                     -- LangGraph thread / session id
    agent_name         TEXT NOT NULL,
    agent_version      TEXT NOT NULL,
    trace_id           TEXT NOT NULL,
    triggered_by       TEXT NOT NULL,                     -- token_threshold | message_count | provider_overflow | manual
    state              TEXT NOT NULL,                     -- TRIGGERED/SUMMARIZING/COMPLETED/FAILED_FALLBACK/FAILED_HARD
    before_tokens      INT NOT NULL,
    after_tokens       INT,
    before_message_cnt INT NOT NULL,
    after_message_cnt  INT,
    summary_model      TEXT,                              -- 摘要用的 model name（haiku / mini-llm / ...）
    summary_tokens_in  INT NOT NULL DEFAULT 0,
    summary_tokens_out INT NOT NULL DEFAULT 0,
    truncation_anchor  TEXT NOT NULL,                     -- 稳定截断点 hash（同 session 内不变）
    started_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at        TIMESTAMPTZ,
    error_code         TEXT,                              -- summary_llm_failed / redactor_rejected / quota_exceeded
    error_message      TEXT
);
CREATE INDEX ON compression_event (tenant_id, thread_id, started_at DESC);
CREATE INDEX ON compression_event (tenant_id, state, started_at);
CREATE INDEX ON compression_event (triggered_by, state);
-- RLS: 表参与统一 RLS；session 变量 app.tenant_id（与 D1 决议一致）
```

### 3.3 Pydantic schema

```python
from pydantic import BaseModel, Field
from uuid import UUID
from typing import Literal

class CompressionPolicy(BaseModel):
    """每 manifest 可在 policies.context_compression 覆盖；缺省走 tenant_config 默认。"""
    token_trigger_ratio: float = Field(0.8, ge=0.5, le=0.95)   # tokens > ratio × model_context_limit
    message_trigger_count: int = Field(200, ge=20)              # messages > count
    keep_recent_turns: int = Field(10, ge=2, le=50)             # K
    summary_model: str | None = None                            # None → 走 tenant 默认（通常 haiku）
    pin_tools: list[str] = []                                   # 永不压缩的工具结果名
    enable_summarization: bool = True                           # False → 直接简单截断（HIPAA 等场景）

class CompressionTrigger(BaseModel):
    triggered_by: Literal["token_threshold", "message_count", "provider_overflow", "manual"]
    before_tokens: int
    before_message_cnt: int
    truncation_anchor: str

class CompressionResult(BaseModel):
    event_id: UUID
    state: Literal["COMPLETED", "FAILED_FALLBACK", "FAILED_HARD"]
    before_tokens: int
    after_tokens: int
    summary_text: str | None = None        # 写回 state；FAILED_HARD 时 None
    fallback_reason: str | None = None
```

---

## 4. 关键接口

### 4.1 Python（middleware 入口）

```python
class ContextCompressor:
    """LangGraph state 中间件：在 LLM 调用前 inspect state.messages，必要时压缩并替换。"""

    async def maybe_compress(
        self,
        state: GraphState,
        policy: CompressionPolicy,
        ctx: AgentContext,
    ) -> CompressionResult | None:
        """None = 未触发；返回 result 表示已修改 state.messages（reducer 替换中间消息为 summary message）。"""

    async def force_compress(
        self,
        state: GraphState,
        policy: CompressionPolicy,
        ctx: AgentContext,
        reason: str = "provider_overflow",
    ) -> CompressionResult:
        """provider 抛 context_length_exceeded 后由 [10] 调用，绕过阈值检查直接压缩。"""

    async def summarize(
        self,
        messages: list[Message],
        model: str,
        ctx: AgentContext,
    ) -> str:
        """内部：调 [10 LLM Gateway] 走 purpose=summarization；输出经 redactor 后才落 state。"""
```

### 4.2 LangGraph state reducer 协同

state 中专设 `compressed_prefix` 字段（list[Message]）保存稳定的"system + 历史摘要 block"；reducer 规则：

- `messages` 字段：append-only（最近 K 轮原文 + 当前 in-flight）
- `compressed_prefix`：仅 27 写；其他节点 read-only
- LLM 调用最终拼装：`compressed_prefix + messages`（前者是 prefix cache 命中关键）

### 4.3 Manifest 字段（[02-AGENT-MANIFEST](../02-AGENT-MANIFEST.md) 扩展）

```yaml
spec:
  policies:
    context_compression:
      enable_summarization: true
      token_trigger_ratio: 0.8
      message_trigger_count: 200
      keep_recent_turns: 10
      summary_model: claude-haiku-4-5     # 可省略走默认
      pin_tools:                          # 工具结果带 pin=true 永不压缩
        - critical_db_query
```

---

## 5. 算法 / 关键决策

### 5.1 触发条件（multi-signal）

任一命中即触发；阈值由 manifest `policies.context_compression` 覆盖、不覆盖走 tenant_config 默认：

| Signal | 默认阈值 | 来源 | 优先级 |
|--------|---------|------|--------|
| `tokens > token_trigger_ratio × model_context_limit` | 0.8 | 主动监控（middleware 在每次 LLM call 前算） | 主要 |
| `messages > message_trigger_count` | 200 | 兜底（防异常累积） | 次要 |
| provider 返回 `context_length_exceeded`（[10 § 5](./10-llm-gateway.md) 路径） | n/a | 兜底；立即触发 force_compress 后由 [10] 重试 | 兜底 |

`model_context_limit` 由 [10 LLM Gateway](./10-llm-gateway.md) 维护的模型元数据查到（如 sonnet=200k、haiku=200k、gpt-4o=128k）。

**关键决策**：**0.8 是保守阈值**——比硬上限早压；理由：(1) provider 实际限制常低于宣称值、(2) 同次 LLM 调用还要塞 tool schema / output 预留 token、(3) 给摘要 LLM 自己也留 token 余量。

### 5.2 分层保留策略（关键决策）

压缩时把消息分四档：

| 档 | 是否压缩 | 内容 |
|----|:-:|------|
| **system_prompt** | ❌ 永不 | manifest 声明的 system prompt（前缀，prefix cache 命中关键） |
| **最近 K 轮** | ❌ 永不 | 默认 K=10；保留原文（含 user / assistant / tool_call / tool_result） |
| **当前 in-flight tool 调用** | ❌ 永不 | 已发出 tool_call 但未收 tool_result，或刚收 result 还未喂 LLM；pinned |
| **manifest pin=true 工具结果** | ❌ 永不 | `policies.context_compression.pin_tools` 列表内的工具结果，整对 (call+result) 保留 |
| **以上之外的中间消息** | ✅ 压缩 | 调摘要 LLM 生成单条 summary message；类型 `system` 子类 `previous_context_summary` |

压缩后的 messages 顺序：`[system_prompt, summary_message, 最近 K 轮 + pinned + in-flight]`。

**关键决策**：**永不压缩当前 in-flight 的 tool_call/tool_result 对**——LLM 没看到 result 就让它消失，会让模型继续要求调用同一 tool（无限循环）；DeerFlow `dynamic_context_middleware` 的消息 ID swap 技术就是为此。

### 5.3 prefix cache 协同（关键决策）

[10 LLM Gateway § 5.4](./10-llm-gateway.md) 的 prefix cache 节省 90%+ 输入 token，**前提是前缀稳定**。压缩破坏前缀 = 全 cache miss → API 成本爆 10×。

规则：

1. **截断点必须稳定**：同一 session 多次压缩时，"哪些消息进 summary"的边界必须**同一截断点**——即"截断点 = 最近一次成功压缩点之后的最旧消息"，写入 `session_meta.last_compression_anchor`（hash + sequence）；下次压缩从该点之后继续累积，不回退也不前移。
2. **summary block 也是稳定前缀**：每次压缩后，`compressed_prefix = [system_prompt, summary_block]` 整体作为新 prefix；只要 LLM 调用前 N 次的 prefix 不变，cache 持续命中。
3. **不稳定的截断点（反例）**："每次按 token 数从后往前截断"——同 session 不同时刻 token 数不同，截断点漂移，prefix cache 全 miss。
4. **跨 model 切换的限制**：同 session 中途从 sonnet 切换到 haiku（不同 tokenizer + 不同 cache 池），prefix cache 必失效；27 不解决，由 [10] 监控该场景告警，业务侧避免中途换 model。

监控指标 `expert_work_llm_cache_hit_ratio` 急降 → 告警 + 怀疑截断点不稳定。

### 5.4 摘要 LLM 选型 + 独立 quota

- 默认 `summary_model = claude-haiku-4-5`（便宜 + 200k 窗口够装压缩输入）；manifest 可 override
- 走 [10 LLM Gateway](./10-llm-gateway.md) 普通调用，但携带：
  - `purpose=summarization`（[10 § 5.2](./10-llm-gateway.md) 路由 + [16 § 10](./16-quota-rate-limit.md) metric 桶独立）
  - `tenant=<原 session tenant>`（强制透传，与 26 § 5.1 同语义）
- token 走 [16 § 4](./16-quota-rate-limit.md) 独立 bucket：`tenant.summarization`（与主对话 `tenant.chat` 分离）；摘要爆预算不影响主对话；主对话耗光预算时摘要还能跑（防压缩失败连锁）
- failure 时 `release_tokens(thread_id=<27 内部 thread>)` 标准流程

### 5.5 vendor 参考

- DeerFlow `agents/middlewares/dynamic_context_middleware.py`（193 行）—— prefix cache 静态化、消息 ID swap；27 直接 vendor（见 [06-OPEN-SOURCE-DEPS § P0 新增](../06-OPEN-SOURCE-DEPS.md)）
- DeerFlow `summarization` 中间件 —— 摘要触发 + state reducer 集成；模式参考自重写
- 自写约 400 行：多 signal 触发器、pin 逻辑、`compression_event` 持久化、完整可观测性、FAILED_FALLBACK 路径（DeerFlow 仅按 message 数 + 无持久化 + 无观测）

---

## 6. 失败模式 & 缓解

| 故障 | 影响 | 缓解 | 检测 |
|------|------|------|------|
| 摘要 LLM 失败（超时 / 5xx / quota 耗尽） | 主对话也无法继续（context 已超） | 退化为简单截断（保留 system_prompt + 最近 K 轮，丢弃中间），仍可推进；标记 `state=FAILED_FALLBACK` 写 event_log | `expert_work_context_compression_total{outcome="failed_fallback"}` 告警 |
| 摘要质量差（信息丢失） | 后续工具调用错乱 / 答非所问 | M2 加入摘要质量 LLM-as-judge 评分（接 [26 Eval](./26-eval-framework.md)）+ 业务侧 thumbs_down 闭环；M0/M1 不防 | offline eval set 跑回归 |
| prefix cache miss（截断点不稳定） | 成本爆 10×；用户感知不明显但账单震惊 | 截断点强制写 `session_meta.last_compression_anchor` + CI lint 防"按 token 倒数截断"反模式 | `expert_work_llm_cache_hit_ratio` 急降 P1 告警 |
| 触发太晚（已被 provider 400） | 整次 LLM 调用失败 + 用户感知 | provider 返回 `context_length_exceeded` → [10] catch + 调 27.force_compress + 重试一次（与 [10 § 5.4](./10-llm-gateway.md) 协议对齐） | `expert_work_context_overflow_total` 计数 |
| 触发太早（无谓压缩 + 摘要 token 浪费） | 成本上升 | 0.8 阈值保守但不过激；observability 看实际触发率（按 session 频次）调优；manifest 可调高到 0.9 | `expert_work_context_compression_total{trigger=token_threshold}` 与 session 长度回归看 |
| 摘要 LLM 自身上下文也爆 | 无限递归压缩 | 输入 > summary_model_limit × 0.7 → 分段摘要再合并；超过 3 段 FAILED_HARD | `error_code='summary_input_too_large'` |
| 摘要内容含新引入 PII（LLM 幻觉） | 合规违规 | 摘要输出过同一 redactor；命中即 FAILED_HARD + audit | `expert_work_redactor_hits_total{stage="summary_output"}` |
| 多 worker 并发改 messages | state corruption | thread 级 advisory lock（[19 § 4.1](./19-durable-execution.md)）；27 不重发明 | `expert_work_resume_total{outcome=conflict}` |
| 摘要 LLM 也被 quota cap | 主对话能跑摘要不能 | summarization bucket 独立配额（§ 5.4）；耗尽 → FAILED_FALLBACK | `expert_work_quota_exceeded_total{purpose=summarization}` |
| pin_tools 列表写错（永不能压缩） | 压缩无效最终 provider 400 | manifest lint：pinned 累计 token > 30% context 警告；运行时仍尊重 pin | `before_tokens / after_tokens` 比例不收敛 |

---

## 7. 可观测性

> 命名规范、必填字段、cardinality 约束统一遵循 [20 § 5.1–5.3](./20-observability.md)；本节仅列本子系统专属。

### 7.1 Metric

```
expert_work_context_compression_total{tenant,agent,trigger,outcome}      counter
   # trigger = token_threshold | message_count | provider_overflow | manual
   # outcome = completed | failed_fallback | failed_hard

expert_work_context_size_tokens{tenant,agent,phase}                       histogram
   # phase = before | after；可对比压缩比

expert_work_context_compression_duration_seconds{tenant,agent}            histogram

expert_work_summarization_tokens_total{tenant,agent,direction}            counter
   # direction = input | output；与主对话 token 分开统计

expert_work_compression_anchor_drift_total{tenant,agent}                  counter
   # 截断点漂移检测；不该 > 0
```

### 7.2 OTel span

- `expert_work.context_compression.run`（attrs：`trigger`, `before_tokens`, `after_tokens`, `summary_model`, `agent_version`, `outcome`, `keep_recent_turns`）
- `expert_work.context_compression.summarize`（attrs：`summary_model`, `input_tokens`, `output_tokens`, `agent_version`，子 span 同 trace）
- `expert_work.context_compression.fallback`（attrs：`fallback_reason`, `agent_version`）

所有 span 必带 `agent_version`（[C8](./REVIEW-NOTES.md) 规范）。

### 7.3 关键日志

每次状态变更写 INFO `context_compression.{state}`，必带 `tenant, thread_id, agent, agent_version, trigger, before_tokens, after_tokens, truncation_anchor`。完整字段遵循 [20 § 5.3](./20-observability.md#53-日志必填字段)。FAILED_HARD 写 ERROR + 同时写 audit。

Dashboard 关键面板：触发率（按 trigger 维度）、压缩比分布（before/after token）、摘要 LLM 失败率（SLO < 1%）、prefix cache 命中率（与 [10] 联动看截断点稳定性）。

---

## 8. 安全考虑

| 攻击面 | 防御 |
|--------|------|
| 摘要内容跨租户混合（LLM 幻觉把 A 租户内容塞进 B 的摘要） | 每次 `summarize()` 调用独立 session（不复用上下文）；调 [10] 强制透传 `tenant`；摘要 LLM 不开任何长 memory |
| 通过特殊 prompt 让摘要 LLM 泄露其他租户数据 | tenant scope 强制 + 单次单租户调用 + M2 接 [26 Eval](./26-eval-framework.md) LLM-as-judge 抽检摘要输出是否含跨租户 marker |
| 摘要把 PII 从 redacted 字段反向推断出来再写入摘要文本 | 摘要输出过同一 redactor（与 [13 § 5.2](./13-memory-store.md) 一致 pipeline）；命中即 FAILED_HARD；定期回归 redactor 规则 |
| 高敏感场景（HIPAA / 金融 PCI）压缩 PHI/PCI 是否合规 | tenant_config.compliance_pack 标记 `disable_summarization=true` → 退化为 enable_summarization=False，**强制只走简单截断**；同时 manifest 该 agent 必须 `isolation_level=dedicated_node`；详见 [21 § X](./21-network-policy.md) compliance_pack 条款 |
| 截断点 hash 被攻击者操纵导致 prefix cache 命中错位 | `truncation_anchor` 由 server 端 hash（含 tenant_id + thread_id + seq），不接受外部输入 |
| 摘要 LLM 调用绕过 quota | 走 [10] 标准流程，强制带 `purpose=summarization`；[16] reservation/commit 不可绕过 |
| 攻击者塞超长 user message 强行触发压缩耗 quota | [16 § 4 user-level rate limit](./16-quota-rate-limit.md) 在前；message 大小硬上限（manifest `policies.max_user_message_bytes`）拦在 27 之前 |

**关键决策**：**摘要内容必须经 redactor**——与 19 event_log redactor 一致 pipeline；理由：摘要 LLM 可能从已 redact 的占位符（如 `<EMAIL_1>`）反向构造看似无害但携带原 PII 暗示的句子。

---

## 9. M0 / M1 / M2 演进

### M0 —— 简单截断兜底（约 2 周）
- **不调摘要 LLM**；仅在 provider 返回 `context_length_exceeded` 时触发简单截断（保留 system_prompt + 最近 K=10 轮，丢弃中间）
- 写 `compression_event` 表 + 基础 metric（`expert_work_context_compression_total{outcome=failed_fallback}`）
- 目的：**先保证不爆**，质量牺牲一点可接受；为 M1 完整算法铺路（表结构 + 中间件挂载点已在）；prefix cache 协同推迟到 M1

### M1（6-8 周）—— 完整三层 + prefix cache 协同
- 三层保留（system + 最近 K + summary + pinned）
- multi-signal 触发（token_threshold 主、message_count + provider_overflow 兜底）
- prefix cache 协同（稳定截断点 + summary block 整体作为新 prefix）
- vendor DeerFlow `dynamic_context_middleware`（193 行）+ 自写 summarization 子模块
- 摘要 LLM 走独立 quota bucket
- 完整 dashboard + 告警

### M2 —— 质量提升 + 长期记忆联动
- 摘要质量 LLM-as-judge 评分（接 [26 Eval](./26-eval-framework.md)）；评分低 → 重摘要或人工 review
- 高价值压缩片段沉淀为 [13 Memory Store](./13-memory-store.md) history layer 长期记忆（跨 session 可检索）
- 分段摘要（极端长 session 防摘要 LLM 自己也爆）
- 自适应 K（根据 agent 任务类型动态调最近保留轮数）

---

## 10. 开放问题

1. **摘要 LLM 选型最优解**：Haiku（200k context、便宜）vs mini-LLM（gpt-4o-mini，跨厂商）vs 自训练蒸馏模型（专门压缩任务）？需要 [26 Eval](./26-eval-framework.md) 跑摘要质量回归对比；M1 默认 Haiku，M2 接 eval 后再选。
2. **K（保留最近多少轮）的合理默认值**：10 是经验值；不同 agent 类型（ReAct 长尾 vs 单轮 RAG）最优 K 不同。需要看实际业务对话长度分布 + 回归实验。
3. **高敏感场景能否压缩 PHI/PCI**：HIPAA / 金融 PCI 是否允许把含敏感数据的对话摘要走 LLM？目前默认 `disable_summarization=true` 强制简单截断；是否需要"on-prem 摘要模型"方案给监管严格客户？延后到 M3 议题。
4. **跨 model 切换的兼容**：同 session 中途从 Sonnet 切到 Haiku（业务侧降级、成本优化），prefix cache 必失效，且 tokenizer 不同导致 token 数估算偏差；是否需要 27 监听 model 切换事件主动重压缩重建 prefix？M2 议题。
5. **summary message 在 LangGraph state 中的角色边界**：summary 是 `system` 类还是 `human` 类消息？影响后续 LLM 是否把它视作"上下文"还是"指令"；M1 选 `system` 子类型，但需要观察对模型 attention 的影响。
