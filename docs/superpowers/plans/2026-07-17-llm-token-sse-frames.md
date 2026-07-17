# Token SSE 帧 + 流式脱敏 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 LLM router 内部已流式消费的 token delta 作为新 `token` SSE 帧暴露给外部 API / playground,附 buffered-release 流式脱敏。

**Architecture:** router 加 async `on_delta` 回调(经 router 私有 contextvar 内部携带,保持 LangGraph 无关);`run_agent` 把"发 token 到 bridge"的 async sink 注入 `config["configurable"]`(**复用既有 `COMPACTION_SINK_KEY` 范式**);graph 节点读该 sink、建 `TokenSink`(脱敏+打标+门控)传给 router 作 on_delta;每安全 delta 经 sink `bridge.publish("token")` 直发(不走 astream、不持久化)。token 帧 provisional,权威 `updates` 帧仍是最终真相。

**Tech Stack:** Python;既有 StreamBridge / SSE(`format_sse`)/ `COMPACTION_SINK_KEY` 注入范式;既有守卫 `expert_work.common.dlp.scan_and_redact` + `expert_work.common.output_screen.screen_output`;P1/P1' 的 `LLMDelta` / `_drive_stream`。

**Spec:** `docs/superpowers/specs/2026-07-17-llm-token-sse-frames-design.md`

## Global Constraints

- **对外契约(现有事件)零破坏。** `updates`/`metadata`/`error`/`end` 等帧不变;新增仅 `token`。**astream 循环(`sse.py:421-448`)零改动。** queue/cache-hit/structured/judge-on/非流式 provider 一律不发 token,行为同今天。
- **router 保持 LangGraph 无关**:只多一个可选 async `on_delta`;不 import langgraph、不碰 bridge。
- **token 帧 provisional + live-only**:`_publish_token` 只 `bridge.publish`,**不** `_persist_event`。权威 `updates` 帧(节点跑完 screen/judge/DLP)是最终真相。
- **门控**:仅 `output_judge is not None`(judge-on)→ 不流;`token_sink` 未注入 → 不流。screen/DLP 是 regex,骑 buffered-release,**不**门控。
- **仅 content 频道**:只 `channel:"content"`;reasoning/tool_args 推迟。
- **不可变 / 复用既有守卫函数**:StreamingRedactor 用 `scan_and_redact` + `screen_output`(与非流式路径同款)。
- 测试:`cd services/orchestrator && export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock && uv run python -m pytest <file> -v`(**必用 `uv run`**,裸 python=系统 3.14 挑不动)。终门:CI-scope mypy + ruff check + ruff format。

---

## 文件结构

| 文件 | 职责 | 任务 |
|---|---|---|
| `graph_builder/streaming_redact.py`(新) | buffered-release 脱敏 + token sink 胶水 | T1(StreamingRedactor)+ T3(TokenSink/make_token_sink) |
| `llm/caller.py` | LLMCaller Protocol | T2(on_delta 参数) |
| `llm/router.py` | 路由 + 流式驱动 | T2(contextvar + on_delta 穿到 _drive_stream) |
| `graph_builder/_config.py` | config 注入键/访问器 | T4(TOKEN_SINK_KEY + token_sink_from_config) |
| `sse.py` | run_agent 生产者 | T4(_publish_token + 注入) |
| `graph_builder/builder.py` | agent_node | T4(节点接线 + 门控) |
| `docs/api/streaming-events.md`(新) | 外部 API 文档 | T4 |

依赖:T1 → T3(TokenSink 用 StreamingRedactor);T2(on_delta)+ T3(make_token_sink)→ T4(节点接线)。顺序 T1,T2,T3,T4。

---

## Task 1: StreamingRedactor(buffered-release 脱敏,纯逻辑)

**Files:**
- Create: `services/orchestrator/src/orchestrator/graph_builder/streaming_redact.py`
- Test: `services/orchestrator/tests/test_streaming_redact.py`

**Interfaces:**
- Consumes: `expert_work.common.dlp.scan_and_redact`、`expert_work.common.output_screen.screen_output`。
- Produces(T3 消费):`class StreamingRedactor(*, dlp: bool, screen: bool)` 带 `feed(text: str) -> str` / `flush() -> str`;模块常量 `HOLD_CHARS: int`。

- [ ] **Step 1: 写失败测试** — 创建 `services/orchestrator/tests/test_streaming_redact.py`:

