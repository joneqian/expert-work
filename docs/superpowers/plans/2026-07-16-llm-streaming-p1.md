# P1 — OpenAI-wire Internal Streaming + Idle Timeout — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the OpenAI-wire provider a real token stream and drive it from the router with a two-threshold idle timeout, so the deadline stops mis-firing on healthy-but-slow generations — with **zero** external/UI contract change (`complete()` still returns a whole `AIMessage`).

**Architecture:** The OpenAI-wire HTTP client gains a `stream_chat_completions` method (`stream=true` SSE). A new `_streaming.py` module normalizes vendor SSE chunks into `LLMDelta` objects and re-assembles them — via the *existing* `_from_openai_response` decoder — into an `AIMessage` byte-identical to today's non-streaming result. `OpenAIProvider.stream()` exposes the delta stream; `OpenAIProvider.complete()` now drains it internally. The router detects streaming-capable providers, drives two timers (`first_token_timeout_s` until the first progress delta → fallback-eligible; `idle_timeout_s` between deltas → ends the turn with the partial), and falls back to the legacy single-`asyncio.wait_for` path for non-streaming providers (Anthropic, test doubles) until P1'.

**Tech Stack:** Python 3.12, httpx streaming (`client.stream`), LangChain `AIMessage`, pytest with `httpx.MockTransport`, ruff + mypy.

## Global Constraints

- **Backward compatible.** `provider.complete()` still returns a whole `AIMessage`; the router, middleware, and graph see the same return type. The assembled `AIMessage` MUST be byte-equal to today's non-streaming result for the same vendor JSON (guarded by a regression fixture that feeds the same content as one non-streaming body and as a chunk sequence).
- **Deadline strictly more permissive.** Reinterpreting the stored `stream_deadline_s=180` as a first-token budget must never newly kill a run that passes today.
- **Fallback policy fixed:** buffer-until-first-token. Before the first *progress* delta (content / reasoning / tool-call fragment) a stall or error is retryable → fall over to the next provider. After the first progress delta the provider is committed: an idle stall ends the turn with the partial output; an in-band error / broken stream raises a terminal (non-retryable, no-fallback) error.
- **Cache hits do not reach the router.** The agent node short-circuits on `ctx.payload["llm_cache_hit"]` (`graph_builder/builder.py:730`) before invoking the router, so no synthesized "one-delta stream" is needed. Do not add cache handling to the streaming path.
- **Only OpenAI-wire in P1.** Implementing `OpenAIProvider.stream()` + `HTTPOpenAIClient.stream_chat_completions` covers `openai`, `azure`, and the compat vendors `kimi`/`glm`/`deepseek`/`qwen`/`doubao` (all ride `OpenAIProvider` over `HTTPOpenAIClient`, see `providers/openai_compatible.py`). Anthropic is P1'.
- Repo conventions: many small files, immutable updates, ruff + mypy clean, per-vendor unit tests with a mocked SSE transport, no direct commits to `main`, squash-merge. Tests that touch Docker/SQL need `export DOCKER_HOST="unix:///Users/mac/.docker/run/docker.sock"` (not applicable to this pure-Python plan). Run orchestrator tests from `services/orchestrator`.

---

## File Structure

- **Create** `services/orchestrator/src/orchestrator/llm/providers/_streaming.py` — SSE-normalized delta types (`LLMDelta`, `ToolCallChunk`), OpenAI chunk→delta mapping, the `OpenAIStreamAssembler` (delegates final decode to `openai._from_openai_response`), and the `StreamingLLMProvider` Protocol + `supports_streaming` unwrap helper. One responsibility: the streaming wire model, independent of transport and router.
- **Modify** `services/orchestrator/src/orchestrator/llm/providers/openai.py` — add `stream_chat_completions` to the `OpenAIClient` Protocol, `HTTPOpenAIClient`, and `RecordingOpenAIClient`; add `OpenAIProvider.stream()`; refactor `OpenAIProvider.complete()` to drain the stream.
- **Modify** `services/orchestrator/src/orchestrator/llm/rate_limit.py` — add `RateLimitedProvider.stream()` (admission gate + delegate).
- **Modify** `packages/expert-work-runtime/src/expert_work/runtime/middleware/llm_error_handling.py` — add `LLMStreamInterruptedError` (terminal, non-retryable).
- **Modify** `packages/expert-work-runtime/src/expert_work/runtime/middleware/__init__.py` — export `LLMStreamInterruptedError`.
- **Modify** `services/orchestrator/src/orchestrator/llm/router.py` — rename `stream_deadline_s` → `first_token_timeout_s`, add `idle_timeout_s`, add `_drive_stream` / `_invoke_provider`, refactor `_invoke_once`, add the post-first-token no-fallback branch in `__call__`.
- **Modify** `packages/expert-work-protocol/src/expert_work/protocol/agent_spec.py` — reinterpret `stream_deadline_s` docstring (first-token budget), add `idle_timeout_s`.
- **Modify** `packages/expert-work-protocol/src/expert_work/protocol/agent_template_resolve.py` — add `idle_timeout_s` field tier.
- **Modify** `services/orchestrator/src/orchestrator/agent_factory.py` — rename `build_llm_router` param, thread `idle_timeout_s` through `build_step_routers` / escalated / VL callers; add `_chat_idle_timeout_s`.
- **Modify** tests: `services/orchestrator/tests/test_llm_router.py`, `test_middleware_chain_wiring.py` (update `stream_deadline_s=` → `first_token_timeout_s=`), and new test files per task.

## Interfaces (locked once, consumed across tasks)

```python
# _streaming.py — the streaming wire model
@dataclass(frozen=True)
class ToolCallChunk:
    index: int
    id: str | None = None
    name: str | None = None
    args_fragment: str = ""

@dataclass(frozen=True)
class LLMDelta:
    content: str = ""
    reasoning: str = ""
    tool_calls: tuple[ToolCallChunk, ...] = ()
    finish_reason: str | None = None
    usage: Mapping[str, Any] | None = None
    model: str | None = None
    system_fingerprint: str | None = None

    @property
    def has_progress(self) -> bool:
        return bool(self.content or self.reasoning or self.tool_calls)

def delta_from_openai_chunk(chunk: Mapping[str, Any]) -> LLMDelta: ...

class OpenAIStreamAssembler:
    def add(self, delta: LLMDelta) -> None: ...
    def build(self, *, interrupted: bool = False) -> AIMessage: ...   # via openai._from_openai_response

@runtime_checkable
class StreamingLLMProvider(Protocol):
    def stream(
        self, *, messages: Sequence[BaseMessage], tools: Sequence[ToolSpec],
        output_schema: StructuredOutputSpec | None = None,
    ) -> AsyncIterator[LLMDelta]: ...

def supports_streaming(provider: object) -> bool:   # unwraps `.inner` then isinstance
    ...

# OpenAIClient Protocol / HTTPOpenAIClient / RecordingOpenAIClient gain:
def stream_chat_completions(
    self, *, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None,
    temperature: float | None = None, extra_body: dict[str, Any] | None = None,
    tool_choice: dict[str, Any] | None = None, response_format: dict[str, Any] | None = None,
) -> AsyncIterator[Mapping[str, Any]]: ...   # yields parsed JSON chunks, stops at [DONE]

# llm_error_handling.py
class LLMStreamInterruptedError(LLMError):   # NOT a LLMServerError → not retryable
    ...

# LLMRouter fields
first_token_timeout_s: float | None = None
idle_timeout_s: float | None = None
```

---

### Task 1: Streaming wire model — `_streaming.py`

**Files:**
- Create: `services/orchestrator/src/orchestrator/llm/providers/_streaming.py`
- Test: `services/orchestrator/tests/test_llm_streaming_wire.py`

**Interfaces:**
- Produces: `LLMDelta`, `ToolCallChunk`, `delta_from_openai_chunk`, `OpenAIStreamAssembler`, `StreamingLLMProvider`, `supports_streaming` (all consumed by Tasks 2–5).
- Consumes: `openai._from_openai_response` (the existing non-streaming decoder — the assembler delegates to it so assembly and non-streaming decode are one code path).

- [ ] **Step 1: Write the failing test** — `tests/test_llm_streaming_wire.py`

