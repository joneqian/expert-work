# Stream E — Orchestrator + 工具体系（设计先行）

> 落实 [docs/ITERATION-PLAN.md](../ITERATION-PLAN.md) § Stream E（M0；E.1 – E.15）。
> 执行的是 [architecture/01-SYSTEM-ARCHITECTURE § Orchestrator](../architecture/01-SYSTEM-ARCHITECTURE.md)、
> [research/05-deerflow-deeper-scan.md](../research/05-deerflow-deeper-scan.md)（Prefix cache / `@Next`/`@Prev` / 6 中间件）、
> [architecture/06-OPEN-SOURCE-DEPS](../architecture/06-OPEN-SOURCE-DEPS.md) §"P0 vendor 表"
> 的 M0 子集；同时落 24 P0 中的 **#15（Langfuse）、#25（cancellation）、#27（三层限流）、#28（response cache）**。
>
> 本 Stream **不**重做 LangGraph saver / event_log / object store / tenant_config / audit redactor —— 这些在 Stream A / C / D 已建好，
> E 在它们之上拼出 ReAct 单 agent 端到端可跑通的执行器 + 工具体系 + 流式输出 + 全链路 cancellation。

设计先行规则（[memory:feedback_design_first_iteration.md](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_design_first_iteration.md)）：
所有架构 / 接口 / mini-ADR 必须在编码前就锁定，E.1 – E.15 PR 仅执行本文档。

> **顺序硬性要求**：E 内部按"中间件链先建 → ReAct → 工具 → Fallback/Cache → SSE → Cancellation 末端"
> 做 bottom-up。任何"先做 ReAct 再补中间件"的捷径都会触发返工，因为 dynamic_context / 断路器 / Langfuse 一旦在
> 首次 LLM 调用之后才接入，前期所有调用都浪费 10x token + 没断路器开发期被限流爆 + trace 缺数据。

---

## 1. 范围 & 边界

### 1.1 In-scope（E.1 – E.15）

| 子项 | 实现内容 | 关联子系统 / P0 |
|------|---------|-----------------|
| **E.1 LangGraph PostgresSaver 接入** | 在 `services/orchestrator/` 新服务中用现成 `helix-runtime/checkpointer.make_checkpointer`（A.2 已建）构建 `GraphRunner`；首次 LangGraph `StateGraph.compile(checkpointer=...)` 跑通；重启可 resume；checkpoint TTL 由 Stream D.3 retention 兜底。 | 01-system § Orchestrator；GAPS § n/a |
| **E.2 `@Next/@Prev` 锚点系统** | 新 `packages/helix-runtime/middleware/`：`Middleware` Protocol + `MiddlewareChain` + `anchor` decorator。中间件声明 `name`、可选 `after: list[str]` / `before: list[str]`；链构建期做拓扑排序；环 → `ChainCycleError`。**所有后续中间件（E.3-E.5 / E.10 / D.2 PII / E.13 cache）都注册到链上**。 | research/05 §"@Next/@Prev"；01-system §"中间件链" |
| **E.3 `dynamic_context_middleware`** | 注册于 anchor `before=["llm_call"]`。从 thread_meta / event_log 拉本会话最近 N=20 条 user/assistant turn，截断到 token budget（先固定 `max_context_tokens=8000`），注入 `state["messages"]`。**绕过等于 10x token 开销** — 测试矩阵 #6 守门。 | 01-system §"动态上下文"；research/05 §"动态 context" |
| **E.4 `llm_error_handling_middleware`** | 注册于 anchor `after=["dynamic_context"]`、`before=["llm_call"]`。包 LLM 调用：断路器 + 指数退避重试 + 4xx 不重试 / 5xx + 429 重试。状态机：CLOSED → OPEN (5 连失败) → HALF_OPEN (cooldown 30s) → CLOSED。per provider key 维度。 | GAPS § n/a；研发期防限流爆 |
| **E.5 Langfuse middleware** | 注册于 anchor `after=["llm_call"]`（也兼 hook before/after 都触发）。把每次 LLM 调用的 prompt / completion / token usage / latency / cost 发到 Langfuse；trace_id 与 W3C traceparent（A.8）打通；失败 fail-soft（Langfuse 离线不阻塞主路径）。**同 PR 顺带把 D.2 TenantAwareRedactor 注册到 `before=["llm_call"]` anchor**，与 audit 写路径共享同一 redactor 实例。 | ADR-0005；GAPS § 5 #15；D.2 cross-stream |
| **E.6 ReAct mode**（单 agent，无 sub-agent） | LangGraph `StateGraph`：节点 `agent`（LLM）→ 条件边到 `tools` 或 `END`；`tools` 节点 dispatch 到工具注册表；`AgentState` 含 `messages: list[Message]` + `step_count` + `cancellation_token`。硬上限 `max_steps=20` 防止 runaway。 | 01-system §"ReAct"；research/04 § ReAct |
| **E.7 工具：builtin `web_search`** | Tavily 或 SerpAPI 适配器（M0 单 provider 即可）；通过 SecretStore（F.6 占位 / dev memory store）拿 API key；通用 `Tool` Protocol：`async def call(args: dict) -> ToolResult`。`web_search` 也是端到端首个 LLM-→-tool-→-LLM 回路的 smoke test。 | research/05 § builtin |
| **E.8 工具：HTTP** | 通用 HTTP 调用工具；M0 直连（**不**经 Credential Proxy；F.5 完成后切换）；schema 限制 `method + url + headers + body`；URL 白名单兜底（per-tenant `http_tool_allowlist`，从 tenant_config 拉，默认 `[]` = 禁用）；audit 每次调用。 | GAPS § n/a；M0 占位以备 F.5 切换 |
| **E.9 工具：MCP** | 单 MCP server 接入（Anthropic 官方 `mcp` SDK）；M0 通过本地 stdio transport；per-tenant `mcp_servers` 白名单（tenant_config）；连接池 1 connection per server；list_tools / call_tool 包装。 | research/05 § MCP；01-system § MCP |
| **E.10 `sandbox_audit_middleware`** | 注册于 anchor `before=["tool_dispatch"]`（仅 sandbox / exec_python 工具触发；HTTP / MCP / web_search 跳过）。校验 LLM 生成的 Python / shell 命令是否在白名单 AST 节点 / 命令前缀内；命中黑名单（`rm -rf`、`curl 169.254.169.254`、`os.system`） → 拒绝 + audit。**Stream F.4 接入 `exec_python` 前装好这层；F 完成前空跑无害**。 | research/05 § sandbox_audit；F cross-stream |
| **E.11 LLM Provider Fallback Chain** | `LLMRouter`：声明 primary / fallback 列表（per agent manifest），按 health 状态选；5xx + 429 + circuit-open → 尝试下一个；4xx 不 fallback（请求错）；fallback 链跨 provider（Anthropic → OpenAI 等），prompt 不变（model 名映射在适配器内部）。 | 01-system § Fallback；GAPS § n/a |
| **E.12 提供商层限流** | per-API-key token bucket（refill rate 来自 manifest `llm.rate_limit_rpm` / `rate_limit_tpm`）；超过即等待（不直接 429 给上游）；与 E.4 断路器协作（限流时不算"失败"，不开断路器）。 | GAPS § 9 #27 第 3 层 |
| **E.13 LLM response cache** | 精确匹配 cache：key = `sha256(tenant_id || model || normalize(messages) || temperature || max_tokens)`；Redis backend（基础设施已建）；`cache_ttl` 默认 3600s，per-agent 可覆盖；**per-tenant 命名空间**（无 cross-tenant 命中风险）；非 deterministic 调用（temperature > 0.1）默认绕过；命中 `cache_hit` audit + metric `helix_llm_cache_hit_total{tenant,model,result}`。 | GAPS § 10 #28 |
| **E.14 SSE 流式输出 + backpressure** | `services/orchestrator/sse.py`：SSE event 由 LangGraph stream API 中转；client 断开 → cancellation token cancel；backpressure 用 `asyncio.Queue(maxsize=128)`；slow consumer 队满 = 触发 cancellation（drop session 比 drop oldest 安全 — 防 client 看到不一致状态）；和 A.2 vendor 的 `stream_bridge`（last-event-id 重连）配合。 | 01-system § Streaming；research/04 § stream |
| **E.15 cancellation engine 节点传播** | `CancellationToken` 注入 `AgentState.cancellation_token`；每个 LangGraph 节点入口 `if token.cancelled(): raise CancelledError`；in-flight LLM call 用 anyio `cancel_scope` 中断；tool 调用同样支持；API 层 (B.3) cancel → orchestrator 在 ≤200ms 内 surface 到 LLM/工具调用层。 | GAPS § 8 #25 第 2 段 |