```python
from expert_work.common.dlp import scan_and_redact
from orchestrator.graph_builder.streaming_redact import HOLD_CHARS, StreamingRedactor


def test_no_guards_passthrough_progressive() -> None:
    r = StreamingRedactor(dlp=False, screen=False)
    text = "A" * 100
    out1 = r.feed(text)
    assert out1 == "A" * (100 - HOLD_CHARS)  # holds the trailing HOLD_CHARS
    out2 = r.flush()
    assert out1 + out2 == text


def test_short_input_all_at_flush() -> None:
    r = StreamingRedactor(dlp=False, screen=False)
    assert r.feed("hello") == ""  # < HOLD_CHARS → nothing stable to release yet
    assert r.flush() == "hello"


def test_dlp_redacts_card_split_across_feeds() -> None:
    r = StreamingRedactor(dlp=True, screen=False)
    a = r.feed("your card is 4111 1111 ")
    b = r.feed("1111 1111 thanks")
    tail = r.flush()
    full = a + b + tail
    assert "4111" not in full  # raw digits never leaked
    assert full == "your card is [redacted] thanks"


def test_prefix_monotonic_chunked_equals_oneshot() -> None:
    text = "call 4111 1111 1111 1111 or 13800138000 now " + "x" * 80
    r = StreamingRedactor(dlp=True, screen=False)
    out = "".join(r.feed(c) for c in text) + r.flush()
    assert out == scan_and_redact(text).redacted


def test_screen_block_withholds_all() -> None:
    r = StreamingRedactor(dlp=False, screen=True)
    key = "sk-" + "a" * 24  # matches output_screen _SECRET_PATTERNS
    assert r.feed("here is the key " + key) == ""
    assert r.feed(" more text") == ""  # stays blocked
    assert r.flush() == ""


def test_screen_off_does_not_block_credentials() -> None:
    r = StreamingRedactor(dlp=False, screen=False)
    key = "sk-" + "a" * 24
    out = r.feed("key " + key) + r.flush()
    assert key in out  # screen disabled → not withheld
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd services/orchestrator && export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock && uv run python -m pytest tests/test_streaming_redact.py -v`
Expected: FAIL(`ModuleNotFoundError: orchestrator.graph_builder.streaming_redact`)。

- [ ] **Step 3: 实现 StreamingRedactor** — 创建 `services/orchestrator/src/orchestrator/graph_builder/streaming_redact.py`:

```python
"""Buffered-release streaming redaction for token SSE frames (流式 epic 子项目 2).

Token deltas escape the LLM router BEFORE the node-level output guards run on the
assembled message, so a per-delta redaction pass runs here as defense in depth.
Reuses the SAME regex guards as the non-streaming path (``scan_and_redact`` for
DLP, ``screen_output`` for the credential/exfil screen), applied incrementally
with a look-back hold so a pattern split across deltas is never partially emitted.

Token frames are provisional: the authoritative ``updates`` frame (full guards on
the complete message) is the source of truth. This redactor is best-effort on the
preview — the fixed-shape DLP patterns (card/id/phone) fit fully inside the hold
window; anything longer is covered by the authoritative frame.
"""

from __future__ import annotations

from expert_work.common.dlp import scan_and_redact
from expert_work.common.output_screen import screen_output

#: Characters held back from the tail of the (redacted) buffer on each feed. Must
#: be >= the longest realistic sensitive token so a pattern still forming is never
#: released raw: fixed-shape DLP patterns (card 19 / id 18 / phone 11) and typical
#: emails all fit inside 64.
HOLD_CHARS = 64


class StreamingRedactor:
    """Incremental buffered-release redactor over one content channel.

    ``feed(text)`` returns the newly-stable redacted prefix safe to emit now;
    ``flush()`` returns the redacted remainder at stream end. Only the guards
    enabled for the run (``dlp`` / ``screen``) are applied. ``screen`` is a BLOCK
    guard: once it trips, the redactor withholds everything (the authoritative
    frame carries the refusal).
    """

    def __init__(self, *, dlp: bool, screen: bool) -> None:
        self._dlp = dlp
        self._screen = screen
        self._buf = ""
        self._emitted_len = 0
        self._blocked = False

    def _redact(self, text: str) -> str:
        return scan_and_redact(text).redacted if self._dlp else text

    def feed(self, text: str) -> str:
        self._buf += text
        if self._blocked or not text:
            return ""
        if self._screen and screen_output(self._buf).blocked:
            self._blocked = True
            return ""
        redacted = self._redact(self._buf)
        boundary = max(self._emitted_len, len(redacted) - HOLD_CHARS)
        out = redacted[self._emitted_len:boundary]
        self._emitted_len = boundary
        return out

    def flush(self) -> str:
        if self._blocked:
            return ""
        if self._screen and screen_output(self._buf).blocked:
            self._blocked = True
            return ""
        redacted = self._redact(self._buf)
        out = redacted[self._emitted_len:]
        self._emitted_len = len(redacted)
        return out
```

