# Stream L — Hermes-derived 单 turn 能力强化 sprint（设计先行）

> 临时 sprint，**与 Stream J 剩余子项并行**。落实 [memory:complete-not-minimal](../../.claude/projects/-Users-mac-src-github-jone-qian-expert-work/memory/feedback_complete_not_minimal.md)。
>
> **背景**：2026-05-20 Stream K 收尾后做了一次跨仓 architecture review，对比 Hermes-Agent（`/Users/mac/src/github/hermes-agent`）的 `run_agent.py` + `agent/conversation_loop.py`（各 4000+ 行）与我们 `services/orchestrator/`（LangGraph 因子化）。形态分歧（Hermes 单体类 vs LangGraph 节点）不重要 —— 真正学得到的是 Hermes 在**单 turn 之内**积累的 8 条生产能力，每条都是我们完全缺失但任何长 session / per-user 持久 agent（[memory:target-product-form](../../.claude/projects/-Users-mac-src-github-jone-qian-expert-work/memory/project_target_product_form.md)）必备的。
>
> **与 Stream K 的关系**：K 补"已勾完 stream 的弱版"，L 补"agent loop 单 turn 内的成熟度"。两者都属于 (c) 类弱版红线。
>
> **设计先行规则**（[memory:design-first-iteration](../../.claude/projects/-Users-mac-src-github-jone-qian-expert-work/memory/feedback_design_first_iteration.md)）：所有总体架构 / 跨切面接口 / Mini-ADR 在本文件锁定；每条 gap PR 在本文件对应章节基础上做局部细化。
>
> **零债收尾规则**（[memory:zero-tech-debt](../../.claude/projects/-Users-mac-src-github-jone-qian-expert-work/memory/feedback_zero_tech_debt.md)）：本 Stream 收尾必须 6 条全过 —— 无 TODO / 测试达标 / 文档同步 / 可观测齐全 / CI 全绿 / bug 不遗留。

---

## 1. 范围 & 边界

### 1.1 In-scope（L1 – L8，源自 Hermes 单 turn 8 大成熟能力）

| ID | Hermes 出处 | Gap | 本 Stream 交付 | 优先级 | Mini-ADR |
|----|-------------|-----|---------------|--------|---------|
| **L1** | `prompt_caching.py:49-80` + `conversation_loop.py:797-826` | 无 Anthropic prompt caching；长 session input 成本无折扣 | Anthropic adapter 注入 `cache_control` system_and_3 layout + system prompt build-once 冻结不变式 | P0 | L-1 |
| **L2** | `conversation_loop.py:429-488` + `context_compressor.py:454-600` | 无 token preflight；长 session 撞 context window | `context/compressor.py` 新模块 + `agent_node` 入口 token 估算 + middle-turn summarize + manifest threshold | P0 | L-2 |
| **L3** | `conversation_loop.py:1030-1078` | provider hang 死信号缺失 | `agent_node` LLM call 套 `asyncio.wait_for(stream_deadline_s=90)` + 归类 retryable 触发 fallback | P0 | L-3 |
| **L4** | `conversation_loop.py:3916-3939` + `tool_result_classification.py:9-26` | tool 失败包成 ToolMessage 单返，无 cross-tool 聚合；模型可能"声称已写"实际未落盘 | `tools_node` 聚合 mutation 调用按路径 + `mutation_classifier.py` 判 landed + `AgentState.failed_mutations` 注入下一 turn advisory footer | P0 | L-4 |
| **L5** | `iteration_budget.py:17-62` | `max_steps` 硬限无 refund；K.8 update_plan 内部多步会消光预算 | `AgentState.step_count` 加 refund 通道 + `ToolResult.refund_iterations` + `update_plan` 返回 refund | P0 | L-5 |
| **L6** | `run_agent.py:3792-3874` `_should_parallelize_tool_batch()` | LangGraph ToolNode 顺序无并行；J.5 RAG / J.7 skill 上线后串行慢 | `ToolSpec.is_read_only` + `path_args` + `tools_node` 按 conflict 分组（同组顺序 / 跨组并行） | P0 | L-6 |
| **L7** | `agent/trajectory.py:30-56` | audit_log 不是 LLM-trainable trajectory；J.13 eval gate 缺数据源 | `trajectory/recorder.py` 写 ObjectStore + sse.py run_agent 终态 dispatch + manifest opt-out | P0 | L-7 |
| **L8** | `credential_pool.py` OAuth refresh + `_try_refresh_*` | 401 直接失败；OAuth 类 provider 不可用 | `LLMRouter` wrap provider call，401 触发 `refresh_credentials()` 重试一次 + `OAuthProvider` Protocol | P0 | L-8 |

### 1.2 设计选择对比（"为什么不全抄 Hermes"）

| Hermes pattern | 借/不借 | 理由 |
|---|---|---|
| 4000-line `AIAgent` + `conversation_loop` 单体 | ❌ 不借 | LangGraph 因子化更干净，反模式 |
| 5+ provider adapter stack（Anthropic/OpenAI/Bedrock/Gemini/Codex） | ❌ 不借 | platform 按需扩，不预扩；Stream E 已抽 LLMProvider Protocol |
| **per-turn 重建 system prompt 动态注入** | ❌ **反模式** | 直接和 L-1 cache prefix byte-stable 不变式打架；Hermes 自己也在 `conversation_loop.py:797-826` 强调 system 必须 stable |
| Background review fork（post-turn 后台学习） | ⚪ 不借 | 我们 J.3 memory writeback 是 inline graph node — 设计选择不同 |
| Iterative summary preservation | ⚪ 暂不借 | L2 落地后视实际压缩频率再决定加 |
| Think scrubber state machine（`<think>` 跨 chunk 拼接） | ⚪ 暂不借 | 只在我们接 extended-thinking provider 时才需要 |
| Subdirectory hints / skill bundles 动态注入 | ⚪ 不借 | 偏 CLI 形态，platform 不适用 |
| Stateless gateway tenancy（每请求新建 agent） | ✅ 已对齐 | 我们 LangGraph + checkpointer 同形态，无需新工作 |
| Streaming health-checking first-class | ✅ L3 借（核心思想） | 强走 streaming + 90s deadline |
| Prompt cache as first-class context cost | ✅ L1 借 | 核心借鉴 |
| 自适应 tool parallelization | ✅ L6 借 | 核心借鉴 |
| 文件 mutation verifier footer | ✅ L4 借 | 核心借鉴 |
| Iteration budget refund | ✅ L5 借 | 核心借鉴；与 K.8 update_plan 配套 |
| Trajectory recording | ✅ L7 借 | 核心借鉴；J.13 eval 数据源 |
| OAuth 401 自动 refresh | ✅ L8 借 | 核心借鉴；J.6 多模态接 VL 提前补 |

