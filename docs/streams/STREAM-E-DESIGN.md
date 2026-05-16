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

> **上下文管理 ↔ deer-flow 对照**：本文档覆盖 M0 阶段；后续 Stream 启动前的 deer-flow 18-middleware 对照与 M1/M2 待锁条款，见
> [docs/decisions/deer-flow-context-mgmt-alignment.md](../decisions/deer-flow-context-mgmt-alignment.md)。

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
| **E.6 ReAct mode**（单 agent，无 sub-agent） | LangGraph `StateGraph`：节点 `agent`（LLM）→ 条件边到 `tools` 或 `END`；`tools` 节点 dispatch 到工具注册表；`AgentState` 含 `messages: list[Message]` + `step_count` + `max_steps`（`cancellation_token` 走 `config` 通道，见 § 2.3 / § 2.7）。硬上限 `max_steps=20` 防止 runaway。**ToolErrorHandling 内置**：`tools` 节点 dispatch 用统一 try/except 包裹每个 Tool.call；任何未捕获异常（含连接错误、超时、第三方库 raise）一律转 `ToolMessage(content=f"[tool error] {type(e).__name__}: {summary}", tool_call_id=...)` 注入 messages，**不向上抛**。让 LLM 在下一轮 reason 出"换 args 重试"或"final answer"，避免 run 崩 + messages 列表不合法。对照 deer-flow `tool_error_handling_middleware.py`。 | 01-system §"ReAct"；research/04 § ReAct；deer-flow tool_error_handling |
| **E.7 工具：builtin `web_search`** | Tavily 或 SerpAPI 适配器（M0 单 provider 即可）；通过 SecretStore（F.6 占位 / dev memory store）拿 API key；通用 `Tool` Protocol：`async def call(args: dict) -> ToolResult`。`web_search` 也是端到端首个 LLM-→-tool-→-LLM 回路的 smoke test。**输出 truncation**：默认 `max_results=5`；每条结果 `content[:4096]` 字符截断（超出标 `ToolResult.meta.truncated=true`），与 deer-flow `community/tavily/tools.py` 截断对齐。 | research/05 § builtin |
| **E.8 工具：HTTP** | 通用 HTTP 调用工具；M0 直连（**不**经 Credential Proxy；F.5 完成后切换）；schema 限制 `method + url + headers + body`；URL 白名单兜底（per-tenant `http_tool_allowlist`，从 tenant_config 拉，默认 `[]` = 禁用）；audit 每次调用。**输出 truncation**：response body 硬上限 20k chars（tail 截断 + 保 status code + `meta.truncated=true`）；response headers 单独上限 4k chars。 | GAPS § n/a；M0 占位以备 F.5 切换 |
| **E.9 工具：MCP** | 单 MCP server 接入（Anthropic 官方 `mcp` SDK）；M0 通过本地 stdio transport；per-tenant `mcp_servers` 白名单（tenant_config）；连接池 1 connection per server；list_tools / call_tool 包装。**输出 truncation**：`call_tool` 结果统一 cap 20k chars（中间截断保头尾各 50%，便于 LLM 看到 'tail' 收敛标记）；超过 → `ToolResult.meta.truncated=true`。 | research/05 § MCP；01-system § MCP |
| **E.10 `sandbox_audit_middleware`** | 注册于 anchor `before=["tool_dispatch"]`（仅 sandbox / exec_python 工具触发；HTTP / MCP / web_search 跳过）。校验 LLM 生成的 Python / shell 命令是否在白名单 AST 节点 / 命令前缀内；命中黑名单（`rm -rf`、`curl 169.254.169.254`、`os.system`） → 拒绝 + audit。**Stream F.4 接入 `exec_python` 前装好这层；F 完成前空跑无害**。 | research/05 § sandbox_audit；F cross-stream |
| **E.10.5 `loop_detection_middleware`** | 注册于 anchor `after_llm_call`。维护 sliding window，记录最近 N=3 次 LLM 返回的 `(tool_name, normalized_args_hash)` 元组；若全相同 → 视为循环：清空 AIMessage 的 `tool_calls`（用 `clone_ai_message_with_tool_calls` helper 同 ID 重写，保留 LangGraph add_messages reducer 不引入新消息位）+ 注入 `<system-reminder>检测到工具循环，请给出 final answer 或换不同参数</system-reminder>` HumanMessage。**与 `max_steps=20` 互补**：前者是步数硬上限（兜底），本中间件是循环早期 abort（防 20 次同一 buggy 调用烧 token）。设计灵感：deer-flow `agents/middlewares/loop_detection_middleware.py`。 | research/04 § loop_detection；deer-flow loop_detection_middleware |
| **E.11 LLM Provider Fallback Chain** | `LLMRouter`：声明 primary / fallback 列表（per agent manifest），按 health 状态选；5xx + 429 + circuit-open → 尝试下一个；4xx 不 fallback（请求错）；fallback 链跨 provider（Anthropic → OpenAI 等），prompt 不变（model 名映射在适配器内部）。 | 01-system § Fallback；GAPS § n/a |
| **E.12 提供商层限流** | per-API-key token bucket（refill rate 来自 manifest `llm.rate_limit_rpm` / `rate_limit_tpm`）；超过即等待（不直接 429 给上游）；与 E.4 断路器协作（限流时不算"失败"，不开断路器）。 | GAPS § 9 #27 第 3 层 |
| **E.12.5 middleware chain wiring** | E.6 graph_builder 注释明确："This PR deliberately does not wire the E.3/E.4/E.5 middleware chains into the agent node. That wiring happens when E.11 LLMRouter lands." — 但 E.11 PR scope 已较大（router + 2 adapter + wire-format mapping），wiring **从 E.11 推后**。本 PR 把所有已实现但未激活的中间件接入：`agent_node` 串 `before_llm_call → around_llm_call → after_llm_call` 三个 anchor；`tools_node` 串 `before_tool_dispatch`；`LLMRouter` 加 `chain: MiddlewareChain` 参数，**每次 provider 调用单独包 `around_llm_call`**（Mini-ADR E-13：per-upstream-key 隔离，让 E.4 断路器能 per-key 计数而非 per-router 累加）。落地后 6 个中间件（E.3 / E.4 / E.5 langfuse / E.5 pii_redact / E.10 sandbox_audit / E.10.5 loop_detection）全部首次"真跑"。 | 自我补全：E.11 推后造成的 wiring 缺口；对照 ITERATION-PLAN E 段未单列 |
| **E.13 LLM response cache** | 精确匹配 cache：key = `sha256(tenant_id || model || normalize(messages) || temperature || max_tokens)`；Redis backend（基础设施已建）；`cache_ttl` 默认 3600s，per-agent 可覆盖；**per-tenant 命名空间**（无 cross-tenant 命中风险）；非 deterministic 调用（temperature > 0.1）默认绕过；命中 `cache_hit` audit + metric `helix_llm_cache_hit_total{tenant,model,result}`。 | GAPS § 10 #28 |
| **E.14 SSE 流式输出 + backpressure** | `services/orchestrator/sse.py`：`run_agent` worker（后台 `asyncio.Task`，`graph.astream()` 逐 chunk → `StreamBridge.publish`）+ `sse_consumer`（`bridge.subscribe` → SSE frame；client 断开 → `run_manager.cancel`）。**in-process 单体**（control-plane import orchestrator 库，非独立服务 — 见 § 2.6 架构修正）。backpressure：`StreamBridge` bounded buffer + drop-oldest（Mini-ADR E-8 修正）；和 A.2 `stream_bridge`（last-event-id 重连）+ `RunManager` 配合。control-plane `runs.py` 切掉 B.7 fake stream。worker graph 注入式；manifest→graph agent factory 是独立后续 PR。 | 01-system § Streaming；research/04 § stream；deer-flow `gateway/services.py` + `runtime/runs/worker.py` |
| **E.15 cancellation engine 节点传播** | `CancellationToken`（背后 `asyncio.Event`）经 `config["configurable"]` 注入（非 `AgentState` — 见 § 2.7 实现修正）；每个 LangGraph 节点入口 `token.raise_if_cancelled()` → 抛 `RunCancelledError`；in-flight LLM / tool call 用 `token.run_cancellable(coro)` 包（`asyncio.wait` 竞速，取消即中断 await）；`run_manager.cancel` → set abort_event → token 即取消，≤200ms 内 surface。**Resume sanitize**：`GraphRunner.sanitize_thread` 在 resume 前 scan checkpoint messages — 若 AIMessage 有 `tool_calls` 但无匹配 ToolMessage（cancel 打断 [LLM 完 → tool 派遣] 窗口造成的 orphan），为每个缺失 `tool_call_id` 注入 placeholder `ToolMessage(content="[cancelled before dispatch]", status="error")`（`aupdate_state(as_node="tools")` 写入，让 resume 流向 agent 重推理）。**不上独立 middleware**（场景罕见，纯函数 + runner 方法即可）。对照 deer-flow `dangling_tool_call_middleware.py`。 | GAPS § 8 #25 第 2 段；deer-flow dangling_tool_call |

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