- [ ] **Step 4: 跑测试确认通过 + mypy**

Run: `cd services/orchestrator && export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock && uv run python -m pytest tests/test_streaming_redact.py -v`
Expected: PASS(6/6)。

Run: `cd services/orchestrator && uv run mypy src/orchestrator/graph_builder/streaming_redact.py`
Expected: no errors。

- [ ] **Step 5: 提交**

```bash
git add services/orchestrator/src/orchestrator/graph_builder/streaming_redact.py \
        services/orchestrator/tests/test_streaming_redact.py
git commit -m "feat(orchestrator): StreamingRedactor —— buffered-release 流式脱敏(复用 DLP/screen)"
```

---

## Task 2: router async on_delta 回调

**Files:**
- Modify: `services/orchestrator/src/orchestrator/llm/caller.py`(`LLMCaller.__call__` 加 `on_delta` 参数)
- Modify: `services/orchestrator/src/orchestrator/llm/router.py`(contextvar + `__call__` 包装 + `_drive_stream` await sink)
- Test: `services/orchestrator/tests/test_llm_router_streaming.py`(追加)

**Interfaces:**
- Consumes: `orchestrator.llm.providers._streaming.LLMDelta`(已在 router 导入)。
- Produces(T4 消费):`LLMCaller.__call__` / `LLMRouter.__call__` 接受 `on_delta: Callable[[LLMDelta], Awaitable[None]] | None = None`;流式路径每 delta `await on_delta(delta)`。

- [ ] **Step 1: 写失败测试** — 追加到 `services/orchestrator/tests/test_llm_router_streaming.py` 末尾:

```python
@pytest.mark.asyncio
async def test_on_delta_awaited_for_each_delta_on_streaming_path() -> None:
    script = [LLMDelta(content="a"), LLMDelta(content="b"), LLMDelta(finish_reason="stop")]
    router = LLMRouter(providers=[_handle(script)], first_token_timeout_s=0.5, idle_timeout_s=0.5)
    seen: list[str] = []

    async def on_delta(d: LLMDelta) -> None:
        seen.append(d.content)

    msg = await router(messages=[], tools=[], on_delta=on_delta)
    assert msg.content == "ab"
    assert seen == ["a", "b", ""]  # every delta, incl. the empty-content finish delta


@pytest.mark.asyncio
async def test_on_delta_not_called_on_structured_path() -> None:
    from langchain_core.messages import AIMessage

    from expert_work.protocol import StructuredOutputSpec

    class _Probe:
        async def stream(self, *, messages, tools, output_schema=None):
            yield LLMDelta(content="SHOULD NOT STREAM")

        async def complete(self, *, messages, tools, output_schema=None) -> AIMessage:
            return AIMessage(content='{"ok": true}', additional_kwargs={"parsed": {"ok": True}})

        def new_stream_assembler(self):
            from orchestrator.llm.providers._streaming import OpenAIStreamAssembler

            return OpenAIStreamAssembler()

    seen: list = []

    async def on_delta(d: LLMDelta) -> None:
        seen.append(d)

    router = LLMRouter(
        providers=[ProviderHandle(provider=_Probe(), key="x")],
        first_token_timeout_s=0.5,
        idle_timeout_s=0.5,
    )
    spec = StructuredOutputSpec(schema={"type": "object"}, name="x")
    await router(messages=[], tools=[], output_schema=spec, on_delta=on_delta)
    assert seen == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd services/orchestrator && export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock && uv run python -m pytest tests/test_llm_router_streaming.py -v`
Expected: FAIL(`__call__() got an unexpected keyword argument 'on_delta'`)。

- [ ] **Step 3: caller.py 加 on_delta**

在 `services/orchestrator/src/orchestrator/llm/caller.py` 顶部 import 区(`from collections.abc import Sequence` 改为):

```python
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Protocol, runtime_checkable
```

在文件已有 import 之后加(`from langchain_core.messages import AIMessage, BaseMessage` 附近):

```python
if TYPE_CHECKING:
    from orchestrator.llm.providers._streaming import LLMDelta
```

`LLMCaller.__call__` 签名加末参:

```python
    async def __call__(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
        output_schema: StructuredOutputSpec | None = None,
        on_delta: "Callable[[LLMDelta], Awaitable[None]] | None" = None,
    ) -> AIMessage:
```

docstring 末尾追加一句:`` ``on_delta`` (子项目 2) is an optional async callback invoked once per streamed ``LLMDelta`` on the streaming path (never on the non-streaming / structured path); ``None`` (default) is the pre-existing behaviour.``

- [ ] **Step 4: router.py 加 contextvar + 包装 __call__ + _drive_stream await sink**

`services/orchestrator/src/orchestrator/llm/router.py` 顶部 import 区确保有(若缺则加):

```python
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
```

在 import 之后、`LLMRouter` 类定义之前加模块级 contextvar:

```python
# 子项目 2 — the per-call token-delta sink. Set at __call__ entry (public
# ``on_delta`` param) and read in ``_drive_stream``; a ContextVar avoids
# threading the callback through the 6-deep private call chain and is
# task-local, so concurrent runs never see each other's sink. The router stays
# LangGraph-agnostic — it only ``await``s an opaque async callback.
_delta_sink: ContextVar["Callable[[LLMDelta], Awaitable[None]] | None"] = ContextVar(
    "_llm_delta_sink", default=None
)
```

把**现有** `async def __call__(self, *, messages, tools, output_schema=None) -> AIMessage:`(router.py:349)**整体重命名为 `_dispatch`(方法体一字不改)**,然后在其上方新增包装 `__call__`:

```python
    async def __call__(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
        output_schema: StructuredOutputSpec | None = None,
        on_delta: Callable[[LLMDelta], Awaitable[None]] | None = None,
    ) -> AIMessage:
        sink_token = _delta_sink.set(on_delta)
        try:
            return await self._dispatch(
                messages=messages, tools=tools, output_schema=output_schema
            )
        finally:
            _delta_sink.reset(sink_token)

    async def _dispatch(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
        output_schema: StructuredOutputSpec | None = None,
    ) -> AIMessage:
        # ... existing __call__ body verbatim (handles = self.providers ... raise AllProvidersExhaustedError) ...
```

`_drive_stream`(router.py:612):在 `it = stream.__aiter__()` 之后加 `sink = _delta_sink.get()`;在 **两处** `assembler.add(delta)` 之后加 sink 调用。

Phase 1(现 :646-647):
```python
            assembler.add(delta)
            if sink is not None:
                await sink(delta)
            first_progress = delta.has_progress
```
Phase 2(现 :668):
```python
            assembler.add(delta)
            if sink is not None:
                await sink(delta)
```

- [ ] **Step 5: 跑测试确认通过 + mypy**

Run: `cd services/orchestrator && export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock && uv run python -m pytest tests/test_llm_router_streaming.py -v`
Expected: PASS(现有 10 测 + 新 2 测全绿;现有测试无 on_delta = 回归证明默认行为不变)。

Run: `cd services/orchestrator && uv run mypy src/orchestrator/llm/router.py src/orchestrator/llm/caller.py`
Expected: no errors。

- [ ] **Step 6: 提交**

```bash
git add services/orchestrator/src/orchestrator/llm/caller.py \
        services/orchestrator/src/orchestrator/llm/router.py \
        services/orchestrator/tests/test_llm_router_streaming.py
git commit -m "feat(orchestrator): LLM router async on_delta 回调(contextvar 携带,保持 LangGraph 无关)"
```

---

## Task 3: TokenSink + make_token_sink(节点侧 token 胶水)

**Files:**
- Modify: `services/orchestrator/src/orchestrator/graph_builder/streaming_redact.py`(追加 TokenSink + make_token_sink)
- Test: `services/orchestrator/tests/test_streaming_redact.py`(追加)

**Interfaces:**
- Consumes: `StreamingRedactor`(T1,同文件)、`orchestrator.llm.providers._streaming.LLMDelta`。
- Produces(T4 消费):`class TokenSink`(async `__call__(delta)` / `flush()`);`make_token_sink(*, step, publish, dlp, screen, judge_enabled) -> TokenSink | None`;类型别名 `TokenPublish = Callable[[dict[str, Any]], Awaitable[None]]`。

- [ ] **Step 1: 写失败测试** — 追加到 `services/orchestrator/tests/test_streaming_redact.py`(顶部加 import,末尾加测):