### 1.3 Out-of-scope（明确推迟，不进本 Stream）

| 推迟项 | 落地 | 备注 |
|-------|------|------|
| OpenAI / Gemini prompt caching（如未来支持） | 各 provider 自有节奏 | L1 只锁 Anthropic（M0 唯一 provider 且 cache 成熟） |
| Token counting 用真 tokenizer（tiktoken 等） | 后续按需 | L2 用 rough char/4 估算（与 Hermes `estimate_request_tokens_rough` 同档），跑出实测偏差 > 15% 再升级 |
| Iterative summary preservation | L 完成后再评估 | L2 一次性 summarize 中间，足够覆盖 95% 长 session |
| Provider-level circuit breaker 状态持久化 | M1-D 韧性 | L 不动 |
| SSE 事件序号 reconnect / 客户端 resume token | M1 之后 | 我们已有 checkpointer durable-resume，客户端层 reconnect 单独评估 |
| Sub-Agent（J.4）的预算下钻 | Stream J.4 自身 | L5 只覆盖单 agent refund |
| 真 VL 模型 OAuth flow | Stream J.6 | L8 用 fake provider + Protocol 锁能力 |

### 1.4 验收（Stream L Exit）

1. **L1 – L8 全部 PR 合并**，ITERATION-PLAN § Stream L 全部 `[x]`。
2. **零债 6 条全过**：无 TODO/FIXME/XXX/HACK；unit ≥ 85% / integration ≥ 70% 关键路径；docs 与实现一致；本 Stream 新增组件均 emit metric+log+trace；CI 8/8 + CodeQL 无新增 high/critical；bug 不遗留。
3. **能力指标可量**（每条至少一条 SLO / Prom recording rule）：
   - L1：`Expert Work:llm:anthropic_cache_read_ratio:5m`（cache_read / total input tokens）
   - L2：`expert_work_context_compression_total` counter + p95 压缩耗时
   - L3：`expert_work_llm_stream_stale_total` counter
   - L4：`expert_work_failed_mutation_footer_injected_total` counter
   - L5：`expert_work_step_refund_total` counter
   - L6：`expert_work_tools_batch_concurrency` histogram
   - L7：`expert_work_trajectory_recorded_total{outcome=success|failed}` counter
   - L8：`expert_work_llm_auth_refresh_total{result=success|fail}` counter
4. **没有新 gap 进档**：本 Stream 完成时若发现新 (c) 类弱版，必须当 sprint 内补完或显式移入下一 Stream checklist。

---

## 2. 总体架构

### 2.1 性质 = 单 turn 内成熟度补全，不动 graph 形态

不新增子系统、不改 graph 编译形态。每条 gap 在**现有扩展面**上补：

- **LLM provider 层补能力**：L1（cache_control 注入）、L3（stream deadline wrap）、L8（OAuth refresh hook）
- **graph 节点边缘加防御 / 聚合**：L2（agent_node 入口 preflight）、L4（tools_node 收集 + agent_node 注入）
- **AgentState 加 narrow channel**：L4（`failed_mutations`）、L5（`step_refund_pending`）
- **新增 sidecar 模块**：L2（`context/compressor.py`）、L7（`trajectory/recorder.py`）
- **registry / spec 扩展**：L6（`ToolSpec.is_read_only` / `path_args`）
- **manifest schema 增字段**：L1（无新增 —— manifest 选项就是 model spec）、L2（`policies.context_compression` 子字段）、L3（`AgentSpecBody.stream_deadline_s`）、L7（`PoliciesSpec.trajectory_recording`）

### 2.2 跨 L 共享的关键不变式（Mini-ADR L-1 / L-4 / L-5 共用）

**System prompt prefix byte-stable**（L-1 不变式）—— `BuiltAgent.system_prompt` 在 `agent_factory.build_agent()` 时一次构建后**冻结**；所有 per-turn 动态注入（plan、recalled_memories、failed_mutations footer）必须进 **last user message** 或新 **HumanMessage tail block**，**不进 system**。这条规则使 L1 cache 工作，同时让 L4 footer 注入有明确放置位点。

**AgentState narrow channels**（L-4 / L-5 共用模式）—— 新增 state field 必须：
- `NotRequired[T | None]`，默认 absent
- 由特定节点 reset（L4：每 turn agent_node 进入时若注入则清；L5：每次 agent_node 消 step_count 时 reset pending）
- 不进 system 渲染，只为下游节点 read

### 2.3 PR 拆分原则（每条 gap 一 PR）

- 每条 gap 独立 PR，便于 review + 回滚
- PR 边界守则：仅碰 § 3 列出的关键文件；不动无关代码（[CLAUDE.md § 3](../../CLAUDE.md)）
- 每 PR 顺序：RED（写测试 / 加 xfail 待修）→ GREEN（实装）→ 文档同步（ITERATION-PLAN checkbox + STREAM-L-DESIGN 局部细化补丁如必要）
- Mini-ADR 在本文件 § 4 一次锁定；PR 不另开 ADR 文件，仅引用 L-1 ~ L-8

### 2.4 与现有 Stream / Gate 的关系

| 项 | 阻塞关系 |
|----|---------|
| **L1 / L2** | 阻塞 J.4 sub-agent + J.5 RAG（两者会显著放大 token 成本 / context 压力） |
| **L3** | 阻塞 canonical agent 上 staging（provider hang 锁死 = 不能上） |
| **L4** | 阻塞 J.7 skill + J.8 HITL（这两项 workflow 涉及多文件 mutation） |
| **L5** | 与 K.8 配套；K.8 已合入 main，L5 独立 PR 补足闭环 |
| **L6** | 阻塞 J.5 RAG 上线后的延迟 SLO |
| **L7** | 阻塞 J.13 eval gate（缺数据源） |
| **L8** | 阻塞 J.6 多模态接外部 VL 模型 OAuth 路径 |
| **整个 L** | M0→M1 Gate 第一次真生产 release 前必须勾完 |

---

## 3. 各 gap 设计要点

### L1. Anthropic prompt caching（cache prefix byte-stable）

