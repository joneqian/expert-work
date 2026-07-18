# glm `tool_stream` 修复(修 stream_idle_timeout 误砍大 tool-call)— 设计

**状态**:设计定案(2026-07-18)
**类型**:bugfix(LLM 请求路径,orchestrator provider 层)
**范围**:给 glm 的**流式**请求注入 `tool_stream: true`,让 glm 增量吐 tool-call 参数,消除组装大 tool-call 时的静默 gap。glm-only。

## 问题(根因,已实测坐实)

真实 run(thread `90ad00fe`,health-manger-agent,glm-5.2)在第 17 步"生成 PPT"时 `finish_reason: stream_idle_timeout`,只留一句开场白、没生成 PPT。

**根因(复现证实)**:glm **默认批量吐 tool-call 参数**——先流式吐 reasoning/content,然后转入内部组装 tool-call arg 时**彻底静默**,直到整段参数 composed 完才一次性一个 chunk 甩出。arg 越大静默越久。router 的 `idle_timeout_s=45`(首 token 后 delta 间隔)在这段静默里触发,把正在干活的模型砍了。

**实测对照**(glm-5.2,同一 OpenAI 端点,裸 httpx probe):
| | 无 `tool_stream` | 有 `tool_stream: true` |
|---|---|---|
| tool-arg 怎么发 | **1 个 chunk**,前置 **165s 静默** | **2014 个增量分片** |
| max_inter_chunk_gap | 165s | **4.1s** |
| idle-45 判决 | WOULD FIRE | would NOT fire |