### 1.2 Out-of-scope（明确推迟）

| 推迟项 | 落地 Stream | 备注 |
|-------|------------|------|
| Sub-Agent 调用 / 多 agent fan-out / Plan-Execute | M1-F / M2-B | 01-system §"Sub-Agent"；M0 ReAct 单 agent 足够 |
| Sandbox 实际启 gVisor + `exec_python` 工具 | F.3 / F.4 | E.10 sandbox_audit 中间件先装，沙盒由 F 接 |
| Credential Proxy（Envoy + Vault dynamic） | F.5 / M1-C | E.8 HTTP 工具 M0 直连 |
| `thread_data_middleware` / `uploads_middleware` / `deferred_tool_filter_middleware` / `token_usage_middleware` | M1-D | research/05 列的 P1 中间件；M0 只做 P0 |
| Python 插槽（`code.package` + `tool/graph/hook` 入口） | M1-F | manifest 已预留字段 |
| Prefix cache 优化 | 与 E.13 LLM cache 共底；prefix cache 是 Anthropic SDK 内置能力，自动启用；M0 不写额外代码，仅 manifest schema 禁止动态 system_prompt | research/05 §"Prefix cache" |
| LLM 调用计费 / cost 大盘 | M1-D `token_usage_middleware` + M1-E 成本大盘 | E.5 Langfuse 已记 token；M0 不做汇总 |
| MCP HTTP / SSE transport | M1 | M0 仅 stdio |
| 多模态（图片 / 音频）输入 | M2 / M3 | M0 纯文本 |
| LLM Realtime API / Claude Computer Use | M3 | 01-system §"未来扩展" |
| Tool 计算资源 quota（CPU / mem）| 由 F sandbox 兜底 + C.5 quota | E 不重复 |

### 1.3 验收门（来自 ITERATION-PLAN § Stream E Verification）

1. **1 个 minimal agent 跑通 builtin / HTTP / MCP 三类工具** — `manifest.yaml` 声明 `tools: [web_search, http, mcp:fs]`，发 prompt `"搜一下 helix-agent 的 GitHub stars 数，然后 GET status.github.com 看是否正常，最后 ls 当前目录"` → SSE 流回完整 ReAct trace。
2. **故意触发 LLM 限流，断路器接管 + fallback chain 切换** — primary key 速率 1 rpm，连发 5 次 → 第 5 次断路器 OPEN + LLMRouter 切到 fallback；Langfuse 显示两条 trace、第一条 `error=circuit_open`、第二条 `provider=fallback`。
3. **PII redactor 工作** — `details: {ssn: "123-45-6789"}` 入 prompt → Langfuse + audit log 显示 `***REDACTED***`；命中 `helix_audit_redact_hit_total{pattern="pii_field"}`。
4. **prefix cache 命中率 > 80%** — 通过 Anthropic API 返回的 `cache_creation_input_tokens` / `cache_read_input_tokens` 头计算；连续 10 次同 system_prompt 调用，后 9 次走 prefix cache（`cache_read_input_tokens > 0`）。**E.13 response cache 命中率（精确匹配）不在此门 — 取决于业务请求模式**。
5. **cancellation 触达 in-flight LLM call** — 发起 long-running prompt，200ms 后 API client 断开 → orchestrator 在 ≤200ms 内停止 LLM stream（不继续付费 tokens）+ 写 `RUN_CANCELLED` audit。
6. **Langfuse 上每步可见 trace** — 一次 ReAct loop（3 步 thought-action-observation） → Langfuse 显示 3 个 LLM span + 2 个 tool span + 跨 span trace_id 一致；timestamps 单调递增。

---

## 2. 架构

### 2.1 服务边界

E 阶段引入**唯一新服务** `services/orchestrator/`，与现有服务的关系：