```python
from collections.abc import AsyncIterator
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from orchestrator.llm.providers._streaming import (
    LLMDelta,
    OpenAIStreamAssembler,
    StreamingLLMProvider,
    ToolCallChunk,
    delta_from_openai_chunk,
    supports_streaming,
)
from orchestrator.llm.providers.openai import _from_openai_response


def _chunk(delta: dict[str, Any], *, finish: str | None = None, **top: Any) -> dict[str, Any]:
    return {"choices": [{"delta": delta, "finish_reason": finish}], **top}


def test_delta_content_and_progress() -> None:
    d = delta_from_openai_chunk(_chunk({"content": "Hel"}))
    assert d.content == "Hel"
    assert d.has_progress is True


def test_delta_role_only_is_not_progress() -> None:
    d = delta_from_openai_chunk(_chunk({"role": "assistant"}))
    assert d.content == ""
    assert d.reasoning == ""
    assert d.tool_calls == ()
    assert d.has_progress is False


def test_delta_reasoning_is_progress() -> None:
    d = delta_from_openai_chunk(_chunk({"reasoning_content": "thinking"}))
    assert d.reasoning == "thinking"
    assert d.has_progress is True


def test_delta_tool_call_fragment() -> None:
    d = delta_from_openai_chunk(
        _chunk({"tool_calls": [{"index": 0, "id": "call_1",
                                "function": {"name": "search", "arguments": '{"q":'}}]})
    )
    assert d.tool_calls == (ToolCallChunk(index=0, id="call_1", name="search", args_fragment='{"q":'),)
    assert d.has_progress is True


def test_delta_final_chunk_usage_and_finish() -> None:
    d = delta_from_openai_chunk(
        _chunk({}, finish="stop", usage={"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
               model="glm-5.2", system_fingerprint="fp_1")
    )
    assert d.finish_reason == "stop"
    assert d.usage == {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8}
    assert d.model == "glm-5.2"
    assert d.system_fingerprint == "fp_1"


def test_assembler_text_matches_non_streaming_decoder() -> None:
    # The regression guarantee: the same content assembled from deltas must
    # byte-equal the AIMessage the non-streaming decoder produces.
    body = {
        "choices": [{"message": {"role": "assistant", "content": "Hello world",
                                 "reasoning_content": "let me think"},
                     "finish_reason": "stop"}],
        "model": "glm-5.2",
        "system_fingerprint": "fp_1",
        "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
    }
    expected = _from_openai_response(body)

    asm = OpenAIStreamAssembler()
    asm.add(delta_from_openai_chunk(_chunk({"role": "assistant"})))
    asm.add(delta_from_openai_chunk(_chunk({"reasoning_content": "let me think"})))
    asm.add(delta_from_openai_chunk(_chunk({"content": "Hello "})))
    asm.add(delta_from_openai_chunk(_chunk({"content": "world"})))
    asm.add(delta_from_openai_chunk(
        _chunk({}, finish="stop", model="glm-5.2", system_fingerprint="fp_1",
               usage={"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8})))
    got = asm.build()

    assert got.content == expected.content
    assert got.additional_kwargs == expected.additional_kwargs
    assert got.response_metadata == expected.response_metadata
    assert got.usage_metadata == expected.usage_metadata
    assert got.tool_calls == expected.tool_calls


def test_assembler_reassembles_tool_call_fragments() -> None:
    body = {"choices": [{"message": {"role": "assistant", "content": None,
                                     "tool_calls": [{"id": "call_1", "type": "function",
                                                     "function": {"name": "search",
                                                                  "arguments": '{"q": "hi"}'}}]},
                         "finish_reason": "tool_calls"}]}
    expected = _from_openai_response(body)

    asm = OpenAIStreamAssembler()
    asm.add(delta_from_openai_chunk(
        _chunk({"tool_calls": [{"index": 0, "id": "call_1",
                                "function": {"name": "search", "arguments": '{"q": '}}]})))
    asm.add(delta_from_openai_chunk(
        _chunk({"tool_calls": [{"index": 0, "function": {"arguments": '"hi"}'}}]}, finish="tool_calls")))
    got = asm.build()
    assert got.tool_calls == expected.tool_calls


def test_assembler_interrupted_drops_incomplete_tool_call() -> None:
    # A tool-args fragment that never completed valid JSON must not become a
    # dispatchable tool call when the stream is interrupted mid-args.
    asm = OpenAIStreamAssembler()
    asm.add(delta_from_openai_chunk(_chunk({"content": "partial answer"})))
    asm.add(delta_from_openai_chunk(
        _chunk({"tool_calls": [{"index": 0, "id": "call_1",
                                "function": {"name": "search", "arguments": '{"q": '}}]})))
    got = asm.build(interrupted=True)
    assert got.content == "partial answer"
    assert got.tool_calls == []
    assert got.response_metadata.get("finish_reason") == "stream_idle_timeout"


def test_supports_streaming_true_for_streaming_provider() -> None:
    class _Streamer:
        async def stream(self, **_: Any) -> AsyncIterator[LLMDelta]:
            if False:
                yield LLMDelta()

    class _Wrapper:  # mimics RateLimitedProvider.inner unwrapping
        def __init__(self, inner: Any) -> None:
            self.inner = inner

    assert isinstance(_Streamer(), StreamingLLMProvider)
    assert supports_streaming(_Streamer()) is True
    assert supports_streaming(_Wrapper(_Streamer())) is True


def test_supports_streaming_false_for_plain_provider() -> None:
    class _Plain:
        async def complete(self, **_: Any) -> AIMessage:
            return AIMessage(content="")

    assert supports_streaming(_Plain()) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/orchestrator && python -m pytest tests/test_llm_streaming_wire.py -q`
Expected: FAIL — `ModuleNotFoundError: orchestrator.llm.providers._streaming`.

- [ ] **Step 3: Write minimal implementation** — `_streaming.py`

```python
"""Streaming wire model for OpenAI Chat Completions SSE — Stream L (P1).

Normalizes the vendor's ``stream=true`` SSE chunks into transport- and
router-agnostic :class:`LLMDelta` objects, and re-assembles a delta
sequence into a :class:`AIMessage` via the SAME decoder the non-streaming
path uses (:func:`orchestrator.llm.providers.openai._from_openai_response`)
— so an assembled message is byte-identical to today's whole-response
result for the same vendor content.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from langchain_core.messages import AIMessage, BaseMessage

from expert_work.protocol import StructuredOutputSpec
from orchestrator.tools.registry import ToolSpec


@dataclass(frozen=True)
class ToolCallChunk:
    """One OpenAI streaming tool-call fragment, keyed by ``index``.

    ``id`` and ``name`` arrive once (first fragment for that index);
    ``args_fragment`` accumulates across fragments into the full JSON
    argument string.
    """

    index: int
    id: str | None = None
    name: str | None = None
    args_fragment: str = ""


@dataclass(frozen=True)
class LLMDelta:
    """One normalized SSE chunk. ``has_progress`` marks a chunk that
    carries real generation (content / reasoning / a tool-call fragment)
    — the router's first-token detection ignores role-only / usage-only
    chunks."""

    content: str = ""
    reasoning: str = ""
    tool_calls: tuple[ToolCallChunk, ...] = ()
    finish_reason: str | None = None
    usage: Mapping[str, Any] | None = None
    model: str | None = None
    system_fingerprint: str | None = None

    @property
    def has_progress(self) -> bool:
        return bool(self.content or self.reasoning or self.tool_calls)


def delta_from_openai_chunk(chunk: Mapping[str, Any]) -> LLMDelta:
    """Map one parsed OpenAI streaming chunk to an :class:`LLMDelta`.

    Tolerant of missing keys — malformed chunks degrade to an empty
    (no-progress) delta rather than raising, mirroring the lenient
    non-streaming decoder.
    """
    choices = chunk.get("choices") or []
    delta: Mapping[str, Any] = {}
    finish_reason: str | None = None
    if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
        raw_delta = choices[0].get("delta")
        if isinstance(raw_delta, Mapping):
            delta = raw_delta
        fr = choices[0].get("finish_reason")
        if isinstance(fr, str) and fr:
            finish_reason = fr

    content = delta.get("content")
    reasoning = delta.get("reasoning_content")
    tool_calls: list[ToolCallChunk] = []
    raw_tcs = delta.get("tool_calls")
    if isinstance(raw_tcs, list):
        for tc in raw_tcs:
            if not isinstance(tc, Mapping):
                continue
            fn = tc.get("function") if isinstance(tc.get("function"), Mapping) else {}
            idx = tc.get("index")
            tool_calls.append(
                ToolCallChunk(
                    index=idx if isinstance(idx, int) else 0,
                    id=str(tc["id"]) if tc.get("id") else None,
                    name=str(fn["name"]) if fn.get("name") else None,
                    args_fragment=str(fn.get("arguments") or ""),
                )
            )

    usage = chunk.get("usage")
    model = chunk.get("model")
    fingerprint = chunk.get("system_fingerprint")
    return LLMDelta(
        content=content if isinstance(content, str) else "",
        reasoning=reasoning if isinstance(reasoning, str) else "",
        tool_calls=tuple(tool_calls),
        finish_reason=finish_reason,
        usage=usage if isinstance(usage, Mapping) else None,
        model=model if isinstance(model, str) and model else None,
        system_fingerprint=fingerprint if isinstance(fingerprint, str) and fingerprint else None,
    )


class _ToolAcc:
    __slots__ = ("id", "name", "args")

    def __init__(self) -> None:
        self.id: str | None = None
        self.name: str | None = None
        self.args: list[str] = []


class OpenAIStreamAssembler:
    """Accumulate :class:`LLMDelta` chunks into a synthetic non-streaming
    body, then decode with the shared
    :func:`~orchestrator.llm.providers.openai._from_openai_response` so the
    result is byte-identical to the whole-response path."""

    def __init__(self) -> None:
        self._content: list[str] = []
        self._reasoning: list[str] = []
        self._tools: dict[int, _ToolAcc] = {}
        self._tool_order: list[int] = []
        self._usage: Mapping[str, Any] | None = None
        self._model: str | None = None
        self._fingerprint: str | None = None
        self._finish: str | None = None

    def add(self, delta: LLMDelta) -> None:
        if delta.content:
            self._content.append(delta.content)
        if delta.reasoning:
            self._reasoning.append(delta.reasoning)
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
            self._usage = delta.usage
        if delta.model is not None:
            self._model = delta.model
        if delta.system_fingerprint is not None:
            self._fingerprint = delta.system_fingerprint
        if delta.finish_reason is not None:
            self._finish = delta.finish_reason

    def build(self, *, interrupted: bool = False) -> AIMessage:
        # Deferred import breaks the openai <-> _streaming import cycle.
        from orchestrator.llm.providers.openai import _from_openai_response

        content = "".join(self._content)
        tool_calls: list[dict[str, Any]] = []
        for idx in self._tool_order:
            acc = self._tools[idx]
            args_str = "".join(acc.args)
            if interrupted and not _is_valid_json_object(args_str):
                # A tool call whose arguments never completed cannot be
                # safely dispatched — drop it from the interrupted result.
                continue
            tool_calls.append(
                {
                    "id": acc.id or "",
                    "type": "function",
                    "function": {"name": acc.name or "", "arguments": args_str},
                }
            )

        message: dict[str, Any] = {"role": "assistant"}
        message["content"] = content if content or not tool_calls else None
        if self._reasoning:
            message["reasoning_content"] = "".join(self._reasoning)
        if tool_calls:
            message["tool_calls"] = tool_calls

        finish = self._finish or ("stream_idle_timeout" if interrupted else None)
        choice: dict[str, Any] = {"message": message, "finish_reason": finish}
        body: dict[str, Any] = {"choices": [choice]}
        if self._model:
            body["model"] = self._model
        if self._fingerprint:
            body["system_fingerprint"] = self._fingerprint
        if self._usage is not None:
            body["usage"] = self._usage
        return _from_openai_response(body)


def _is_valid_json_object(raw: str) -> bool:
    if not raw:
        return False
    try:
        return isinstance(json.loads(raw), dict)
    except json.JSONDecodeError:
        return False


@runtime_checkable
class StreamingLLMProvider(Protocol):
    """A provider that can yield :class:`LLMDelta` chunks. The router
    drives its idle-timeout over this; providers without it use the
    legacy single-deadline ``complete()`` path."""

    def stream(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
        output_schema: StructuredOutputSpec | None = None,
    ) -> AsyncIterator[LLMDelta]: ...


def supports_streaming(provider: object) -> bool:
    """Whether the innermost provider can stream.

    Unwraps admission/wrapper layers that expose ``.inner`` (e.g.
    :class:`RateLimitedProvider`) so the check reflects the real adapter,
    not the wrapper. Avoids importing ``RateLimitedProvider`` here (that
    module imports the router) — a duck-typed ``.inner`` walk keeps this
    module dependency-light.
    """
    seen: set[int] = set()
    p: Any = provider
    while hasattr(p, "inner") and id(p) not in seen:
        seen.add(id(p))
        p = p.inner
    return isinstance(p, StreamingLLMProvider)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/orchestrator && python -m pytest tests/test_llm_streaming_wire.py -q`