**Anthropic adapter 改动**：`services/orchestrator/src/orchestrator/llm/providers/anthropic.py`

1. `AnthropicProvider` 加 `cache_enabled: bool = True` 配置（由 `ModelSpec` 推导）
2. `_to_anthropic_messages` 在拼好 `system` 与 `messages` 后调 `_apply_cache_control(system, messages)`：
   - `system` 字符串场景 → wire format 改成 block 形式 `[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]`
   - `messages` 列表末尾（最后 3 个非 system message）每个最后一个 content block 加 `"cache_control": {"type": "ephemeral"}`
3. `AnthropicClient.messages` 的 `system` 参数类型放宽：`str | list[dict[str, Any]] | None`
4. Response usage 字段透出：`_from_anthropic_response` 解析 `usage.cache_creation_input_tokens` / `usage.cache_read_input_tokens`，封装进 `AIMessage.usage_metadata`

**System prompt 冻结不变式**（Mini-ADR L-1）：
- `agent_factory.build_agent()` 构建 `BuiltAgent.system_prompt` 后**不再修改**
- `builder.py:agent_node` 中 `_inject_plan(messages, plan)` / `_inject_memories(messages, memories)` 改写：
  - **当前实现**：plan / recalled_memories 渲染进 system message（`_merge_into_system`，line 298）→ 让 cache prefix 每 turn 变化
  - **新实现**：plan / recalled_memories 渲染进 last user message 的前置 block（一个 `HumanMessage` 包含 `system_context_blocks + user_query`），系统消息保持 build-once 内容不变
- `_merge_into_system` 函数保留但只用于真正的 startup-time system prompt build；运行时注入走新 `_inject_into_last_user_message`

**测试**：
- `test_anthropic_cache_control_applied_to_system_and_last_3` —— `RecordingAnthropicClient` 抓 outbound payload，断言 `system[-1].cache_control == {"type": "ephemeral"}` + 最后 3 个非 system message 的最后 content block 各有 cache_control
- `test_agent_node_system_prompt_byte_stable_across_turns` —— 2 turn 同 thread，每 turn 抓 outbound system，assert SHA-256 一致；plan / memory 出现在 last user message 而非 system
- `test_cache_disabled_when_model_spec_supports_false` —— `ModelSpec.cache_enabled=False` 时 outbound 无 cache_control

**关键文件**：
- `services/orchestrator/src/orchestrator/llm/providers/anthropic.py`（cache_control 注入）
- `services/orchestrator/src/orchestrator/graph_builder/builder.py`（`_inject_plan` / `_inject_memories` 改走 last user message）
- `services/orchestrator/src/orchestrator/graph_builder/planner.py`（`render_plan` 是否需要拆分接口）
- `packages/expert-work-protocol/src/expert_work/protocol/model_spec.py`（`ModelSpec.cache_enabled: bool = True`）
- `services/orchestrator/tests/test_anthropic_provider.py`（新增 3 测）+ `test_builder_react.py`（更新 inject 路径断言）

---

### L2. Token preflight + context compressor

**新模块**：`services/orchestrator/src/orchestrator/context/compressor.py`

```python
@dataclass(frozen=True)
class CompressionPolicy:
    enabled: bool = True
    threshold_pct: float = 0.7   # 撞到 model context window * threshold_pct 时触发
    head_keep: int = 4           # 保最早 N 条非 system message
    tail_keep: int = 6           # 保最近 N 条
    max_passes: int = 3
    summarizer_model: str | None = None  # None → 复用 agent 主 model

class ContextCompressor:
    async def compress(
        self,
        messages: list[BaseMessage],
        *,
        context_window: int,
    ) -> list[BaseMessage]: ...

def estimate_tokens(messages: Iterable[BaseMessage]) -> int:
    """Rough token count: ``len(text) // 4``（与 Hermes ``estimate_request_tokens_rough`` 同档）.
    Bias 偏高 ~15%（与 tiktoken 实测对照），足够触发 preflight; M1 可换 tiktoken."""
```

**agent_node 入口改动**：
- `state["messages"]` 进入 → `estimate_tokens(messages)` ≥ `context_window * threshold_pct` → 调 `ContextCompressor.compress`
- compressor 3-pass 后仍超阈值 → 抛 `ContextOverflowError`（middleware 归类不可恢复 → 通过 audit + 终止 run，不进 fallback）
- 压缩成功 → 把 summary 替换中间段（保 head_keep + tail_keep），summary 写成一个 `SystemMessage`（位置：head 之后、tail 之前），**不进 system prompt 本体**（保 L-1 不变式）

**压缩节点 Mini-ADR L-2**：
- summarizer 走独立 LLM 调用（不复用 agent 主 router 的 fallback chain，避免压缩失败放大；超时降级 → 抛 `ContextOverflowError`）
- summary message 类型：使用 `SystemMessage` 但 content 用明确标识 `<context-summary> ... </context-summary>` 包裹，allow downstream debug
- 不引入 iterative summary preservation（每次压缩从头算 —— Hermes preservation 是优化项，L 一次性 compress 已能覆盖 95% 场景）

**Manifest schema**（`packages/expert-work-protocol/src/expert_work/protocol/agent_spec.py`）：
```yaml
spec:
  policies:
    context_compression:
      enabled: true
      threshold_pct: 0.7
      head_keep: 4
      tail_keep: 6
```

**测试**：
- `test_compressor_preserves_head_and_tail`
- `test_compressor_middle_replaced_by_summary`
- `test_compressor_raises_when_still_over_threshold_after_max_passes`
- `test_estimate_tokens_within_15pct_of_tiktoken` —— 用 known string 对照 tiktoken
- 集成测试：构造 50 turn 长对话（mock provider），`context_window=8000` 时压缩自动触发、不撞 422

**关键文件**：
- `services/orchestrator/src/orchestrator/context/__init__.py`（新建）
- `services/orchestrator/src/orchestrator/context/compressor.py`（新建）
- `services/orchestrator/src/orchestrator/graph_builder/builder.py:agent_node`（入口接入）
- `packages/expert-work-protocol/src/expert_work/protocol/agent_spec.py`（CompressionPolicy spec）
- `services/orchestrator/tests/test_context_compressor.py`（新建）

---

### L3. Stream stale-detection（router 加 per-provider wall-clock deadline）

**实装位点（按 Mini-ADR L-3 per-provider 语义）**：`services/orchestrator/src/orchestrator/llm/router.py`