E 阶段引入 `services/orchestrator/` —— **M0 它是个 library，不是独立部署的服务**（架构修正，详见 § 2.6）。control-plane FastAPI app 直接 `import orchestrator`，graph 当后台 `asyncio.Task` 在 control-plane 进程内跑。原设计设想的"独立 orchestrator 服务 + HTTP/SSE/mTLS 调用"推到 M1+（水平扩展需要拆进程时再做）。

```
┌────────────────────────────────────────────────────┐
│  control-plane 进程（Stream B FastAPI app）         │
│   import orchestrator（library）                    │
│   ┌──────────────────────────────────────────────┐ │
│   │ orchestrator（Stream E，in-process library）  │ │
│   │  - GraphRunner / build_react_graph            │ │
│   │  - MiddlewareChain / LLMRouter / ToolRegistry │ │
│   │  - run_agent worker + sse_consumer（E.14）    │ │
│   └──────────────────────────────────────────────┘ │
│   RunManager / StreamBridge（A.2 helix-runtime 库） │
└────────┬───────────────────────────┬───────────────┘
         │ write audit                │
         ▼                            ▼
┌──────────────────┐   ┌──────────────────────────────────────┐
│  audit_log (DB)  │   │ PostgresSaver / LLM providers / tools │
└──────────────────┘   └──────────────────────────────────────┘
```

**关键不变量**：
- M0 orchestrator 是 control-plane 进程内的 library；无自己的公网 / 内网 endpoint。M1+ 若为水平扩展拆独立进程，再引入 mTLS（C.2）服务间调用。
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
```

**实际落地的 `AgentState`**（E.6 + E.13 后）只有 `messages` / `step_count` / `max_steps` 三个字段 —— TypedDict 的每个 channel 都被 checkpoint，所以**不可序列化的运行时对象一律不进 AgentState**：

- `cancellation_token`（E.15）：背后是 live `asyncio.Event`，走 `config["configurable"]`（见 § 2.7 实现修正）。
- `tenant_id` / `session_id` / `run_id`：走 `config["configurable"]`（LangGraph 惯用法，§ 2.3 开头已说明）。
- `provider_chain` / `current_provider_idx`：E.11 `LLMRouter` 自己持有 provider 列表 + fallback 状态，不进 state。

**checkpoint 策略**：`AgentState` 三个字段全 checkpoint（dill）。运行时对象（token / router / registry）经 `config` 通道传入，天然不参与 checkpoint，resume 时由 `run_agent` worker 重新注入。

### 2.4 LLMRouter / Provider Fallback（E.11 + E.12 + E.12.5）

```python
# services/orchestrator/src/orchestrator/llm/router.py
class LLMRouter:
    def __init__(
        self,
        providers: Sequence[ProviderHandle],
        chain: MiddlewareChain | None = None,  # E.12.5
    ) -> None: ...

    async def __call__(
        self, *, messages: Sequence[BaseMessage], tools: Sequence[ToolSpec],
    ) -> AIMessage:
        for handle in self.providers:
            try:
                # E.12.5: 每次 provider 调用单独包 around_llm_call
                # ctx.payload["provider_key"] 让 E.4 断路器能 per-key 隔离
                if self.chain is not None:
                    ctx = MiddlewareContext(payload={
                        "provider_key": handle.key,
                        "messages": messages, "tools": tools,
                    })
                    await self.chain.invoke(
                        "around_llm_call", ctx,
                        terminal=lambda c: handle.provider.complete(
                            messages=c.payload["messages"],
                            tools=c.payload["tools"],
                        ),
                    )
                    return ctx.payload["response"]
                # 无 chain 时（M0 dev / unit test）直接调
                return await handle.provider.complete(messages=messages, tools=tools)
            except LLMClientError:                       # 4xx 不 fallback
                raise
            except LLMError:                             # 5xx / 429 / breaker → 下家
                continue
        raise AllProvidersExhaustedError(last_exc)