Expected: PASS (10 tests).

- [ ] **Step 5: Lint + commit**

```bash
cd services/orchestrator && ruff check src/orchestrator/llm/providers/_streaming.py tests/test_llm_streaming_wire.py && ruff format src/orchestrator/llm/providers/_streaming.py tests/test_llm_streaming_wire.py
git add services/orchestrator/src/orchestrator/llm/providers/_streaming.py services/orchestrator/tests/test_llm_streaming_wire.py
git commit -m "feat(llm): OpenAI-wire streaming delta model + assembler"
```

---

### Task 2: HTTP client streaming — `stream_chat_completions`

**Files:**
- Modify: `services/orchestrator/src/orchestrator/llm/providers/openai.py` (Protocol `OpenAIClient`, `HTTPOpenAIClient`, `RecordingOpenAIClient`)
- Test: `services/orchestrator/tests/test_openai_client_stream.py`

**Interfaces:**
- Consumes: `classify_http_error`, `LLMNetworkError`, `LLMServerError` (already imported in `openai.py`).
- Produces: `OpenAIClient.stream_chat_completions(...) -> AsyncIterator[Mapping[str, Any]]` yielding parsed JSON chunks, stopping at `[DONE]`; `RecordingOpenAIClient.stream_chunks` field for tests (consumed by Task 3).

- [ ] **Step 1: Write the failing test** — `tests/test_openai_client_stream.py`

```python
import json
from collections.abc import AsyncIterator

import httpx
import pytest

from expert_work.runtime.middleware import LLMClientError, LLMServerError
from orchestrator.llm.providers.openai import HTTPOpenAIClient, RecordingOpenAIClient


def _sse(*objs: dict) -> bytes:
    lines = [f"data: {json.dumps(o)}" for o in objs] + ["data: [DONE]"]
    return ("\n\n".join(lines) + "\n\n").encode()


async def _collect(client: HTTPOpenAIClient) -> list[dict]:
    return [
        dict(c)
        async for c in client.stream_chat_completions(
            model="glm-5.2", messages=[{"role": "user", "content": "hi"}], tools=None
        )
    ]


@pytest.mark.asyncio
async def test_stream_yields_chunks_and_stops_at_done() -> None:
    body = _sse(
        {"choices": [{"delta": {"content": "Hel"}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]},
        {"choices": [{"delta": {}}], "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}},
    )
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=body))
    client = HTTPOpenAIClient(api_key="k", base_url="https://x", transport=transport)
    chunks = await _collect(client)
    assert [c["choices"][0]["delta"].get("content") for c in chunks[:2]] == ["Hel", "lo"]
    assert chunks[-1]["usage"]["total_tokens"] == 3


@pytest.mark.asyncio
async def test_stream_skips_keepalive_comments() -> None:
    raw = (": keep-alive\n\n"
           'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'
           "data: [DONE]\n\n").encode()
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=raw))
    client = HTTPOpenAIClient(api_key="k", base_url="https://x", transport=transport)
    chunks = await _collect(client)
    assert len(chunks) == 1
    assert chunks[0]["choices"][0]["delta"]["content"] == "x"


@pytest.mark.asyncio
async def test_stream_http_400_classifies_before_first_chunk() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(400, text="bad request"))
    client = HTTPOpenAIClient(api_key="k", base_url="https://x", transport=transport)
    with pytest.raises(LLMClientError):
        await _collect(client)


@pytest.mark.asyncio
async def test_stream_in_band_error_event_raises() -> None:
    raw = ('data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
           'data: {"error":{"message":"upstream exploded","type":"server_error"}}\n\n').encode()
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=raw))
    client = HTTPOpenAIClient(api_key="k", base_url="https://x", transport=transport)
    seen: list[str] = []
    with pytest.raises(LLMServerError):
        async for c in client.stream_chat_completions(
            model="m", messages=[{"role": "user", "content": "hi"}], tools=None
        ):
            seen.append(c["choices"][0]["delta"].get("content", ""))
    assert seen == ["partial"]  # the good chunk was delivered before the error


@pytest.mark.asyncio
async def test_stream_sets_stream_true_and_include_usage() -> None:
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured.update(json.loads(req.content))
        return httpx.Response(200, content=_sse({"choices": [{"delta": {"content": "x"}}]}))

    transport = httpx.MockTransport(handler)
    client = HTTPOpenAIClient(api_key="k", base_url="https://x", transport=transport)
    await _collect(client)
    assert captured["stream"] is True
    assert captured["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_recording_client_streams_canned_chunks() -> None:
    client = RecordingOpenAIClient(
        stream_chunks=[{"choices": [{"delta": {"content": "a"}}]},
                       {"choices": [{"delta": {"content": "b"}}]}]
    )
    out = [
        dict(c)
        async for c in client.stream_chat_completions(
            model="m", messages=[{"role": "user", "content": "hi"}], tools=None
        )
    ]
    assert [c["choices"][0]["delta"]["content"] for c in out] == ["a", "b"]
    assert client.calls[-1]["stream"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/orchestrator && python -m pytest tests/test_openai_client_stream.py -q`
Expected: FAIL — `AttributeError: 'HTTPOpenAIClient' object has no attribute 'stream_chat_completions'`.

- [ ] **Step 3: Write minimal implementation** — edits to `openai.py`

Add to the imports block near the top of `openai.py` (join the existing `from collections.abc import Mapping, Sequence`):

```python
from collections.abc import AsyncIterator, Mapping, Sequence
```

Add the streaming method to the `OpenAIClient` Protocol (after `chat_completions`):

```python
    def stream_chat_completions(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        temperature: float | None = None,
        extra_body: dict[str, Any] | None = None,
        tool_choice: dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> AsyncIterator[Mapping[str, Any]]:
        """Stream ``/v1/chat/completions`` (``stream=true``), yielding each
        parsed SSE JSON chunk and stopping at ``[DONE]``. An HTTP >= 400
        status raises the classified :class:`LLMError` before the first
        chunk; an in-band ``error`` event raises mid-stream (Stream L, P1)."""
        ...
```