```python
# —— 追加到顶部 import ——
import pytest

from orchestrator.graph_builder.streaming_redact import TokenSink, make_token_sink
from orchestrator.llm.providers._streaming import LLMDelta


# —— 追加到末尾 ——
@pytest.mark.asyncio
async def test_token_sink_publishes_content_frames() -> None:
    frames: list[dict] = []

    async def pub(f: dict) -> None:
        frames.append(f)

    sink = TokenSink(step=3, publish=pub, dlp=False, screen=False)
    await sink(LLMDelta(content="A" * 100))
    await sink.flush()
    assert all(f["step"] == 3 and f["channel"] == "content" for f in frames)
    assert "".join(f["text"] for f in frames) == "A" * 100


@pytest.mark.asyncio
async def test_token_sink_redacts_pii() -> None:
    frames: list[dict] = []

    async def pub(f: dict) -> None:
        frames.append(f)

    sink = TokenSink(step=0, publish=pub, dlp=True, screen=False)
    await sink(LLMDelta(content="card 4111 1111 1111 1111 done " + "x" * 60))
    await sink.flush()
    joined = "".join(f["text"] for f in frames)
    assert "4111" not in joined and "[redacted]" in joined


async def _noop_pub(f: dict) -> None:
    return None


def test_make_token_sink_gates_off_when_judge_enabled() -> None:
    assert make_token_sink(step=0, publish=_noop_pub, dlp=False, screen=False, judge_enabled=True) is None


def test_make_token_sink_none_without_publish() -> None:
    assert make_token_sink(step=0, publish=None, dlp=False, screen=False, judge_enabled=False) is None


def test_make_token_sink_builds_when_enabled() -> None:
    sink = make_token_sink(step=1, publish=_noop_pub, dlp=True, screen=True, judge_enabled=False)
    assert isinstance(sink, TokenSink)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd services/orchestrator && export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock && uv run python -m pytest tests/test_streaming_redact.py -v`
Expected: FAIL(`ImportError: cannot import name 'TokenSink'`)。

- [ ] **Step 3: 追加 TokenSink + make_token_sink 到 streaming_redact.py**

`streaming_redact.py` 顶部 import 区加:

```python
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orchestrator.llm.providers._streaming import LLMDelta
```

文件末尾追加:

```python
#: Async callback that ships one token frame to the SSE bridge (injected by
#: run_agent via ``TOKEN_SINK_KEY``; see graph_builder/_config.py).
TokenPublish = Callable[[dict[str, Any]], Awaitable[None]]


class TokenSink:
    """Per-run content-channel token emitter.

    Wraps a :class:`StreamingRedactor`; each streamed ``LLMDelta``'s content is
    redacted incrementally and the newly-stable text is published as a
    ``{"step", "channel": "content", "text"}`` frame. ``flush`` emits the
    buffered-release tail after the router returns.
    """

    def __init__(self, *, step: int, publish: TokenPublish, dlp: bool, screen: bool) -> None:
        self._step = step
        self._publish = publish
        self._redactor = StreamingRedactor(dlp=dlp, screen=screen)

    async def __call__(self, delta: LLMDelta) -> None:
        safe = self._redactor.feed(delta.content)
        if safe:
            await self._publish({"step": self._step, "channel": "content", "text": safe})

    async def flush(self) -> None:
        tail = self._redactor.flush()
        if tail:
            await self._publish({"step": self._step, "channel": "content", "text": tail})


def make_token_sink(
    *,
    step: int,
    publish: TokenPublish | None,
    dlp: bool,
    screen: bool,
    judge_enabled: bool,
) -> TokenSink | None:
    """Build a :class:`TokenSink`, or ``None`` when token streaming is gated off.

    Gate: an LLM output judge (``judge_enabled``) can only decide on the complete
    message, so its runs never token-stream; and without an injected ``publish``
    sink there is nowhere to send frames.
    """
    if judge_enabled or publish is None:
        return None
    return TokenSink(step=step, publish=publish, dlp=dlp, screen=screen)
```

- [ ] **Step 4: 跑测试确认通过 + mypy**

Run: `cd services/orchestrator && export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock && uv run python -m pytest tests/test_streaming_redact.py -v`
Expected: PASS(6 + 5 = 11)。

Run: `cd services/orchestrator && uv run mypy src/orchestrator/graph_builder/streaming_redact.py`
Expected: no errors。

- [ ] **Step 5: 提交**

```bash
git add services/orchestrator/src/orchestrator/graph_builder/streaming_redact.py \
        services/orchestrator/tests/test_streaming_redact.py
git commit -m "feat(orchestrator): TokenSink + make_token_sink —— 节点侧 token 帧胶水 + judge 门控"
```