```

**E.12 token bucket**（已实现）：`RateLimitedProvider` 包装 LLMProvider，包内部持有 `aiolimiter.AsyncLimiter(rate_limit_rpm, 60)`；超限 `async with limiter:` await，不抛 429。每个 ProviderHandle 用独立的 RateLimitedProvider 实例 → per-key 隔离自动落地。

**E.12.5 wiring**（待实现）：见上面 router 内部 `chain.invoke("around_llm_call", ...)`。一次 ReAct step 可能触发 N 次 `around_llm_call`（fallback 链上 N 个 provider 都被试一遍），每次触发 ctx.payload["provider_key"] 都是当前 provider 的 key——这是 E.4 断路器 per-key 计数的关键。Mini-ADR E-13 解释了"per-provider 包"vs"per-router 包"的取舍。

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

**manifest → registry 装配**：`AgentSpecBody.tools` 是 `type`-判别联合（Mini-ADR E-14）；`build_tool_registry(tool_specs, *, tool_env)` 把声明映射成具体适配器实例并注册。`ToolEnv` 注入包携带平台运行期依赖（Tavily client / `AllowlistProvider` / `MCPServerPool`）；`build_agent` 调装配器后把 registry 交给 `build_react_graph`。声明 builtin 名未知 / 依赖缺失 → `AgentFactoryError`。

### 2.6 SSE 流（E.14）

**架构修正（E.14 实施时定稿）**：原草图画的是"control-plane → gRPC/httpx → 独立 orchestrator 服务"。对照 deer-flow（`backend/app/gateway` + `backend/packages/harness/deerflow/runtime` 同进程）后改为 **in-process 单体**：orchestrator 是 library，control-plane FastAPI app 直接 import，graph 当**后台 `asyncio.Task`** 跑。M0 不引入独立 orchestrator 服务 / 服务间 RPC（那是 M1+ 水平扩展时的事）。

```
┌─────────────────────────────────────────────────────────────────────┐
│ control-plane FastAPI route  POST /v1/sessions/{thread}/runs         │
│   record = run_manager.create(...)                                   │
│   task   = asyncio.create_task(run_agent(bridge, record, graph, ...))│
│   return StreamingResponse(sse_consumer(bridge, record, request))    │
└───────────────┬──────────────────────────────┬──────────────────────┘
                │ subscribe                     │ create_task
                ▼                                ▼
┌───────────────────────────┐   ┌──────────────────────────────────────┐
│ sse_consumer               │   │ run_agent worker (background Task)   │
│  async for ev in           │◄──│  async for chunk in graph.astream(): │
│    bridge.subscribe(run_id)│   │    await bridge.publish(run_id, ...) │
│  → yield SSE frame         │   │  finally: bridge.publish_end(run_id) │
└───────────────────────────┘   └──────────────────────────────────────┘
         StreamBridge（A.2，已存）是生产者 / 消费者解耦总线
```

三个角色（全部 in-process）：
- **`RunManager`**（A.2 `runtime/runs/`，已存）— 持 `RunRecord`（`run_id` / `status` / `task` / `abort_event`），生命周期注册表。
- **`run_agent` worker**（E.14 新增，`orchestrator/sse.py`）— 后台 task：`graph.astream()` 逐 chunk `bridge.publish()`；结束 `bridge.publish_end()`；异常 → `bridge.publish("error", ...)`。
- **`sse_consumer`**（E.14 新增）— SSE 生成器：`bridge.subscribe(run_id)` → yield SSE frame；`finally` 段实现 `on_disconnect`（client 断开 → `run_manager.cancel`）。

**worker graph 注入式**：`run_agent` 接收一个已编译 graph（或 graph 工厂），不自己装配。manifest → 完整 agent graph 的装配（"agent factory"）是**独立 PR**，不在 E.14 范围 —— E.14 用 scripted graph 测 streaming 机制本身。

**backpressure 策略**：见 Mini-ADR E-8 —— M0 用 `StreamBridge` 的 bounded buffer + drop-oldest（满了丢最旧，`start_offset` 前移），未消费部分由 last-event-id 重连补；cancel-on-full 推 M1。

**heartbeat**：`StreamBridge.subscribe` 每 `heartbeat_interval`（默认 15s）无事件即 yield `HEARTBEAT_SENTINEL`；`sse_consumer` 转成 `: heartbeat\n\n` 注释帧。client 断开 read 直接 EOF；同时绑定 `request.is_disconnected()`（FastAPI / asgi 标准）→ 触发 cancellation。

### 2.7 Cancellation token 全链路（E.15）

```python
# packages/helix-runtime/src/helix_agent/runtime/cancellation.py
class RunCancelledError(Exception):
    """协作取消在 checkpoint 处抛出 — 普通领域异常（非 asyncio.CancelledError）。"""