`LLMRouter` 加 `stream_deadline_s: float | None` 字段；`_call_one` 在调 `provider.complete()` 或 around-LLM 中间件链时套 `_invoke_with_deadline(handle, coro)` helper —— 内部 `asyncio.wait_for(coro, timeout=stream_deadline_s)` 捕 `TimeoutError` 转为 `LLMStreamStaleError(...)`（继承 `LLMServerError`，retryable）。router 的 fallback 循环看到这个 error 自动尝试下一 provider。

**为什么 per-provider 而非 agent_node**：Mini-ADR L-3 写 "用 `asyncio.wait_for(complete(), timeout=90)` 在整个调用上套 deadline" —— `complete()` 是 provider 级。若放 agent_node 外层 wrap `llm_caller(...)`，timeout 会盖住整个 router 链，hung 的 primary provider 吃掉所有预算让 fallback 没时间。per-provider 是正确语义。

**LLMStreamStaleError**：新增于 `packages/expert-work-runtime/src/expert_work/runtime/middleware/llm_error_handling.py`，继承 `LLMServerError`（已是 retryable 类）→ 自动进 fallback chain。从 `expert_work.runtime.middleware` 导出。

**Manifest schema**（`packages/expert-work-protocol/src/expert_work/protocol/agent_spec.py`）：
```yaml
spec:
  stream_deadline_s: 90   # 默认 90s；可关（设 0 = 无超时）；上限 3600s
```

**`agent_factory.build_step_routers`**：从 `spec.spec.stream_deadline_s` 推导 `float | None`（>0 转 float，=0 转 None），把 deadline 统一传到 default / planning / reflection 三个 router；J.6 vision router 同样共享该 deadline。

**Mini-ADR L-3**：
- 默认 90s 来自 Hermes 实测（`conversation_loop.py:1030` 90s stale-stream timeout），覆盖 OpenAI/Anthropic 95% 正常请求
- **不**强制 streaming 路径（区别于 Hermes）—— 我们走 `complete()` 非流式；deadline 套在整个 `await complete()` 上即可获得等价 stale 检测
- 当 `stream_deadline_s=0` 时关掉超时（dev / 长 batch 场景）；manifest 校验拒 < 0
- Stale 错误归类 retryable 触发 fallback 而非直接终止 —— provider B 在 provider A hang 时可能正常

**测试**（落在 `services/orchestrator/tests/test_llm_router.py` 新增 6 条 L3 section）：
- `test_stream_deadline_triggers_stale_error_on_single_provider`
- `test_stream_stale_falls_back_to_next_provider` —— 验证 fallback chain 触发
- `test_stream_deadline_zero_disables_timeout` —— `None` 关闭
- `test_stream_deadline_zero_explicit_int_disables_timeout` —— `0` 关闭（manifest 路径）
- `test_fast_provider_under_deadline_succeeds` —— happy path 不退化
- `test_stream_stale_emits_counter` —— `expert_work_llm_stream_stale_total` +1

**关键文件**：
- `services/orchestrator/src/orchestrator/llm/router.py`（`stream_deadline_s` 字段 + `_invoke_with_deadline` helper + counter）
- `services/orchestrator/src/orchestrator/agent_factory.py`（`build_llm_router` / `build_step_routers` 透传 deadline）
- `packages/expert-work-runtime/src/expert_work/runtime/middleware/llm_error_handling.py`（`LLMStreamStaleError`）
- `packages/expert-work-runtime/src/expert_work/runtime/middleware/__init__.py`（导出）
- `packages/expert-work-protocol/src/expert_work/protocol/agent_spec.py`（`AgentSpecBody.stream_deadline_s`）
- `services/orchestrator/tests/test_llm_router.py`（6 个新测试）

---

### L4. File-mutation verifier footer

**新模块**：`services/orchestrator/src/orchestrator/tools/mutation_classifier.py`

```python
@dataclass(frozen=True)
class MutationOutcome:
    tool_name: str
    path: str
    landed: bool
    error: str | None

def classify(tool_name: str, args: Mapping[str, Any], result: ToolResult) -> MutationOutcome | None:
    """Return outcome iff tool is a known file-mutation tool, else None.
    Known: ``write_file`` / ``patch`` / ``delete_file`` (M0 set; extends as tools grow)."""
```

**AgentState 改动**（`state.py`）：
```python
class AgentState(TypedDict):
    ...
    #: Stream L.L4 — file-mutation outcomes accumulated within the current
    #: turn. Cleared by agent_node when it injects the advisory footer.
    failed_mutations: NotRequired[list[MutationOutcome]]
```

**tools_node 改动**（`builder.py`）：
- 每个 tool 调用结束后调 `classify(tc.name, tc.args, result)`
- 若 outcome 非 None 且 `landed=False` → 累加进 `accumulated_state.setdefault("failed_mutations_pending", []).append(outcome)`
- 提升进 graph state（与 K.8 plan 走相同 allowlist；扩 `TOOL_ALLOWED_STATE_KEYS` 加 `failed_mutations`）

**agent_node 改动**：
- 进入时取 `state.get("failed_mutations")`，非空 → 渲染 advisory footer 注入 last user message（保 L-1 不变式）：
  ```
  <mutation-advisory>
  The following file mutations from the previous turn did NOT land:
  - write_file path=src/foo.py: <error>
  - patch path=src/bar.py: <error>
  Do not assume these changes are present. Retry or report failure.
  </mutation-advisory>
  ```
- 注入后清空：返回 dict 包含 `{"failed_mutations": []}` 覆盖

**Mini-ADR L-4**：
- footer 注入只在 `failed_mutations` 非空时发生 —— 不污染 happy path 的 prompt
- 不进 system —— 否则 L-1 cache prefix 碎；进 last user message 满足"per-turn 动态注入但 cache 保留 prefix"
- mutation classifier 范围按需扩 —— 不为不存在的 tool 写 stub
- 不区分"tool 抛异常"与"tool 返回成功但实际未落盘"—— 都进 `landed=False`，模型不需要区分

**测试**：
- `test_mutation_classifier_landed_for_successful_write`
- `test_mutation_classifier_not_landed_for_error_result`
- `test_tools_node_collects_failed_mutations`
- `test_agent_node_injects_advisory_footer_when_failures_pending`
- `test_advisory_footer_in_last_user_message_not_system`（守住 L-1 不变式）
- `test_failed_mutations_cleared_after_injection`