```
┌──────────────────┐  HTTP/SSE   ┌──────────────────────┐
│  control-plane   │ ─────────→  │  orchestrator        │
│  (Stream B)      │  cancel ←   │  (Stream E)          │
└──────────────────┘             │  - GraphRunner       │
        │                        │  - MiddlewareChain   │
        │ write audit            │  - LLMRouter         │
        ▼                        │  - ToolRegistry      │
┌──────────────────┐             │  - SSEStreamer       │
│  audit_log (DB)  │ ◄───────────┤  - CancellationToken │
└──────────────────┘             └──────┬───────────────┘
                                        │
            ┌───────────────────────────┼────────────────────────┐
            ▼                           ▼                        ▼
   ┌────────────────┐         ┌──────────────────┐    ┌──────────────────┐
   │ PostgresSaver  │         │   LLM providers  │    │   tools          │
   │ (event_log /   │         │   (Anthropic /   │    │   - web_search   │
   │  checkpoints)  │         │    OpenAI / ...) │    │   - http         │
   └────────────────┘         └──────────────────┘    │   - mcp:* (M0)   │
                                                       │   - exec_python  │
                                                       │     (F.4)        │
                                                       └──────────────────┘
```

**关键不变量**：
- orchestrator **不直接** 暴露公网 endpoint；只由 control-plane 通过 mTLS（C.2）调用
- LangGraph state 唯一持久化路径是 PostgresSaver；middleware 不写自己的状态表
- 中间件 / 工具 / LLMRouter 都通过 `MiddlewareChain` 触发；orchestrator main loop 不直接调 LLM
- cancellation token 是 first-class state：在 AgentState、middleware、tool、LLM client 间显式传递

### 2.2 中间件链与 anchor 系统（E.2）

声明式中间件：

```python
# packages/helix-runtime/src/helix_agent/runtime/middleware/base.py
class Middleware(Protocol):
    name: str
    after: tuple[str, ...] = ()
    before: tuple[str, ...] = ()

    async def __call__(
        self,
        ctx: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Awaitable[None]],
    ) -> None: ...

class MiddlewareChain:
    def __init__(self, middlewares: Sequence[Middleware]) -> None:
        self._ordered = topological_sort(middlewares)  # 拓扑排序，环报错

    async def invoke(self, ctx: MiddlewareContext, terminal: Callable[..., Awaitable[None]]) -> None:
        """terminal 是链尾兜底（如 llm_call）；中间件按顺序包裹它。"""
```

**Anchor 与 hook 点**：M0 阶段只暴露 4 个 anchor（**不能让用户随意命名**）：

| anchor 名 | 触发点 | 当前 M0 中间件 |
|----------|--------|---------------|
| `before_llm_call` | 准备 LLM payload 前 | `dynamic_context`, `pii_redact`, `llm_response_cache_lookup` |
| `around_llm_call` | 实际 LLM 调用包裹 | `llm_error_handling`, `langfuse` |
| `after_llm_call` | LLM 返回后、回到 ReAct loop 前 | `llm_response_cache_store`, `langfuse` |
| `before_tool_dispatch` | tool args 准备好、call tool 前 | `sandbox_audit` |

中间件声明哪个 anchor 通过 `name = "anchor:position"` 或显式 `after=(...)`：

```python
@middleware
class DynamicContextMiddleware:
    name = "dynamic_context"
    before = ("pii_redact",)  # 先注入再脱敏；保证脱敏覆盖注入的历史
```

**为什么不直接给用户暴露 `@Next("name")` 接口**：M0 用户不能注册自己的 middleware（manifest 没暴露），只有内置链；锚点的"声明式 + 拓扑排序"保留 deer-flow 的灵活度供 M1 业务侧使用，M0 仅以内部测试守门。

### 2.3 LangGraph state shape

```python
# services/orchestrator/src/orchestrator/state.py
class AgentState(TypedDict):
    # ReAct loop
    messages: Annotated[list[BaseMessage], add_messages]   # langgraph reducer
    step_count: int
    max_steps: int

    # cancellation
    cancellation_token: CancellationToken  # 不参与 checkpoint（runtime-only）

    # tenant binding
    tenant_id: UUID
    session_id: UUID
    run_id: UUID

    # LLM routing state
    provider_chain: list[str]
    current_provider_idx: int
```

**checkpoint 策略**：`cancellation_token` 标 `__skip_checkpoint__`（用 reducer 模式过滤），其他字段全 checkpoint。重启 resume 时 `cancellation_token` 重新创建（resume 默认未取消）。

### 2.4 LLMRouter / Provider Fallback（E.11 + E.12）

```python
# services/orchestrator/src/orchestrator/llm/router.py
class LLMRouter:
    def __init__(self, providers: Sequence[ProviderHandle]) -> None: ...

    async def complete(
        self, *, messages: list[BaseMessage], **kwargs: Any,
        cancellation_token: CancellationToken,
    ) -> CompletionResult:
        for handle in self._eligible(providers):
            try:
                async with handle.acquire_rate_limit_slot():  # E.12 token bucket
                    return await handle.complete(...)
            except (RateLimitError, ServerError, CircuitOpenError):
                continue
            except ClientError:  # 4xx 不 fallback
                raise
        raise AllProvidersExhaustedError()
```

**E.12 token bucket**：每 ProviderHandle 内部维护 `aiolimiter.AsyncLimiter(rate_limit_rpm, 60)`，超限 await（不直接抛 429）。

**与 E.4 断路器配合**：429 / 5xx 触发断路器计数（per provider key），但 429 本身**不**算"故障"——断路器只在持续 5xx 或调用超时连发 5 次后 OPEN。

### 2.5 工具体系（E.7 – E.9）

统一 `Tool` Protocol，dispatch 走 `ToolRegistry`：

```python
class Tool(Protocol):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema

    async def call(
        self, args: dict[str, Any], *,
        ctx: ToolContext,  # tenant_id, run_id, cancellation_token, secrets
    ) -> ToolResult: ...

class ToolRegistry:
    def register(self, tool: Tool) -> None: ...
    def get(self, name: str) -> Tool: ...
    async def dispatch(
        self, *, name: str, args: dict, ctx: ToolContext,
        chain: MiddlewareChain,
    ) -> ToolResult:
        """chain.invoke 跑 before_tool_dispatch anchor 后 → tool.call。"""
```

**MCP 接入（E.9）**：把每个 MCP server 的 list_tools 结果 wrap 成 `MCPToolAdapter(server, mcp_tool_name)` 实例，注册到 registry。