class CancellationToken:
    """协作式取消令牌。背后是一个 asyncio.Event。"""
    @classmethod
    def from_event(cls, event: asyncio.Event) -> Self: ...   # 绑定到 RunRecord.abort_event
    def cancel(self) -> None: ...
    def cancelled(self) -> bool: ...
    def raise_if_cancelled(self) -> None: ...                # 同步 checkpoint（节点入口）
    async def run_cancellable(self, coro) -> T: ...          # 包一个 await，cancel 即中断
```

**实现修正（E.15 落地）**：

1. **token 走 `config["configurable"]` 而非 `AgentState`**：原 § 2.3 设想把 `cancellation_token` 放进 `AgentState` 并标 `__skip_checkpoint__`。但 `AgentState` 是 TypedDict、每个 channel 都被 checkpoint，且 LangGraph 没有"单 channel 跳过"的公开 API；而 token 背后是个 live `asyncio.Event`，**不可序列化**。改走 `config["configurable"]`（per-invocation、不 checkpoint，与 `tenant_id` / `run_id` 同一通道）。`run_agent` worker 用 `CancellationToken.from_event(record.abort_event)` 把 token 绑到 run 的 abort_event 上再注入 config。
2. **取消用 `asyncio.wait(FIRST_COMPLETED)` 竞速，而非 anyio `CancelScope`**：`run_cancellable(coro)` 把 coro 与"取消事件 wait"竞速；取消先到 → cancel coro 的 task → 抛 `RunCancelledError`。纯 stdlib，无需 anyio 依赖，语义更易推理。
3. **抛 `RunCancelledError` 而非 `asyncio.CancelledError`**：自定义领域异常，`run_agent` 可用普通 `except` 接住并把 run 收为 `INTERRUPTED`，不与 task 级 asyncio 取消混淆。

**信号链**：
1. FastAPI `request.is_disconnected()`（E.14 `sse_consumer`）→ `run_manager.cancel(run_id)` → set `record.abort_event`
2. token（`from_event(abort_event)`）即 `cancelled()` → 每个 LangGraph 节点入口 `token.raise_if_cancelled()` → 抛 `RunCancelledError`
3. LLM call 用 `await token.run_cancellable(llm_caller(...))` → 取消即中断 in-flight await
4. tool call 同样 `await token.run_cancellable(_dispatch_tool(...))`（取消在 `_dispatch_tool` 内表现为 `asyncio.CancelledError` — 非 `Exception`，不会被工具自己的 `except Exception` 吞成 ToolMessage）
5. `run_agent` worker 接 `RunCancelledError` → run 收为 `INTERRUPTED`；`StreamBridge` 照常 `publish_end`

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

### E-8：SSE backpressure M0 用 drop-oldest（cancel-on-full 推 M1）

- **原决策**：满 → cancel 整个 run（而非 drop oldest）。
- **修正（E.14 实施时）**：M0 用 **drop-oldest** —— `StreamBridge` 的 bounded buffer 满了删最旧 event、`start_offset` 前移；落后的订阅者用 last-event-id 重连时若游标已被挤出，从最早保留 event 续上（带 warning）。
- **为何翻转**：(1) helix-agent 的 `InMemoryStreamBridge`（A.2 从 deer-flow 抄）**本来就是 drop-oldest**——原 Mini-ADR E-8 跟已落地代码自相矛盾，等于一条没人执行的 ADR；(2) deer-flow 生产环境就是 drop-oldest + last-event-id 兜底，跑得通；(3) cancel-on-full 要把 `StreamBridge` 从 bounded-drop 改成 blocking-queue + 5s 超时检测（语义大改 ~100 LOC），M0 不值当；(4) drop-oldest 真正丢 event 只在订阅者持续慢于生产者且超出 256 buffer 时——正常 dogfood 流量到不了。
- **M1 再评估**：若 dogfood 出现"客户端看到不一致 trace"，再上 cancel-on-full（恢复原决策的理由 (1)：ReAct trace 顺序敏感）。届时 `StreamBridge` 加一个 `overflow_policy` 参数即可，不破坏现有接口。
- **代价**：M0 极端慢客户端可能丢中段 event；可观测性靠 `stream_bridge.subscriber_fell_behind` warning 日志兜。

### E-9：cancellation 协作式而非抢占式

- **替代**：每个 task 装 watchdog 线程强 kill；或 OS signal。
- **选择**：协作式 —— 节点入口同步 checkpoint（`raise_if_cancelled`）+ in-flight await 用 `asyncio.wait(FIRST_COMPLETED)` 竞速中断（`run_cancellable`）。E.15 落地时弃用了原计划的 anyio `CancelScope`：纯 stdlib `asyncio` 竞速已够、语义更易推理、不引入 anyio 直接依赖。
- **理由**：(1) Python asyncio 模型本身就是协作式；(2) 强 kill 会留 inconsistent state（半写的 checkpoint、半发的 SSE event）；(3) 100ms 内 surface 足够 — 远小于人感 200ms；(4) sandbox 进程 kill 由 F.7 sandbox-supervisor 做（那里有强 kill）。
- **代价**：blocking 调用（同步 IO）无法中断 — 但 orchestrator 全 async 栈，不存在此问题。

### E-10：tool output truncation 走"每工具自管"而非中间件统一拦

- **替代**：写一个 `output_truncation_middleware`，在 `after_tool_dispatch` anchor 统一拦 ToolResult。
- **选择**：每个工具的适配器内部 truncate；通过 `helix_agent.runtime.tools.truncation` 共享 helper（≈30 LOC）复用 head-only / tail-only / middle-bias 三种策略。
- **理由**：(1) 不同工具的 truncate 策略不一样 — bash 要中间截（保头尾命令 + exit code），read_file 要头部截（提示 start_line/end_line），HTTP 要尾部截（保 status code）；统一中间件做不到细分。(2) 测试边界清晰 — truncation bug 出在 web_search 就只看 web_search.py。(3) deer-flow 也是 per-tool 各自 `_truncate_*_output` helper，证明 pattern 在生产环境跑得通。
- **代价**：每个工具 PR 都要写一份 truncate；helper 共享降低重复代码。

### E-11：LoopDetectionMiddleware 用 normalized_args hash 而非完整 args 比较

- **替代**：用 `json.dumps(args, sort_keys=True)` 全量字符串比较。
- **选择**：normalize（lower-case + strip whitespace + sort keys）后 sha256 前 16 字节做 fingerprint。
- **理由**：(1) LLM 会在重试时改大小写、加空格 — 全量比较漏检。(2) 短 hash 比较 O(1)，sliding window 内存占用可控（每条 record 24 字节）。(3) hash 碰撞概率忽略不计（16 字节 = 2^128 命名空间，N=3 检测撞不到）。
- **代价**：normalize 规则要文档化；LLM 故意改 args 但语义不变（如 `{a:1, b:2}` vs `{b:2, a:1}`）会被判断为循环 — 这是合理的（同语义重复 = 循环），不是 bug。

### E-12：tool 异常转 `ToolMessage(error)` 而非向上 raise

- **替代**：tool 抛异常 → orchestrator 捕获 → run 标 FAILED → 返回 client。
- **选择**：tool 抛异常 → wrap 成 `ToolMessage(content="[tool error] ...", tool_call_id=...)` 注入 messages → 让 LLM 在下一轮 reason 出 retry / 换 args / final answer。
- **理由**：(1) deer-flow / Anthropic SDK 推荐做法 — tool 错是 LLM 自己处理的 reasoning 信号，不是系统故障。(2) raise 会让 messages 列表卡在"AIMessage(tool_calls=[X]) 无 ToolMessage"的非法状态（同 E.15 resume sanitize 同类问题）。(3) LLM 见到 error message 通常能换种方式做（GitHub stars 工具失败 → 换 web_search）。
- **代价**：tool 错本身被吞 → 不会立即触发告警；通过 audit log + Langfuse span error 字段定位。区分"业务 error 应让 LLM 处理"（→ ToolMessage）vs"基础设施 error 应告警"（→ Langfuse 高严重度 span）：连接超时 / 鉴权失败 / quota 耗尽 仍然 ToolMessage，但 audit `severity=high`。

### E-13：`around_llm_call` 包**单个 provider 调用** 而非**整个 LLMRouter**

- **替代**：`agent_node` 把整个 `LLMRouter.__call__` 包在 `chain.invoke("around_llm_call", ...)` 里——chain 只触发一次，router 内部 fallback 对 chain 透明。
- **选择**：`LLMRouter` 内部 fallback 循环里，**每次 provider 尝试**都单独包 `chain.invoke("around_llm_call", ctx={provider_key, ...}, terminal=handle.provider.complete)`——chain 每次切换 provider 都重新触发一次（一次 ReAct 步可能触发多次 `around_llm_call`）。
- **理由**：(1) **E.4 断路器要 per-upstream-key 计数**（Mini-ADR E-4）——anthropic primary 这把 key 五次失败 → OPEN，但 kimi fallback 那把 key 的失败计数不能受影响。包整个 router 时 chain 看不到 fallback 切换 → 断路器只能按 router 聚合维度计数，丢失 per-key 隔离。(2) **Langfuse span 要按 provider 拆**——`provider=anthropic-primary error=503 → provider=kimi-fallback success`：两个独立 span 才能让看板按 provider 拆 latency / 错误率。(3) **retry 中间件**应当让单个 provider 完成自己的重试预算后再 fallback，而不是把"router 重试 + provider fallback"两层语义糊在一起。
- **代价**：router 多接一个 `chain: MiddlewareChain | None = None` 参数（向后兼容默认 None，单元测试不传）；chain 触发多次 = 中间件本身要能幂等被调（langfuse 每次产生独立 span 是 by-design，breaker 本身就是 per-call，retry 本身就是循环——都天然幂等）。`provider_key` payload 必须由 router 设置好再 invoke，不能由中间件回读 router 状态。

### E-14：manifest `tools:` 用 `type`-判别联合，M0 schema 对齐已实现的适配器

- **背景**：`02-AGENT-MANIFEST.md` 早期示例把 http/mcp 工具画成**声明式逐 API**（每个 `http:` 条目内联 url/method/schema/auth；`mcp:` 内联 transport/url）。但已合并的 E.8/E.9 实现不是这个模型——`HTTPTool` 是**一个通用工具**（"发 HTTP 请求，URL 必须命中租户 allowlist"），allowlist 来自 `tenant_config.http_tool_allowlist`；`MCPTool` 包装 MCP server 自己 advertise 的工具，server 来自 `tenant_config.mcp_servers`。manifest 文档与实现分叉。
- **替代**：(1) 重构 E.8/E.9 去贴文档的逐 API 模型；(2) manifest 条目用"单键即判别"形状（`- builtin: web_search` / `- mcp: {...}`，键名即类型）。
- **选择**：M0 tool-spec 对齐**实现**，不返工已合并的 E.8/E.9。manifest `tools:` 是一个 **`type` 字段判别的 Pydantic discriminated union**：`builtin`（`name` + `config`）/ `http`（启用开关，allowlist 仍租户作用域）/ `mcp`（启用开关 + 可选 `allow_tools` 过滤，server 仍租户作用域）。`python` / `subagent` 不在 M0 union 内——声明即 422 失败，明确推 M1-F。
- **理由**：(1) E.8/E.9 已上线测过，逐 API 返工成本高且收益存疑——通用 HTTP 工具 + 租户 allowlist 的隔离模型本身更干净。(2) `type` 判别字段是 Pydantic `Field(discriminator=...)` 的惯用形状，比"单键即判别"代码健壮、错误信息清晰。(3) http/mcp 的真实配置本就是租户作用域（allowlist / mcp_servers 跨 agent 共享），不该塞进单 agent 的 manifest。
- **代价**：`02-AGENT-MANIFEST.md` 的 `tools:` 段要改写成 `type:` 形状（M0 无真实 manifest 用到 tools，无破坏性）；逐 API http 工具的需求若 M1 仍要，另开设计。
- **装配**：`build_tool_registry(tool_specs, *, tool_env)` 把每条 typed spec 映射成具体适配器；平台运行期依赖（Tavily client / allowlist provider / MCP pool）走 `ToolEnv` 注入包。声明了某工具但 `ToolEnv` 未提供对应依赖 → `AgentFactoryError`。本 PR 交付 schema + 装配器 + `build_agent` 接缝（装配器全单测覆盖）；control-plane 把真实后端（Tavily key 设置项 + secret ref / allowlist 接 `TenantConfigService` / MCP server pool 生命周期）注入 `ToolEnv` 是单列 follow-up —— 在此之前 control-plane 传空 `ToolEnv()`，纯 LLM agent 正常跑，声明工具的 agent 在 build 期得到明确 `AgentFactoryError`。

### E-15：中间件链装配 = "无依赖中间件 always-on + 平台依赖中间件 env-gated"

- **背景**：7 个 middleware 都已实现（E.3/E.4/E.5/E.10.5/E.13 + sandbox_audit + pii_redact），但 `build_agent` 调 `build_react_graph` 不传任何 chain —— manifest → 编译 graph 的中间件装配没人做。
- **替代**：(1) 全部 always-on；(2) 全部 manifest 字段开关驱动。
- **选择**：按"是否需要平台运行期依赖"二分。
  - **always-on（无依赖）**：`DynamicContextMiddleware`（before_llm_call）、`LLMErrorHandlingMiddleware`（around_llm_call）、`LoopDetectionMiddleware`（after_llm_call）—— 每个 agent 都装。
  - **env-gated（需平台依赖）**：`PIIRedactorMiddleware`（需 `RedactText`）、`LLMCacheLookup/StoreMiddleware`（需 `LLMResponseCache`）、`LangfuseMiddleware`（需 `LangfuseClient`）—— 仅当 `MiddlewareEnv` 提供对应依赖才装。
  - **推迟**：`SandboxAuditMiddleware`（before_tool_dispatch）随 Stream F sandbox 工具接入时再装（设计 § 1.1 E.10 原文"Sandbox 工具接入时装"）。
- **理由**：(1) always-on 三件是成本/稳定性底线 —— E.3 "API 成本 10x 绝不能省"、E.4 断路器"防开发期被限流爆"、E.10.5 循环 runaway guard；无依赖，无理由可关。(2) env-gated 三件依赖平台资源（Langfuse 实例 / cache 后端 / redactor），未注入即静默跳过，与 `ToolEnv`（Mini-ADR E-14）同一注入模式，保持一致。(3) manifest 字段（`PolicySpec.pii` / `context_compression` 等）M0 只用于 `DynamicContextMiddleware` 的 `max_turns/max_tokens` 调参；PII/cache 的 manifest 级开关推 M1。
- **装配**：`build_middleware_chains(spec, *, env) -> MiddlewareChains` 产出 4 个 anchor 的 `MiddlewareChain`（某 anchor 无中间件则为 `None`，保留 graph 的 no-chain 快路径）。`build_agent` 把 3 个 graph chain 传 `build_react_graph`，`around_llm_call` chain 经 `build_llm_router` 传 `LLMRouter.around_llm_chain`（Mini-ADR E-13）。本 PR 交付装配器 + 接缝；control-plane 注入 `MiddlewareEnv` 真实后端是单列 follow-up —— 在此之前传空 `MiddlewareEnv()`，三个 always-on 中间件已生效。

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

**实现修正（E.14）**：A.2 落地的 `stream_bridge` 已经定义了 `StreamEvent`（`runtime/stream_bridge/base.py`），E.14 直接复用，**不另造**。实际形状是 frozen dataclass，比下面草图更简（event 名是开放字符串而非 Literal —— LangGraph `astream` 的 stream_mode 名直接当 SSE event 名，worker 不硬编码枚举）：

```python
# 实际：helix_agent.runtime.stream_bridge.base.StreamEvent
@dataclass(frozen=True)
class StreamEvent:
    id: str       # 单调递增（"{ts_ms}-{seq}"），SSE id: 字段 / Last-Event-ID 重连
    event: str    # SSE event 名 — "values" / "updates" / "messages" / "error" / "end" ...
    data: Any     # JSON-serialisable payload