**关键文件**：
- `services/orchestrator/src/orchestrator/tools/mutation_classifier.py`（新建）
- `services/orchestrator/src/orchestrator/state.py`（`failed_mutations` field）
- `services/orchestrator/src/orchestrator/graph_builder/builder.py:tools_node / agent_node`
- `services/orchestrator/src/orchestrator/tools/registry.py:TOOL_ALLOWED_STATE_KEYS`（扩 `failed_mutations`）

---

### L5. Iteration budget refund

**ToolResult 扩展**（`tools/registry.py`）：
```python
@dataclass(frozen=True)
class ToolResult:
    content: str
    meta: Mapping[str, Any] = field(default_factory=dict)
    state_updates: Mapping[str, Any] = field(default_factory=dict)
    #: Stream L.L5 — iterations this tool wants the agent to refund
    #: so internal multi-step work doesn't burn user-visible budget.
    refund_iterations: int = 0
```

**tools_node 累加**：
- 每 tool 调用结束累加 `refund_total += result.refund_iterations`
- tools_node 返回 dict 含 `{"step_count_refund_pending": refund_total}` （narrow channel）

**agent_node 消费**：
- 进入时读 `state.get("step_count_refund_pending", 0)`
- 当前 step_count 计算改：`step_count = state.get("step_count", 0) - refund_pending`（不到 0）
- agent_node 返回 dict 含 `{"step_count": step_count + 1, "step_count_refund_pending": 0}` 重置

**update_plan tool 改动**（`tools/update_plan.py`）：
```python
return ToolResult(
    content=f"Plan revised: {len(new_plan.steps)} steps",
    state_updates={"plan": new_plan},
    refund_iterations=1,  # plan revision shouldn't count against user-visible budget
)
```

**Mini-ADR L-5**：
- refund 走 narrow state channel `step_count_refund_pending`，不直接改 `step_count`（保 AgentState reducer 语义清晰：step_count 只由 agent_node 写）
- 不允许 `refund_iterations < 0` —— ToolResult `__post_init__` 校验
- 防御性 invariant：`step_count - refund_pending` 不到 0
- 只覆盖单 agent 内 refund；Sub-Agent（J.4）的预算下钻由 J.4 自身设计
- 与 K.8 `update_plan` 是配套：K.8 落了状态写回路径，L5 落预算保护，二者合在一起 update_plan 才是完整能力

**测试**：
- `test_tool_result_rejects_negative_refund`
- `test_tools_node_accumulates_refund_across_tool_calls`
- `test_agent_node_consumes_refund_before_step_increment`
- `test_step_count_never_goes_negative`
- `test_update_plan_call_does_not_increment_user_visible_step_count`（e2e）

**关键文件**：
- `services/orchestrator/src/orchestrator/tools/registry.py`（`refund_iterations`）
- `services/orchestrator/src/orchestrator/state.py`（`step_count_refund_pending`）
- `services/orchestrator/src/orchestrator/graph_builder/builder.py:tools_node / agent_node`
- `services/orchestrator/src/orchestrator/tools/update_plan.py`（填 refund）

---

### L6. Adaptive tool parallelization

**ToolSpec 扩展**（`tools/registry.py`）：
```python
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    #: Stream L.L6 — read-only tools can batch-execute in parallel
    #: when their path_args don't collide with concurrent write calls.
    is_read_only: bool = False
    #: Argument names whose values are filesystem paths; used by tools_node
    #: to detect conflict with concurrent write calls. Empty tuple = no path.
    path_args: tuple[str, ...] = ()
```

**tools_node 重写**（`builder.py:tools_node`）：

```python
# 1. 收集所有 tool_call → 给每个 tool 解析 (spec, args)
# 2. 按 conflict graph 分组：
#    - read-only ↔ read-only：无 conflict（同组）
#    - read-only ↔ write 同 path：conflict（分组）
#    - write ↔ write 同 path：conflict（分组）
# 3. 同 group 内顺序执行；不同 group 并行（asyncio.gather）
# 4. _MAX_TOOL_WORKERS = 8 限制并发
```

**Mini-ADR L-6**：
- conflict 检测用 resolved path（`pathlib.PurePath.resolve()` 不存在的路径返字符串；本质是字符串等值比较 + 父目录前缀）
- 顺序内 deterministic：按 `tool_call.id` 字典序保稳定
- 工具默认 `is_read_only=False`（最保守）；逐个标注 read-only：`web_search` / `http` GET（M0 我们只有 GET 路径）/ `knowledge` / `vision` / `subagent` 各自评估
- **subagent 工具**：因为可能内部产生 mutation，标 `is_read_only=False`
- 当所有 tool 都顺序兼容（默认）时 tools_node 行为与现状一致 —— 渐进部署安全
- 超时由 `stream_deadline_s`（L3）覆盖 —— 不引入 per-tool deadline（避免与 cancellation_token + L3 三重叠）

**所有 builtin tools 标注**（实装表，与初稿调整）：
| Tool | `is_read_only` | `path_args` | 理由 |
|---|---|---|---|
| `web_search` | True | () | 纯网络读取，多个 query 安全并行 |
| `http` | **False**（默认）| () | 初稿说 "M0 只 GET" 错，代码允许 POST/PUT/PATCH/DELETE；保守默认避免外部副作用并行 |
| `mcp:*` | False（默认）| () | 第三方 MCP server 不透明 |
| `ask_image` | True | () | 纯 VL LLM 调用（图像 ref 不可变） |
| `knowledge_search` | True | () | 纯向量检索 |
| `update_plan` | False（默认）| () | 写 `AgentState.plan` 全局通道 |
| `subagent` (J.4) | False（默认）| () | 子 agent 内部可能 mutation |
| `save_artifact` | False（默认）| ("name",) | 版本号竞态；按 `name` 检测冲突 |
| `list_artifacts` | True | () | 纯读 |
| `exec_python` (sandbox) | False（默认）| () | 沙盒副作用 |

**测试**：
- `test_two_read_only_tools_execute_in_parallel` —— mock 2 个 sleep(1) read-only tool 总耗时 ~1s 非 ~2s
- `test_write_and_read_same_path_execute_sequentially` —— 路径冲突时顺序
- `test_two_writes_different_paths_execute_in_parallel`
- `test_max_workers_caps_concurrency` —— 16 个并发请求最多 8 同跑
- `test_path_resolution_handles_relative_and_absolute`