**HTTP 工具（E.8）**：M0 直连 `httpx.AsyncClient`；URL 白名单从 `tenant_config.http_tool_allowlist`（D.3 已埋；新增 alembic 0011 加这一字段）；缺失 / 不在白名单 → `ToolBlockedError` + audit。

**web_search（E.7）**：单 provider 适配器（Tavily / SerpAPI 二选一，先选 Tavily — 国内可访问；M1 加 fallback）。

### 2.6 SSE 流（E.14）

```
┌─────────────────────────────────────────────────────────────────────┐
│ control-plane FastAPI route                                          │
│   yield ev async for ev in orchestrator_client.stream(run_id):       │
└──────────────────┬──────────────────────────────────────────────────┘
                   │ gRPC / httpx SSE
                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│ orchestrator SSEStreamer.stream(run_id)                              │
│   queue = asyncio.Queue[Event](maxsize=128)                          │
│   async with langgraph.astream_events(...) as stream:                │
│       async for chunk in stream:                                      │
│           await queue.put(chunk)  # 满 → 触发 backpressure            │
└─────────────────────────────────────────────────────────────────────┘
```

**backpressure 策略**：队列满 → `queue.put(chunk)` block；orchestrator 检测 block 超 5s → cancel 整个 run（防 OOM）。不丢 event（last-event-id 由 stream_bridge 兜底重连）。

**heartbeat**：每 15s 发空 `event: heartbeat\ndata: {}\n\n`；client 断开 read 直接 EOF；同时绑定 `request.is_disconnected()` 监听（FastAPI / asgi 标准）→ 触发 cancellation。

### 2.7 Cancellation token 全链路（E.15）

```python
# packages/helix-runtime/src/helix_agent/runtime/cancellation.py
class CancellationToken:
    """协作式取消令牌。所有节点 / 中间件 / LLM / tool 入口 check 一次。"""
    def cancel(self) -> None: ...
    def cancelled(self) -> bool: ...
    @asynccontextmanager
    async def scope(self) -> AsyncIterator[anyio.CancelScope]:
        """anyio cancel_scope；token.cancel() 即触发 scope.cancel()."""
```

**信号链**：
1. FastAPI `request.is_disconnected()` → control-plane 调 orchestrator `POST /runs/{run_id}/cancel` → 设置 token
2. orchestrator main loop 每个 LangGraph 节点入口 check `token.cancelled()` → raise `asyncio.CancelledError`
3. LLM call 用 `async with token.scope() as cs: await llm.stream(...)` → token 触发即 `cs.cancel()`
4. tool call 同样用 `token.scope()` 包裹
5. SSE 流：上面任一环节 `CancelledError` → 流终止 → audit `RUN_CANCELLED`

**关键不变量**：cancellation **协作式**（每个 await 点至少 100ms 内 surface）；**不**用 SIGKILL（那是 F.7 sandbox 端做的）。

### 2.8 LLM cache（E.13）

```python
# packages/helix-runtime/src/helix_agent/runtime/llm/cache.py
class LLMResponseCache:
    def __init__(self, *, redis: Redis, default_ttl_s: int = 3600) -> None: ...

    async def get(
        self, *, tenant_id: UUID, model: str,
        messages: list[BaseMessage], params: LLMParams,
    ) -> CompletionResult | None: ...

    async def put(self, key: str, value: CompletionResult, ttl_s: int | None) -> None: ...

    @staticmethod
    def _key(tenant_id: UUID, model: str, messages, params) -> str:
        return f"llm:cache:{tenant_id}:{sha256(canonical_repr(model, messages, params))}"
```

**绕过条件**：
- `temperature > 0.1` → 视为非 deterministic，直接绕
- `stream=True` 且 manifest 标 `cache: false` → 绕
- 包含 tool_calls 的 message → 绕（结果依赖外部状态）

**命中后行为**：直接 emit fake stream event 让 SSE 仍走完整路径（保证客户端语义一致），不打 LLM。

---

## 3. Mini-ADRs

### E-1：用 LangGraph PostgresSaver 而不自建 checkpoint store

- **替代**：自建 event-sourcing replay 引擎；或用 LangGraph 但用 MemorySaver。
- **选择**：LangGraph 官方 `AsyncPostgresSaver`，已被 A.2 factory wrap。
- **理由**：(1) deer-flow 实证选过 LangGraph + Postgres saver，跑通生产场景；(2) LangGraph 的 checkpoint 是 step-level，replay 粒度合适；(3) 自建 replay 是 M2 范围（durable execution）；(4) saver 序列化是 dill，schema 演进留 M1 处理 — M0 接受重大变更下"清 checkpoint"成本。
- **代价**：dill 序列化跨版本不兼容；M0 任何 schema 变更（如 AgentState 加字段）要么 backward-compatible 加默认值、要么 release notes 标 `BREAKING: 旧 checkpoint 不可恢复`。

### E-2：anchor 用"声明式拓扑排序"而非"显式索引插入"

- **替代**：链上每个中间件传 `position: int`（0, 100, 200...）让用户插入。
- **选择**：name + after/before 声明依赖，构建期拓扑排序，环 → 启动失败。
- **理由**：(1) 索引方式在 M1 用户自定义 middleware 时极易冲突；(2) 拓扑排序使依赖关系**自记录**，任何新 middleware 加 anchor 即生效；(3) 环检测可在 CI 启动 smoke 中守门；(4) deer-flow 用 `@Next/@Prev` decorator 模式即是此结构，沿用减少重学习成本。
- **代价**：不支持"同 anchor 下多 middleware 的稳定排序"——只能靠 secondary `after` 链；M0 内置链人为编排 OK。

### E-3：dynamic_context 用"最近 N turn + token budget" 而不上 RAG / summarization

- **替代**：每 turn 跑 embedding + 检索 top-K；或用 LLM 做 summarization 压缩历史。
- **选择**：最近 20 turn + 8000 token 截断（先 truncate 旧的）。
- **理由**：(1) RAG / summarization 是 M2 Memory 三层范围（M2-C）；(2) M0 ReAct loop 通常 ≤ 10 步、上下文不会爆；(3) 截断策略对成本 / 命中率最直接可控；(4) 8000 token 远小于 Claude / GPT-4 200k 上限，预留充足 prefix cache 空间。
- **代价**：长会话信息丢失；M0 manifest 暴露 `dynamic_context.max_turns` / `.max_tokens` 让 dogfood 业务实测后调优。