# 另有 HEARTBEAT_SENTINEL / END_SENTINEL 两个哨兵常量
```

下面的草图（`event_id: int` + `type: Literal[...]`）是 E.0 设计期的设想，**已被 A.2 的实际实现取代**，保留仅作历史对照：

```python
# 草图（已废，见上）
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
| 27 | SSE 事件单调递增 + 顺序 | E.14 | integration | scripted graph 跑 → `run_agent` publish 的 event_id 单调；`sse_consumer` yield 的 SSE frame 顺序符合 ReAct 状态机；末尾恰一个 `end` |
| 28 | SSE backpressure drop-oldest | E.14 | unit | `StreamBridge` buffer=8，publish 20 条；慢订阅者从头 subscribe → 收到尾部保留段 + `subscriber_fell_behind` warning（Mini-ADR E-8：M0 drop-oldest 不 cancel）|
| 29 | SSE heartbeat | E.14 | integration | `heartbeat_interval` 缩到 0.1s + 静置 worker → `sse_consumer` 至少 yield 1 个 `: heartbeat` 注释帧 |
| 28b | SSE client 断开 → cancel run | E.14 | integration | `request.is_disconnected()` 返回 True → `sse_consumer` finally 段调 `run_manager.cancel`，worker task 收 `CancelledError` 终止 |
| 30 | cancellation in-flight LLM | E.15 | integration | LLM mock sleep 5s → token.cancel() → run 在 ≤1.5s 内抛 `RunCancelledError`（LLM 未跑完）|
| 31 | cancellation tool 中 | E.15 | integration | tool mock sleep 5s → tool 启动后 cancel → `run_cancellable` 中断 tool task + 抛 `RunCancelledError`（取消不被吞成 ToolMessage）|
| 32 | cancellation 重启不传染 | E.15 | unit | 一次 run cancel 后开启新 run → 新 run cancellation_token 默认未取消 |
| 33 | manifest schema 兼容 | E.* | unit | manifest 无 `fallback` 字段 → primary-only 正常工作；无 `cache` 字段 → 默认启用 |
| 34 | web_search 输出截断 | E.7 | unit | mock Tavily 返回单条 8000 字符 → `ToolResult.content` 4096 字符 + `meta.truncated=true` |
| 35 | HTTP body 截断 | E.8 | unit | mock 30k chars JSON 响应 → 截到 20k（尾部截）+ meta 标记；status code 保留 |
| 36 | MCP 结果中间截 | E.9 | unit | mock 100k chars 返回 → 截到 20k（中间截：5k 头 + 占位符 + 5k 尾）+ meta 标记 |
| 37 | LoopDetection 触发 | E.10.5 | unit | mock LLM 连发 3 次 `read_file("/etc/passwd")` → 第 3 次后 AIMessage 的 `tool_calls` 被清 + `<system-reminder>` HumanMessage 注入 |
| 38 | LoopDetection 不误报 | E.10.5 | unit | 3 次不同工具 / 3 次同工具不同 args / 中间夹 1 个不同 tool_call → 均不触发 |
| 39 | LoopDetection args normalize | E.10.5 | unit | `{a:1, b:2}` vs `{b:2, a:1}` 判同；`{path: "/etc"}` vs `{path: "/etc "}` 判同 |
| 40 | tool 异常转 ToolMessage | E.6 | unit + integration | mock Tavily raise `httpx.ConnectError` → tools 节点不向上抛；messages 末尾追加 `ToolMessage(content="[tool error] ConnectError: ...", tool_call_id=t1)`；step_count +1；audit `TOOL_ERROR` 行 |
| 41 | resume sanitize dangling tool_calls | E.15 | integration | checkpoint 内 messages: `[Human, AIMessage(tool_calls=[T1, T2])]`（无 ToolMessage）→ GraphRunner.run resume → 自动注入 2 条 `ToolMessage(content="[cancelled before dispatch]", tool_call_id=T1/T2)` → LLM 下一轮正常继续 |
| 42 | middleware chain anchor 触发顺序 | E.12.5 | integration | mock LLM + 6 个 spying 中间件全部注册 → 跑 1 个 ReAct step（agent → tools → agent）→ 观察 anchor 触发序列：`before_llm_call → around_llm_call (LLM call) → after_llm_call → before_tool_dispatch → before_llm_call → around_llm_call → after_llm_call`；每个中间件被调用次数符合预期 |
| 43 | around_llm_call per-provider 触发 | E.12.5 | integration | LLMRouter primary + fallback；mock primary 抛 LLMServerError；chain 注册 spying 中间件 → 验证 chain.invoke("around_llm_call") **被调用 2 次**，第 1 次 `provider_key=primary`、第 2 次 `provider_key=fallback`（per-key 隔离，给 E.4 断路器 per-key 计数留口子）|
| 44 | chain.invoke 异常透出 router | E.12.5 | unit | mock 中间件在 `around_llm_call` 抛 `LLMClientError` → router 不 fallback、直接 raise（4xx 短路语义在 chain 包裹后保持）|

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
| ReAct 无限循环 | token 烧光 | `max_steps=20` 兜底 + **`loop_detection_middleware` (E.10.5)** 检测 N=3 同 tool_call 早期 abort（清 tool_calls + 注入 system-reminder）+ audit `RUN_FAILED` |
| 单次 tool 输出过大（HTTP 大响应 / MCP 大文件 / sandbox bash dump）→ 后续 LLM 调用爆 token budget | 1MB 工具返回直接打爆 8000 token 预算 | 每工具内置 truncate 上限（web 4k / HTTP 20k / MCP 20k 中间截 / sandbox 20k-50k 走 F.4）+ `meta.truncated=true` 让 LLM 知道可换 args 重查或读分段 |
| tool 抛未捕获异常 → run 标 FAILED / messages 不合法 | dogfood 期连接抖动 / 第三方 SDK bug 直接 kill run | E.6 内置 tool error wrapper：任何 tool 异常 → `ToolMessage(content="[tool error] ...", tool_call_id=...)` 注入 messages → LLM 下一轮自己 reason 出 retry / 换 args / final answer；不向上抛 |
| cancel + resume 后 messages 不合法（orphan tool_calls）→ LLM API 报 invalid_request | 罕见但真实：cancel 打断 [LLM 完 → tool 派遣] ~100ms 窗口 + 用户尝试 resume | E.15 GraphRunner.run resume 路径 sanitize：scan messages，缺失 ToolMessage 注入 `[cancelled before dispatch]` placeholder（per tool_call_id） |
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

