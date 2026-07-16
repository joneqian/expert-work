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
            fn_raw = tc.get("function")
            fn: Mapping[str, Any] = fn_raw if isinstance(fn_raw, Mapping) else {}
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
    __slots__ = ("args", "id", "name")

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
    ) -> AsyncIterator[LLMDelta]:
        """Yield normalized :class:`LLMDelta` chunks from the provider."""


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