### E-4：断路器粒度 = per provider API key

- **替代**：per tenant / per agent / per provider 名字（Anthropic / OpenAI）/ global。
- **选择**：per provider API key。
- **理由**：(1) 限流是 per key 维度（厂商角度）；(2) 一个租户 / agent 可能用多 key 做 fallback；(3) per provider 名字粒度不够（同 provider 多 key）；(4) global 太粗、单 key 故障误伤全租户。
- **代价**：内存状态多一个维度 (`provider_key → BreakerState`)；管理负担可接受（单租户量级）。

### E-5：MCP server 用 stdio transport，不上 HTTP

- **替代**：MCP HTTP / SSE transport，或全自研协议。
- **选择**：M0 stdio（Anthropic 官方 SDK 默认）。
- **理由**：(1) stdio 启动最简，每个 MCP server 1 个子进程；(2) HTTP transport 需引入 gateway 服务、放大 attack surface；(3) M0 单租户测试用例为主、性能足够；(4) M1 加 HTTP 不会破 stdio 路径（adapter 模式）。
- **代价**：每 MCP server 多 1 个子进程；M0 内存 / fd 可接受（白名单上限 5 server）。

### E-6：LLM cache 用 Redis 而非 PG / 应用内存

- **替代**：Postgres + 部分索引；或进程内 LRU。
- **选择**：Redis（已建，C.5 quota 共用）。
- **理由**：(1) 写多读多场景 Redis 性能最优；(2) TTL 原生支持；(3) 进程内 LRU 不能跨 orchestrator 实例共享，hit rate 降；(4) PG 加表破坏"event_log + audit_log 二分"原则。
- **代价**：Redis 单点（M0 单实例）→ 缓存丢失即重打 LLM；M1 上 Redis HA。

### E-7：HTTP 工具 M0 直连，**不**经 Credential Proxy

- **替代**：M0 即用 Credential Proxy（推迟 E.8 到 F.5 之后）。
- **选择**：M0 直连 + 白名单兜底；F.5 完成后切换。
- **理由**：(1) Credential Proxy 是 F.5 范围、依赖 SecretStore（F.6）；(2) M0 HTTP 工具的主要威胁是"访问错地址"而非"凭据泄漏"——白名单已防；(3) per-tenant `http_tool_allowlist` 缺省 `[]` = 禁用，dogfood 业务显式开通；(4) 切换是 adapter swap，业务无感。
- **代价**：M0 阶段凭据通过 manifest secret_ref 注入 header — manifest secret_ref 由 F.6 SecretStore 解析（F.6 也是 M0 范围），所以 secret 不裸露；只是 proxy 层缺失。

### E-8：SSE backpressure 满 → cancel run（而非 drop oldest）

- **替代**：drop oldest event；或扩大队列；或阻塞 push 无限等。
- **选择**：满 → 触发 cancellation。
- **理由**：(1) ReAct trace 顺序敏感（thought / action / observation 必须严格连续）——丢 event 客户端会看到不一致状态；(2) 慢消费者 = 客户端有问题，cancel 比 OOM 安全；(3) 5s + 128 队列在正常流量下永远不会触发，触发即报警信号；(4) last-event-id 由 stream_bridge 兜底，cancel 后客户端可重连恢复未消费部分。
- **代价**：慢客户端体验差；M0 dogfood 范围内 acceptable。

### E-9：cancellation 协作式而非抢占式

- **替代**：每个 task 装 watchdog 线程强 kill；或 OS signal。
- **选择**：协作式（asyncio cancel + anyio cancel_scope）。
- **理由**：(1) Python asyncio 模型本身就是协作式；(2) 强 kill 会留 inconsistent state（半写的 checkpoint、半发的 SSE event）；(3) 100ms 内 surface 足够 — 远小于人感 200ms；(4) sandbox 进程 kill 由 F.7 sandbox-supervisor 做（那里有强 kill）。
- **代价**：blocking 调用（同步 IO）无法中断 — 但 orchestrator 全 async 栈，不存在此问题。

---

## 4. 接口

### 4.1 GraphRunner

```python
# services/orchestrator/src/orchestrator/runner.py
class GraphRunner:
    def __init__(
        self, *,
        checkpointer: BaseCheckpointSaver,
        middleware_chain: MiddlewareChain,
        llm_router: LLMRouter,
        tool_registry: ToolRegistry,
        audit_logger: AuditLogger,
    ) -> None: ...

    async def run(
        self, *,
        manifest: AgentManifest,
        session_id: UUID,
        run_id: UUID,
        tenant_id: UUID,
        input_messages: list[BaseMessage],
        cancellation_token: CancellationToken,
    ) -> AsyncIterator[StreamEvent]:
        """yields LangGraph stream events; raises CancelledError on cancel."""
```

### 4.2 Middleware 注册

```python
# 内置链固化于 orchestrator startup
chain = MiddlewareChain([
    DynamicContextMiddleware(...),
    TenantAwareRedactorMiddleware(redactor),    # D.2 anchor 注册
    LLMResponseCacheLookupMiddleware(cache),
    LLMErrorHandlingMiddleware(...),
    LangfuseMiddleware(client),
    LLMResponseCacheStoreMiddleware(cache),
    SandboxAuditMiddleware(...),
])
```

### 4.3 工具注册

```python
# services/orchestrator/src/orchestrator/tools/registry.py
registry = ToolRegistry()
registry.register(WebSearchTool(api_key=secrets.get("tavily_api_key")))
registry.register(HTTPTool(tenant_config_service=tcs))
for server_cfg in tenant_config.mcp_servers:
    async with mcp.stdio_client(server_cfg) as session:
        for tool_meta in await session.list_tools():
            registry.register(MCPToolAdapter(session, tool_meta))
```

### 4.4 LLMRouter

```python
@dataclass(frozen=True)
class ProviderHandle:
    name: str           # "anthropic-primary" / "openai-fallback"
    api_key: str
    model: str
    rate_limit_rpm: int
    breaker: CircuitBreaker

class LLMRouter:
    async def complete(
        self, *,
        messages: list[BaseMessage],
        params: LLMParams,
        cancellation_token: CancellationToken,
    ) -> CompletionResult: ...
```

### 4.5 SSE event schema