**关键文件**：
- `services/orchestrator/src/orchestrator/tools/registry.py`（`ToolSpec.is_read_only` / `path_args`）
- `services/orchestrator/src/orchestrator/tools/*.py`（所有 builtin tools 标注）
- `services/orchestrator/src/orchestrator/graph_builder/builder.py:tools_node`（重写调度）
- `services/orchestrator/tests/test_tools_parallelization.py`（新建）

---

### L7. Trajectory recording（success / failed 分流）

**新模块**：`services/orchestrator/src/orchestrator/trajectory/recorder.py`

```python
@dataclass(frozen=True)
class TrajectoryRecord:
    thread_id: UUID
    tenant_id: UUID
    user_id: UUID | None
    outcome: Literal["success", "failed", "max_steps", "cancelled"]
    messages: list[dict[str, Any]]   # ShareGPT-flavored: {role, content}
    metadata: Mapping[str, Any]      # model, started_at, finished_at, step_count

class TrajectoryRecorder:
    def __init__(self, object_store: ObjectStore, prefix: str = "trajectories"): ...
    async def record(self, record: TrajectoryRecord) -> None:
        """Write to ``{prefix}/{tenant_id}/{outcome}/{YYYY}/{MM}/{DD}/{thread_id}.jsonl``.
        Outcome separation lets eval gates load by directory."""
```

**sse.py:run_agent 改动**：
- 在 `try / except / finally` 中加 record dispatch
- success：graph 正常结束
- failed：捕获 LLMError / RunFailedError
- max_steps：捕获 `MaxStepsExceededError`
- cancelled：捕获 `RunCancelledError` / 客户端断
- 每分支构造 `TrajectoryRecord` 调 recorder（非阻塞 —— fire-and-forget via `asyncio.create_task` + 超时 5s + 失败 log 不影响主路径）

**ShareGPT 格式化**：
- 输入：`list[BaseMessage]` from `AgentState.messages`
- 输出：`[{"role": "user"|"assistant"|"system"|"tool", "content": str, ...optional: tool_calls/tool_call_id}]`
- 与 J.13 eval gate 数据期望对齐（tools/eval/ 已有 `RecallCase` 风格）

**Manifest opt-out**（`AgentSpecBody.policies.trajectory_recording: bool = True`）。

**Mini-ADR L-7**：
- 用 ObjectStore（与 audit-backup-worker 同抽象）—— 不引新依赖
- prefix `trajectories/{tenant_id}/{outcome}/...` 让 eval 按 outcome scan 不用 SQL JOIN
- 复用 `ObjectStore.put` 已有的 `content_type="application/jsonl"` 路径
- 失败 → log + emit `expert_work_trajectory_record_errors_total` counter；不重试（best-effort）—— audit_log 是 source of truth，trajectory 是 LLM-trainable side data，丢一条不致命
- **不**走 audit-backup-worker 的 WORM/Object-Lock 路径（trajectory 不是合规数据），普通 ObjectStore 即可

**测试**：
- `test_trajectory_recorder_writes_to_expected_key_layout`
- `test_trajectory_outcome_separation_success_vs_failed`
- `test_sse_run_agent_dispatches_trajectory_on_completion`
- `test_sse_run_agent_dispatches_trajectory_on_cancellation`
- `test_trajectory_recording_disabled_by_manifest`
- e2e：跑一个真实 minimal agent run → 验证 ObjectStore 出现对应 key + JSONL 内容可被 `tools/eval/memory_recall.py` 风格 loader 加载

**关键文件**：
- `services/orchestrator/src/orchestrator/trajectory/__init__.py`（新建）
- `services/orchestrator/src/orchestrator/trajectory/recorder.py`（新建）
- `services/orchestrator/src/orchestrator/sse.py:run_agent`（接入）
- `packages/expert-work-protocol/src/expert_work/protocol/agent_spec.py`（`PoliciesSpec.trajectory_recording`）
- `services/orchestrator/tests/test_trajectory_recorder.py`（新建）

---

### L8. OAuth 401 自动 refresh + 重试一次

**新 Protocol**：`services/orchestrator/src/orchestrator/llm/oauth_provider.py`

```python
@runtime_checkable
class OAuthCapableProvider(Protocol):
    """Optional mixin for LLMProvider implementations that support
    credential refresh on 401. The router invokes ``refresh_credentials``
    once on auth failure and retries the original call; if the second
    attempt still 401s the provider is marked unhealthy and the router
    moves to the fallback chain."""

    async def refresh_credentials(self) -> bool:
        """Return True if refresh succeeded, False otherwise."""
```

**Router 改动**（`llm/router.py`）：
- 检测**新的** `LLMUnauthorizedError`（继承 `LLMClientError`，由 anthropic/openai adapter 对 401 raise）→ if `isinstance(provider, OAuthCapableProvider)` → 调 `refresh_credentials()` → 重试 1 次
- non-OAuth provider 401：re-raise `LLMUnauthorizedError`（仍是 `LLMClientError` 子类，4xx-no-fallback 语义不变）
- refresh `False` / 实现抛异常 → 立刻 raise `LLMAuthError`（继承 `LLMServerError`，**retryable** → 触发 fallback chain；Mini-ADR L-8 笔误更正，`LLMServerError` 子类才 retryable）
- 第二次仍 401 → wrap 为 `LLMAuthError`，同样 fallback
- 不进 401 loop —— 严格"至多 refresh 1 次"
- emit `expert_work_llm_auth_refresh_total{provider_key, result=success|fail}` counter

**Mini-ADR L-8**：
- 用 Protocol 而非基类 —— `AnthropicProvider` / `OpenAIProvider` M0 不需实现（API key 不刷）；只有 OAuth-based provider 才 opt-in
- 不在 provider 内部自动刷 —— 让 router 控制次数防 loop
- refresh 失败 → 把 provider 短暂标 unhealthy（仅当前 run；M1-D 加 breaker 时再持久化），fallback 到下一 provider
- 不引入 OAuth flow 本身（无 token endpoint client / refresh_token 持久化）—— L8 只锁能力契约，J.6 接真 VL 模型时实现具体 OAuth provider

**测试**：
- `test_oauth_provider_protocol_runtime_check`
- `test_router_calls_refresh_on_401_and_retries`
- `test_router_does_not_retry_more_than_once_on_persistent_401`
- `test_non_oauth_provider_returns_401_directly_without_refresh`
- `test_refresh_failure_marks_provider_unhealthy_and_falls_back`
- 集成测试：`FakeOAuthProvider`（第一次 401，refresh 后第二次 200）