`tool_stream` 是 glm/Z.AI **官方参数**(需 `stream=true`),专为增量流式 tool-call 参数设计。社区多方踩同坑并确认此解(vercel/ai #12949、sglang #11888、aarongxa Hermes-agent 帖)。

**"自动续期"没坏**:router 的 idle 每个进度 delta(content/reasoning/tool 片段)都重置——glm 组装期发的 delta = 0,续期没有输入。`delta_from_openai_chunk` 本就接 `tool_calls[].function.arguments` 分片(实测 2014 片全被解析),所以 `tool_stream` 开了之后**解析器零改**、每片自动重置 idle。

**换 wire 不解**(已排除):glm 的 Anthropic-compat 端点同样批量(`content_block_start(tool_use)` 在静默组装完之后才发,非早信号;`input_json_delta` 批量)。

## 多厂商范围(实测确认 glm 是唯一批量的)

| vendor | 默认 tool-arg 形状 | 需修? |
|---|---|---|
| **glm** | **BATCHED**(1 块,165s 静默) | **是** |
| deepseek | INCREMENTAL(686 片,gap 1.0s) | 否 |
| qwen | INCREMENTAL(273 片,gap 0.7s) | 否 |
| kimi | INCREMENTAL(802 片,gap 0.8s) | 否 |
| doubao | INCREMENTAL(749 片,gap 1.8s) | 否 |

deepseek/qwen/kimi/doubao 默认就增量流式 tool-arg(不需 `tool_stream`,且它们接受该参数不报错=no-op)。OpenAI/Anthropic 亦默认增量/走另一 wire。**所以修复 gate 给 glm-only**(按 YAGNI 不给其余家发未验证的参数)。

## 命门:`tool_stream` 只能进流式请求

`OpenAIProvider._prepare_request` **complete() 与 stream() 共用**(`extra_body: thinking_payload` 两路都带)。glm 的**结构化输出走非流式 `complete()` 路径**——`tool_stream` 需 `stream=true`,漏到 `complete()` 可能被 glm 拒(400)。故 `tool_stream` **必须 stream-only**,绝不能进 `complete()`。

## 设计(方案 A:stream-only provider 字段)

### provider 层(`services/orchestrator/src/orchestrator/llm/providers/openai.py`)
- `OpenAIProvider` 新增字段 `stream_extra_body: dict[str, Any] | None = None`。
  - 默认 `None` → 对所有未触及的 agent/provider **请求 byte-identical**(零回归)。
- `stream()` 里,`_prepare_request` 返回后、`stream_chat_completions(**request)` 前,合并:
  ```python
  if self.stream_extra_body is not None:
      request["extra_body"] = {**(request["extra_body"] or {}), **self.stream_extra_body}
  ```
  **两个 stream 点都合并**(主路径 + HX-13 allowed_tools 回退后的 retry 重流)。
- `complete()` 与 `_prepare_request` **原样不动** → `tool_stream` 永不进非流式/结构化输出路径(stream-only by construction)。
- `stream_extra_body` 与 `thinking_payload` 独立:glm 的流式请求 body 最终 = `{...thinking..., "tool_stream": true}`,与 thinking 开关无关(thinking 关时 glm 照样批量,tool_stream 照样需要)。

### gating(`services/orchestrator/src/orchestrator/agent_factory.py` `_build_provider`)
- openai_compatible 分支构造 `OpenAICompatibleProvider` 时:`provider == "glm"` → 传 `stream_extra_body={"tool_stream": True}`;其余 vendor 传 `None`(默认)。
- **always-on**,无 manifest 开关(YAGNI:glm 批量是服务端固有行为,增量流式 tool-arg 严格更优、无副作用;glm 若将来原生增量,该参数冗余但无害)。
- **不用 catalog 能力位**:只 glm 需要,provider 字符串检查最简、诚实(YAGNI)。

### parser
零改。`delta_from_openai_chunk`(`_streaming.py`)已把 `tool_calls[].function.arguments` 累积进 `ToolCallChunk.args_fragment`;`OpenAIStreamAssembler` 已重组为完整 tool call。`tool_stream` 开后 glm 的分片走同一路径(实测 2014 片正常解析重组)。

## 错误处理 / 兼容
- glm-only 注入 → openai/azure/anthropic/deepseek/qwen/kimi/doubao 请求不变。
- 流式重组 byte-equal 不变式(assembled message 与非流式逐字节相等)**不受影响**:`tool_stream` 只改 args 的**分片粒度**,不改最终 args 内容 → 重组后的 tool call 相同。
- glm 若某响应无 tool-call(纯 content/reasoning)→ `tool_stream` 无副作用(no tool_calls 分片可流)。

## 测试

- **单元(orchestrator)**:
  - glm(`OpenAICompatibleProvider(stream_extra_body={"tool_stream": True})`)的 **stream 请求** body 的 `extra_body` 含 `tool_stream: True`(主路径 + allowed_tools retry 路径都验)。
  - glm 的 **complete 请求**(结构化输出)`extra_body` **不含** `tool_stream`(命门:stream-only)。
  - `stream_extra_body=None`(默认,非 glm)→ stream 与 complete 请求 body 均不含 `tool_stream`(byte-identical 回归)。
  - 若 `thinking_payload` 也在:glm stream 请求 `extra_body` 同时含 thinking + tool_stream(两者独立合并)。
- **agent_factory**:`provider == "glm"` 构造的 provider `stream_extra_body == {"tool_stream": True}`;其余 vendor `stream_extra_body is None`。
- **不变式**:现有 stream/complete byte-equal 测(`_from_openai_response` 组装等价)仍绿——本改动不碰组装。
- **实证归档**(本 spec + PR):probe 数据 165s→4.1s;多厂商表。
- 验证命令:`cd services/orchestrator && uv run python -m pytest`(裸 python 挑不动编译扩展);改 provider/factory 后跑 provider + factory + streaming 相关套件 + `uv run ruff check`(全库含 tests)。

## 非目标

- 不给 deepseek/qwen/kimi/doubao 发 `tool_stream`(默认已增量,实测)。
- 不调 `idle_timeout_s`(根治后不需要)。
- 不做 manifest 开关(always-on for glm)。
- 不改 parser / assembler(已支持增量 args)。
- 不动 Anthropic wire(已排除,glm anthropic 端点同样批量)。
- ping-aware watchdog(社区另一类方案)——glm 静默期不发 ping,对 glm 无效,不做。

## 后续(本 spec 不做)
- 若将来接入新的 OpenAI-wire 国产 vendor,按同法探其 tool-arg 是否批量,批量则加进 glm 同款 gating(或届时抽 catalog 能力位)。