```python
class StreamEvent(BaseModel):
    event_id: int             # 单调递增，stream_bridge 用作 last-event-id
    run_id: UUID
    type: Literal[
        "message_delta",      # LLM token 流
        "tool_call_start",
        "tool_call_end",
        "step_complete",
        "run_complete",
        "run_error",
        "run_cancelled",
        "heartbeat",
    ]
    data: dict[str, Any]
    timestamp: datetime
```

### 4.6 新 AuditAction（Stream E 新增）

```python
class AuditAction(StrEnum):
    # 已有...
    RUN_STARTED            = "run:started"
    RUN_COMPLETED          = "run:completed"
    RUN_CANCELLED          = "run:cancelled"
    RUN_FAILED             = "run:failed"
    TOOL_CALL              = "tool:call"
    TOOL_BLOCKED           = "tool:blocked"           # E.8 white-list + E.10 sandbox_audit
    LLM_CACHE_HIT          = "llm:cache_hit"
    LLM_CIRCUIT_OPENED     = "llm:circuit_opened"
    LLM_FALLBACK_TRIGGERED = "llm:fallback_triggered"
```

### 4.7 manifest schema delta（E 阶段新增字段）

```yaml
# packages/helix-protocol/src/helix_agent/protocol/manifest.py
llm:
  primary:
    provider: anthropic
    model: claude-sonnet-4-6
    api_key_ref: secret://tenant/{tenant}/anthropic_primary
    rate_limit_rpm: 60
    rate_limit_tpm: 100000
  fallback:               # 可选，顺序匹配
    - provider: anthropic
      model: claude-haiku-4-5
      api_key_ref: secret://tenant/{tenant}/anthropic_secondary
    - provider: openai
      model: gpt-4o-mini
      api_key_ref: secret://tenant/{tenant}/openai_fallback
  cache:
    enabled: true
    ttl_s: 3600

react:
  max_steps: 20

dynamic_context:
  max_turns: 20
  max_tokens: 8000

tools:
  - name: web_search
  - name: http
    config:
      allowlist:           # 覆盖 tenant_config.http_tool_allowlist；可空表示 deny-all
        - https://api.github.com/*
  - name: mcp:fs
    config:
      command: ["mcp-server-filesystem", "--root", "/workspace"]
```

### 4.8 migration 0011 — tenant_config http_tool_allowlist + mcp_servers

```python
# 0011_tool_config.py
op.add_column(
    "tenant_config",
    sa.Column(
        "http_tool_allowlist",
        postgresql.JSONB(),
        nullable=False,
        server_default="[]",
    ),
)
op.add_column(
    "tenant_config",
    sa.Column(
        "mcp_servers",
        postgresql.JSONB(),
        nullable=False,
        server_default="[]",
    ),
)
# JSON schema：
#   http_tool_allowlist: ["https://api.github.com/*", ...]     # glob pattern
#   mcp_servers: [{name, command: [...], env: {}, ...}]
```

---

## 5. 测试矩阵

| # | 维度 | 覆盖 PR | 测试类型 | 关键 case |
|---|------|---------|---------|-----------|
| 1 | LangGraph PostgresSaver resume | E.1 | integration | 跑一个 graph 到第 2 步 kill orchestrator，重启后从第 2 步继续 |
| 2 | MiddlewareChain 拓扑排序 | E.2 | unit | 声明 A `before=B`、B `after=A` → 顺序 [A, B]；声明 A `before=B`、B `before=A` → `ChainCycleError` |
| 3 | MiddlewareChain 顺序触发 | E.2 | unit | 链 [A, B] 调用 invoke → A 进入 → call_next → B 进入 → terminal → B 出 → A 出 |
| 4 | dynamic_context 截断到 token budget | E.3 | unit | 30 条 turn、每条 1000 token → 注入 8 条（合 8000 token）；最旧的丢 |
| 5 | dynamic_context 不破坏 prefix cache | E.3 | unit | system_prompt 不被 dynamic_context 改写（验证位置先于 system） |
| 6 | dynamic_context 绕过对比 | E.3 | unit + integration | 关闭后单 turn token 使用量上升 ≥ 5x（与基线对比） |
| 7 | llm_error_handling 重试 5xx | E.4 | unit + integration | 注入 500 → 重试 3 次 → 成功；4xx → 不重试直接抛 |
| 8 | 断路器 OPEN | E.4 | unit | 5 连失败 → state=OPEN；30s 后 HALF_OPEN；成功后 CLOSED |
| 9 | langfuse trace span 全 | E.5 | integration | 跑一次 ReAct（3 步）→ Langfuse 显示 3 LLM span + 2 tool span + trace_id 一致 |
| 10 | langfuse 离线不阻塞 | E.5 | integration | mock client 抛 ConnectionError → ReAct 继续完成 + 日志 warn |
| 11 | PII redactor anchor 注册命中 | E.5 / D.2 | integration | ssn 字段进 prompt → Langfuse + audit 都看到 `***REDACTED***` |
| 12 | ReAct max_steps 守门 | E.6 | unit | manifest `max_steps=3`；LLM 永远返回 tool_call → 第 4 步 raise + `RUN_FAILED` |
| 13 | web_search e2e | E.7 | integration | mock Tavily client → tool 返回 + LLM 继续 |
| 14 | HTTP 白名单拒绝 | E.8 | unit | `allowlist=[]` → 任何 URL → `ToolBlockedError` + `TOOL_BLOCKED` audit |
| 15 | HTTP 白名单通过 | E.8 | unit | `allowlist=["https://api.github.com/*"]` + 调 `https://api.github.com/users/x` → 通过 |
| 16 | MCP stdio 启动 + list_tools | E.9 | integration | 启 mcp-server-filesystem → 注册 `fs.read_file` / `fs.list_directory` |
| 17 | MCP 工具调用 e2e | E.9 | integration | LLM 决定 call `fs.list_directory(/tmp)` → 返回正确 |
| 18 | sandbox_audit 拒绝危险命令 | E.10 | unit | `os.system("rm -rf /")` 进 args → `ToolBlockedError` + audit |
| 19 | sandbox_audit 非 sandbox 工具跳过 | E.10 | unit | web_search tool → middleware 直接 pass through |
| 20 | LLMRouter primary 失败 fallback | E.11 | integration | mock primary 抛 ServerError → fallback provider 成功 + `LLM_FALLBACK_TRIGGERED` audit |
| 21 | LLMRouter 4xx 不 fallback | E.11 | unit | mock primary 抛 ClientError → 直接 raise |
| 22 | LLMRouter 全失败 | E.11 | unit | primary + fallback 都 5xx → `AllProvidersExhaustedError` |
| 23 | provider token bucket 限流 | E.12 | unit | rate_limit_rpm=2 + 5 个并发 → 实际 5 个请求 hit provider 间隔 ≥ 30s（不抛 429）|
| 24 | LLM cache 命中 | E.13 | integration | 同参数两次 complete → 第 2 次不调 LLM + `LLM_CACHE_HIT` audit + metric +1 |
| 25 | LLM cache 跨 tenant 不命中 | E.13 | unit | tenant_A 写 → tenant_B 同参数读 → miss |
| 26 | LLM cache 高 temperature 绕过 | E.13 | unit | `temperature=0.5` → 写 / 读都绕过 cache |
| 27 | SSE 事件单调递增 + 顺序 | E.14 | integration | 跑 ReAct → event_id 单调；type 顺序符合 ReAct 状态机 |
| 28 | SSE backpressure 触发 cancel | E.14 | integration | 注入 slow consumer（queue.put 阻塞 6s）→ orchestrator cancel run + `RUN_CANCELLED` audit |
| 29 | SSE heartbeat | E.14 | integration | 静置 LLM mock 30s → 至少 1 个 heartbeat event |
| 30 | cancellation in-flight LLM | E.15 | integration | LLM mock stream 50 tokens（each 100ms）→ token.cancel() → ≤200ms 后停止 |
| 31 | cancellation tool 中 | E.15 | integration | tool mock sleep 5s → 1s 后 cancel → tool task 取消 + audit |
| 32 | cancellation 重启不传染 | E.15 | unit | 一次 run cancel 后开启新 run → 新 run cancellation_token 默认未取消 |
| 33 | manifest schema 兼容 | E.* | unit | manifest 无 `fallback` 字段 → primary-only 正常工作；无 `cache` 字段 → 默认启用 |