Add a shared body-builder + the `HTTPOpenAIClient` implementation. Refactor the existing `chat_completions` body assembly into `_build_body` to avoid duplication (surgical: extract, don't rewrite the POST):

```python
def _build_request_body(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    temperature: float | None,
    extra_body: dict[str, Any] | None,
    tool_choice: dict[str, Any] | None,
    response_format: dict[str, Any] | None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"model": model, "messages": messages}
    if tools:
        body["tools"] = tools
    if tool_choice is not None:
        body["tool_choice"] = tool_choice
    if response_format is not None:
        body["response_format"] = response_format
    if temperature is not None:
        body["temperature"] = temperature
    if extra_body:
        body.update(extra_body)
    return body
```

Replace the inline body assembly in `HTTPOpenAIClient.chat_completions` (lines 156–170) with `body = _build_request_body(...)`. Then add the streaming method to `HTTPOpenAIClient`:

```python
    async def stream_chat_completions(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        temperature: float | None = None,
        extra_body: dict[str, Any] | None = None,
        tool_choice: dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> AsyncIterator[Mapping[str, Any]]:
        body = _build_request_body(
            model=model, messages=messages, tools=tools, temperature=temperature,
            extra_body=extra_body, tool_choice=tool_choice, response_format=response_format,
        )
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
        # Disable httpx's read timeout on the stream — the router's
        # idle_timeout_s governs inter-chunk silence (Stream L, P1).
        timeout = httpx.Timeout(self.timeout_s, read=None)
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}{self.chat_completions_path}",
                    headers={
                        self.api_key_header: f"{self.api_key_prefix}{self.api_key}",
                        "content-type": "application/json",
                    },
                    json=body,
                ) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        raise classify_http_error("openai", response.status_code, _truncate(response.text))
                    async for line in response.aiter_lines():
                        chunk = _parse_sse_line(line)
                        if chunk is _SSE_SKIP:
                            continue
                        if chunk is _SSE_DONE:
                            return
                        assert isinstance(chunk, Mapping)  # noqa: S101
                        error = chunk.get("error")
                        if isinstance(error, Mapping):
                            raise _classify_stream_error(error)
                        yield chunk
        except httpx.HTTPError as exc:
            raise LLMNetworkError(f"openai: {exc}") from exc
```

Add the SSE line parser + error classifier as module helpers:

```python
_SSE_SKIP = object()
_SSE_DONE = object()


def _parse_sse_line(line: str) -> Any:
    """Parse one SSE line into a JSON chunk, ``_SSE_DONE``, or ``_SSE_SKIP``.

    Blank lines, comment lines (``:`` keepalive), and non-``data:`` lines
    are skipped; ``data: [DONE]`` terminates; malformed JSON is skipped
    (lenient, like the non-streaming decoder)."""
    line = line.strip()
    if not line or line.startswith(":") or not line.startswith("data:"):
        return _SSE_SKIP
    data = line[len("data:"):].strip()
    if data == "[DONE]":
        return _SSE_DONE
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return _SSE_SKIP
    return parsed if isinstance(parsed, Mapping) else _SSE_SKIP


def _classify_stream_error(error: Mapping[str, Any]) -> LLMError:
    """Map an in-band SSE ``error`` object to an :class:`LLMError`.

    OpenAI-wire error events carry ``{message, type, code}``. Without an
    HTTP status we default to a retryable :class:`LLMServerError`; a
    billing/quota marker in the message escalates to key-level via the
    shared classifier (status 429 forces the marker inspection path)."""
    message = str(error.get("message") or "")
    return classify_http_error("openai", 429, message) if _looks_billing(message) else LLMServerError(
        f"openai stream error: {message}"
    )


def _looks_billing(message: str) -> bool:
    low = message.lower()
    return "quota" in low or "billing" in low or "balance" in low
```

Add the needed imports to `openai.py` (extend the existing middleware import to include `LLMError`, and `_streaming` is NOT imported here to avoid a cycle):

```python
from expert_work.runtime.middleware import (
    LLMClientError,
    LLMError,
    LLMNetworkError,
    LLMServerError,
)
```

Add `stream_chunks` + streaming method to `RecordingOpenAIClient`:

```python
@dataclass
class RecordingOpenAIClient:
    response: Mapping[str, Any] = field(default_factory=dict)
    stream_chunks: list[Mapping[str, Any]] = field(default_factory=list)
    raise_with: BaseException | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    # ... existing chat_completions unchanged ...

    async def stream_chat_completions(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        temperature: float | None = None,
        extra_body: dict[str, Any] | None = None,
        tool_choice: dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> AsyncIterator[Mapping[str, Any]]:
        self.calls.append(
            {"model": model, "messages": messages, "tools": tools, "temperature": temperature,
             "extra_body": extra_body, "tool_choice": tool_choice,
             "response_format": response_format, "stream": True}
        )
        if self.raise_with is not None:
            raise self.raise_with
        for chunk in self.stream_chunks:
            yield chunk
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/orchestrator && python -m pytest tests/test_openai_client_stream.py tests/test_openai_provider.py -q`
Expected: PASS — new stream tests pass; the extracted `_build_request_body` keeps the existing `test_openai_provider.py` (non-streaming) suite green.

- [ ] **Step 5: Lint + commit**

```bash
cd services/orchestrator && ruff check src/orchestrator/llm/providers/openai.py tests/test_openai_client_stream.py && ruff format src/orchestrator/llm/providers/openai.py
git add services/orchestrator/src/orchestrator/llm/providers/openai.py services/orchestrator/tests/test_openai_client_stream.py
git commit -m "feat(llm): HTTPOpenAIClient.stream_chat_completions (SSE) + recording double"
```

---

### Task 3: `OpenAIProvider.stream()` + drain in `complete()`

**Files:**
- Modify: `services/orchestrator/src/orchestrator/llm/providers/openai.py` (`OpenAIProvider`)
- Test: `services/orchestrator/tests/test_openai_provider_stream.py`

**Interfaces:**
- Consumes: `_streaming.LLMDelta`, `delta_from_openai_chunk`, `OpenAIStreamAssembler`; the client `stream_chat_completions` (Task 2).
- Produces: `OpenAIProvider.stream(...) -> AsyncIterator[LLMDelta]`; `OpenAIProvider.complete()` now drains `stream()`. `complete()` keeps the same signature/return and the HX-13 allowed_tools fallback.

- [ ] **Step 1: Write the failing test** — `tests/test_openai_provider_stream.py`

```python
import pytest
from langchain_core.messages import HumanMessage

from orchestrator.llm.providers._streaming import LLMDelta
from orchestrator.llm.providers.openai import OpenAIProvider, RecordingOpenAIClient


def _text_chunks() -> list[dict]:
    return [
        {"choices": [{"delta": {"role": "assistant"}}]},
        {"choices": [{"delta": {"content": "Hel"}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]},
        {"choices": [{"delta": {}}], "model": "glm-5.2",
         "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}},
    ]


@pytest.mark.asyncio
async def test_stream_yields_normalized_deltas() -> None:
    client = RecordingOpenAIClient(stream_chunks=_text_chunks())
    provider = OpenAIProvider(client=client, model="glm-5.2")
    deltas = [
        d async for d in provider.stream(messages=[HumanMessage(content="hi")], tools=[])
    ]
    assert [d.content for d in deltas if d.content] == ["Hel", "lo"]
    assert any(d.usage and d.usage["total_tokens"] == 5 for d in deltas)


@pytest.mark.asyncio
async def test_complete_drains_stream_to_full_message() -> None:
    client = RecordingOpenAIClient(stream_chunks=_text_chunks())
    provider = OpenAIProvider(client=client, model="glm-5.2")
    msg = await provider.complete(messages=[HumanMessage(content="hi")], tools=[])
    assert msg.content == "Hello"
    assert msg.usage_metadata is not None
    assert msg.usage_metadata["total_tokens"] == 5
    assert msg.response_metadata["finish_reason"] == "stop"
    assert client.calls[-1]["stream"] is True  # complete() now goes through the stream path


@pytest.mark.asyncio
async def test_complete_reassembles_tool_call() -> None:
    client = RecordingOpenAIClient(stream_chunks=[
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "function": {"name": "search", "arguments": '{"q": '}}]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": '"hi"}'}}]}, "finish_reason": "tool_calls"}]},
    ])
    provider = OpenAIProvider(client=client, model="glm-5.2")
    msg = await provider.complete(messages=[HumanMessage(content="hi")], tools=[])
    assert msg.tool_calls == [{"id": "call_1", "name": "search", "args": {"q": "hi"}, "type": "tool_call"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/orchestrator && python -m pytest tests/test_openai_provider_stream.py -q`
Expected: FAIL — `AttributeError: 'OpenAIProvider' object has no attribute 'stream'`.

- [ ] **Step 3: Write minimal implementation** — edits to `OpenAIProvider`

Import the streaming model at the top of `openai.py` **inside** the provider methods is not needed — add a module import guarded against the cycle. Since `_streaming.build()` imports `openai` lazily, `openai` can import `_streaming` at module top safely:

```python
from orchestrator.llm.providers._streaming import (
    LLMDelta,
    OpenAIStreamAssembler,
    delta_from_openai_chunk,
)
```

Refactor `OpenAIProvider.complete()` to build the request via a shared helper and drain the stream. Extract the message/tool/response_format assembly (lines 276–322) into `_prepare_request` returning the kwargs dict, so `complete()` and `stream()` share it:

```python
    async def _prepare_request(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
        output_schema: StructuredOutputSpec | None,
        use_allowed: bool,
    ) -> dict[str, Any]:
        """Translate history + tools into ``chat_completions`` kwargs.
        Shared by ``complete`` and ``stream`` so both put the exact same
        request on the wire (only ``stream`` differs at the client)."""
        if output_schema is not None and self.structured_output_capability != "native":
            messages = [*messages, SystemMessage(content=schema_instruction(output_schema))]
        messages = coalesce_system_messages(messages)
        mapped = await _to_openai_messages(messages, self.image_resolver)
        response_format: dict[str, Any] | None = None
        if output_schema is not None and self.structured_output_capability == "native":
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": output_schema.name,
                    "schema": output_schema.schema,
                    "strict": output_schema.strict,
                },
            }
        tool_payload = [_to_openai_tool(spec) for spec in tools] if tools else None
        tool_choice: dict[str, Any] | None = None
        if use_allowed:
            tool_choice = {
                "type": "allowed_tools",
                "mode": "auto",
                "tools": [
                    {"type": "function", "function": {"name": spec.name}}
                    for spec in tools
                    if not spec.defer_loading
                ],
            }
        return {
            "model": self.model,
            "messages": mapped,
            "tools": tool_payload,
            "temperature": self.temperature,
            "extra_body": self.thinking_payload,
            "tool_choice": tool_choice,
            "response_format": response_format,
        }
```

Rewrite `complete()` to reuse `_prepare_request` + drain `stream()` (preserving the HX-13 allowed_tools fallback):

```python
    async def complete(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
        output_schema: StructuredOutputSpec | None = None,
    ) -> AIMessage:
        assembler = OpenAIStreamAssembler()
        async for delta in self.stream(messages=messages, tools=tools, output_schema=output_schema):
            assembler.add(delta)
        return assembler.build()

    async def stream(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
        output_schema: StructuredOutputSpec | None = None,
    ) -> AsyncIterator[LLMDelta]:
        use_allowed = (
            any(spec.defer_loading for spec in tools) and not self._allowed_tools_disabled
        )
        request = await self._prepare_request(
            messages=messages, tools=tools, output_schema=output_schema, use_allowed=use_allowed
        )
        try:
            async for chunk in self.client.stream_chat_completions(**request):
                yield delta_from_openai_chunk(chunk)
            return
        except LLMClientError:
            if not use_allowed:
                raise
            # HX-13 (Mini-ADR HX-J4) — allowed_tools rejected pre-stream.
            # Fail open: drop to the application tier and re-stream once.
            self._allowed_tools_disabled = True
            disclosure_fallback_total.labels(provider="openai").inc()
            logger.warning("openai.allowed_tools_rejected — falling back to app tier")
        retry = await self._prepare_request(
            messages=messages, tools=tools, output_schema=output_schema, use_allowed=False
        )
        async for chunk in self.client.stream_chat_completions(**retry):
            yield delta_from_openai_chunk(chunk)
```

Note: the HX-13 `LLMClientError` fallback only works cleanly because a 400 rejection arrives before the first chunk (the client raises during status check, no delta yielded yet) — matching buffer-until-first-token. Keep the `except LLMClientError` re-stream **outside** the first `async for` so a mid-stream client error (post-first-token) is not silently retried; here the only source of `LLMClientError` from `stream_chat_completions` is the pre-chunk status classification.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/orchestrator && python -m pytest tests/test_openai_provider_stream.py tests/test_openai_provider.py -q`
Expected: PASS — new streaming provider tests pass AND the existing non-streaming `test_openai_provider.py` suite still passes (now routed through the drain path; assembled message is byte-equal).

- [ ] **Step 5: Lint + commit**

```bash
cd services/orchestrator && ruff check src/orchestrator/llm/providers/openai.py tests/test_openai_provider_stream.py && ruff format src/orchestrator/llm/providers/openai.py
git add services/orchestrator/src/orchestrator/llm/providers/openai.py services/orchestrator/tests/test_openai_provider_stream.py
git commit -m "feat(llm): OpenAIProvider.stream() + complete() drains the stream"
```

---

### Task 4: `RateLimitedProvider.stream()`

**Files:**
- Modify: `services/orchestrator/src/orchestrator/llm/rate_limit.py`
- Test: `services/orchestrator/tests/test_rate_limit.py` (append)

**Interfaces:**
- Consumes: `_streaming.LLMDelta`; the inner provider's `stream()`.
- Produces: `RateLimitedProvider.stream(...)` (admission gate then delegate). `supports_streaming` already unwraps `.inner`, so a `RateLimitedProvider` around an `OpenAIProvider` is streaming-capable and around a plain provider is not.

- [ ] **Step 1: Write the failing test** — append to `tests/test_rate_limit.py`

```python
@pytest.mark.asyncio
async def test_stream_delegates_and_admits() -> None:
    from collections.abc import AsyncIterator

    from aiolimiter import AsyncLimiter

    from orchestrator.llm.providers._streaming import LLMDelta, supports_streaming
    from orchestrator.llm.rate_limit import RateLimitedProvider

    class _StreamingInner:
        def __init__(self) -> None:
            self.seen: list = []

        async def stream(self, *, messages, tools, output_schema=None) -> AsyncIterator[LLMDelta]:
            self.seen.append((list(messages), list(tools)))
            yield LLMDelta(content="a")
            yield LLMDelta(content="b")

    inner = _StreamingInner()
    limited = RateLimitedProvider(inner=inner, limiter=AsyncLimiter(max_rate=100, time_period=60))
    out = [d.content async for d in limited.stream(messages=["m"], tools=[])]
    assert out == ["a", "b"]
    assert inner.seen == [(["m"], [])]
    assert supports_streaming(limited) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/orchestrator && python -m pytest tests/test_rate_limit.py::test_stream_delegates_and_admits -q`
Expected: FAIL — `AttributeError: 'RateLimitedProvider' object has no attribute 'stream'`.

- [ ] **Step 3: Write minimal implementation** — add to `RateLimitedProvider`

Extend the imports:

```python
from collections.abc import AsyncIterator, Sequence
from orchestrator.llm.providers._streaming import LLMDelta
```

Add the method after `complete`:

```python
    async def stream(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
        output_schema: StructuredOutputSpec | None = None,
    ) -> AsyncIterator[LLMDelta]:
        """Acquire a token (admission), then delegate to the inner
        provider's stream. Only called when the inner provider is
        streaming-capable (the router probes via ``supports_streaming``).
        The token bucket gates admission; ``aiolimiter`` does not refund
        on exit, so spanning the iteration is harmless."""
        async with self.limiter:
            if output_schema is None:
                inner_stream = self.inner.stream(messages=messages, tools=tools)  # type: ignore[attr-defined]
            else:
                inner_stream = self.inner.stream(  # type: ignore[attr-defined]
                    messages=messages, tools=tools, output_schema=output_schema
                )
            async for delta in inner_stream:
                yield delta
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/orchestrator && python -m pytest tests/test_rate_limit.py -q`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
cd services/orchestrator && ruff check src/orchestrator/llm/rate_limit.py tests/test_rate_limit.py && ruff format src/orchestrator/llm/rate_limit.py
git add services/orchestrator/src/orchestrator/llm/rate_limit.py services/orchestrator/tests/test_rate_limit.py
git commit -m "feat(llm): RateLimitedProvider.stream() admission gate + delegate"
```

---

### Task 5: Router two-threshold driver + fallback semantics

**Files:**
- Modify: `packages/expert-work-runtime/src/expert_work/runtime/middleware/llm_error_handling.py` (add `LLMStreamInterruptedError`)
- Modify: `packages/expert-work-runtime/src/expert_work/runtime/middleware/__init__.py` (export it)
- Modify: `services/orchestrator/src/orchestrator/llm/router.py`
- Modify: `services/orchestrator/tests/test_llm_router.py` (rename existing `stream_deadline_s=` → `first_token_timeout_s=`)
- Modify: `services/orchestrator/tests/test_middleware_chain_wiring.py` (rename if any `stream_deadline_s=`)
- Test: `services/orchestrator/tests/test_llm_router_streaming.py` (new)

**Interfaces:**
- Consumes: `_streaming.supports_streaming`, `OpenAIStreamAssembler`; `LLMStreamInterruptedError`.
- Produces: `LLMRouter.first_token_timeout_s`, `LLMRouter.idle_timeout_s`; a post-first-token no-fallback branch in `__call__`. Field `stream_deadline_s` is REMOVED from `LLMRouter` (renamed).

- [ ] **Step 1: Write the failing test** — `tests/test_llm_router_streaming.py`

```python
import asyncio
from collections.abc import AsyncIterator

import pytest

from expert_work.runtime.middleware import (
    LLMClientError,
    LLMServerError,
    LLMStreamInterruptedError,
)
from orchestrator.llm.providers._streaming import LLMDelta
from orchestrator.llm.router import AllProvidersExhaustedError, LLMRouter, ProviderHandle


class _StreamProvider:
    """Streaming double: a scripted list where an item is either an LLMDelta
    or a float (a sleep gap before the next delta) or an Exception (raised)."""

    def __init__(self, script: list) -> None:
        self.script = script

    async def stream(self, *, messages, tools, output_schema=None) -> AsyncIterator[LLMDelta]:
        for item in self.script:
            if isinstance(item, (int, float)):
                await asyncio.sleep(item)
            elif isinstance(item, Exception):
                raise item
            else:
                yield item


def _handle(script: list, key: str = "glm:glm-5.2") -> ProviderHandle:
    return ProviderHandle(provider=_StreamProvider(script), key=key)


@pytest.mark.asyncio
async def test_idle_fires_on_silence_not_on_slow_total() -> None:
    # First token is slow (0.05s < first_token 0.2s), then steady sub-idle
    # deltas that far outlast any single total cap — must NOT time out.
    script = [0.05, LLMDelta(content="a"), 0.02, LLMDelta(content="b"),
              0.02, LLMDelta(content="c"), LLMDelta(finish_reason="stop")]
    router = LLMRouter(providers=[_handle(script)], first_token_timeout_s=0.2, idle_timeout_s=0.1)
    msg = await router(messages=[], tools=[])
    assert msg.content == "abc"


@pytest.mark.asyncio
async def test_first_token_timeout_falls_over_to_next_provider() -> None:
    slow = _handle([0.3, LLMDelta(content="never")], key="glm:a")   # stalls before first token
    good = _handle([LLMDelta(content="ok"), LLMDelta(finish_reason="stop")], key="glm:b")
    router = LLMRouter(providers=[slow, good], first_token_timeout_s=0.1, idle_timeout_s=0.5)
    msg = await router(messages=[], tools=[])
    assert msg.content == "ok"  # fell over to the second provider


@pytest.mark.asyncio
async def test_idle_after_first_token_ends_turn_with_partial() -> None:
    # First token arrives, then the stream stalls past idle_timeout.
    slow = _handle([LLMDelta(content="partial "), LLMDelta(content="answer"), 0.3], key="glm:a")
    never = _handle([LLMDelta(content="SHOULD NOT REACH")], key="glm:b")
    router = LLMRouter(providers=[slow, never], first_token_timeout_s=0.5, idle_timeout_s=0.1)
    msg = await router(messages=[], tools=[])
    assert msg.content == "partial answer"
    assert msg.response_metadata.get("finish_reason") == "stream_idle_timeout"


@pytest.mark.asyncio
async def test_in_band_error_before_first_token_is_retryable() -> None:
    err = _handle([LLMServerError("boom before token")], key="glm:a")
    good = _handle([LLMDelta(content="ok"), LLMDelta(finish_reason="stop")], key="glm:b")
    router = LLMRouter(providers=[err, good], first_token_timeout_s=0.5, idle_timeout_s=0.5)
    msg = await router(messages=[], tools=[])
    assert msg.content == "ok"  # server error pre-token → fell over


@pytest.mark.asyncio
async def test_error_after_first_token_is_terminal_no_fallback() -> None:
    err = _handle([LLMDelta(content="partial"), LLMServerError("mid-stream boom")], key="glm:a")
    good = _handle([LLMDelta(content="SHOULD NOT REACH")], key="glm:b")
    router = LLMRouter(providers=[err, good], first_token_timeout_s=0.5, idle_timeout_s=0.5)
    with pytest.raises(LLMStreamInterruptedError):
        await router(messages=[], tools=[])


@pytest.mark.asyncio
async def test_client_error_before_first_token_no_fallback() -> None:
    err = _handle([LLMClientError("400 malformed")], key="glm:a")
    good = _handle([LLMDelta(content="ok")], key="glm:b")
    router = LLMRouter(providers=[err, good], first_token_timeout_s=0.5, idle_timeout_s=0.5)
    with pytest.raises(LLMClientError):
        await router(messages=[], tools=[])


@pytest.mark.asyncio
async def test_first_token_timeout_all_exhausted() -> None:
    a = _handle([0.3, LLMDelta(content="x")], key="glm:a")
    b = _handle([0.3, LLMDelta(content="y")], key="glm:b")
    router = LLMRouter(providers=[a, b], first_token_timeout_s=0.05, idle_timeout_s=0.5)
    with pytest.raises(AllProvidersExhaustedError):
        await router(messages=[], tools=[])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/orchestrator && python -m pytest tests/test_llm_router_streaming.py -q`
Expected: FAIL — `ImportError: cannot import name 'LLMStreamInterruptedError'` (and `LLMRouter` has no `first_token_timeout_s`).

- [ ] **Step 3a: Add `LLMStreamInterruptedError`** — `llm_error_handling.py`

Add after `LLMStreamStaleError` (line ~119):

```python
class LLMStreamInterruptedError(LLMError):
    """Stream L (P1) — a streaming provider stalled or errored AFTER the
    first progress delta. Buffer-until-first-token commits the run to
    that provider once tokens flow, so this is terminal: it inherits the
    plain :class:`LLMError` (NOT :class:`LLMServerError`), so the E.4
    error-handling middleware does not retry it and the router does not
    fall back. Carries the partial :class:`AIMessage` assembled so far
    for callers that want to surface it."""

    def __init__(self, message: str, *, partial: object | None = None) -> None:
        super().__init__(message)
        self.partial = partial
```

- [ ] **Step 3b: Export it** — `middleware/__init__.py`

Add `LLMStreamInterruptedError` to the `from .llm_error_handling import (...)` list and to `__all__` (keep `__all__` sorted — run `ruff check --fix` after).

- [ ] **Step 3c: Router changes** — `router.py`

Add imports:

```python
from collections.abc import AsyncIterator, Awaitable, Sequence
from expert_work.runtime.middleware import (
    ...,
    LLMStreamInterruptedError,
    ...,
)
from orchestrator.llm.providers._streaming import (
    LLMDelta,
    OpenAIStreamAssembler,
    supports_streaming,
)
```

Rename the field + add idle (replace lines 291–297):

```python
    #: Stream L (P1) — the streaming idle-timeout pair. ``first_token_timeout_s``
    #: bounds time-to-first-token (fallback-eligible on expiry); ``idle_timeout_s``
    #: bounds inter-delta silence AFTER the first token (ends the turn with the
    #: partial output). For a non-streaming provider (Anthropic until P1', test
    #: doubles) ``first_token_timeout_s`` degrades to the legacy total wall-clock
    #: cap around ``complete()``. ``None``/``0`` disables the respective timer.
    first_token_timeout_s: float | None = field(default=None)
    idle_timeout_s: float | None = field(default=None)
```

Add a no-fallback branch in `__call__` — insert BEFORE the `except _KEY_LEVEL_ERRORS` clause (after the `except LLMOutputValidationError` block, ~line 334):

```python
            except LLMStreamInterruptedError:
                # Buffer-until-first-token — a stall/error AFTER the first
                # delta commits the run to this provider (partial output
                # already streamed). No key rotation, no failover.
                raise
```

Replace the legacy `_complete` dispatch in `_invoke_once` with a streaming-aware `_invoke_provider`. In the non-chain branch (lines 482–493):

```python
        if self.around_llm_chain is None:
            result = await self._invoke_provider(
                handle, messages=messages, tools=tools, output_schema=output_schema
            )
            assert isinstance(result, AIMessage)  # noqa: S101 - provider Protocol contract
            return result
```

In the chain branch, change `terminal` to call `_invoke_provider`, and drop the outer `_invoke_with_deadline` wrap (the per-attempt deadline now lives inside `_invoke_provider`):

```python
        async def terminal(c: MiddlewareContext) -> None:
            response = await self._invoke_provider(
                handle,
                messages=c.payload["messages"],
                tools=c.payload["tools"],
                output_schema=output_schema,
            )
            c.payload["response"] = response
            c.payload["llm_response"] = _llm_response_payload(response)

        await self.around_llm_chain.invoke(ctx, terminal)
```

Add `_invoke_provider` + `_drive_stream` + a `_stream` helper (mirrors `_complete`). Keep `_invoke_with_deadline` for the non-streaming path but rename its use of `stream_deadline_s`:

```python
    async def _invoke_provider(
        self,
        handle: ProviderHandle,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
        output_schema: StructuredOutputSpec | None,
    ) -> AIMessage:
        """Dispatch one provider attempt — streaming (two-threshold idle
        driver) when the provider supports it, else the legacy
        single-deadline ``complete()`` path."""
        if supports_streaming(handle.provider):
            return await self._drive_stream(
                handle,
                _stream(handle.provider, messages=messages, tools=tools, output_schema=output_schema),
            )
        result = await self._invoke_with_deadline(
            handle,
            _complete(handle.provider, messages=messages, tools=tools, output_schema=output_schema),
        )
        assert isinstance(result, AIMessage)  # noqa: S101
        return result

    async def _drive_stream(
        self, handle: ProviderHandle, stream: AsyncIterator[LLMDelta]
    ) -> AIMessage:
        """Consume a provider delta stream under the two-threshold policy.

        Phase 1 (until the first *progress* delta): bounded by
        ``first_token_timeout_s``; a stall or error is retryable →
        fallback. Phase 2 (after the first progress delta): bounded by
        ``idle_timeout_s``; a stall ends the turn with the partial
        output; an error is terminal (:class:`LLMStreamInterruptedError`,
        no fallback)."""
        assembler = OpenAIStreamAssembler()
        it = stream.__aiter__()
        first_progress = False

        # Phase 1 — wait for the first progress delta.
        while not first_progress:
            try:
                delta = await _next_delta(it, self.first_token_timeout_s)
            except _StreamEnded:
                return assembler.build()  # ended with no progress → empty answer
            except TimeoutError as exc:
                _llm_stream_stale_total.labels(provider_key=handle.key).inc()
                logger.warning(
                    "llm_router.first_token_timeout key=%s deadline_s=%s",
                    handle.key,
                    self.first_token_timeout_s,
                )
                raise LLMStreamStaleError(
                    f"provider {handle.key!r} produced no token within "
                    f"first_token_timeout_s={self.first_token_timeout_s}"
                ) from exc
            assembler.add(delta)
            first_progress = delta.has_progress

        # Phase 2 — consume the rest under the idle timeout.
        while True:
            try:
                delta = await _next_delta(it, self.idle_timeout_s)
            except _StreamEnded:
                return assembler.build()
            except TimeoutError:
                logger.warning(
                    "llm_router.idle_timeout key=%s deadline_s=%s (ending turn with partial)",
                    handle.key,
                    self.idle_timeout_s,
                )
                return assembler.build(interrupted=True)
            except LLMError as exc:
                # Post-first-token hard error → terminal, no fallback.
                raise LLMStreamInterruptedError(
                    f"provider {handle.key!r} stream failed after first token: {exc}",
                    partial=assembler.build(interrupted=True),
                ) from exc
            assembler.add(delta)
```

Add the module-level helpers (near `_complete`):

```python
class _StreamEnded(Exception):
    """Internal — the delta iterator is exhausted (StopAsyncIteration)."""


async def _next_delta(it: AsyncIterator[LLMDelta], timeout: float | None) -> LLMDelta:
    """One ``__anext__`` under an optional timeout. Raises ``_StreamEnded``
    on exhaustion, ``TimeoutError`` on expiry, or the provider's
    :class:`LLMError` on a stream fault."""
    coro = it.__anext__()
    try:
        if timeout is None or timeout <= 0:
            return await coro
        return await asyncio.wait_for(coro, timeout=timeout)
    except StopAsyncIteration as exc:
        raise _StreamEnded from exc


def _stream(
    provider: LLMProvider,
    *,
    messages: Sequence[BaseMessage],
    tools: Sequence[ToolSpec],
    output_schema: StructuredOutputSpec | None,
) -> AsyncIterator[LLMDelta]:
    """Call ``provider.stream``, forwarding ``output_schema`` only when set
    (mirrors ``_complete`` so pre-streaming doubles stay call-identical)."""
    if output_schema is None:
        return provider.stream(messages=messages, tools=tools)  # type: ignore[attr-defined]
    return provider.stream(  # type: ignore[attr-defined]
        messages=messages, tools=tools, output_schema=output_schema
    )
```

Note the pre-first-token `LLMError` from the stream (e.g. `LLMServerError`, `LLMClientError`) propagates out of `_next_delta` → out of `_drive_stream` (Phase 1 has no `except LLMError`) → up to `__call__`, where the existing classification handles it (server → fallback, client → no fallback). That is exactly buffer-until-first-token for errors. Only Phase 2 wraps errors into the terminal `LLMStreamInterruptedError`.

- [ ] **Step 3d: Update existing router tests** — `test_llm_router.py`, `test_middleware_chain_wiring.py`

Rename every `stream_deadline_s=` kwarg on `LLMRouter(...)` / `build_llm_router(...)` to `first_token_timeout_s=` in these test files. The existing L3 timeout tests (`test_llm_router.py:302–420`) use non-streaming provider doubles (no `.stream`), so they exercise the legacy `_invoke_with_deadline` path unchanged under the new field name.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/orchestrator && python -m pytest tests/test_llm_router_streaming.py tests/test_llm_router.py tests/test_middleware_chain_wiring.py -q`
Expected: PASS — new streaming router tests pass; legacy L3 deadline tests pass under the renamed field.

- [ ] **Step 5: Lint + commit**

```bash
cd services/orchestrator && ruff check src/orchestrator/llm/router.py tests/test_llm_router_streaming.py
ruff check --fix packages/expert-work-runtime/src/expert_work/runtime/middleware/__init__.py
cd .. && cd .. && ruff format services/orchestrator/src/orchestrator/llm/router.py packages/expert-work-runtime/src/expert_work/runtime/middleware/llm_error_handling.py
git add services/orchestrator/src/orchestrator/llm/router.py services/orchestrator/tests/test_llm_router_streaming.py services/orchestrator/tests/test_llm_router.py services/orchestrator/tests/test_middleware_chain_wiring.py packages/expert-work-runtime/src/expert_work/runtime/middleware/llm_error_handling.py packages/expert-work-runtime/src/expert_work/runtime/middleware/__init__.py
git commit -m "feat(llm): router two-threshold idle timeout + buffer-until-first-token fallback"
```

---

### Task 6: Config — `idle_timeout_s` field + thread through the factory

**Files:**
- Modify: `packages/expert-work-protocol/src/expert_work/protocol/agent_spec.py` (reinterpret `stream_deadline_s` docstring; add `idle_timeout_s`)
- Modify: `packages/expert-work-protocol/src/expert_work/protocol/agent_template_resolve.py` (field tier for `idle_timeout_s`)
- Modify: `services/orchestrator/src/orchestrator/agent_factory.py` (`build_llm_router` param rename; `_chat_idle_timeout_s`; thread through `build_step_routers` / escalated / VL)
- Test: `services/orchestrator/tests/test_agent_factory.py` (extend), `packages/expert-work-protocol/tests/` spec test for the new field.

**Interfaces:**
- Consumes: `LLMRouter.first_token_timeout_s` / `idle_timeout_s` (Task 5).
- Produces: `spec.spec.idle_timeout_s` (manifest field); `build_llm_router(..., first_token_timeout_s=, idle_timeout_s=)`.

**Config decision (pinned):** Keep the manifest field name `stream_deadline_s` (reinterpreted as the **first-token budget** — stored manifests keep working unchanged, and the stored `180` becomes a 180s TTFT budget: strictly more permissive). Add a NEW manifest field `idle_timeout_s` (default 45s). The router-internal fields are named `first_token_timeout_s` / `idle_timeout_s`. Renaming the *manifest* key to `first_token_timeout_s` (cosmetic; needs form-UI + template-tier churn) is deferred to a follow-up — out of P1 scope.

- [ ] **Step 1: Write the failing test** — extend `test_agent_factory.py`

```python
def test_chat_idle_timeout_default_and_off() -> None:
    from orchestrator.agent_factory import _chat_idle_timeout_s

    assert _chat_idle_timeout_s(45) == 45.0
    assert _chat_idle_timeout_s(0) is None       # 0 disables the idle timer
    assert _chat_idle_timeout_s(30) == 30.0
```

And a protocol-level test (in `packages/expert-work-protocol/tests/test_agent_spec.py` or the nearest spec test file). `AgentSpecBody` (the class at `agent_spec.py:1100` holding `stream_deadline_s`) has required fields (`tenant_config`, `model`, `system_prompt`, `sandbox`), so assert the field default via `model_fields` introspection rather than constructing:

```python
def test_idle_timeout_field_default() -> None:
    from expert_work.protocol.agent_spec import AgentSpecBody

    assert AgentSpecBody.model_fields["idle_timeout_s"].default == 45
    # stream_deadline_s stays a field (reinterpreted as the first-token budget)
    assert AgentSpecBody.model_fields["stream_deadline_s"].default == 180
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/orchestrator && python -m pytest tests/test_agent_factory.py::test_chat_idle_timeout_default_and_off -q`
Expected: FAIL — `ImportError: cannot import name '_chat_idle_timeout_s'`.

- [ ] **Step 3a: Add the manifest field** — `agent_spec.py`

Update the `stream_deadline_s` docstring (line 1193–1201) to describe it as the first-token budget (keep the field, default, bounds), and add `idle_timeout_s` immediately after it:

```python
    idle_timeout_s: int = Field(
        default=45,
        ge=0,
        le=600,
        description=(
            "Stream L (P1) — inter-token idle cap for a STREAMING provider "
            "call. After the first token, a gap between deltas longer than "
            "this ends the turn with the partial output (the model went "
            "silent mid-stream). Distinct from ``stream_deadline_s`` (the "
            "time-to-first-token budget). Set ``0`` to disable the idle "
            "timer (dev / long-batch). Non-streaming providers ignore this."
        ),
    )
```

- [ ] **Step 3b: Field tier** — `agent_template_resolve.py`

Add next to the existing `"stream_deadline_s": FieldTier.TENANT_OWNED` line:

```python
    "idle_timeout_s": FieldTier.TENANT_OWNED,
```

- [ ] **Step 3c: Factory threading** — `agent_factory.py`

Add the idle helper next to `_chat_stream_deadline_s`:

```python
#: Floor is not applied to the idle timer — it is an inter-token gap, not a
#: total budget, so the manifest value is honoured directly (0 = off).
def _chat_idle_timeout_s(manifest_idle_s: int) -> float | None:
    """Effective inter-token idle timeout; ``0`` disables (dev / long-batch)."""
    return float(manifest_idle_s) if manifest_idle_s > 0 else None
```

Rename `build_llm_router`'s `stream_deadline_s` param to `first_token_timeout_s` and add `idle_timeout_s`; pass both to the `LLMRouter(...)` construction:

```python
async def build_llm_router(
    model: ModelSpec,
    *,
    secret_store: SecretStore,
    around_llm_chain: MiddlewareChain | None = None,
    image_resolver: ImageResolver | None = None,
    first_token_timeout_s: float | None = None,
    idle_timeout_s: float | None = None,
    provider_timeout_s: float | None = None,
    extra_fallbacks: list[ModelSpec] | None = None,
    provider_key_resolver: ProviderKeyResolver | None = None,
    ignore_api_key_ref: bool = False,
) -> LLMRouter:
    ...
    return LLMRouter(
        providers=handles,
        around_llm_chain=around_llm_chain,
        first_token_timeout_s=first_token_timeout_s,
        idle_timeout_s=idle_timeout_s,
    )
```

In `build_step_routers` compute the idle timeout once and pass both to every `build_llm_router` call (default + each routing rule):

```python
    first_token: float | None = _chat_stream_deadline_s(spec.spec.stream_deadline_s)
    idle: float | None = _chat_idle_timeout_s(spec.spec.idle_timeout_s)
    default = await build_llm_router(
        spec.spec.model,
        secret_store=secret_store,
        around_llm_chain=around_llm_chain,
        image_resolver=image_resolver,
        first_token_timeout_s=first_token,
        idle_timeout_s=idle,
        provider_timeout_s=first_token,
        provider_key_resolver=provider_key_resolver,
        ignore_api_key_ref=ignore_api_key_ref,
    )
    ...
    routed = await build_llm_router(
        rule.model,
        ...,
        first_token_timeout_s=first_token,
        idle_timeout_s=idle,
        provider_timeout_s=first_token,
        ...,
    )
```

In `build_agent`, update the escalated caller (line 595–605) and the VL caller (line 618–633):

```python
    # escalated
    escalated_first_token = _chat_stream_deadline_s(spec.spec.stream_deadline_s)
    escalated_idle = _chat_idle_timeout_s(spec.spec.idle_timeout_s)
    escalated_llm_caller = await build_llm_router(
        escalated_spec,
        ...,
        first_token_timeout_s=escalated_first_token,
        idle_timeout_s=escalated_idle,
        provider_timeout_s=escalated_first_token,
        ...,
    )
    # VL — keep the VL floor for first-token; VL also streams (same OpenAI
    # provider), so pass the same idle timer.
    vl_caller = await build_llm_router(
        spec.spec.vision.model,
        ...,
        first_token_timeout_s=vl_deadline_s,
        idle_timeout_s=_chat_idle_timeout_s(spec.spec.idle_timeout_s),
        provider_timeout_s=vl_deadline_s,
        extra_fallbacks=list(spec.spec.vision.fallbacks),
        ...,
    )
```

Update `provider_timeout_s`: for the streaming path the httpx read timeout is already disabled inside `stream_chat_completions`, so `provider_timeout_s` only governs connect/write + the legacy non-streaming path — leave it aligned to `first_token` as today.

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd services/orchestrator && python -m pytest tests/test_agent_factory.py -q
cd ../../packages/expert-work-protocol && python -m pytest tests -q -k "agent_spec or idle"
```
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
cd /Users/mac/src/github/jone_qian/expert-work
ruff check packages/expert-work-protocol/src/expert_work/protocol/agent_spec.py packages/expert-work-protocol/src/expert_work/protocol/agent_template_resolve.py services/orchestrator/src/orchestrator/agent_factory.py
ruff format packages/expert-work-protocol/src/expert_work/protocol/agent_spec.py services/orchestrator/src/orchestrator/agent_factory.py
git add packages/expert-work-protocol/src/expert_work/protocol/agent_spec.py packages/expert-work-protocol/src/expert_work/protocol/agent_template_resolve.py services/orchestrator/src/orchestrator/agent_factory.py services/orchestrator/tests/test_agent_factory.py packages/expert-work-protocol/tests/
git commit -m "feat(config): idle_timeout_s manifest field; thread first-token+idle through routers"
```

---

### Task 7: Full-suite regression + mypy gate

**Files:** none new — a verification task folding the whole branch's checks into one reviewable gate.

- [ ] **Step 1: Run the orchestrator + runtime + protocol suites**

```bash
cd services/orchestrator && python -m pytest tests -q -k "openai or router or rate_limit or streaming or agent_factory"
cd ../../packages/expert-work-runtime && python -m pytest tests -q -k "error_handling or middleware"
cd ../expert-work-protocol && python -m pytest tests -q
```
Expected: PASS across the board. Any failure → return to the owning task (do not patch tests to pass).

- [ ] **Step 2: mypy (CI-equivalent scope)**

```bash
cd /Users/mac/src/github/jone_qian/expert-work
mypy services/orchestrator/src/orchestrator/llm packages/expert-work-runtime/src/expert_work/runtime/middleware packages/expert-work-protocol/src/expert_work/protocol
```
Expected: no new errors. Common fixes: `AsyncIterator` return-type annotations on the async generators; `# type: ignore[attr-defined]` on the `.stream(...)` duck-typed calls in `_stream` / `RateLimitedProvider.stream` (already noted).

- [ ] **Step 3: ruff whole-changed-set**

```bash
cd /Users/mac/src/github/jone_qian/expert-work
ruff check services/orchestrator packages/expert-work-runtime packages/expert-work-protocol
ruff format --check services/orchestrator/src/orchestrator/llm packages/expert-work-runtime/src/expert_work/runtime/middleware
```
Expected: clean. (Repo CI also runs `ruff format --check` — do not skip.)

- [ ] **Step 4: Final commit if any lint/type fixups were needed**

```bash
git add -A && git commit -m "chore(llm): mypy + ruff fixups for streaming P1"
```

---

## Verification (maps to the spec's P1 "Verification")

| Spec requirement | Covered by |
| --- | --- |
| multi-delta text assembly | Task 1 `test_assembler_text_matches_non_streaming_decoder`, Task 3 `test_complete_drains_stream_to_full_message` |
| reassembled tool-call fragments | Task 1 `test_assembler_reassembles_tool_call_fragments`, Task 3 `test_complete_reassembles_tool_call` |
| reasoning deltas | Task 1 `test_delta_reasoning_is_progress` + assembler test (reasoning in `additional_kwargs`) |
| mid-stream `error` event | Task 2 `test_stream_in_band_error_event_raises`, Task 5 `test_error_after_first_token_is_terminal_no_fallback` |
| first-token stall | Task 5 `test_first_token_timeout_falls_over_to_next_provider`, `test_first_token_timeout_all_exhausted` |
| inter-token stall | Task 5 `test_idle_after_first_token_ends_turn_with_partial` |
| `[DONE]` + usage chunk | Task 2 `test_stream_yields_chunks_and_stops_at_done` |
| idle fires on silence not slowness | Task 5 `test_idle_fires_on_silence_not_on_slow_total` |
| buffer-until-first-token honored | Task 5 pre/post-token error + idle tests |
| cache-hit returns whole, no timeout | N/A at router — cache short-circuits at the agent node (`builder.py:730`); documented in Global Constraints, no code |
| assembled == old non-streaming (byte-equal) | Task 1 assembler-vs-`_from_openai_response` test; Task 3 runs the existing non-streaming suite through the drain path |

## Out of Scope (deferred to later phases, per the spec)

- Anthropic streaming (P1').
- Token SSE frames + external API docs + streaming redaction (P2a).
- Playground live-token rendering (P2b).
- Renaming the manifest key `stream_deadline_s` → `first_token_timeout_s` (cosmetic; needs form-UI + template-tier work).
- VL-specific streaming presentation tuning.

## Self-Review Notes

- **Spec coverage:** every P1 bullet maps to a task (see the verification table). The one spec bullet with no code — cache-hit synthesis — is intentionally dropped because investigation showed cache hits never reach the router (`builder.py:730`); recorded in Global Constraints so a reviewer sees the deliberate omission.
- **Type consistency:** `first_token_timeout_s` / `idle_timeout_s` names are identical across `LLMRouter` (Task 5), `build_llm_router` (Task 6), and the manifest→factory mapping (Task 6). `LLMDelta` / `OpenAIStreamAssembler` / `supports_streaming` signatures in the Interfaces block match every consumer. `stream_chat_completions` signature is identical in the Protocol, `HTTPOpenAIClient`, and `RecordingOpenAIClient`.
- **No placeholders:** all code steps carry real code; test steps carry real assertions. The manifest field class is pinned to `AgentSpecBody` (`agent_spec.py:1100`); the field-default test uses `model_fields` introspection (no fixture dependency).
