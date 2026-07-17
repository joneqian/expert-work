# Token SSE 帧 + 流式脱敏 — 设计文档

> 端到端 LLM token 流式 epic 的**子项目 2**(共 3 个)。子项目 1 = Agent 防御守卫 UI(已合 #998);子项目 3 = playground 打字机(前端,后续)。见 `llm-token-streaming-epic` / `agent-defenses-ui` memory。

**Goal:** 把 LLM router 内部已在流式消费的 token delta(P1/P1' 后已组装成完整 AIMessage)**同时**作为细粒度 `token` SSE 帧暴露给外部 API / playground,附流式脱敏,让消费者能做打字机式实时渲染。

**Architecture:** router 增加可选 async `on_delta` 回调(保持 LangGraph 无关);`run_agent` 把一个"发 token 到 bridge"的 async sink 注入 `config["configurable"]`(**完全复用既有 `COMPACTION_SINK_KEY` 范式**);graph 节点(唯一同时握有 run_id/step/租户/守卫旗标)读该 sink、建"脱敏+打标+门控"闭包(`TokenSink`)传给 router 作 `on_delta`;每个安全 delta 经 sink `bridge.publish(run_id,"token",frame)` 直发(**不走 astream、不持久化**)。token 帧是 **provisional 预览**,权威 `updates` 帧(全守卫)仍是最终真相。

**Tech Stack:** Python;既有 LangGraph astream / StreamBridge / SSE(`format_sse`)管道;既有守卫函数(`expert_work.common.dlp.scan_and_redact` / `expert_work.common.output_screen.screen_output`);P1/P1' 的 `LLMDelta` / `_drive_stream` 流式件。

## Global Constraints

- **对外契约(现有事件)零破坏。** 现有 `updates` / `metadata` / `error` / `end` 等帧不变;新增仅 `token` 一种事件。非流式路径(queue / cache-hit / structured / judge-on / 非流式 provider)一律**不**发 token 帧,行为与今天完全一致。
- **router 保持 LangGraph 无关。** router 只多一个可选 `on_delta` 回调;不得 import langgraph、不得直接调 `get_stream_writer`。
- **信任模型(定论,不可动摇):** token 帧 = provisional 预览;权威 `updates` 帧(节点跑完 screen/judge/DLP 于整条消息)是最终真相。流式脱敏是**纵深防御**,不是唯一守卫。
- **门控:** 仅当 provider 实际流式(有 delta)**且** `output_judge != "block"` **且** run_agent 注入了 token sink(`TOKEN_SINK_KEY` 存在)时才发 token 帧。judge 是整条消息的 LLM 守卫,per-chunk 跑 = N× 成本 → judge-on agent 回退现状(只 step 级 `updates`)。screen/DLP 是 regex,骑 buffered-release,**不**门控。节点实现只显式判 judge 旗标 + token sink 是否存在;provider 流式与否由"on_delta 是否被调"自然处理(§5)。
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

**Seam 3 — run_agent 注入 token sink(复用 COMPACTION_SINK_KEY 范式)** `services/orchestrator/src/orchestrator/sse.py`
- **既有先例(直接照抄):** `sse.py:357` `async def _publish_compaction(payload): await bridge.publish(run_id, EventType.COMPACTION.value, payload)`;`:370` `effective_config["configurable"][COMPACTION_SINK_KEY] = _publish_compaction`;节点经 `compaction_sink_from_config(config)`(`graph_builder/_config.py:92`,读 `configurable.get(COMPACTION_SINK_KEY)`)取用并 `await sink(payload)` fire。`COMPACTION_SINK_KEY`(`_config.py:28`)。
- **token 走同一模式**:新增 `async def _publish_token(frame): await bridge.publish(run_id, "token", frame)`(**仅 bridge.publish,不 `_persist_event`** = live-only);注入 `effective_config["configurable"][TOKEN_SINK_KEY] = _publish_token`。**astream 循环(:421-448)完全不动**(updates 路径/stream_mode/持久化逐字不变);token 帧不走 astream,由节点 TokenSink 经注入的 sink 直发。
- `bridge.publish(self, run_id, event, data)`(`stream_bridge/base.py:51`);`StreamEvent{id,event,data}`(:26-40);`format_sse`(:1070)—— 新 `token` 事件 = `bridge.publish(run_id, "token", frame)`,**零信封改动**。
- 帧 body 由节点组装(§5),含 `step`(节点的 `step_count`);`run_id`/`seq` 由 SSE 信封承载。

## 3. 架构:on_delta + 注入 sink 旁路(端到端)

```
run_agent(§6): effective_config["configurable"][TOKEN_SINK_KEY] = _publish_token
                (_publish_token(frame) = await bridge.publish(run_id,"token",frame),不持久化)

provider 流 → router._drive_stream 循环
  ├─ assembler.add(delta)                 [不变,组装权威 AIMessage]
  └─ if sink: await sink(delta)           [新,async on_delta,仅流式路径]
        ↓ 节点 TokenSink(见 §5)
      safe = redactor.feed(delta.content)
      if safe: await token_publish({"step": step_count, "channel": "content", "text": safe})
                                          [token_publish = 注入的 _publish_token → bridge.publish("token")]
  → router 返回组装好的 AIMessage
节点(§5):
  token_publish = token_sink_from_config(config)
  sink = TokenSink(step_count, token_publish, dlp, screen) 当门控开,否则 None
  response = await active_caller(messages, tools, on_delta=sink)
  if sink: await sink.flush()             [发 buffered 尾部]
  → 现有 screen/judge/DLP 守卫跑于 response(不变)→ 节点返回 → LangGraph 发 "updates"(权威)

astream 循环不动:updates 帧照旧 publish + persist。token 帧不经 astream,由 TokenSink 直发 bridge。
```

## 4. StreamingRedactor(唯一难点,新组件)

新模块 `services/orchestrator/src/orchestrator/graph_builder/streaming_redact.py`。**buffered-release**:攒全量 buffer、hold 尾部 lookback 窗口、只 release 稳定前缀;复用**现有** `scan_and_redact`(DLP)+ `screen_output`(screen),与非流式路径同款 regex = parity。

**状态:** `_buf`(全量原文累积)、`_emitted_len`(已发出的**脱敏后**字符数)、`_blocked`(screen 已触发)。构造带 `dlp: bool` / `screen: bool`(照 agent 的 `output_dlp`/`output_screen` 旗标,只跑启用的)。

**`feed(text) -> str`:**
1. `_buf += text`;若 `_blocked` 或 `text` 空 → 返 `""`。
2. 若 `screen` 启用且 `screen_output(_buf).blocked` → 置 `_blocked`,返 `""`(停发;权威帧走 REFUSAL)。
3. **redact 整个 buffer**:`red = scan_and_redact(_buf).redacted`(dlp 启用)`else _buf`。
4. `boundary = max(_emitted_len, len(red) - HOLD_CHARS)`(从**脱敏后**文本尾部 hold `HOLD_CHARS`;`max(_emitted_len, …)` 防 pattern 完成使 `red` 变短时 boundary 回退)。
5. `out = red[_emitted_len:boundary]`;`_emitted_len = boundary`;返 `out`。

**`flush() -> str`**(节点在 router 返回后调,发尾部):`_blocked` → `""`;screen 启用且整 `_buf` blocked → 置 `_blocked` 返 `""`;否则 `red = scan_and_redact(_buf).redacted`(dlp 启用)、`out = red[_emitted_len:]`、`_emitted_len = len(red)`、返 `out`。

**为何 redact 整 buffer 而非 `_buf[:boundary]`:** 若只 redact `_buf[:boundary]`,一个**起于 released 区、尾在 held 区**的 pattern(如卡号)在 `_buf[:boundary]` 里是残缺的 → 不匹配 → 头部裸泄漏。redact 整 buffer 则:完整 pattern 被整条 redact;仍在形成的 pattern 恒落在 held 尾(因未完成 pattern 长度 < 最长 pattern ≤ `HOLD_CHARS`,故其起点 > `len(red)-HOLD_CHARS` = boundary)→ 不释放。

**正确性不变量:** `red[:_emitted_len]` 稳定(前缀单调)—— pattern 完成只改 held 尾区(> boundary)的内容,已释放前缀不变。`HOLD_CHARS` 常量(**64**)≥ 所有定长 DLP pattern(卡号 19 / 身份证 18 / 手机 11)+ email 现实长度。**content 频道守卫只有 DLP(email/phone/id/card)+ screen**;除 email 外全定长,email ≤ 64 → 窗口足够。病态超长敏感串(> HOLD_CHARS;注:secret 脱敏本就不在输出路径—见 [[agent-defenses-ui]])= 残留边界,由**权威帧全守卫兜底**(provisional 契约)。**特性**:答案 < HOLD_CHARS 时 boundary 恒 0,全部在 `flush()` 一次发出(短答案无渐进打字机,但帧仍到达;长答案渐进流)。

**screen(BLOCK 型)与 buffered-release:** screen 触发 → 停发已 release 的安全前缀(它们逐 chunk 过了 screen=非泄漏部分),risky 尾部永不 release,权威帧整条 REFUSAL,client 弃 provisional 换 REFUSAL。因 screen 默认 `block`(开)覆盖几乎全 agent,故 screen **必须**走 buffered-release 而非门控(否则等于关掉全员流式)。

## 5. 节点接线 + 门控(Seam 2)

`agent_node` 内,调 router 前(cache-hit `else` 分支,`builder.py:790-798`):
- **门控判定(节点只查两件事):**
  1. `output_judge is not None`(judge-on → 不流;`output_judge` 在节点是 `OutputJudge | None`)。
  2. `token_publish = token_sink_from_config(config)` 存在(非 None)—— run_agent 注入了(见 §6);若某执行路径没注入 → None → 不流。**provider 是否真流式不用节点判**:on_delta 只在 router 走流式路径(有 delta)时被 `await`,非流式 provider / structured / cache-hit 下一次都不被调 → 无帧。故门控 = judge-off ∧ token_publish 存在。
- 由 `make_token_sink(step=step_count, token_publish=…, dlp=output_dlp, screen=output_screen, judge_enabled=output_judge is not None)` 建 `TokenSink | None`(该工厂封装两条门控:judge_enabled 或 token_publish 为 None → 返 None)。
- `response = await token.run_cancellable(active_caller(messages=messages, tools=tools, on_delta=sink))`。
- 若 `sink is not None`:`await sink.flush()`(发 buffered 尾部)。
- 其后现有 screen/judge/DLP 守卫于 `response` 不变。

`TokenSink.__call__` 是 **async**(要 `await token_publish(...)`),router 在 `assembler.add` 旁 `await sink(delta)`;开销可忽略(`bridge.publish` = 内存 append)。

## 6. run_agent 注入 token sink(Seam 3,复用 COMPACTION_SINK_KEY 范式)

- `graph_builder/_config.py`:加 `TOKEN_SINK_KEY = "token_event_sink"`(镜像 `COMPACTION_SINK_KEY` :28)+ `token_sink_from_config(config) -> TokenEventSink | None`(镜像 `compaction_sink_from_config` :92,读 `configurable.get(TOKEN_SINK_KEY)`)+ 类型别名 `TokenEventSink = Callable[[dict[str, Any]], Awaitable[None]]`。
- `sse.py`:加 `async def _publish_token(frame): await bridge.publish(run_id, "token", frame)`(**仅 bridge.publish,无 `_persist_event`** = live-only);在已有 configurable 注入处(`:370` `_publish_compaction` 旁)加 `effective_config["configurable"][TOKEN_SINK_KEY] = _publish_token`。
- **astream 循环(:421-448)零改动** —— updates/metadata/retry/approval/error/end 全走原路,`stream_mode` 保持 `str`,持久化不变。token 帧不经 astream,由节点 TokenSink 经注入的 `_publish_token` 直发 bridge。
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
- `graph_builder/streaming_redact.py` —— `StreamingRedactor`(§4,纯逻辑)+ `HOLD_CHARS` 常量 + `TokenSink`(async `__call__`/`flush`,持 redactor + step + token_publish)+ `make_token_sink(...) -> TokenSink | None`(封装 judge/sink 存在两条门控)。
- `docs/api/streaming-events.md` —— 新文档(§8)。

**改:**
- `llm/caller.py` —— `LLMCaller.__call__` 加 `on_delta: Callable[[LLMDelta], Awaitable[None]] | None = None`(**async 回调**)。
- `llm/router.py` —— `LLMRouter.__call__` 加同参。**内部经 router 私有 contextvar 携带**(`__call__` 入口 set / finally reset,`_drive_stream` read)—— 调用链 6 层深(`__call__→_call_one→_attempt_call→_invoke_once→_invoke_provider→_drive_stream`),contextvar 避免穿 6 个私有签名,task-local 对并发 run 安全;public `on_delta` 不变、router 仍不 import langgraph。`_drive_stream` 在 :646/:668 两个 `assembler.add` 旁 `if sink is not None: await sink(delta)`。**去重安全**:content 只在首个 progress delta 后经 buffered-release 发出(Phase-1 只有空 content 的非进度 delta,`feed("")` 不发),Phase-2 硬错终态不回退 → fallover 只发生在无 content 已发时,换 provider 重流不产生重复 token。非流式路径 sink 一次不被 await。
- `graph_builder/_config.py` —— `TOKEN_SINK_KEY` + `token_sink_from_config` + `TokenEventSink` 类型(§6,镜像 COMPACTION_SINK_KEY)。
- `graph_builder/builder.py` —— `agent_node` 经 `make_token_sink` 建 sink+门控(§5);router 返回后 `await sink.flush()`。
- `sse.py` —— `_publish_token` + 注入 `TOKEN_SINK_KEY`(§6);**astream 循环不动**。

## 10. 测试

- **StreamingRedactor 单测**(纯逻辑,离线):跨 delta 卡号分片(`"4111 1111"`+`"1111 1111"` → hold 后整体 `[redacted]`,前缀不泄漏)、email 跨边界、screen 触发→后续 feed 全 `""`、flush 发尾部、无守卫透传、`_emitted_len` 前缀单调(多次 feed 拼接 = 一次性 redact 全量)、只启用 dlp / 只 screen / 都不启用。
- **router on_delta**(镜像 `test_llm_router_streaming.py` 的 `_StreamProvider`/`_handle` harness):流式路径每 delta `await` on_delta(含空 content 的 finish delta);structured 路径不调(call-count 探针);on_delta=None 默认时行为与今天逐字节一致(现有全套即回归)。
- **TokenSink / make_token_sink**(fake async publish 收帧):TokenSink 发 `{step,channel:"content",text}` 且文本脱敏;make_token_sink 门控——judge_enabled=True → None、token_publish=None → None、都满足 → TokenSink。
- **run_agent 注入 sink**(镜像 `test_sse_persistence.py:98` 的 compaction-sink 测试:fake graph 读 `configurable[TOKEN_SINK_KEY]` 并 fire):token 事件**发到 bridge 但不进 store**(断言 `store.list` 只有 metadata+updates);现有 sse 持久化测试不破(astream 未动)。
- **端到端**:上条 run_agent 测试即端到端覆盖(节点侧 sink fire → bridge token 事件 live-only)。router↔TokenSink 的字面接线由 TokenSink 单测 + router on_delta 单测 compositional 覆盖。

## 11. 明确排除(Out of Scope)

- `reasoning` / `tool_args` 频道(各需独立脱敏,随子项目 3 消费者设计)。
- playground 打字机前端 / 双轨渲染(子项目 3)。
- token 帧持久化 / replay。
- judge-on agent 的流式(定论门控关)。
- 给输出路径新增 secret 脱敏(非流式路径没有,parity,见 [[agent-defenses-ui]])。

## 12. 参考锚点

见 §2(三接缝逐一 file:line)。守卫函数:`packages/expert-work-common/src/expert_work/common/dlp.py`(`scan_and_redact` / `DlpResult{redacted,categories}`)、`expert_work.common.output_screen`(`screen_output(text)->OutputVerdict{blocked,categories}` / `REFUSAL_TEXT`;screen 触发例=`"sk-"+"a"*24`)。注入 sink 范式:`graph_builder/_config.py:28`(`COMPACTION_SINK_KEY`)/`:92`(`compaction_sink_from_config`)、`sse.py:357`(`_publish_compaction`)/`:370`(注入)。测试 harness:`tests/test_llm_router_streaming.py`(`_StreamProvider`/`_handle`)、`tests/test_sse_persistence.py:98`(compaction-sink fire 范式)。相关 memory:[[llm-token-streaming-epic]]、[[agent-defenses-ui]](3 守卫事实 + secret 不在输出路径)、[[playground-history-debug-reconstruction]](run_event 持久化/replay)。