**关键文件**：
- `services/orchestrator/src/orchestrator/llm/oauth_provider.py`（新建 Protocol）
- `services/orchestrator/src/orchestrator/llm/router.py`（refresh + retry once）
- `services/orchestrator/tests/test_llm_router_oauth.py`（新建）

---

## 4. Mini-ADR

### Mini-ADR L-1：System prompt prefix byte-stable 不变式
Anthropic prompt cache 要求 prefix 跨请求字节稳定。我们把 `BuiltAgent.system_prompt` 在 `agent_factory.build_agent()` 时一次构建后**冻结**，**所有运行时动态注入**（plan、recalled_memories、failed_mutations advisory footer）走 last user message，**不进 system**。决策代价：原先 plan / memory 进 system 的路径要重写 `_inject_plan` / `_inject_memories` 改写 user-block 注入；收益：长 session input token 成本砍 ~75%（Anthropic 实测）。该不变式同时是 L4 footer 注入位点的约束 —— 任何"per-turn 动态注入"统一走 user message tail block。

### Mini-ADR L-2：context compressor 用一次性压缩，不引入 iterative summary preservation
Hermes `context_compressor.py:454-600` 在多次压缩间保留并更新 prior summary，是优化项。我们 L2 做一次性压缩（每次从 `head + middle + tail` 算起，summary 走 fresh LLM 调用）。理由：(1) 实测 50-turn 长 session 触发压缩次数预计 ≤ 3 次 —— preservation 开销不抵收益；(2) preservation 引入跨 turn 状态污染风险（summary 漂移）；(3) L2 落地后若实际压缩频率高于预期，再加 preservation 是 backward-compatible 优化。压缩失败抛 `ContextOverflowError`（不可恢复）而非 fallback —— 压缩本身就是 last resort，再 fallback 等于隐藏 context window 已撞墙的事实。

### Mini-ADR L-3：stream deadline 用 asyncio.wait_for 套整个 complete()，不切 streaming 路径
Hermes 强走 streaming 是因为 90s timeout 需要 chunk-level 观测。我们的 LLM call 走 non-streaming `complete()`（SSE 在 sse.py 层独立），不必为 L3 改 streaming —— 用 `asyncio.wait_for(complete(), timeout=90)` 在整个调用上套 deadline 是等价的 stale 检测（hang 90s 后 raise）。决策：保持 provider 接口单一性（complete-only），不为 L3 引入 streaming chunks 概念。默认 90s 来自 Hermes 实测；manifest 可调；`stream_deadline_s=0` 关闭超时（dev / 长 batch）。Stale 归类 retryable → 触发 fallback chain（provider B 在 provider A hang 时可能正常）。

### Mini-ADR L-4：file-mutation 失败 advisory footer 注入 last user message，不进 system
Hermes 把 footer 注入 user message（`conversation_loop.py:3916-3939` "前置 user message tail"），同样不进 system。我们沿用：footer 进 last user message 的前置 block，包成 `<mutation-advisory>` 标签让模型清晰识别。Mutation classifier 范围按需扩 —— 仅当前已有 mutation tool（M0 set：`write_file`/`patch`/`delete_file`，由 J.7 skill / J.8 HITL / J.9 artifact 真上线时扩）；为不存在 tool 写 classifier stub 违反 [CLAUDE.md § 2 不写speculative 代码]。footer 注入后清空 `failed_mutations` state field —— 不在多 turn 间残留。

### Mini-ADR L-5：refund 走 narrow state channel，不直接改 step_count
ToolResult 加 `refund_iterations` → tools_node 累加进 `step_count_refund_pending` → agent_node 进入时一次性消费并重置。决策代价：多一个 state field；收益：(1) `step_count` 只由 agent_node 写，reducer 语义不破；(2) refund 累加可观察可测；(3) 防御性 invariant 集中（step_count 不到 0）。不允许 `refund_iterations < 0`（ToolResult `__post_init__` 校验，防止 tool 反向消耗预算）。L5 只覆盖单 agent 内 refund；Sub-Agent（J.4）的子 agent 预算下钻由 J.4 自身设计 —— 不在 L5 提前抽象。

### Mini-ADR L-6：adaptive parallelization 用 path conflict 分组，不引 per-tool deadline
Hermes `_should_parallelize_tool_batch()` 用 path overlap + destructive pattern 分析。我们简化为：`ToolSpec.is_read_only` + `path_args` 两字段；同 group 内顺序、跨 group 并行；`MAX_WORKERS=8`。决策代价：每个 builtin tool 需要标注两字段；收益：(1) 显式声明优于 Hermes 的"按名字 pattern 猜"（destructive pattern `write/delete/chmod` 不够 robust）；(2) 第三方 MCP tool 默认 `is_read_only=False`（保守）；(3) 不引 per-tool deadline 避免与 cancellation_token + L3 stream_deadline 三重叠。所有 builtin tools 在 L6 PR 中一次性标注完（见 § 3.L6 表格）。

### Mini-ADR L-7：trajectory 用普通 ObjectStore，不走 WORM/Object-Lock
audit_log 是合规 source of truth → WORM 保护（Stream D.1）。trajectory 是 LLM-trainable side data（J.13 eval gate / 模型 finetune 数据源）→ 普通 ObjectStore，best-effort write 失败 log 不重试。prefix `trajectories/{tenant_id}/{outcome}/{YYYY}/{MM}/{DD}/{thread_id}.jsonl` 让 eval 按 outcome scan 不用 SQL JOIN。outcome 分流 4 档：`success` / `failed` / `max_steps` / `cancelled` —— 与 Hermes ShareGPT split 一致但多一档 `cancelled`（durable resume 场景需要区分）。失败 → log + counter，不阻塞主 run 路径（fire-and-forget via `asyncio.create_task` + 5s timeout）。

### Mini-ADR L-8：OAuth refresh 用 Protocol opt-in，router 控制次数防 loop
Hermes 在 provider 内部 try refresh + retry。我们让 router 控制：检测 401 → if `isinstance(provider, OAuthCapableProvider)` → `await provider.refresh_credentials()` → 重试 1 次 → 仍 401 → 抛 `LLMAuthError` → 触发 fallback chain。决策代价：provider 实现 OAuth 时多实现一个 Protocol 方法；收益：(1) 防 401 loop（强制单次 refresh）；(2) provider 自身实现可保持简单（不需要内部计数）；(3) `AnthropicProvider` / `OpenAIProvider` 不需实现该 Protocol（API key 不刷）—— Protocol 是真 OAuth provider 的 opt-in。L8 不引 OAuth flow 本身（token endpoint client / refresh_token 持久化），J.6 真接外部 VL 模型时再实现具体 OAuth provider。