---

## Task 4: config 注入 + run_agent 发射 + 节点接线 + 外部 API 文档

**Files:**
- Modify: `services/orchestrator/src/orchestrator/graph_builder/_config.py`(`TOKEN_SINK_KEY` + `token_sink_from_config` + `TokenEventSink`)
- Modify: `services/orchestrator/src/orchestrator/sse.py`(`_publish_token` + 注入)
- Modify: `services/orchestrator/src/orchestrator/graph_builder/builder.py`(`agent_node` 接线)
- Create: `docs/api/streaming-events.md`
- Test: `services/orchestrator/tests/test_sse_persistence.py`(追加)

**Interfaces:**
- Consumes: `make_token_sink`(T3)、`LLMCaller.on_delta`(T2)、既有 `COMPACTION_SINK_KEY` 范式(`_config.py:28/92`、`sse.py:357/370`)。
- Produces: 新 `token` SSE 事件(live-only)。

- [ ] **Step 1: 写失败测试** — 追加到 `services/orchestrator/tests/test_sse_persistence.py` 末尾(复用文件已有的 `_new_record`/`_drain`/`InMemoryStreamBridge`/`RunManager`/`InMemoryRunEventStore`/`run_agent`):

```python
@pytest.mark.asyncio
async def test_run_agent_token_sink_publishes_live_only_not_persisted() -> None:
    """Token frames go to the bridge (live) but NOT the durable store — they are
    provisional; the authoritative ``updates`` frame is what replays. Mirrors the
    COMPACTION_SINK_KEY pattern (a node fires the injected sink mid-turn)."""
    from orchestrator.graph_builder._config import TOKEN_SINK_KEY

    @dataclass
    class _TokenGraph:
        async def astream(
            self, _input: Any, config: Any = None, *, stream_mode: str = "updates"
        ) -> AsyncIterator[Any]:
            sink = (config.get("configurable") or {})[TOKEN_SINK_KEY]
            await sink({"step": 0, "channel": "content", "text": "he"})
            await sink({"step": 0, "channel": "content", "text": "llo"})
            yield {"agent": {"step_count": 1}}

        async def aget_state(self, _config: Any) -> Any:
            from types import SimpleNamespace

            return SimpleNamespace(values={})

    bridge = InMemoryStreamBridge()
    rm = RunManager()
    record = await _new_record(rm)
    store = InMemoryRunEventStore()

    await run_agent(
        bridge=bridge,
        run_manager=rm,
        record=record,
        graph=_TokenGraph(),
        graph_input={"messages": []},
        config={},
        event_store=store,
    )

    events = await _drain(bridge, record.run_id)
    assert [e.event for e in events] == ["metadata", "token", "token", "updates"]
    token_frames = [e.data for e in events if e.event == "token"]
    assert token_frames == [
        {"step": 0, "channel": "content", "text": "he"},
        {"step": 0, "channel": "content", "text": "llo"},
    ]
    listed = await store.list(run_id=record.run_id)
    assert [r.event_name for r in listed] == ["metadata", "updates"]  # token NOT persisted
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd services/orchestrator && export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock && uv run python -m pytest tests/test_sse_persistence.py::test_run_agent_token_sink_publishes_live_only_not_persisted -v`
Expected: FAIL(`ImportError: cannot import name 'TOKEN_SINK_KEY'`)。

- [ ] **Step 3: _config.py 加 TOKEN_SINK_KEY + 访问器**

`services/orchestrator/src/orchestrator/graph_builder/_config.py`:确保顶部有 `from collections.abc import Awaitable, Callable` 与 `from typing import Any`(缺则加)。在 `COMPACTION_SINK_KEY = "compaction_event_sink"`(:28)之后加:

```python
#: 子项目 2 — config key under which run_agent injects the async token-frame sink
#: (mirrors COMPACTION_SINK_KEY). The agent node lifts it out via
#: ``token_sink_from_config`` and feeds it to a TokenSink.
TOKEN_SINK_KEY = "token_event_sink"

#: An async callable that ships one token frame ``{step, channel, text}`` to the
#: SSE bridge.
TokenEventSink = Callable[[dict[str, Any]], Awaitable[None]]
```

在 `compaction_sink_from_config`(:92)之后加:

```python
def token_sink_from_config(config: RunnableConfig) -> TokenEventSink | None:
    """Lift the run's token-frame sink out of ``config`` (子项目 2).

    ``None`` when run_agent injected no sink (e.g. a non-streaming execution
    path) — the node then simply does not token-stream.
    """
    configurable = config.get("configurable") or {}
    sink = configurable.get(TOKEN_SINK_KEY)
    return sink if callable(sink) else None
```