E.6  feat(e-6): ReAct mode + AgentState + tool error wrapper
     - state.py 完善 + graph_builder/builder.py
     - max_steps 守门
     - tools 节点 dispatch 统一 try/except → ToolMessage(error) 注入（不向上抛）
     - integration：mock LLM 跑 1-step / 3-step / max-step 三种 case
     - integration：mock tool raise → ToolMessage 注入 → LLM 下一轮继续

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

E.10.5 feat(e-10-5): loop_detection_middleware
       - packages/helix-runtime/.../middleware/loop_detection.py
       - normalize args + sha256 fingerprint helper（~40 LOC）
       - clone_ai_message_with_tool_calls helper（重写同 ID AIMessage）
       - 注册到 after_llm_call anchor
       - unit tests：触发 / 不误报 / args normalize
       - tool output truncation 不另起 PR（走 E.7/E.8/E.9 各自 PR 内含 truncate impl + unit）

E.11 feat(e-11): LLMRouter + provider fallback chain
     - llm/router.py + llm/providers/{anthropic,openai}.py
     - 4xx / 5xx 分类
     - manifest fallback 字段
     - integration：mock provider 失败切 fallback

E.12 feat(e-12): provider 层 token bucket 限流
     - llm/rate_limiter.py（aiolimiter wrapper per key）
     - 与 LLMRouter 集成
     - integration：rate_limit_rpm=2 + 5 并发 → 总时长 ≥ 60s