---

## 5. Verification（8 条 gap 闭合清单）

每条 PR 合并时回头打勾：

- [ ] **L1**：Anthropic cache_control 注入测试 + system byte-stable 测试绿；长 session 实测 `cache_read_input_tokens / (cache_creation + cache_read + uncached)` ≥ 0.5（Prom recording rule `Expert Work:llm:anthropic_cache_read_ratio:5m`）
- [ ] **L2**：50-turn 长对话集成测试不撞 422 + 3-pass 不死循环 + estimate_tokens 偏差 ≤ 15%
- [ ] **L3**：mock provider sleep 100s → 90s fail-fast 测试绿 + `expert_work_llm_stream_stale_total` emit
- [ ] **L4**：mock write_file 失败 → 下一 turn prompt 含 `<mutation-advisory>` footer + footer **不在 system message**（守 L-1 不变式）+ `expert_work_failed_mutation_footer_injected_total` emit
- [ ] **L5**：`update_plan` 调用后 step_count 不增加 + 防 `refund_iterations < 0` + step_count 不到 0 invariant 测试
- [ ] **L6**：2 read-only 并行总耗时 ≈ 单条耗时 + 同 path write 顺序 + `MAX_TOOL_WORKERS=8` cap + 所有 builtin tools 已标注；监测用 `expert_work_tools_stages_total` + `expert_work_tools_dispatched_total` counter pair（不走 histogram 因为 validator 要求 `_seconds` 后缀；avg concurrency = dispatched / stages）
- [ ] **L7**：trajectory key layout 测试 + 4 outcome 分流测试 + manifest opt-out 测试 + e2e ObjectStore 出 key 可被 eval loader 加载
- [ ] **L8**：fake OAuth provider 401 → refresh → 200 + 不超 1 次 refresh + Protocol runtime 检查 + AnthropicProvider 不实现 Protocol 不影响 happy path
- [ ] **设计文档 v1**：本文件随 L0 PR 合入 main，§ 3 各 PR 局部细化可补丁
- [ ] **零债收尾**：6 条核验全过

---

## 6. PR 顺序

按依赖 + 风险拆：

| PR # | Gap | 依赖 | 备注 |
|------|-----|------|------|
| 1 | **本文件 + ITERATION-PLAN 插入 Stream L**（L0 设计先行） | — | 必须先合入 |
| 2 | L3 stream stale-detection（最简，独立） | PR 1 | 1 个 wait_for 改动 |
| 3 | L5 budget refund（与 K.8 配套；改 narrow state） | PR 1 | 不与其他 L 交集 |
| 4 | L8 OAuth refresh（新 Protocol + router 改动） | PR 1 | 不影响现有 provider |
| 5 | L7 trajectory recording（新 sidecar 模块） | PR 1 | sse.py 接入是唯一耦合点 |
| 6 | L6 adaptive tool parallelization（重写 tools_node） | PR 1 | 标注 builtin 与 tools_node 重写在同 PR |
| 7 | L4 file-mutation verifier（state + 注入路径） | PR 6（与 tools_node 改动同区） | 在 L6 上加 collect 逻辑更省 review |
| 8 | L1 Anthropic prompt caching（最大改动：system 冻结） | PR 1 | 与 L2 都改 builder.py inject 路径，先合 L1 |
| 9 | L2 context compressor（新模块 + agent_node 入口） | PR 8 | 接 agent_node 入口 — 在 L1 已改完 inject 路径之上加 |
| 10 | **Stream L 收尾**：零债 6 条核验 + ITERATION-PLAN 全勾 | PRs 2–9 | 同 K 收尾模板 |

**节奏建议**：L3 / L5 / L8 / L7 是低风险独立 PR，可快推；L1 / L2 / L4 / L6 改 builder.py + state.py 同一片代码，按"L6 → L4 → L1 → L2"顺序合避免 rebase 风暴。

---

## 7. 失败模式（Stream 级）

| 失败 | 触发 | 缓解 |
|------|------|------|
| L1 cache prefix 仍然碎（cache_read_ratio < 0.5） | inject 路径漏改某处 / model spec 路径仍写 system | 集成测试 `test_agent_node_system_prompt_byte_stable_across_turns` 抓 outbound SHA-256；不达标 → 不合 PR |
| L2 estimate_tokens 偏差远大于 15% | char/4 估算对中文 / code 偏差大 | M0 接受 ≤ 30%；偏差 > 30% 升级用 tiktoken（成本：+5MB 依赖）|
| L2 summarizer 自身 hang | summarizer 走 LLM 调用，可能 hang | summarizer call 用 `asyncio.wait_for(timeout=30)`；超时 → `ContextOverflowError` |
| L3 90s 默认对慢 model 过严 | 大 batch / 大 max_tokens 慢正常 | manifest 可调 + 默认前先在 staging 取样 p99 LLM latency 验证 |
| L4 mutation 误判 landed=False | tool 返回 success 但 `content_hash` 不匹配等 | classifier 保守 —— 只识别明确 success 信号（`"bytes_written"` / `"success": true`），其它一律 landed=False 注入 footer；模型层面没有"宁错过不冤枉"问题（agent 看到 footer 会复查） |
| L5 step_count 出现负值 | refund 超过 consumed | agent_node `max(0, ...)`；同时单测覆盖该 invariant |
| L6 path resolve 跨平台不一致 | Linux / macOS path normalization 差异 | 用 `PurePath.as_posix()` 比较；测试覆盖 relative + absolute + symlink-未解析 |
| L7 ObjectStore put 失败拖慢 run | fire-and-forget 但 task 仍在 event loop | `asyncio.create_task` + 5s timeout + drop on failure（log + counter） |
| L8 refresh 实现错误导致 401 loop | provider.refresh_credentials() 内部 retry 多次 | router 强制 ≤ 1 次 refresh per call；provider 内部 retry 是反模式 |
| Stream L 与 Stream J 剩余子项的代码冲突 | 都改 builder.py / state.py | L 与 J.4-J.15 并行 PR 时 rebase 频繁；建议 L1 / L2 / L4 / L6 合完之后再开 J.4+ |