**覆盖目标**：单元 ≥ 90%；integration 覆盖每个 anchor / 每条 PR 至少 1 个 happy path + 1 个 failure path。

---

## 6. 风险 & 缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| LangGraph dill 序列化跨版本不兼容 | 升级 LangGraph 即丢 checkpoint | M0 pin LangGraph 主版本；升级前 release notes 标 `BREAKING`；M1 引入 schema versioning（M2-A 一并做）|
| anchor 系统让用户自定义 → 死锁 / 环 | 启动失败 | 构建期拓扑排序 + 环检测；CI 启动 smoke 在 main 上验证内置链可建（守门）|
| dynamic_context 与 prefix cache 冲突 | API 成本暴涨 | manifest schema 禁止动态 system_prompt（CI lint 守门）；dynamic_context 只插 `messages` 段，不动 `system`|
| LLM 断路器开错 → 整 tenant 不能用 | 业务中断 | per provider key 粒度；fallback 链兜底；告警 P0 `helix_llm_circuit_open_total > 0`|
| HTTP 工具白名单配错 → 数据外泄 | per-tenant 配置错 | 缺省 `[]` = deny-all；audit 每次 call；M1 加 egress proxy 兜底 |
| MCP stdio 子进程泄漏（崩溃没清理）| fd / mem 耗尽 | 启动 N=5 上限；30s 超时 kill；orchestrator restart 清理|
| Langfuse 队列堆积阻塞主路径 | LLM 调用超时 | client 用独立 task + bounded queue（max 1000）；满即 drop trace + warn log；不阻塞主调用 |
| Redis 缓存击穿 | 重 LLM 调用 | M0 单实例 acceptable；M1 上 Redis HA |
| cancellation 协作式 → 卡 sync 调用 | 取消不生效 | orchestrator 全 async；如有 sync 调用必须包 `asyncio.to_thread`；CI lint 检 `time.sleep` / blocking io 调用 |
| ReAct 无限循环 | token 烧光 | `max_steps=20` 默认 + audit `RUN_FAILED` |
| MCP server 行为不可控（第三方 server）| 风险输出 | M0 仅白名单 `fs` / 自家 server；M1 加 MCP 输出 schema 校验 |
| token bucket 等待时间过长被误判为挂起 | UX 差 | bucket 等待超 30s → emit progress event `event: limiter_wait` 让前端可视化 |

---

## 7. 里程碑 / PR 切分

每个 E.x 一 PR；每 PR 自给自足、可独立合入 main 且 CI 绿；每 PR 收尾必须满足
[零技术债规则](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_zero_tech_debt.md)。