E.12.5 feat(e-12-5): middleware chain wiring — agent_node + tools_node + LLMRouter
       - LLMRouter 加 chain: MiddlewareChain | None = None 参数；router 内部 fallback 循环每次 provider 调用单独包 chain.invoke("around_llm_call")，ctx.payload 含 provider_key（Mini-ADR E-13）
       - graph_builder.agent_node 串 before_llm_call → around_llm_call (router) → after_llm_call 三个 anchor
       - graph_builder.tools_node 串 before_tool_dispatch（per tool_call）
       - MiddlewareContext.payload 字段约定固化：messages, tools, provider_key, response, tool_name, tool_args
       - integration：mock LLM + 6 个 spying 中间件全部注册 → 验证 anchor 序列 + per-provider around_llm_call + LLMClientError 短路 fallback（测试矩阵 #42-#44）
       - 验收：6 个中间件（E.3 dynamic_context / E.4 llm_error_handling / E.5 langfuse + pii_redact / E.10 sandbox_audit / E.10.5 loop_detection）首次端到端"真跑"

E.13 feat(e-13): LLM response cache（Redis）
     - llm/cache.py + 注册到 anchor before/after_llm_call
     - 绕过条件（temperature, stream, tool_calls）
     - per-tenant 命名空间
     - audit + metric