- [ ] **Step 4: sse.py 加 _publish_token + 注入**

`services/orchestrator/src/orchestrator/sse.py`:import 行(`:86` `from orchestrator.graph_builder._config import AUDIT_LOGGER_KEY, COMPACTION_SINK_KEY`)加 `TOKEN_SINK_KEY`:

```python
from orchestrator.graph_builder._config import AUDIT_LOGGER_KEY, COMPACTION_SINK_KEY, TOKEN_SINK_KEY
```

在 `_publish_compaction`(:357-359)之后加:

```python
    async def _publish_token(frame: Any) -> None:
        # Live-only: token frames are provisional; do NOT mirror to the event
        # store (the authoritative ``updates`` frame is what replays).
        await bridge.publish(run_id, "token", frame)
```

在 `effective_config["configurable"][COMPACTION_SINK_KEY] = _publish_compaction`(:370)之后加:

```python
    effective_config["configurable"][TOKEN_SINK_KEY] = _publish_token
```

- [ ] **Step 5: builder.py 节点接线**

`services/orchestrator/src/orchestrator/graph_builder/builder.py`:import 区加:

```python
from orchestrator.graph_builder.streaming_redact import make_token_sink
```

并把 `_config` 的 import 加上 `token_sink_from_config`(与现有 `cancellation_token` / `audit_logger_from_config` 同一 import 行）。

把 cache-miss 分支(:790-798)改为:

```python
        else:
            _token_sink = make_token_sink(
                step=step_count,
                publish=token_sink_from_config(config),
                dlp=output_dlp,
                screen=output_screen,
                judge_enabled=output_judge is not None,
            )
            # Wrap the LLM call so a cancel mid-call interrupts the
            # in-flight await rather than waiting it out (E.15).
            # 10.1 — one ``expert_work.orchestrator.llm_call`` child span per
            # provider call, attached under the session root span.
            with expert_work_span(ExpertWorkComponent.ORCHESTRATOR, "llm_call"):
                response = await token.run_cancellable(
                    active_caller(messages=messages, tools=tools, on_delta=_token_sink)
                )
            if _token_sink is not None:
                await _token_sink.flush()
```

- [ ] **Step 6: 创建外部 API 文档** — `docs/api/streaming-events.md`:

```markdown
# Agent Run Streaming Events (SSE)

A streaming agent run emits **Server-Sent Events**. The stream is the response
body of `POST /v1/agents/{agent_code}/runs` (unless `mode=queue`, which returns
`202` JSON and no stream) and can be re-attached via
`GET /v1/sessions/{thread_id}/runs/{run_id}/events`.

Each event has an SSE `event:` name and a JSON `data:` payload. This page
documents the event kinds a client sees; the authoritative, durable record is
the set of persisted frames replayed by the events endpoint.

## Event kinds

| `event:` | When | Persisted (replayed on reconnect) |
|---|---|---|
| `metadata` | Once at run start (`run_id`, `thread_id`, trace id) | yes |
| `updates`  | Once per agent/tool step — the **authoritative** step result | yes |
| `token`    | Fine-grained token preview during an LLM step (see below) | **no (live-only)** |
| `approval` | Run paused at a human-approval gate | yes |
| `retry` / `error` / `end` | Retry notice / failure / terminal | yes / yes / — |

## The `token` event (provisional preview)

For a streaming-capable run, the model's answer text is previewed token-by-token
as it is generated:

```
event: token
data: {"step": 0, "channel": "content", "text": "partial answer fragment"}
```

- `step` — the agent step index the fragment belongs to.
- `channel` — always `"content"` (the answer text). Other channels are reserved.
- `text` — an already-redacted fragment of the answer.

**`token` frames are provisional.** Treat them as a live typewriter preview only:

1. Accumulate `token.text` (per `step`) for live display.
2. When the `updates` frame for that step arrives, it is **authoritative** —
   replace the accumulated preview with the content from `updates`. The
   `updates` content has passed the full output-safety guards; a run that is
   blocked by a guard yields a refusal in `updates` that supersedes any preview.
3. On reconnect, `token` frames are **not** replayed — only the persisted
   `metadata` / `updates` / … frames are. Rebuild state from those.

## Which runs emit `token`

Emitted for streaming-provider runs **without** a model-backed output judge.
Not emitted (only step-level `updates`, exactly as before) for: `mode=queue`,
cached responses, structured-output runs, non-streaming providers, and runs
with the output judge enabled.
```

- [ ] **Step 7: 跑测试确认通过 + 回归 + mypy + ruff**

Run: `cd services/orchestrator && export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock && uv run python -m pytest tests/test_sse_persistence.py tests/test_sse.py tests/test_run_retry.py -v`
Expected: PASS(新 token 测 + 全部现有 sse/持久化/retry 测 —— astream 未动,现有全绿=回归)。

Run: `cd services/orchestrator && uv run mypy src/orchestrator/graph_builder/_config.py src/orchestrator/graph_builder/builder.py src/orchestrator/sse.py`
Expected: no errors。

Run: `cd services/orchestrator && uv run ruff check src/orchestrator/graph_builder src/orchestrator/sse.py`
Expected: clean。

- [ ] **Step 8: 提交**

```bash
git add services/orchestrator/src/orchestrator/graph_builder/_config.py \
        services/orchestrator/src/orchestrator/sse.py \
        services/orchestrator/src/orchestrator/graph_builder/builder.py \
        services/orchestrator/tests/test_sse_persistence.py \
        docs/api/streaming-events.md
git commit -m "feat(orchestrator): token SSE 帧 —— run_agent 注入 sink + 节点接线 + 外部 API 文档"
```

---

## 最终验证(全部任务后)

- [ ] `cd services/orchestrator && export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock && uv run python -m pytest tests/test_streaming_redact.py tests/test_llm_router_streaming.py tests/test_sse_persistence.py -v` —— 全绿。
- [ ] **CI-scope mypy**:`cd services/orchestrator && uv run mypy src/orchestrator/graph_builder src/orchestrator/llm src/orchestrator/sse.py` —— 零错误。
- [ ] `cd services/orchestrator && uv run ruff check src/orchestrator && uv run ruff format --check src/orchestrator/graph_builder/streaming_redact.py src/orchestrator/graph_builder/_config.py src/orchestrator/llm/router.py src/orchestrator/llm/caller.py src/orchestrator/sse.py` —— 干净。
- [ ] 全套 orchestrator 回归:`uv run python -m pytest -q`(确认 on_delta / 注入 sink 未破任何现有测试)。

---

## Self-Review(计划作者已跑)

**1. Spec coverage:**
- spec §3 数据流(on_delta + 注入 sink)→ T2(on_delta)+ T4(注入)✅
- spec §4 StreamingRedactor(redact 整 buffer + HOLD_CHARS + 前缀单调 + screen-block + flush)→ T1 实现 + 6 测 ✅
- spec §5 节点门控(judge-off ∧ sink 存在)→ T3 make_token_sink + T4 节点接线 ✅
- spec §6 注入 sink(TOKEN_SINK_KEY + _publish_token,astream 零改,live-only)→ T4 ✅
- spec §7 不受影响(queue/cache-hit/structured/judge-on/非流式)→ astream 未动 + 门控 + on_delta 只在流式路径调,T2 structured 测 + T4 门控证 ✅
- spec §8 外部 API 文档 → T4 `docs/api/streaming-events.md` ✅
- spec §9 组件清单 → T1-T4 逐一 ✅
- spec §10 测试(redactor / router on_delta / TokenSink+make / run_agent 注入)→ T1-T4 测试 ✅
- spec §11 排除(reasoning/tool_args/前端/持久化/judge-on 流式/新 secret 守卫)→ 计划未涉及 ✅

**2. Placeholder scan:** 无 TBD/TODO;每 code step 完整可粘贴;命令含预期输出。✅

**3. Type consistency:** `on_delta: Callable[[LLMDelta], Awaitable[None]] | None`(caller/router 一致);`TokenPublish`/`TokenEventSink` 同形 `Callable[[dict[str, Any]], Awaitable[None]]`;`make_token_sink(step, publish, dlp, screen, judge_enabled)` 签名 T3 定义、T4 调用一致;`StreamingRedactor(dlp=, screen=)` T1 定义、T3 用一致;`TOKEN_SINK_KEY`/`token_sink_from_config` T4 内自洽。✅

> 备注(交给终审 triage):T4 节点接线(builder.py 6 行胶水)本身无独立单测 —— 由 T4 的 run_agent 注入测试(节点侧 fire sink)+ T2 router on_delta 测 + T3 TokenSink 测 compositional 覆盖,字面接线靠 mypy + 全套回归。若终审要求显式节点级测试,补一条(需 build_agent_graph + fake streaming caller harness),非阻塞。
