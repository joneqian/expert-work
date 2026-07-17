# Token SSE 帧 + 流式脱敏 — 设计文档

> 端到端 LLM token 流式 epic 的**子项目 2**(共 3 个)。子项目 1 = Agent 防御守卫 UI(已合 #998);子项目 3 = playground 打字机(前端,后续)。见 `llm-token-streaming-epic` / `agent-defenses-ui` memory。

**Goal:** 把 LLM router 内部已在流式消费的 token delta(P1/P1' 后已组装成完整 AIMessage)**同时**作为细粒度 `token` SSE 帧暴露给外部 API / playground,附流式脱敏,让消费者能做打字机式实时渲染。

**Architecture:** 三接缝加一条可选旁路。router 增加可选 `on_delta` 回调(保持 LangGraph 无关);graph 节点(唯一同时握有 run_id/step/租户/守卫旗标的地方)建"脱敏+打标+门控"闭包传入,经 LangGraph custom 流写出;`run_agent` 把 custom 帧作为新 `token` 事件发到 SSE bridge。token 帧是 **provisional 预览**,权威 `updates` 帧(全守卫)仍是最终真相。

**Tech Stack:** Python;既有 LangGraph astream / StreamBridge / SSE(`format_sse`)管道;既有守卫函数(`expert_work.common.dlp.scan_and_redact` / `expert_work.common.output_screen.screen_output`);P1/P1' 的 `LLMDelta` / `_drive_stream` 流式件。

## Global Constraints

- **对外契约(现有事件)零破坏。** 现有 `updates` / `metadata` / `error` / `end` 等帧不变;新增仅 `token` 一种事件。非流式路径(queue / cache-hit / structured / judge-on / 非流式 provider)一律**不**发 token 帧,行为与今天完全一致。
- **router 保持 LangGraph 无关。** router 只多一个可选 `on_delta` 回调;不得 import langgraph、不得直接调 `get_stream_writer`。
- **信任模型(定论,不可动摇):** token 帧 = provisional 预览;权威 `updates` 帧(节点跑完 screen/judge/DLP 于整条消息)是最终真相。流式脱敏是**纵深防御**,不是唯一守卫。
- **门控:** 仅当 provider 实际流式(有 delta)**且** `output_judge != "block"` **且** LangGraph custom writer 可得时才发 token 帧。judge 是整条消息的 LLM 守卫,per-chunk 跑 = N× 成本 → judge-on agent 回退现状(只 step 级 `updates`)。screen/DLP 是 regex,骑 buffered-release,**不**门控。节点实现只显式判 judge 旗标 + writer 是否 None;provider 流式与否由"sink 是否被调"自然处理(§5)。
- **仅 content 频道。** 只发 `channel:"content"`(答案正文);`reasoning` / `tool_args` 频道推迟(各需独立脱敏方案,随子项目 3 消费者一起设计)。
- **token 帧仅 live,不持久化。** 不写 `RunEventStore`;重连/replay 只回放持久化的 `updates`。
- 测试:`cd services/orchestrator && export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock && uv run python -m pytest`;终门跑 CI-scope mypy(`uv run mypy services/orchestrator/src/orchestrator/llm services/orchestrator/src/orchestrator/graph_builder ...`)+ ruff check + ruff format。

---

## 1. 背景(由来)

P1(#996)/ P1'(#997)让 router 内部对 OpenAI-wire 与 Anthropic provider **真流式**消费 token delta,但对外仍只发 LangGraph `updates`(每步一帧)—— token 粒度停在 router 里、组装完就丢。子项目 2 把这些内部 delta 旁路出来成 `token` SSE 帧。

流式脱敏是难点:所有输出守卫(`output_screen` / `output_judge` / `output_dlp`)跑在 graph 节点内**组装完的 AIMessage** 上(`builder.py:863-909`),token 帧从 router 发出会绕过它们。定论方案(brainstorm + web research:NeMo Guardrails / Guardrails AI / RavenGate 均如此):**buffered-release** —— 攒 buffer、跨 delta 边界 hold、守卫过了才 release;便宜 regex 守卫(screen/DLP)骑得上、LLM judge 骑不上(门控关流式)。

## 2. 现状锚点(已探明,实现照此)

**Seam 1 — router delta 循环** `services/orchestrator/src/orchestrator/llm/router.py`
- `_drive_stream(self, handle, stream, assembler) -> AIMessage`(:612):delta 唯一消费点是两处 `assembler.add(delta)`(:646 Phase-1 首 delta、:668 Phase-2 循环)。
- `_next_delta(it, timeout)`(:218):每个 delta 的单一 choke-point。
- `_invoke_provider`(:582):流式判定在 :599 `if output_schema is None and supports_streaming(handle.provider)` → `_drive_stream`,否则非流式 `_complete`。
- `LLMRouter.__call__(self, *, messages, tools, output_schema=None) -> AIMessage`(:349)。`LLMRouter` 是无状态 `@dataclass`,**今天无 run_id/step/callback 流入**。
- `LLMCaller` Protocol `caller.py:22`(`__call__` :30)—— **唯一实现是 `LLMRouter`**;`escalated_llm_caller`(builder.py:373 typed `LLMCaller`)是同类另一实例。故 `on_delta` 只需上 Protocol + `LLMRouter`,两 caller 全覆盖。`RateLimitedProvider` 是 provider 包装(router 内),不碰。
- `LLMDelta`(`llm/providers/_streaming.py:39`,frozen):`content:str` / `reasoning:str` / `tool_calls:tuple[ToolCallChunk,...]` / `finish_reason` / `usage` / `model`;`has_progress` = `bool(content or reasoning or tool_calls)`。content 频道 = `.content`。

**Seam 2 — 节点 → router 调用点** `services/orchestrator/src/orchestrator/graph_builder/builder.py`
- 调用点 :795-798:`response = await token.run_cancellable(active_caller(messages=messages, tools=tools))`(`output_schema` 故意不传,schema 在后续 RT-1 块另处理)。
- `agent_node(state, config)`(:494,async,LangGraph 内)。就近可得:`step_count`(:507)、`configurable`(:716,含 `run_id`)、`tenant_id`(:717)/`user_id`(:720)、`output_screen`/`output_judge`/`output_dlp`(`build_agent_graph` 闭包参数 :430/:436/:449)、`active_caller`(:759)。
- `get_stream_writer` / `StreamWriter` **全 orchestrator 零使用**(需新引 `from langgraph.config import get_stream_writer`,且仅当 astream 带 `"custom"` mode 才有效)。
- step 索引唯一来源 = `step_count`(:507);节点名静态 `"agent"`。

**Seam 3 — run_agent SSE 生产者** `services/orchestrator/src/orchestrator/sse.py`
- `DEFAULT_STREAM_MODE = "updates"`(:160);`run_agent(..., stream_mode: str = ...)`(:269,类型是 `str`,需拓宽)。`StreamableGraph.astream` Protocol 已允许 `str | list[str] | None`(:174-180)。
- astream 调用 :421 `async for chunk in graph.astream(graph_input, effective_config, stream_mode=stream_mode)`;当前 dispatch :433-448 **不区分 chunk 类型**,`bridge.publish(run_id, stream_mode, jsonable_chunk)`(:441)+ `_persist_event`(:442-448)。stream_mode 变 list 后 LangGraph 吐 `(mode, chunk)` 元组 → **此处需加元组解包分支**。
- `bridge.publish(self, run_id, event, data)`(`stream_bridge/base.py:51`);`StreamEvent{id,event,data}`(:26-40),`id` 由 bridge 分配(`_next_id` = `ts-seq`)。`format_sse`(:1070)行 :1079 `event: {event}` —— 新 `token` 事件 = 直接 `bridge.publish(run_id, "token", frame)`,**零信封改动**。
- `run_id` 已在 run_agent 绑 contextvar(:303 `set_current_run_id`)—— router 在同 async 任务树,`get_current_run_id()` 从 `_drive_stream` 可用;**step 不在 contextvar**。

## 3. 架构:on_delta 旁路(端到端)

```
provider 流 → router._drive_stream 循环
  ├─ assembler.add(delta)          [不变,组装权威 AIMessage]
  └─ if on_delta: on_delta(delta)  [新,仅流式路径,同步调用]
        ↓ 节点 sink 闭包(见 §5)
      safe = redactor.feed(delta.content)
      if safe: writer({"step": step_count, "channel": "content", "text": safe})   [LangGraph custom writer]
  → router 返回组装好的 AIMessage
节点(§5):
  response = await active_caller(messages, tools, on_delta=sink)   [sink=None 当门控关]
  if sink: tail = redactor.flush(); if tail: writer({...tail})
  → 现有 screen/judge/DLP 守卫跑于 response(不变)→ 节点返回 → LangGraph 发 "updates"(权威)

run_agent(§6) astream(stream_mode=["updates","custom"]):
  收 (mode, chunk):
    mode=="custom":  bridge.publish(run_id, "token", chunk)          [仅 live,跳 _persist_event]
    else:            现有 updates 路径(publish + persist,不变)
```

## 4. StreamingRedactor(唯一难点,新组件)

新模块 `services/orchestrator/src/orchestrator/graph_builder/streaming_redact.py`。**buffered-release**:攒全量 buffer、hold 尾部 lookback 窗口、只 release 稳定前缀;复用**现有** `scan_and_redact`(DLP)+ `screen_output`(screen),与非流式路径同款 regex = parity。

**状态:** `_buf`(全量原文累积)、`_emitted_len`(已发出的**脱敏后**字符数)、`_blocked`(screen 已触发)。构造带 `dlp: bool` / `screen: bool`(照 agent 的 `output_dlp`/`output_screen` 旗标,只跑启用的)。

**`feed(text) -> str`:**
1. `_buf += text`;若 `_blocked` 或 `text` 空 → 返 `""`。
2. 若 `screen` 启用且 `screen_output(_buf).blocked` → 置 `_blocked`,返 `""`(停发;权威帧走 REFUSAL)。
3. `boundary = max(0, len(_buf) - HOLD_CHARS)`(hold 尾部 `HOLD_CHARS` 字符,保护正在形成的 pattern)。
4. `red = scan_and_redact(_buf[:boundary]).redacted`(dlp 启用)`else _buf[:boundary]`。
5. `out = red[_emitted_len:]`;`_emitted_len = len(red)`;返 `out`。

**`flush() -> str`**(节点在 router 返回后调,发尾部):`_blocked` → `""`;screen 启用且整 `_buf` blocked → `""`;否则对整 `_buf` 跑 dlp、返 `red[_emitted_len:]`、更新 `_emitted_len`。

**正确性不变量:** `red[:_emitted_len]` 稳定(前缀单调)—— 因 hold `HOLD_CHARS` 保证无 pattern 跨 boundary,且 boundary 只增。`HOLD_CHARS` 常量(建议 **64**)≥ 所有定长 DLP pattern(卡号 19 / 身份证 18 / 手机 11 / email 现实长度)。**content 频道守卫只有 DLP(email/phone/id/card)+ screen** —— 除 email 外全定长,email 现实长度 ≤ 64 → 窗口足够。病态超长敏感串(> HOLD_CHARS,如超长 JWT;注:secret 脱敏本就不在输出路径—见 [[agent-defenses-ui]])= 残留边界,由**权威帧全守卫兜底**(provisional 契约)。

**screen(BLOCK 型)与 buffered-release:** screen 触发 → 停发已 release 的安全前缀(它们逐 chunk 过了 screen=非泄漏部分),risky 尾部永不 release,权威帧整条 REFUSAL,client 弃 provisional 换 REFUSAL。因 screen 默认 `block`(开)覆盖几乎全 agent,故 screen **必须**走 buffered-release 而非门控(否则等于关掉全员流式)。

## 5. 节点接线 + 门控(Seam 2)

`agent_node` 内,调 router 前:
- **门控判定(节点只查两件事):**
  1. `output_judge != "block"`(judge-on → 不流)。
  2. `writer = get_stream_writer()` 拿得到(非 None)—— 仅当 graph 以带 `"custom"` 的 stream_mode 执行时才有(run_agent 总带,见 §6);若某执行路径(如 queue 后台若不走 run_agent)没带 → `writer is None` → 不流。**provider 是否真流式不用节点判**:sink 只在 router 走流式路径(有 delta)时被调,非流式 provider / structured / cache-hit 下 sink 自然一次都不被调 → 无帧。故门控 = judge-off ∧ writer 可得。
- 若门控通过:`redactor = StreamingRedactor(dlp=output_dlp, screen=output_screen)`;`sink = lambda delta: _emit(delta, redactor, writer, step_count)`(`_emit` 取 `delta.content`、`feed`、非空则 `writer({"step":step_count,"channel":"content","text":safe})`)。否则 `sink=None`。
- `response = await token.run_cancellable(active_caller(messages=messages, tools=tools, on_delta=sink))`。
- 若 `sink`:`tail = redactor.flush(); if tail: writer({"step":step_count,"channel":"content","text":tail})`。
- 其后现有 screen/judge/DLP 守卫于 `response` 不变。

`on_delta` 同步回调(LangGraph custom writer 是同步 callable),router 在 `assembler.add` 旁同步调用,不 await、不改 router 的 async 结构。

## 6. run_agent + SSE(Seam 3)

- `run_agent` 的 `stream_mode` 参数拓宽为 `str | list[str]`;流式调用处传 `["updates", "custom"]`(单一来源常量)。
- astream 循环(:421)加元组解包:list 模式下 chunk 是 `(mode, payload)`。`mode == "custom"` → `bridge.publish(run_id, "token", payload)`(**跳** `_persist_event` 与 `_duration_ms` 注入);否则走现有 updates 分支(`_to_jsonable` + duration 注入 + publish + persist,逐字不变)。
- 帧 body:`{"step": int, "channel": "content", "text": str}`。`run_id` / `seq` 由 SSE 信封承载(per-run 流身份 + bridge `id:` = `ts-seq`),body 不重复。
- 新事件名 `token`;`format_sse` 无需改。

## 7. 不受影响路径(逐条确认,行为同今天)

- **queue(`mode:queue`):** 返 202 JSON,无 SSE 消费者 → 无 token 帧。
- **cache-hit:** graph 在 `builder.py:788` agent-node 短路,不进 router → 无 delta → 无 token 帧;`updates`(缓存消息)照发。
- **structured output:** router `_invoke_provider` 对 `output_schema is not None` 走非流式 `_complete` → 无 delta → 无 token 帧。
- **judge-on agent:** 门控 `sink=None` → 无 token 帧,仅 `updates`(现状)。
- **非流式 provider:** 无 delta → 无 token 帧。

## 8. 外部 API 文档

现无用户向 SSE 事件参考文档(仅散在 STREAM design)。**新建**聚焦文档 `docs/api/streaming-events.md`(约一页),覆盖:
- `POST /v1/agents/{agent_code}/runs`(流式响应体 = SSE)+ `GET /v1/sessions/{thread_id}/runs/{run_id}/events` 的事件种类;至少详列新 `token` 事件。
- `token` 帧 body `{step, channel, text}` + **provisional 契约**:累积 token 作实时预览;该 step 的 `updates` 帧到达时**以它为权威并替换**累积的 token;**重连不回放 token**(只回放持久化的 `updates`)。
- 哪些 run 会发 token(流式 provider + judge-off);哪些不会(queue/structured/judge-on)。
- 不承诺 token 帧的持久性 / 顺序保证以外的语义。

## 9. 组件清单

**新增:**
- `graph_builder/streaming_redact.py` —— `StreamingRedactor`(§4)+ `HOLD_CHARS` 常量。

**改:**
- `llm/caller.py` —— `LLMCaller.__call__` 加 `on_delta: Callable[[LLMDelta], None] | None = None`。
- `llm/router.py` —— `LLMRouter.__call__` 加同参;穿到 `_invoke_provider` / `_drive_stream`;在 :646/:668 `assembler.add` 旁 `if on_delta: on_delta(delta)`。非流式路径 on_delta 不用。
- `graph_builder/builder.py` —— `agent_node` 建 sink+redactor+门控(§5);引 `get_stream_writer`;router 返回后 flush。
- `sse.py` —— `run_agent` stream_mode 拓 list + 元组解包分支 + `token` 事件(§6,跳持久化)。
- `docs/api/streaming-events.md` —— 新文档(§8)。

## 10. 测试

- **StreamingRedactor 单测**(纯逻辑,离线):跨 delta 卡号分片(`"4111 1111"`+`"1111 1111"` → hold 后整体 `[redacted]`,前缀不泄漏)、email 跨边界、screen 触发→后续 feed 全 `""`、flush 发尾部、无守卫透传、`_emitted_len` 前缀单调(多次 feed 拼接 = 一次性 redact 全量)、只启用 dlp / 只 screen / 都不启用。
- **router on_delta**:流式路径每 content delta 调 on_delta;非流式 / structured 路径不调(call-count 探针);on_delta=None 时行为与今天逐字节一致(回归)。
- **节点门控**:judge-off → sink 挂、writer 收帧;judge-on → sink=None、无帧、`updates` 照发。
- **run_agent 元组解包**:custom→`token` 事件且**不**进 `_persist_event`;updates→publish+persist 逐字不变;单 stream_mode(str)回归不破。
- **端到端**:流式 provider 走真 LLMRouter+真节点 → token 帧先于该 step `updates` 到达,累积 token 脱敏后 ⊆ 权威 content(或 screen-block 时权威=REFUSAL)。

## 11. 明确排除(Out of Scope)

- `reasoning` / `tool_args` 频道(各需独立脱敏,随子项目 3 消费者设计)。
- playground 打字机前端 / 双轨渲染(子项目 3)。
- token 帧持久化 / replay。
- judge-on agent 的流式(定论门控关)。
- 给输出路径新增 secret 脱敏(非流式路径没有,parity,见 [[agent-defenses-ui]])。

## 12. 参考锚点

见 §2(三接缝逐一 file:line)。守卫函数:`packages/expert-work-common/src/expert_work/common/dlp.py`(`scan_and_redact` / `DlpResult`)、`expert_work.common.output_screen`(`screen_output` / `REFUSAL_TEXT`)。相关 memory:[[llm-token-streaming-epic]]、[[agent-defenses-ui]](3 守卫事实 + secret 不在输出路径)、[[playground-history-debug-reconstruction]](run_event 持久化/replay)。