```
E.0  docs(stream-e): 本设计文档（即将 PR）
     - docs/streams/STREAM-E-DESIGN.md

E.1  feat(e-1): orchestrator GraphRunner + LangGraph 接入
     - services/orchestrator/（pyproject + src + tests）
     - services/orchestrator/src/orchestrator/runner.py（GraphRunner）
     - services/orchestrator/src/orchestrator/state.py（AgentState）
     - integration test：trivial graph 跑通 + resume from checkpoint

E.2  feat(e-2): MiddlewareChain + anchor 系统
     - packages/helix-runtime/src/helix_agent/runtime/middleware/{base,chain}.py
     - 拓扑排序 + 环检测
     - unit tests（顺序、循环、anchor 注册）
     - 不挂任何具体 middleware（占空架）

E.3  feat(e-3): dynamic_context_middleware
     - middleware/dynamic_context.py
     - 从 event_log / thread_meta 拉历史
     - manifest schema 加 dynamic_context 段
     - unit + integration 测试

E.4  feat(e-4): llm_error_handling_middleware + 断路器
     - middleware/llm_error_handling.py
     - CircuitBreaker 实现（per provider key）
     - 重试 + 指数退避
     - unit 覆盖状态机

E.5  feat(e-5): langfuse middleware + D.2 redactor anchor 注册
     - middleware/langfuse.py（独立后台 task 上送）
     - middleware/pii_redact.py（wrap 已有 TenantAwareRedactor）
     - environments/dev.yaml 配 Langfuse self-hosted endpoint
     - integration test：trace 全 span 可见

E.6  feat(e-6): ReAct mode + AgentState
     - state.py 完善 + graph_builder/builder.py
     - max_steps 守门
     - integration：mock LLM 跑 1-step / 3-step / max-step 三种 case

E.7  feat(e-7): tool: web_search
     - tools/web_search.py（Tavily 适配器）
     - ToolRegistry + Tool Protocol（同 PR 一起落，第一次有 tool）
     - integration test：mock Tavily

E.8  feat(e-8): tool: http + tenant http_tool_allowlist
     - tools/http.py
     - migration 0011（http_tool_allowlist 字段；mcp_servers 也一起加但 E.9 才用）
     - protocol/tenant_config.py 增字段
     - 白名单测试矩阵

E.9  feat(e-9): tool: MCP (stdio)
     - tools/mcp.py（Anthropic mcp SDK 适配）
     - tenant_config.mcp_servers 启动 + list_tools 注册
     - integration：起 mcp-server-filesystem

E.10 feat(e-10): sandbox_audit_middleware
     - middleware/sandbox_audit.py
     - AST / 命令前缀黑白名单
     - 仅 `exec_python` / `shell` 工具触发；其他 pass through
     - 与 F.4 接入预留（F 阶段挂上 sandbox 工具即生效）

E.11 feat(e-11): LLMRouter + provider fallback chain
     - llm/router.py + llm/providers/{anthropic,openai}.py
     - 4xx / 5xx 分类
     - manifest fallback 字段
     - integration：mock provider 失败切 fallback

E.12 feat(e-12): provider 层 token bucket 限流
     - llm/rate_limiter.py（aiolimiter wrapper per key）
     - 与 LLMRouter 集成
     - integration：rate_limit_rpm=2 + 5 并发 → 总时长 ≥ 60s

E.13 feat(e-13): LLM response cache（Redis）
     - llm/cache.py + 注册到 anchor before/after_llm_call
     - 绕过条件（temperature, stream, tool_calls）
     - per-tenant 命名空间
     - audit + metric

E.14 feat(e-14): SSE streaming + backpressure
     - services/orchestrator/src/orchestrator/sse.py
     - 与 stream_bridge（A.2）配合
     - heartbeat + cancel on slow consumer
     - control-plane API 桥接（B.7 已存 fake stream，本 PR 切真）

E.15 feat(e-15): cancellation 全链路传播
     - runtime/cancellation.py（CancellationToken + anyio scope）
     - GraphRunner 节点入口 check
     - LLM / tool 调用包 scope
     - control-plane POST /runs/{id}/cancel 联通 orchestrator
     - integration：≤200ms surface
```

**预期总 PR 数**：1 设计 + 15 实施 = 16 PR；累计 ~7-9 周（含 review / CI 重跑）。

---

## 8. 横切依赖回看（自下而上验证）

| Stream E 使用的下层能力 | 来源 | 状态 |
|------|------|------|
| LangGraph saver factory（PostgresSaver / InMemorySaver） | A.2 | ✅（`runtime/checkpointer/factory.py`）|
| event_log 表 + DbEventStore | A.2 | ✅ |
| audit_log + AuditLogger + TenantAwareRedactor | A.4 / D.2 | ✅（E.5 注册到 anchor）|
| TenantConfigService 60s 缓存 + tenant_config 表 | C.7 | ✅（E.8/E.9 新增字段走 0011 migration）|
| Redis（quota / cache 共享） | C.5 | ✅ |
| structured logging + W3C trace context + Prometheus metric | A.7 / A.8 / A.9 | ✅（E 所有组件 emit）|
| RLS baseline + audit_writer + audit_reader | C.4 / D.1a | ✅ |
| SecretStore Protocol（manifest secret_ref 解析） | F.6（同 M0） | ⚠️ E.7 / E.11 需要；E 阶段引入 `InMemorySecretStore` dev 兜底，F.6 实现后切换 |
| Stream bridge（last-event-id 重连） | A.2 | ✅（E.14 配合）|
| 全链路 TLS / mTLS（control-plane → orchestrator） | A.10 / C.2 | ✅ |
| Health check / graceful shutdown / 超时分层 | A.11 / A.12 / A.13 | ✅ |

**前向引用**：
- E.8 HTTP 工具 M0 直连；F.5 Credential Proxy 上线后切（adapter swap）。
- E.10 sandbox_audit 中间件 M0 实现完整逻辑；F.4 `exec_python` 工具接入后第一次有真实触发面。
- E.7 / E.11 用 `InMemorySecretStore` dev fallback；F.6 KMS Secrets Manager 实现完成后切换。

**无反向边**（中间件链先建保证）。

---

## 9. 与 ITERATION-PLAN 对照

| Plan 项 | 本文档 PR | 备注 |
|---------|----------|------|
| E.1 LangGraph PostgresSaver 接入 | E.1 | A.2 factory 已建；本 PR 接入 GraphRunner |
| E.2 `@Next/@Prev` 锚点系统 | E.2 | 顺序硬性要求第一项 |
| E.3 dynamic_context_middleware | E.3 | 10x 成本影响 |
| E.4 llm_error_handling_middleware | E.4 | 断路器防开发期被限流爆 |
| E.5 Langfuse middleware | E.5 | P0 #15；同 PR 注册 D.2 redactor |
| D.2 PII redactor anchor 注册（跨 Stream） | E.5 | D.2 实现已在 Stream D；本 PR 接入 middleware 链 |
| E.6 ReAct mode | E.6 | 单 agent、max_steps=20 |
| E.7 builtin: web_search | E.7 | Tavily M0 |
| E.8 HTTP via Credential Proxy | E.8 | M0 直连 + 白名单；F.5 完成后切 |
| E.9 MCP (单 server 接入) | E.9 | stdio transport M0 |
| E.10 sandbox_audit_middleware | E.10 | F.4 接入后真实触发 |
| E.11 LLM Provider Fallback Chain | E.11 | per provider key 断路器 + 4xx 不 fallback |
| E.12 提供商层限流 | E.12 | P0 #27 第 3 层；与 E.11 一起 |
| E.13 LLM response cache | E.13 | P0 #28；Redis；per-tenant 命名空间 |
| E.14 SSE 流式输出 + backpressure | E.14 | stream_bridge 配合；满即 cancel |
| E.15 请求取消的 engine 节点传播 | E.15 | P0 #25 第 2 段；协作式 anyio scope |

完成后 Stream E 15/15 + D.2 anchor 注册完成、24 P0 中 #15 / #25 / #27 / #28 全部勾选。