E.14 feat(e-14): SSE streaming + backpressure（in-process 单体）
     - services/orchestrator/src/orchestrator/sse.py：run_agent worker + sse_consumer
     - worker graph 注入式（graph.astream → StreamBridge.publish）
     - 与 stream_bridge（A.2）+ RunManager（A.2）配合
     - heartbeat（StreamBridge HEARTBEAT_SENTINEL）+ client 断开 → run_manager.cancel
     - backpressure：drop-oldest（Mini-ADR E-8 修正，不 cancel-on-full）
     - control-plane runs.py 切掉 B.7 fake stream，用 RunManager + worker + sse_consumer
     - 测试用 scripted graph；manifest→graph agent factory 不在本 PR
     - NOT：独立 orchestrator 服务 / gRPC（in-process，control-plane import orchestrator 库）

（后续）feat: agent factory — manifest（AgentSpec）→ 编译好的 ReAct graph
     - ModelSpec → providers → LLMRouter；tools → ToolRegistry；middleware chains 装配
     - build_react_graph + GraphRunner.compile 串起来
     - control-plane 启动时按 agent 注册；E.14 的 worker 消费它产出的 graph

E.15 feat(e-15): cancellation 全链路传播 + resume sanitize
     - runtime/cancellation.py（CancellationToken + run_cancellable，纯 asyncio 竞速）
     - GraphRunner 节点入口 check
     - LLM / tool 调用包 scope
     - control-plane POST /runs/{id}/cancel 联通 orchestrator
     - GraphRunner.run resume 路径：scan checkpoint messages，orphan tool_calls 注入 placeholder ToolMessage
     - integration：≤200ms surface
     - integration：cancel + resume → messages 合法 + LLM 继续
```

**预期总 PR 数**：1 设计 + 15 + 1 (E.10.5) + 1 (E.12.5) + 1 (agent factory，E.14 拆出) + 3 (设计补全 #62 / E.12.5 doc / E.14 doc) = 22 PR；累计 ~8-10 周（含 review / CI 重跑）。E.10.5 是对照 deer-flow 上下文管理分析后补的 compaction 防御（详见 § 9）；E.12.5 是 E.11 scope 控制下推后的 middleware chain wiring 自我补全；E.14 实施时对照 deer-flow 把"独立 orchestrator 服务 + gRPC"改成 in-process 单体，并拆出 agent factory 为后续 PR（设计补全 PR 与实施 PR 各自单列）。

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
| **E.10.5 loop_detection_middleware** | E.10.5 | 对照 deer-flow `loop_detection_middleware`；ITERATION-PLAN E 段原未列，作为 Stream E 设计细化新增；tool truncation 不另列条目（散在 E.7/E.8/E.9 PR 内执行）|
| E.11 LLM Provider Fallback Chain | E.11 | per provider key 断路器 + 4xx 不 fallback |
| E.12 提供商层限流 | E.12 | P0 #27 第 3 层；与 E.11 一起 |
| **E.12.5 middleware chain wiring** | E.12.5 | E.11 PR scope 控制下推后的 wiring；ITERATION-PLAN E 段原未列，作为 Stream E 设计细化新增；落地后 6 个已实现但未激活的中间件首次端到端"真跑" |
| E.13 LLM response cache | E.13 | P0 #28；Redis；per-tenant 命名空间 |
| E.14 SSE 流式输出 + backpressure | E.14 | in-process 单体（非独立服务）；run_agent worker + sse_consumer + RunManager；drop-oldest backpressure（Mini-ADR E-8 修正）|
| agent factory（manifest→graph 装配）| E.14 后续独立 PR | E.14 worker graph 注入式所致；manifest→编译 graph 的装配单列；ITERATION-PLAN E 段原未列 |
| E.15 请求取消的 engine 节点传播 | E.15 | P0 #25 第 2 段；协作式（raise_if_cancelled + run_cancellable 竞速）+ resume sanitize |

完成后 Stream E 17/17（含 E.10.5 + E.12.5）+ D.2 anchor 注册完成、24 P0 中 #15 / #25 / #27 / #28 全部勾选。agent factory 作为 E.14 拆分出的后续 PR 单独跟踪。
