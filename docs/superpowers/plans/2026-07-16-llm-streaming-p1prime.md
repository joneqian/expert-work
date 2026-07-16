# P1' — Anthropic Internal Streaming — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Anthropic Messages provider a real token stream (Anthropic SSE), reusing the P1 router/idle-timeout/rate-limit/config machinery, so an Anthropic-backed agent gets the same buffer-until-first-token + two-threshold idle behavior — with zero external/UI contract change (`complete()` still returns a whole `AIMessage`).

**Architecture:** P1 built the generic pieces (`LLMDelta`/`ToolCallChunk`, `StreamingLLMProvider`/`supports_streaming`, the router's `_drive_stream`/`_next_delta`/two-threshold driver, `RateLimitedProvider.stream()`, `idle_timeout_s` config, `LLMStreamInterruptedError`). P1' adds only the Anthropic-specific wire pieces: an Anthropic SSE event→`LLMDelta` parser, an `AnthropicStreamAssembler` (reuses `_from_anthropic_response` for byte-equality), `AnthropicProvider.stream()`, and `HTTPAnthropicClient.stream_messages`. It also pays down one P1 debt: the router's `_drive_stream` hard-codes `OpenAIStreamAssembler`, so P1' generalizes assembler selection via a `StreamAssembler` Protocol + `provider.new_stream_assembler()`.

**Tech Stack:** Python 3.12, httpx streaming (`client.stream`), LangChain `AIMessage`, pytest with `httpx.MockTransport`, ruff + mypy. Test runner: `cd services/orchestrator && uv run python -m pytest` (bare `python` = broken system 3.14).

## Global Constraints

- **Backward compatible.** `AnthropicProvider.complete()` still returns a whole `AIMessage`, unchanged. The streaming-assembled `AIMessage` MUST be byte-equal to `complete()`'s for the same logical response (guarded by an equivalence test). OpenAI behavior and all P1 tests stay green.
- **Deadline strictly more permissive** — same as P1 (`first_token_timeout_s`/`idle_timeout_s`), already wired; P1' changes nothing here.
- **Fallback policy fixed:** buffer-until-first-token — already in the router; P1' inherits it. An Anthropic stall/error before the first *progress* delta propagates to the router's classification (fallover); after it, `idle_timeout` ends the turn with the partial and a hard error becomes terminal `LLMStreamInterruptedError`.
- **Structured output does NOT stream (both vendors).** The router's `_invoke_provider` routes any `output_schema is not None` call to the non-streaming `complete()` path. Rationale: structured output's contract is a complete validated JSON object — token streaming has no user value, and it avoids the Anthropic tool-call-path `_extract_structured_response` post-processing that a streaming assembler cannot replicate. This is a deliberate, byte-equal-preserving change to P1's OpenAI structured path (structured JSON never went to a user token-by-token anyway); `test_structured_output.py` must stay green on the `complete()` path.
- **Anthropic assembly = byte-equal to `_from_anthropic_response`.** The assembler builds a synthetic response body (`{"content": [...blocks], "usage": {...}}`) and calls `_from_anthropic_response` — the same decoder `complete()` uses. Text is concatenated into a single text block (the decoder concatenates ALL text blocks regardless, so no per-block index is needed); tool_use blocks are reassembled by `index`; `thinking` is dropped (the decoder ignores non-text/tool_use blocks, so non-streaming does too).
- **Anthropic usage spans two events:** `input_tokens` (+ cache counters) arrive on `message_start`, `output_tokens` on `message_delta`. The assembler MERGES both into one usage dict before decoding.
- Repo conventions: many small files, immutable updates, ruff + mypy clean, per-vendor unit tests with a mocked SSE transport, no direct commits to `main`, squash-merge. Deferred-import `openai↔_streaming` / `anthropic↔_streaming` cycles are intentional (assembler `build()` lazily imports the decoder); CodeQL will flag them — resolve as by-design at merge (see the P1 precedent in [[llm-token-streaming-epic]]).

---

## File Structure

- **Modify** `services/orchestrator/src/orchestrator/llm/providers/_streaming.py` — add `StreamAssembler` Protocol; add `new_stream_assembler` to the `StreamingLLMProvider` Protocol; add `delta_from_anthropic_event` + `AnthropicStreamAssembler`. (`OpenAIStreamAssembler` unchanged; it now also nominally satisfies `StreamAssembler`.)
- **Modify** `services/orchestrator/src/orchestrator/llm/router.py` — `_drive_stream` takes the assembler from the provider (`_provider_assembler`) instead of hard-coding `OpenAIStreamAssembler`; `_invoke_provider` routes `output_schema is not None` to the non-streaming path.
- **Modify** `services/orchestrator/src/orchestrator/llm/providers/openai.py` — `OpenAIProvider.new_stream_assembler()` returns `OpenAIStreamAssembler()`.
- **Modify** `services/orchestrator/src/orchestrator/llm/providers/anthropic.py` — `AnthropicClient` Protocol + `stream_messages`; `HTTPAnthropicClient.stream_messages` (SSE); `RecordingAnthropicClient.stream_events` + `stream_messages` (synthesize from `response` when empty); `AnthropicProvider.stream()` + `new_stream_assembler()`; extract `_prepare_anthropic_request` shared by `complete`/`stream`.
- **Test files:** `tests/test_llm_streaming_wire_anthropic.py` (T2), `tests/test_anthropic_client_stream.py` (T3), `tests/test_llm_provider_anthropic_stream.py` (T4), plus edits to `tests/test_llm_streaming_wire.py` / `tests/test_llm_router_streaming.py` (T1 generalization).

## Interfaces (locked once, consumed across tasks)

```python
# _streaming.py
@runtime_checkable
class StreamAssembler(Protocol):
    def add(self, delta: LLMDelta) -> None: ...
    def build(self, *, interrupted: bool = False) -> AIMessage: ...

# StreamingLLMProvider gains:
class StreamingLLMProvider(Protocol):
    def stream(self, *, messages, tools, output_schema=None) -> AsyncIterator[LLMDelta]: ...
    def new_stream_assembler(self) -> StreamAssembler: ...

def delta_from_anthropic_event(event: Mapping[str, Any]) -> LLMDelta: ...

class AnthropicStreamAssembler:              # implements StreamAssembler
    def add(self, delta: LLMDelta) -> None: ...
    def build(self, *, interrupted: bool = False) -> AIMessage: ...

# router.py
def _provider_assembler(provider: object) -> StreamAssembler:  # unwrap .inner, call new_stream_assembler
    ...
# _drive_stream(self, handle, stream, assembler)   # assembler now passed in
# _invoke_provider: output_schema is not None  -> non-streaming complete path

# anthropic.py — AnthropicClient Protocol / HTTPAnthropicClient / RecordingAnthropicClient gain:
def stream_messages(self, *, model, system, messages, tools, max_tokens,
    temperature=None, thinking=None, output_config=None, betas=None, tool_choice=None,
) -> AsyncIterator[Mapping[str, Any]]: ...   # yields parsed SSE event dicts (each has a "type")

# AnthropicProvider gains:
def stream(self, *, messages, tools, output_schema=None) -> AsyncIterator[LLMDelta]: ...
def new_stream_assembler(self) -> AnthropicStreamAssembler: ...
```

---

### Task 1: Generalize assembler selection (pay down P1 debt) + structured non-streaming route

**Files:**
- Modify: `services/orchestrator/src/orchestrator/llm/providers/_streaming.py`
- Modify: `services/orchestrator/src/orchestrator/llm/providers/openai.py`
- Modify: `services/orchestrator/src/orchestrator/llm/router.py`
- Modify: `services/orchestrator/tests/test_llm_router_streaming.py` (extend + update existing `_StreamProvider` double)

**Note:** `RateLimitedProvider` needs NO change — `_provider_assembler` and `supports_streaming` both unwrap `.inner` to the innermost provider (OpenAI/Anthropic), so the rate-limit wrapper only needs its existing `.stream()` + `.inner`, not `new_stream_assembler`.

**Interfaces:**
- Produces: `StreamAssembler` Protocol; `StreamingLLMProvider.new_stream_assembler`; `_provider_assembler`; `_drive_stream(..., assembler)`; `_invoke_provider` structured route. Consumed by T2–T4.
- Consumes: P1's `supports_streaming`, `OpenAIStreamAssembler`.

- [ ] **Step 1: Write the failing test** — extend `tests/test_llm_router_streaming.py`

```python
@pytest.mark.asyncio
async def test_structured_output_uses_non_streaming_path() -> None:
    # output_schema set -> router must NOT drive the stream; it calls the
    # provider's complete() path (structured output does not stream).
    from expert_work.protocol import StructuredOutputSpec
    from langchain_core.messages import AIMessage

    class _Probe:
        def __init__(self) -> None:
            self.stream_calls = 0
            self.complete_calls = 0

        async def stream(self, *, messages, tools, output_schema=None):
            self.stream_calls += 1
            yield LLMDelta(content="SHOULD NOT STREAM")

        async def complete(self, *, messages, tools, output_schema=None) -> AIMessage:
            self.complete_calls += 1
            return AIMessage(content='{"ok": true}', additional_kwargs={"parsed": {"ok": True}})

        def new_stream_assembler(self):
            from orchestrator.llm.providers._streaming import OpenAIStreamAssembler
            return OpenAIStreamAssembler()

    probe = _Probe()
    router = LLMRouter(providers=[ProviderHandle(provider=probe, key="x")],
                       first_token_timeout_s=0.5, idle_timeout_s=0.5)
    spec = StructuredOutputSpec(schema={"type": "object"}, name="x")
    msg = await router(messages=[], tools=[], output_schema=spec)
    assert probe.stream_calls == 0
    assert probe.complete_calls == 1
    assert msg.additional_kwargs["parsed"] == {"ok": True}


@pytest.mark.asyncio
async def test_drive_stream_uses_provider_assembler() -> None:
    # A streaming provider that supplies its own assembler must have THAT
    # assembler used by the router (not a hard-coded OpenAI one).
    from orchestrator.llm.providers._streaming import OpenAIStreamAssembler

    used = {}

    class _MarkAssembler(OpenAIStreamAssembler):
        def build(self, *, interrupted: bool = False):
            used["hit"] = True
            return super().build(interrupted=interrupted)

    class _P:
        async def stream(self, *, messages, tools, output_schema=None):
            yield LLMDelta(content="ok")
            yield LLMDelta(finish_reason="stop")

        def new_stream_assembler(self):
            return _MarkAssembler()

    router = LLMRouter(providers=[ProviderHandle(provider=_P(), key="x")],
                       first_token_timeout_s=0.5, idle_timeout_s=0.5)
    msg = await router(messages=[], tools=[])
    assert msg.content == "ok"
    assert used.get("hit") is True
```

- [ ] **Step 2: Run to verify it fails** — `cd services/orchestrator && uv run python -m pytest tests/test_llm_router_streaming.py -q -k "structured_output_uses_non_streaming or drive_stream_uses_provider"`. Expected: FAIL (`_Probe`/`_P` has no path yet; `new_stream_assembler` unused).

- [ ] **Step 3: Implement**

`_streaming.py` — add the Protocol and extend `StreamingLLMProvider`:

```python
@runtime_checkable
class StreamAssembler(Protocol):
    """Accumulates provider deltas into a final :class:`AIMessage`. Each
    provider supplies its own (OpenAI-wire vs Anthropic wire assemble
    differently) via ``StreamingLLMProvider.new_stream_assembler``."""

    def add(self, delta: LLMDelta) -> None: ...
    def build(self, *, interrupted: bool = False) -> AIMessage: ...
```

Add `new_stream_assembler` to `StreamingLLMProvider`:

```python
class StreamingLLMProvider(Protocol):
    def stream(
        self, *, messages: Sequence[BaseMessage], tools: Sequence[ToolSpec],
        output_schema: StructuredOutputSpec | None = None,
    ) -> AsyncIterator[LLMDelta]: ...

    def new_stream_assembler(self) -> StreamAssembler:
        """A fresh assembler for one stream drain (provider-specific)."""
        ...
```

`openai.py` — add to `OpenAIProvider`:

```python
    def new_stream_assembler(self) -> OpenAIStreamAssembler:
        return OpenAIStreamAssembler()
```

`router.py` — add `_provider_assembler` helper (mirror `supports_streaming`'s unwrap):

```python
def _provider_assembler(provider: object) -> Any:
    """The innermost provider's stream assembler (unwraps ``.inner``)."""
    p: Any = provider
    seen: set[int] = set()
    while hasattr(p, "inner") and id(p) not in seen:
        seen.add(id(p))
        p = p.inner
    return p.new_stream_assembler()
```

Change `_invoke_provider` — structured output skips streaming:

```python
    async def _invoke_provider(self, handle, *, messages, tools, output_schema):
        # Structured output does not stream (both vendors) — its contract is
        # a complete validated JSON object; token streaming has no value and
        # Anthropic's tool-call structured path needs post-processing the
        # streaming assembler cannot replicate.
        if output_schema is None and supports_streaming(handle.provider):
            return await self._drive_stream(
                handle,
                _stream(handle.provider, messages=messages, tools=tools, output_schema=None),
                _provider_assembler(handle.provider),
            )
        result = await self._invoke_with_deadline(
            handle, _complete(handle.provider, messages=messages, tools=tools, output_schema=output_schema),
        )
        assert isinstance(result, AIMessage)  # noqa: S101
        return result
```

Change `_drive_stream` signature to take the assembler:

```python
    async def _drive_stream(self, handle, stream, assembler):  # assembler: StreamAssembler
        # (body identical to P1 except the first line:)
        # assembler = OpenAIStreamAssembler()   <-- REMOVE this hard-coded line
        it = stream.__aiter__()
        ...  # rest unchanged
```

`tests/test_llm_router_streaming.py` — the existing P1 `_StreamProvider` double (used by 7 streaming tests) has only `stream()`; after `_invoke_provider` calls `_provider_assembler(handle.provider)`, it must also supply an assembler, or those tests raise `AttributeError`. Add to `_StreamProvider`:

```python
    def new_stream_assembler(self):
        from orchestrator.llm.providers._streaming import OpenAIStreamAssembler
        return OpenAIStreamAssembler()
```

- [ ] **Step 4: Run to verify pass** — `cd services/orchestrator && uv run python -m pytest tests/test_llm_router_streaming.py tests/test_llm_router.py tests/test_structured_output.py tests/test_llm_provider_openai_stream.py -q`. Expected: PASS — new tests pass; P1 OpenAI streaming + structured-output suites stay green (structured now goes through `complete()`).

- [ ] **Step 5: Lint + commit**

```bash
cd services/orchestrator && uv run ruff check src/orchestrator/llm/providers/_streaming.py src/orchestrator/llm/providers/openai.py src/orchestrator/llm/router.py tests/test_llm_router_streaming.py && uv run ruff format src/orchestrator/llm/providers/_streaming.py src/orchestrator/llm/router.py
git add services/orchestrator/src/orchestrator/llm/providers/_streaming.py services/orchestrator/src/orchestrator/llm/providers/openai.py services/orchestrator/src/orchestrator/llm/router.py services/orchestrator/tests/test_llm_router_streaming.py
git commit -m "refactor(llm): provider-supplied stream assembler + structured output skips streaming"
```

---

### Task 2: Anthropic SSE parser + assembler

**Files:**
- Modify: `services/orchestrator/src/orchestrator/llm/providers/_streaming.py`
- Test: `services/orchestrator/tests/test_llm_streaming_wire_anthropic.py`

**Interfaces:**
- Consumes: `LLMDelta`, `ToolCallChunk` (P1); `anthropic._from_anthropic_response` (deferred import in `build`).
- Produces: `delta_from_anthropic_event`, `AnthropicStreamAssembler` (consumed by T4).

**Anthropic SSE event → `LLMDelta` mapping (verbatim contract):**
- `message_start`: `event["message"]["usage"]` → usage (`input_tokens`, cache counters); `event["message"]["model"]` → model. No progress.
- `content_block_start`: `event["content_block"]`; if `type=="tool_use"` → `ToolCallChunk(index=event["index"], id=cb["id"], name=cb["name"])`. Else (text/thinking) → empty delta.
- `content_block_delta`: `event["delta"]` by `delta["type"]`: `text_delta`→`content=delta["text"]`; `thinking_delta`→`reasoning=delta["thinking"]`; `input_json_delta`→`ToolCallChunk(index=event["index"], args_fragment=delta["partial_json"])`.
- `content_block_stop` / `message_stop`: empty delta.
- `message_delta`: `event["delta"]["stop_reason"]`→finish_reason; `event["usage"]["output_tokens"]`→usage.

- [ ] **Step 1: Write the failing test** — `tests/test_llm_streaming_wire_anthropic.py`

```python
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from orchestrator.llm.providers._streaming import (
    AnthropicStreamAssembler,
    ToolCallChunk,
    delta_from_anthropic_event,
)
from orchestrator.llm.providers.anthropic import _from_anthropic_response


def test_text_delta_is_progress() -> None:
    d = delta_from_anthropic_event({"type": "content_block_delta", "index": 0,
                                    "delta": {"type": "text_delta", "text": "Hel"}})
    assert d.content == "Hel"
    assert d.has_progress is True


def test_thinking_delta_is_reasoning_progress() -> None:
    d = delta_from_anthropic_event({"type": "content_block_delta", "index": 0,
                                    "delta": {"type": "thinking_delta", "thinking": "hmm"}})
    assert d.reasoning == "hmm"
    assert d.has_progress is True


def test_message_start_usage_no_progress() -> None:
    d = delta_from_anthropic_event({"type": "message_start",
        "message": {"model": "claude-x", "usage": {"input_tokens": 10, "cache_read_input_tokens": 4}}})
    assert d.has_progress is False
    assert d.usage == {"input_tokens": 10, "cache_read_input_tokens": 4}
    assert d.model == "claude-x"


def test_tool_use_start_and_json_delta() -> None:
    start = delta_from_anthropic_event({"type": "content_block_start", "index": 1,
        "content_block": {"type": "tool_use", "id": "toolu_1", "name": "search"}})
    assert start.tool_calls == (ToolCallChunk(index=1, id="toolu_1", name="search"),)
    frag = delta_from_anthropic_event({"type": "content_block_delta", "index": 1,
        "delta": {"type": "input_json_delta", "partial_json": '{"q":'}})
    assert frag.tool_calls == (ToolCallChunk(index=1, args_fragment='{"q":'),)


def test_message_delta_finish_and_output_usage() -> None:
    d = delta_from_anthropic_event({"type": "message_delta",
        "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 7}})
    assert d.finish_reason == "end_turn"
    assert d.usage == {"output_tokens": 7}


def test_assembler_text_matches_decoder() -> None:
    body = {"content": [{"type": "text", "text": "Hello world"}],
            "usage": {"input_tokens": 10, "output_tokens": 5}}
    expected = _from_anthropic_response(body)
    asm = AnthropicStreamAssembler()
    asm.add(delta_from_anthropic_event({"type": "message_start",
        "message": {"model": "claude-x", "usage": {"input_tokens": 10}}}))
    asm.add(delta_from_anthropic_event({"type": "content_block_delta", "index": 0,
        "delta": {"type": "text_delta", "text": "Hello "}}))
    asm.add(delta_from_anthropic_event({"type": "content_block_delta", "index": 0,
        "delta": {"type": "text_delta", "text": "world"}}))
    asm.add(delta_from_anthropic_event({"type": "message_delta",
        "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 5}}))
    got = asm.build()
    assert got.content == expected.content
    assert got.usage_metadata == expected.usage_metadata
    assert got.tool_calls == expected.tool_calls


def test_assembler_reassembles_tool_use() -> None:
    body = {"content": [{"type": "tool_use", "id": "toolu_1", "name": "search",
                         "input": {"q": "hi"}}],
            "usage": {"input_tokens": 3, "output_tokens": 2}}
    expected = _from_anthropic_response(body)
    asm = AnthropicStreamAssembler()
    asm.add(delta_from_anthropic_event({"type": "message_start", "message": {"usage": {"input_tokens": 3}}}))
    asm.add(delta_from_anthropic_event({"type": "content_block_start", "index": 0,
        "content_block": {"type": "tool_use", "id": "toolu_1", "name": "search"}}))
    asm.add(delta_from_anthropic_event({"type": "content_block_delta", "index": 0,
        "delta": {"type": "input_json_delta", "partial_json": '{"q": '}}))
    asm.add(delta_from_anthropic_event({"type": "content_block_delta", "index": 0,
        "delta": {"type": "input_json_delta", "partial_json": '"hi"}'}}))
    asm.add(delta_from_anthropic_event({"type": "message_delta",
        "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 2}}))
    got = asm.build()
    assert got.tool_calls == expected.tool_calls


def test_assembler_interrupted_drops_incomplete_tool() -> None:
    asm = AnthropicStreamAssembler()
    asm.add(delta_from_anthropic_event({"type": "content_block_delta", "index": 0,
        "delta": {"type": "text_delta", "text": "partial"}}))
    asm.add(delta_from_anthropic_event({"type": "content_block_start", "index": 1,
        "content_block": {"type": "tool_use", "id": "toolu_1", "name": "search"}}))
    asm.add(delta_from_anthropic_event({"type": "content_block_delta", "index": 1,
        "delta": {"type": "input_json_delta", "partial_json": '{"q": '}}))
    got = asm.build(interrupted=True)
    assert got.content == "partial"
    assert got.tool_calls == []


def test_assembler_thinking_dropped_from_final() -> None:
    # thinking is progress (resets idle) but NOT part of the final message
    # (the decoder ignores non-text/tool_use blocks).
    asm = AnthropicStreamAssembler()
    asm.add(delta_from_anthropic_event({"type": "content_block_delta", "index": 0,
        "delta": {"type": "thinking_delta", "thinking": "reasoning..."}}))
    asm.add(delta_from_anthropic_event({"type": "content_block_delta", "index": 1,
        "delta": {"type": "text_delta", "text": "answer"}}))
    got = asm.build()
    assert got.content == "answer"
    assert "reasoning" not in str(got.additional_kwargs)
```

- [ ] **Step 2: Run to verify it fails** — `cd services/orchestrator && uv run python -m pytest tests/test_llm_streaming_wire_anthropic.py -q`. Expected: FAIL — `ImportError` on `delta_from_anthropic_event` / `AnthropicStreamAssembler`.

- [ ] **Step 3: Implement** — add to `_streaming.py`

```python
def delta_from_anthropic_event(event: Mapping[str, Any]) -> LLMDelta:
    """Map one Anthropic Messages SSE event to an :class:`LLMDelta`.
    Lenient: unknown / structural events (content_block_stop, message_stop,
    ping) yield an empty (no-progress) delta."""
    etype = event.get("type")
    if etype == "message_start":
        message = event.get("message")
        message = message if isinstance(message, Mapping) else {}
        usage = message.get("usage")
        model = message.get("model")
        return LLMDelta(
            usage=usage if isinstance(usage, Mapping) else None,
            model=model if isinstance(model, str) and model else None,
        )
    if etype == "content_block_start":
        cb = event.get("content_block")
        cb = cb if isinstance(cb, Mapping) else {}
        if cb.get("type") == "tool_use":
            idx = event.get("index")
            return LLMDelta(tool_calls=(ToolCallChunk(
                index=idx if isinstance(idx, int) else 0,
                id=str(cb["id"]) if cb.get("id") else None,
                name=str(cb["name"]) if cb.get("name") else None,
            ),))
        return LLMDelta()
    if etype == "content_block_delta":
        delta = event.get("delta")
        delta = delta if isinstance(delta, Mapping) else {}
        dtype = delta.get("type")
        if dtype == "text_delta":
            text = delta.get("text")
            return LLMDelta(content=text if isinstance(text, str) else "")
        if dtype == "thinking_delta":
            th = delta.get("thinking")
            return LLMDelta(reasoning=th if isinstance(th, str) else "")
        if dtype == "input_json_delta":
            idx = event.get("index")
            pj = delta.get("partial_json")
            return LLMDelta(tool_calls=(ToolCallChunk(
                index=idx if isinstance(idx, int) else 0,
                args_fragment=pj if isinstance(pj, str) else "",
            ),))
        return LLMDelta()
    if etype == "message_delta":
        delta = event.get("delta")
        delta = delta if isinstance(delta, Mapping) else {}
        stop = delta.get("stop_reason")
        usage = event.get("usage")
        return LLMDelta(
            finish_reason=stop if isinstance(stop, str) and stop else None,
            usage=usage if isinstance(usage, Mapping) else None,
        )
    return LLMDelta()


class AnthropicStreamAssembler:
    """Accumulate Anthropic :class:`LLMDelta` chunks into a synthetic
    Messages response body, then decode with the shared
    :func:`~orchestrator.llm.providers.anthropic._from_anthropic_response`
    (byte-identical to the non-streaming path). ``thinking`` is dropped
    (the decoder ignores it); usage from message_start + message_delta is
    merged."""

    def __init__(self) -> None:
        self._content: list[str] = []
        self._tools: dict[int, _ToolAcc] = {}
        self._tool_order: list[int] = []
        self._usage: dict[str, Any] = {}
        self._finish: str | None = None

    def add(self, delta: LLMDelta) -> None:
        if delta.content:
            self._content.append(delta.content)
        # reasoning intentionally dropped from the final message
        for tc in delta.tool_calls:
            acc = self._tools.get(tc.index)
            if acc is None:
                acc = _ToolAcc()
                self._tools[tc.index] = acc
                self._tool_order.append(tc.index)
            if tc.id is not None:
                acc.id = tc.id
            if tc.name is not None:
                acc.name = tc.name
            if tc.args_fragment:
                acc.args.append(tc.args_fragment)
        if delta.usage is not None:
            self._usage.update(delta.usage)   # MERGE (input from start, output from delta)
        if delta.finish_reason is not None:
            self._finish = delta.finish_reason

    def build(self, *, interrupted: bool = False) -> AIMessage:
        from orchestrator.llm.providers.anthropic import _from_anthropic_response

        blocks: list[dict[str, Any]] = []
        text = "".join(self._content)
        if text:
            blocks.append({"type": "text", "text": text})
        for idx in self._tool_order:
            acc = self._tools[idx]
            args_str = "".join(acc.args)
            if interrupted and not _is_valid_json_object(args_str):
                continue
            input_obj = json.loads(args_str) if _is_valid_json_object(args_str) else {}
            blocks.append({"type": "tool_use", "id": acc.id or "",
                           "name": acc.name or "", "input": input_obj})
        body: dict[str, Any] = {"content": blocks}
        if self._usage:
            body["usage"] = self._usage
        return _from_anthropic_response(body)
```

(`_ToolAcc` and `_is_valid_json_object` already exist in `_streaming.py` from P1 — reuse them.)

- [ ] **Step 4: Run to verify pass** — `cd services/orchestrator && uv run python -m pytest tests/test_llm_streaming_wire_anthropic.py tests/test_llm_streaming_wire.py -q`. Expected: PASS (new Anthropic wire tests + P1 OpenAI wire tests).

- [ ] **Step 5: Lint + commit**

```bash
cd services/orchestrator && uv run ruff check src/orchestrator/llm/providers/_streaming.py tests/test_llm_streaming_wire_anthropic.py && uv run ruff format src/orchestrator/llm/providers/_streaming.py tests/test_llm_streaming_wire_anthropic.py
git add services/orchestrator/src/orchestrator/llm/providers/_streaming.py services/orchestrator/tests/test_llm_streaming_wire_anthropic.py
git commit -m "feat(llm): Anthropic SSE event->LLMDelta parser + stream assembler"
```

---

### Task 3: `HTTPAnthropicClient.stream_messages` (SSE) + recording double

**Files:**
- Modify: `services/orchestrator/src/orchestrator/llm/providers/anthropic.py` (Protocol, HTTP client, recording client)
- Test: `services/orchestrator/tests/test_anthropic_client_stream.py`

**Interfaces:**
- Consumes: `classify_http_error`, `LLMNetworkError`, `LLMServerError`, `LLMError` (already imported).
- Produces: `AnthropicClient.stream_messages(...) -> AsyncIterator[Mapping[str, Any]]` yielding parsed event dicts; `RecordingAnthropicClient.stream_events` (consumed by T4).

**Anthropic SSE line format** differs from OpenAI: `event: <type>\ndata: <json>\n\n`. The `data:` JSON itself carries a `"type"` field, so we parse only `data:` lines (ignore `event:` lines) and dispatch on the JSON `type`. There is NO `[DONE]` sentinel — the stream ends after `message_stop` (or the byte stream closing). An in-band error is `data: {"type":"error","error":{...}}`.

- [ ] **Step 1: Write the failing test** — `tests/test_anthropic_client_stream.py`

```python
import json
from collections.abc import AsyncIterator

import httpx
import pytest

from expert_work.runtime.middleware import LLMClientError, LLMServerError
from orchestrator.llm.providers.anthropic import HTTPAnthropicClient, RecordingAnthropicClient


def _sse(*events: dict) -> bytes:
    parts = []
    for e in events:
        parts.append(f"event: {e['type']}\ndata: {json.dumps(e)}")
    return ("\n\n".join(parts) + "\n\n").encode()


async def _collect(client: HTTPAnthropicClient) -> list[dict]:
    return [dict(e) async for e in client.stream_messages(
        model="claude-x", system=None, messages=[{"role": "user", "content": "hi"}],
        tools=None, max_tokens=1024)]


@pytest.mark.asyncio
async def test_stream_yields_events_until_message_stop() -> None:
    body = _sse(
        {"type": "message_start", "message": {"usage": {"input_tokens": 3}}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hi"}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}},
        {"type": "message_stop"},
    )
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=body))
    client = HTTPAnthropicClient(api_key="k", base_url="https://x", transport=transport)
    events = await _collect(client)
    assert events[0]["type"] == "message_start"
    assert events[1]["delta"]["text"] == "Hi"
    assert events[-1]["type"] == "message_stop"


@pytest.mark.asyncio
async def test_stream_sets_stream_true_on_wire() -> None:
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured.update(json.loads(req.content))
        return httpx.Response(200, content=_sse({"type": "message_stop"}))

    transport = httpx.MockTransport(handler)
    client = HTTPAnthropicClient(api_key="k", base_url="https://x", transport=transport)
    await _collect(client)
    assert captured["stream"] is True


@pytest.mark.asyncio
async def test_stream_http_400_classifies_before_first_event() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(400, text="bad request"))
    client = HTTPAnthropicClient(api_key="k", base_url="https://x", transport=transport)
    with pytest.raises(LLMClientError):
        await _collect(client)


@pytest.mark.asyncio
async def test_stream_in_band_error_event_raises_after_good_events() -> None:
    raw = ('event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
           '"delta":{"type":"text_delta","text":"partial"}}\n\n'
           'event: error\ndata: {"type":"error","error":{"type":"overloaded_error","message":"overloaded"}}\n\n').encode()
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=raw))
    client = HTTPAnthropicClient(api_key="k", base_url="https://x", transport=transport)
    seen: list[str] = []
    with pytest.raises(LLMServerError):
        async for e in client.stream_messages(model="m", system=None,
                messages=[{"role": "user", "content": "hi"}], tools=None, max_tokens=16):
            if e.get("type") == "content_block_delta":
                seen.append(e["delta"]["text"])
    assert seen == ["partial"]


@pytest.mark.asyncio
async def test_recording_client_streams_canned_events() -> None:
    client = RecordingAnthropicClient(stream_events=[
        {"type": "message_start", "message": {"usage": {"input_tokens": 1}}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "a"}},
        {"type": "message_stop"}])
    out = [dict(e) async for e in client.stream_messages(model="m", system=None,
            messages=[{"role": "user", "content": "hi"}], tools=None, max_tokens=16)]
    assert [e["type"] for e in out] == ["message_start", "content_block_delta", "message_stop"]
    assert client.calls[-1]["stream"] is True
```

- [ ] **Step 2: Run to verify it fails** — `cd services/orchestrator && uv run python -m pytest tests/test_anthropic_client_stream.py -q`. Expected: FAIL — no `stream_messages`.

- [ ] **Step 3: Implement** — edits to `anthropic.py`

Add `AsyncIterator` to the `collections.abc` import. Extract the body assembly currently inline in `HTTPAnthropicClient.messages` (lines 156–174) into a module helper `_build_messages_body(...)` and call it from both `messages` and `stream_messages` (surgical: extract, keep the POST as-is). Add the streaming method to the Protocol (plain `def`, not `async def` — returns an async iterator, mirrors P1's `OpenAIClient.stream_chat_completions` fix), and to `HTTPAnthropicClient`:

```python
    async def stream_messages(
        self, *, model, system, messages, tools, max_tokens,
        temperature=None, thinking=None, output_config=None, betas=None, tool_choice=None,
    ) -> AsyncIterator[Mapping[str, Any]]:
        body = _build_messages_body(model=model, system=system, messages=messages, tools=tools,
            max_tokens=max_tokens, temperature=temperature, thinking=thinking,
            output_config=output_config, tool_choice=tool_choice)
        body["stream"] = True
        timeout = httpx.Timeout(self.timeout_s, read=None)  # router idle_timeout governs silence
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as client:
                async with client.stream("POST", f"{self.base_url}/v1/messages",
                    headers={"x-api-key": self.api_key, "anthropic-version": _ANTHROPIC_VERSION,
                             "content-type": "application/json",
                             **({"anthropic-beta": ",".join(betas)} if betas else {})},
                    json=body,
                ) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        raise classify_http_error("anthropic", response.status_code, _truncate(response.text))
                    async for line in response.aiter_lines():
                        event = _parse_anthropic_sse_line(line)
                        if event is None:
                            continue
                        if event.get("type") == "error":
                            raise _classify_anthropic_stream_error(event)
                        yield event
        except httpx.HTTPError as exc:
            raise LLMNetworkError(f"anthropic: {exc}") from exc
```

Module helpers:

```python
def _parse_anthropic_sse_line(line: str) -> dict[str, Any] | None:
    """Parse one Anthropic SSE line: only ``data:`` lines carry JSON (the
    JSON's own ``type`` field is authoritative; ``event:`` lines are
    ignored). Blank / comment / non-data / malformed lines → None."""
    line = line.strip()
    if not line.startswith("data:"):
        return None
    data = line[len("data:"):].strip()
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _classify_anthropic_stream_error(event: Mapping[str, Any]) -> LLMError:
    err = event.get("error")
    err = err if isinstance(err, Mapping) else {}
    message = str(err.get("message") or "")
    etype = str(err.get("type") or "")
    if "overloaded" in etype or "rate_limit" in etype:
        return classify_http_error("anthropic", 429, message)
    return LLMServerError(f"anthropic stream error: {etype}: {message}")
```

Add `stream_events` + `stream_messages` to `RecordingAnthropicClient` (synthesize from `response` when `stream_events` is empty — the P1 recording-double lesson):

```python
    stream_events: list[Mapping[str, Any]] = field(default_factory=list)
    # ... after messages() ...
    async def stream_messages(self, *, model, system, messages, tools, max_tokens,
        temperature=None, thinking=None, output_config=None, betas=None, tool_choice=None,
    ) -> AsyncIterator[Mapping[str, Any]]:
        self.calls.append({"model": model, "system": system, "messages": messages, "tools": tools,
            "max_tokens": max_tokens, "temperature": temperature, "thinking": thinking,
            "output_config": output_config, "betas": betas, "tool_choice": tool_choice, "stream": True})
        if self.raise_with is not None:
            raise self.raise_with
        events = list(self.stream_events) or _response_to_anthropic_events(self.response)
        for e in events:
            yield e
```

Synthesizer (module helper):

```python
def _response_to_anthropic_events(response: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Build a minimal event sequence from a whole Messages body, so a
    RecordingAnthropicClient primed with only ``response`` yields coherent
    deltas on the streaming path (the router prefers stream())."""
    blocks = response.get("content") or []
    usage = response.get("usage") if isinstance(response.get("usage"), Mapping) else {}
    events: list[Mapping[str, Any]] = [
        {"type": "message_start", "message": {"model": response.get("model"),
         "usage": {"input_tokens": usage.get("input_tokens", 0)}}}]
    if isinstance(blocks, list):
        for i, b in enumerate(blocks):
            if not isinstance(b, Mapping):
                continue
            if b.get("type") == "text":
                events.append({"type": "content_block_delta", "index": i,
                    "delta": {"type": "text_delta", "text": b.get("text", "")}})
            elif b.get("type") == "tool_use":
                events.append({"type": "content_block_start", "index": i,
                    "content_block": {"type": "tool_use", "id": b.get("id"), "name": b.get("name")}})
                events.append({"type": "content_block_delta", "index": i,
                    "delta": {"type": "input_json_delta", "partial_json": json.dumps(b.get("input") or {})}})
    events.append({"type": "message_delta", "delta": {"stop_reason": response.get("stop_reason")},
                   "usage": {"output_tokens": usage.get("output_tokens", 0)}})
    events.append({"type": "message_stop"})
    return events
```

- [ ] **Step 4: Run to verify pass** — `cd services/orchestrator && uv run python -m pytest tests/test_anthropic_client_stream.py tests/test_llm_provider_anthropic.py -q`. Expected: PASS — new stream tests pass; existing non-streaming Anthropic suite green (the `_build_messages_body` extraction is behavior-preserving).

- [ ] **Step 5: Lint + commit**

```bash
cd services/orchestrator && uv run ruff check src/orchestrator/llm/providers/anthropic.py tests/test_anthropic_client_stream.py && uv run ruff format src/orchestrator/llm/providers/anthropic.py tests/test_anthropic_client_stream.py
git add services/orchestrator/src/orchestrator/llm/providers/anthropic.py services/orchestrator/tests/test_anthropic_client_stream.py
git commit -m "feat(llm): HTTPAnthropicClient.stream_messages (Anthropic SSE) + recording double"
```

---

### Task 4: `AnthropicProvider.stream()` + `new_stream_assembler()`

**Files:**
- Modify: `services/orchestrator/src/orchestrator/llm/providers/anthropic.py` (`AnthropicProvider`)
- Test: `services/orchestrator/tests/test_llm_provider_anthropic_stream.py`

**Interfaces:**
- Consumes: `_streaming.{LLMDelta, delta_from_anthropic_event, AnthropicStreamAssembler}`; `stream_messages` (T3).
- Produces: `AnthropicProvider.stream()` + `new_stream_assembler()`. `complete()` unchanged (non-streaming + `_extract_structured_response`); both share `_prepare_anthropic_request`.

- [ ] **Step 1: Write the failing test** — `tests/test_llm_provider_anthropic_stream.py`

```python
import pytest
from langchain_core.messages import HumanMessage

from orchestrator.llm.providers._streaming import AnthropicStreamAssembler
from orchestrator.llm.providers.anthropic import AnthropicProvider, RecordingAnthropicClient


def _text_events() -> list[dict]:
    return [
        {"type": "message_start", "message": {"model": "claude-x", "usage": {"input_tokens": 3}}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hel"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "lo"}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}},
        {"type": "message_stop"},
    ]


@pytest.mark.asyncio
async def test_stream_yields_deltas() -> None:
    client = RecordingAnthropicClient(stream_events=_text_events())
    provider = AnthropicProvider(client=client, model="claude-x", max_tokens=1024)
    out = [d async for d in provider.stream(messages=[HumanMessage(content="hi")], tools=[])]
    assert "".join(d.content for d in out if d.content) == "Hello"
    assert client.calls[-1]["stream"] is True


@pytest.mark.asyncio
async def test_stream_then_assemble_equals_complete() -> None:
    whole = {"content": [{"type": "text", "text": "Hello"}], "model": "claude-x",
             "usage": {"input_tokens": 3, "output_tokens": 2}}
    complete_p = AnthropicProvider(client=RecordingAnthropicClient(response=whole),
                                   model="claude-x", max_tokens=1024)
    expected = await complete_p.complete(messages=[HumanMessage(content="hi")], tools=[])

    stream_p = AnthropicProvider(client=RecordingAnthropicClient(stream_events=_text_events()),
                                 model="claude-x", max_tokens=1024)
    asm = stream_p.new_stream_assembler()
    assert isinstance(asm, AnthropicStreamAssembler)
    async for d in stream_p.stream(messages=[HumanMessage(content="hi")], tools=[]):
        asm.add(d)
    got = asm.build()
    assert got.content == expected.content
    assert got.usage_metadata == expected.usage_metadata
    assert got.tool_calls == expected.tool_calls
```

- [ ] **Step 2: Run to verify it fails** — `cd services/orchestrator && uv run python -m pytest tests/test_llm_provider_anthropic_stream.py -q`. Expected: FAIL — no `stream` on `AnthropicProvider`.

- [ ] **Step 3: Implement** — edits to `AnthropicProvider`

Add the streaming imports at the top of `anthropic.py`:

```python
from orchestrator.llm.providers._streaming import (
    AnthropicStreamAssembler,
    LLMDelta,
    delta_from_anthropic_event,
)
```

Extract the request assembly currently inline in `complete()` (lines 314–412, the coalesce → map → tool/cache/thinking → the `client.messages(...)` kwargs) into `_prepare_anthropic_request(self, *, messages, tools, output_schema, use_native) -> dict[str, Any]` returning the kwargs dict (model/system/messages/tools/max_tokens/temperature/thinking/output_config/betas/tool_choice). `complete()` keeps its exact behavior (calls `self.client.messages(**request)`, HX-13 fallback, `_from_anthropic_response`, `_extract_structured_response`). Add `stream()` + `new_stream_assembler()`:

```python
    def new_stream_assembler(self) -> AnthropicStreamAssembler:
        return AnthropicStreamAssembler()

    async def stream(
        self, *, messages: Sequence[BaseMessage], tools: Sequence[ToolSpec],
        output_schema: StructuredOutputSpec | None = None,
    ) -> AsyncIterator[Mapping[str, Any]]:  # -> AsyncIterator[LLMDelta]
        # The router never drives the stream for structured output (it routes
        # output_schema to complete()); a defensive assert documents that.
        assert output_schema is None  # noqa: S101 - structured output uses complete()
        use_native = (
            any(spec.defer_loading for spec in tools) and not self._native_search_disabled
        )
        request = await self._prepare_anthropic_request(
            messages=messages, tools=tools, output_schema=None, use_native=use_native)
        try:
            async for event in self.client.stream_messages(**request):
                yield delta_from_anthropic_event(event)
            return
        except LLMClientError:
            if not use_native:
                raise
            self._native_search_disabled = True
            disclosure_fallback_total.labels(provider="anthropic").inc()
            logger.warning("anthropic.tool_search_beta_rejected — falling back to app tier")
        retry = await self._prepare_anthropic_request(
            messages=messages, tools=tools, output_schema=None, use_native=False)
        async for event in self.client.stream_messages(**retry):
            yield delta_from_anthropic_event(event)
```

(Type the return as `AsyncIterator[LLMDelta]`; the annotation above shows the yield type. `_prepare_anthropic_request` builds `betas=[_TOOL_SEARCH_BETA] if use_native else None` and, since `output_schema is None` here, `tool_choice=None`, `thinking`/`output_config` per the non-structured branch.)

- [ ] **Step 4: Run to verify pass** — `cd services/orchestrator && uv run python -m pytest tests/test_llm_provider_anthropic_stream.py tests/test_llm_provider_anthropic.py -q`. Expected: PASS — streaming tests + existing non-streaming Anthropic suite (behavior-preserving `_prepare_anthropic_request` extraction).

- [ ] **Step 5: Lint + commit**

```bash
cd services/orchestrator && uv run ruff check src/orchestrator/llm/providers/anthropic.py tests/test_llm_provider_anthropic_stream.py && uv run ruff format src/orchestrator/llm/providers/anthropic.py tests/test_llm_provider_anthropic_stream.py
git add services/orchestrator/src/orchestrator/llm/providers/anthropic.py services/orchestrator/tests/test_llm_provider_anthropic_stream.py
git commit -m "feat(llm): AnthropicProvider.stream() + new_stream_assembler sharing _prepare_anthropic_request"
```

---

### Task 5: Integration regression + mypy/ruff gate

**Files:** none new — verification gate.

- [ ] **Step 1: Full affected suites**

```bash
cd services/orchestrator && export DOCKER_HOST="unix:///Users/mac/.docker/run/docker.sock"
uv run python -m pytest tests -q -k "anthropic or openai or router or streaming or rate_limit or structured_output"
```
Expected: PASS. Any failure → owning task (do not patch tests to pass).

- [ ] **Step 2: mypy (CI-equivalent scope)**

```bash
cd /Users/mac/src/github/jone_qian/expert-work
uv run mypy services/orchestrator/src/orchestrator/llm packages/expert-work-runtime/src/expert_work/runtime/middleware packages/expert-work-protocol/src/expert_work/protocol
```
Expected: 0 errors. Likely fixups: `AsyncIterator` return annotations on the async generators; `# type: ignore[attr-defined]` on the `.new_stream_assembler()` / `.stream(...)` duck-typed calls; Protocol methods returning an async iterator must be plain `def` (not `async def`) — same as P1.

- [ ] **Step 3: Full orchestrator suite + ruff**

```bash
cd services/orchestrator && export DOCKER_HOST="unix:///Users/mac/.docker/run/docker.sock" && uv run python -m pytest tests -q
cd /Users/mac/src/github/jone_qian/expert-work
uv run ruff check services/orchestrator && uv run ruff format --check services/orchestrator/src/orchestrator/llm
```
Expected: full suite green (1 pre-existing docx skip), ruff clean.

- [ ] **Step 4: Commit any fixups**

```bash
git add -A && git commit -m "chore(llm): mypy + ruff fixups for Anthropic streaming P1'"
```

---

## Verification (maps to spec P1')

| Requirement | Covered by |
| --- | --- |
| Anthropic SSE parse (message_start/content_block/message_delta) | T2 delta tests |
| tool_use reassembly (input_json_delta) | T2 `test_assembler_reassembles_tool_use` |
| thinking → progress but dropped from final | T2 `test_assembler_thinking_dropped_from_final` |
| usage merged from two events | T2 `test_assembler_text_matches_decoder` (input+output) |
| byte-equal to `_from_anthropic_response` | T2 assembler-vs-decoder; T4 `test_stream_then_assemble_equals_complete` |
| in-band error after good events | T3 `test_stream_in_band_error_event_raises_after_good_events` |
| 400 before first event | T3 `test_stream_http_400_classifies_before_first_event` |
| stream=true on wire | T3 `test_stream_sets_stream_true_on_wire` |
| router uses provider's assembler | T1 `test_drive_stream_uses_provider_assembler` |
| structured output non-streaming | T1 `test_structured_output_uses_non_streaming_path` |
| buffer-until-first-token / idle timeout | inherited from P1 router (unchanged); T5 broad suite |
| recording double no-empty-stream | T3 `test_recording_client_streams_canned_events` + T4 equivalence via synthesizer |

## Out of Scope (later phases)

- Token SSE frames + external API + streaming redaction (P2a).
- Playground live tokens (P2b).
- Streaming structured output (deliberately non-streaming — see Global Constraints).
- Extracting a shared `_decode` module to remove the deferred-import cycle (P1 chose "resolve as by-design"; unchanged here).

## Self-Review Notes

- **Spec coverage:** every P1' bullet (separate parser, same assembly contract, router integration, distinct wire format + error mapping) maps to a task (see table). The generalization (T1) is required because P1's `_drive_stream` hard-coded `OpenAIStreamAssembler` — Anthropic could not otherwise plug in.
- **Type consistency:** `StreamAssembler`, `new_stream_assembler`, `delta_from_anthropic_event`, `AnthropicStreamAssembler` names are identical across the Interfaces block and every consumer. `stream_messages` signature matches across Protocol / HTTP / Recording.
- **Byte-equality:** both the assembler-vs-decoder test (T2) and the stream-vs-complete equivalence test (T4) pin it; the assembler reuses the exact `_from_anthropic_response` decoder.
- **No placeholders:** all code steps carry real code; test steps carry real assertions.
