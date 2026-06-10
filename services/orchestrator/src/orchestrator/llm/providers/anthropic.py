"""Anthropic Messages API adapter — Stream E.11.

Translates the orchestrator's :class:`BaseMessage` history +
:class:`ToolSpec` catalogue into Anthropic's Messages API wire format,
posts via :class:`AnthropicClient` (httpx in production, recording
client in tests), and maps the response back to a LangChain
:class:`AIMessage` with ``tool_calls`` populated.

Wire-format mapping (covered, deliberately minimal for M0):

- ``SystemMessage`` → top-level ``system`` field. Multiple system
  messages are concatenated with ``\\n\\n`` separators.
- ``HumanMessage`` → ``{"role": "user", "content": <text>}``; when the
  message carries ``image_ref`` blocks (J.6 Path A) ``content`` becomes a
  block list with ``{"type": "image", "source": {...}}`` entries, the
  images resolved to base64 via an :class:`ImageResolver`.
- ``AIMessage`` with text only → ``{"role": "assistant", "content": <text>}``.
- ``AIMessage`` with ``tool_calls`` → ``{"role": "assistant", "content":
  [{"type": "text", ...}, {"type": "tool_use", "id": ..., "name": ...,
  "input": ...}, ...]}``.
- ``ToolMessage`` → ``{"role": "user", "content": [{"type":
  "tool_result", "tool_use_id": ..., "content": <text>}]}``.

Out of scope for M0 (deferred to M1-D hardening):

- Streaming responses (``stream=true``). Routed through E.14 SSE later.
- Tool-level ``cache_control`` blocks. (System + message-level
  ``cache_control`` landed in Stream L.L1; tool definitions don't yet
  participate.)

Error mapping per :class:`LLMError` hierarchy (E.4):

- HTTP 429 → :class:`LLMRateLimitError`
- HTTP 4xx other → :class:`LLMClientError` (NOT retried, NOT fallback)
- HTTP 5xx → :class:`LLMServerError`
- :class:`httpx.HTTPError` (network / TLS) → :class:`LLMNetworkError`
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from helix_agent.common.uplift_metrics import record_anthropic_cache_anchor
from helix_agent.runtime.middleware import (
    LLMClientError,
    LLMNetworkError,
    LLMRateLimitError,
    LLMServerError,
    LLMUnauthorizedError,
)
from orchestrator.multimodal import ImageResolver, split_human_content
from orchestrator.tools.registry import ToolSpec

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://api.anthropic.com"
_DEFAULT_TIMEOUT_S = 60.0
DEFAULT_MAX_TOKENS = 4096
_ANTHROPIC_VERSION = "2023-06-01"
_ERROR_BODY_CHAR_CAP = 500

#: Number of trailing non-system messages that get a ``cache_control``
#: marker. Capability Uplift Sprint #8 (Mini-ADR U-7) lowered this from
#: 3 to 2 to make room for the Sprint #8 memory-anchor breakpoint
#: while keeping the total under Anthropic's 4-breakpoint cap:
#: ``system (1) + tail (2) + memory anchor (0..1) ≤ 4``.
#: The lost tail message (typically a Tool result) costs a few hundred
#: tokens not being cached; the memory anchor saves ~25K tokens on a
#: 50-turn session with a 10-fact memory list — net positive.
_CACHE_CONTROL_TAIL_COUNT: int = 2

#: Stream L.L1 — the wire-level ``cache_control`` shape Anthropic
#: expects for ephemeral (5-minute TTL) caching.
_CACHE_CONTROL_EPHEMERAL: dict[str, str] = {"type": "ephemeral"}


@runtime_checkable
class AnthropicClient(Protocol):
    """Sized to the one Messages API endpoint we use.

    Both :class:`HTTPAnthropicClient` and
    :class:`RecordingAnthropicClient` implement this so unit tests don't
    need to mock httpx. Adapters raise :class:`LLMError` subclasses
    rather than letting transport exceptions leak.
    """

    async def messages(
        self,
        *,
        model: str,
        system: str | list[dict[str, Any]] | None,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
        temperature: float | None = None,
        thinking: dict[str, Any] | None = None,
        output_config: dict[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        """POST ``/v1/messages`` and return the parsed JSON body."""


@dataclass
class HTTPAnthropicClient:
    """Production :class:`AnthropicClient` — talks to the real API.

    ``transport`` is an optional injection point for tests: pass an
    :class:`httpx.MockTransport` to exercise the status-code → error
    mapping without hitting the network. Production callers leave it
    as ``None`` so httpx uses its default transport.
    """

    api_key: str
    base_url: str = _DEFAULT_BASE_URL
    timeout_s: float = _DEFAULT_TIMEOUT_S
    transport: httpx.AsyncBaseTransport | None = None

    async def messages(
        self,
        *,
        model: str,
        system: str | list[dict[str, Any]] | None,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
        temperature: float | None = None,
        thinking: dict[str, Any] | None = None,
        output_config: dict[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if system is not None:
            body["system"] = system
        if tools:
            body["tools"] = tools
        if temperature is not None:
            body["temperature"] = temperature
        # Stream CM-9 — compute-control fields (both GA, no beta header).
        if thinking is not None:
            body["thinking"] = thinking
        if output_config is not None:
            body["output_config"] = output_config

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_s, transport=self.transport
            ) as client:
                response = await client.post(
                    f"{self.base_url}/v1/messages",
                    headers={
                        "x-api-key": self.api_key,
                        "anthropic-version": _ANTHROPIC_VERSION,
                        "content-type": "application/json",
                    },
                    json=body,
                )
        except httpx.HTTPError as exc:
            raise LLMNetworkError(f"anthropic: {exc}") from exc

        status = response.status_code
        if status == 401:
            # Stream L.L8 — surface 401 distinctly so OAuth-capable
            # providers can opt into the router's refresh + retry path.
            raise LLMUnauthorizedError(f"anthropic 401: {_truncate(response.text)}")
        if status == 429:
            raise LLMRateLimitError(f"anthropic 429: {_truncate(response.text)}")
        if 400 <= status < 500:
            raise LLMClientError(f"anthropic {status}: {_truncate(response.text)}")
        if status >= 500:
            raise LLMServerError(f"anthropic {status}: {_truncate(response.text)}")

        data = response.json()
        if not isinstance(data, Mapping):
            raise LLMServerError(f"anthropic returned non-object body: {type(data).__name__}")
        return data


@dataclass
class RecordingAnthropicClient:
    """In-memory :class:`AnthropicClient` for dev / tests.

    Returns ``response`` for every call; records every kwargs dict into
    ``calls`` so tests can assert on the request shape. If
    ``raise_with`` is set, raises that exception instead — used to
    exercise the router's fallback path.
    """

    response: Mapping[str, Any] = field(default_factory=dict)
    raise_with: BaseException | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def messages(
        self,
        *,
        model: str,
        system: str | list[dict[str, Any]] | None,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
        temperature: float | None = None,
        thinking: dict[str, Any] | None = None,
        output_config: dict[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        self.calls.append(
            {
                "model": model,
                "system": system,
                "messages": messages,
                "tools": tools,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "thinking": thinking,
                "output_config": output_config,
            }
        )
        if self.raise_with is not None:
            raise self.raise_with
        return self.response


@dataclass
class AnthropicProvider:
    """:class:`LLMProvider` for Anthropic Messages API.

    The provider is intentionally lightweight: encode messages + tools,
    delegate to :class:`AnthropicClient`, decode the response. All
    fallback / retry / breaker logic lives in
    :class:`~orchestrator.llm.router.LLMRouter` + E.4 middleware so the
    adapter has a single responsibility and is trivial to swap.
    """

    client: AnthropicClient
    model: str
    max_tokens: int = DEFAULT_MAX_TOKENS
    #: Sampling temperature (``ModelSpec.temperature``). ``None`` omits
    #: it from the request so the API applies its own default.
    temperature: float | None = None
    #: Resolves ``image_ref`` content blocks to bytes at call time (J.6
    #: Path A). ``None`` → image blocks are dropped with a warning.
    image_resolver: ImageResolver | None = None
    #: Stream L.L1 — flip Anthropic prompt caching markers on. When
    #: ``True`` the adapter wraps the outbound ``system`` field and the
    #: trailing ``_CACHE_CONTROL_TAIL_COUNT`` non-system messages in
    #: ``cache_control: {"type": "ephemeral"}`` so the upstream caches
    #: the prefix (Mini-ADR L-1). Defaults ``True`` because the
    #: feature is upstream-supported and lossless when the prefix is
    #: stable. The agent factory wires this from
    #: :attr:`ModelSpec.cache_enabled`.
    cache_enabled: bool = True
    #: Stream CM-9 (Mini-ADR CM-J2) — ``output_config.effort`` level.
    #: ``None`` omits the field (API default). The factory gates this on
    #: the model catalog's ``effort`` capability bit.
    effort: str | None = None
    #: Stream CM-9 — send ``thinking: {"type": "adaptive"}`` (4.6+).
    adaptive_thinking: bool = False

    async def complete(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
    ) -> AIMessage:
        system_text, mapped, anchor_indices = await _to_anthropic_messages(
            messages, self.image_resolver
        )
        tool_payload = [_to_anthropic_tool(spec) for spec in tools] if tools else None

        # Stream L.L1 — convert ``system`` from string to block list with
        # cache_control marker, then mark the trailing messages.
        # Capability Uplift Sprint #8 (Mini-ADR U-7) — also mark any
        # messages flagged with ``helix_cache_anchor`` (currently only
        # the per_session memory block) so the cache covers the prefix
        # ``[system, task, memories]`` across all turns.
        # When ``cache_enabled`` is False the adapter emits the original
        # string-shaped system so a manifest-level opt-out cleanly
        # disables the feature on a per-model basis.
        system_payload: str | list[dict[str, Any]] | None
        if self.cache_enabled:
            system_payload, mapped = _apply_cache_control(
                system_text, mapped, cache_anchor_indices=anchor_indices
            )
        else:
            system_payload = system_text

        body = await self.client.messages(
            model=self.model,
            system=system_payload,
            messages=mapped,
            tools=tool_payload,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            thinking={"type": "adaptive"} if self.adaptive_thinking else None,
            output_config={"effort": self.effort} if self.effort is not None else None,
        )

        return _from_anthropic_response(body)


def _truncate(text: str) -> str:
    if len(text) <= _ERROR_BODY_CHAR_CAP:
        return text
    return text[:_ERROR_BODY_CHAR_CAP] + "...[truncated]"


def _apply_cache_control(
    system: str | None,
    mapped: list[dict[str, Any]],
    *,
    cache_anchor_indices: Sequence[int] = (),
) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]]]:
    """Stream L.L1 — annotate the outbound payload for prompt caching.

    Rewrites ``system`` from string form to a one-element block list
    with ``cache_control`` on the block, and adds ``cache_control`` to
    the last content block of each of the trailing
    ``_CACHE_CONTROL_TAIL_COUNT`` non-system messages. The block-list
    shape is what Anthropic requires when marking ``system`` for
    caching; the per-message marker lets the upstream cache
    progressively longer prefixes as the conversation grows. See
    Mini-ADR L-1.

    Capability Uplift Sprint #8 (Mini-ADR U-7): also marks any
    messages whose index appears in ``cache_anchor_indices`` —
    currently the ``per_session`` memory block at position 1. These
    are deduplicated against the trailing window so a message that
    is both an anchor and in the tail only gets one marker. Anthropic
    caps total ``cache_control`` markers at 4 per request
    (``system + _CACHE_CONTROL_TAIL_COUNT (2) + anchors (≤ 1) = ≤ 4``
    in Sprint #8 setup).

    Returns ``(system_payload, mapped)`` — ``mapped`` is mutated in
    place for clarity but also returned so callers can keep a
    single-expression assignment.
    """
    system_payload: list[dict[str, Any]] | None = None
    if system:
        system_payload = [
            {"type": "text", "text": system, "cache_control": dict(_CACHE_CONTROL_EPHEMERAL)}
        ]

    if not mapped:
        return system_payload, mapped

    # Build the set of indices to mark — union of the tail window and
    # the explicit anchor list. ``set`` collapses overlap (an anchor
    # already inside the tail window only gets one marker).
    tail_start = max(0, len(mapped) - _CACHE_CONTROL_TAIL_COUNT)
    indices_to_mark: set[int] = set(range(tail_start, len(mapped)))
    for idx in cache_anchor_indices:
        if 0 <= idx < len(mapped):
            indices_to_mark.add(idx)
            # Track anchor application separately from the tail so the
            # uplift metric reflects helix-injected breakpoints only.
            if idx < tail_start:
                record_anthropic_cache_anchor()

    for idx in sorted(indices_to_mark):
        _mark_message_cache_control(mapped[idx])

    return system_payload, mapped


def _mark_message_cache_control(message: dict[str, Any]) -> None:
    """Add ``cache_control`` to the last content block of a single
    outbound message. Plain-text content is upgraded to a block list so
    the marker has a place to live.
    """
    content = message.get("content")
    if isinstance(content, str):
        message["content"] = [
            {"type": "text", "text": content, "cache_control": dict(_CACHE_CONTROL_EPHEMERAL)}
        ]
        return
    if isinstance(content, list) and content:
        last = content[-1]
        if isinstance(last, dict):
            last["cache_control"] = dict(_CACHE_CONTROL_EPHEMERAL)
        else:
            # ``content`` list with a non-dict tail entry — defensive
            # path; promote to a wrapper block carrying the marker so
            # the wire payload stays schema-valid.
            content[-1] = {
                "type": "text",
                "text": str(last),
                "cache_control": dict(_CACHE_CONTROL_EPHEMERAL),
            }


async def _to_anthropic_messages(
    messages: Sequence[BaseMessage], resolver: ImageResolver | None
) -> tuple[str | None, list[dict[str, Any]], list[int]]:
    """Split system content from the message list and map the rest.

    Anthropic places system prompts in a top-level ``system`` field; we
    concatenate any :class:`SystemMessage` instances we find rather
    than emitting an unsupported ``role: "system"`` message.

    Capability Uplift Sprint #8 (Mini-ADR U-7): tracks the indices
    (in the mapped list) of messages whose ``additional_kwargs`` carry
    ``"helix_cache_anchor": True`` so :func:`_apply_cache_control` can
    mark them with ``cache_control`` for prompt-cache prefix coverage.
    Returned as a side channel rather than baked into the mapped dicts
    so the wire payload stays Anthropic-clean.
    """
    system_parts: list[str] = []
    mapped: list[dict[str, Any]] = []
    cache_anchor_indices: list[int] = []

    for msg in messages:
        if isinstance(msg, SystemMessage):
            system_parts.append(_message_text(msg))
            continue
        if isinstance(msg, HumanMessage):
            mapped.append({"role": "user", "content": await _human_content(msg, resolver)})
        elif isinstance(msg, AIMessage):
            mapped.append(_ai_message_to_anthropic(msg))
        elif isinstance(msg, ToolMessage):
            mapped.append(_tool_message_to_anthropic(msg))
        else:
            # Unknown message subclass — fall back to a user message
            # with whatever text we can recover. Better than silently
            # dropping it.
            logger.warning(
                "anthropic_adapter.unknown_message_type type=%s",
                type(msg).__name__,
            )
            mapped.append({"role": "user", "content": _message_text(msg)})
        # Sprint #8 — note cache anchors AFTER the mapped append so the
        # index points at the freshly-added dict.
        extra = getattr(msg, "additional_kwargs", None)
        if isinstance(extra, dict) and extra.get("helix_cache_anchor"):
            cache_anchor_indices.append(len(mapped) - 1)

    system = "\n\n".join(p for p in system_parts if p) or None
    return system, mapped, cache_anchor_indices


def _ai_message_to_anthropic(msg: AIMessage) -> dict[str, Any]:
    tool_calls = list(getattr(msg, "tool_calls", None) or [])
    text = _message_text(msg)

    if not tool_calls:
        return {"role": "assistant", "content": text}

    content: list[dict[str, Any]] = []
    if text:
        content.append({"type": "text", "text": text})
    for tc in tool_calls:
        content.append(
            {
                "type": "tool_use",
                "id": str(tc.get("id") or ""),
                "name": str(tc.get("name") or ""),
                "input": tc.get("args") or {},
            }
        )
    return {"role": "assistant", "content": content}


def _tool_message_to_anthropic(msg: ToolMessage) -> dict[str, Any]:
    return {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": str(getattr(msg, "tool_call_id", "") or ""),
                "content": _message_text(msg),
            }
        ],
    }


def _message_text(msg: BaseMessage) -> str:
    """Stringify ``msg.content`` regardless of whether it's str or block list.

    LangChain allows ``content`` to be either ``str`` or
    ``list[ContentBlock]``; we flatten the list form by concatenating
    any block's ``"text"`` value so the adapter never sees a list at
    the wire boundary.
    """
    content = msg.content
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, Mapping):
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


async def _human_content(
    msg: HumanMessage, resolver: ImageResolver | None
) -> str | list[dict[str, Any]]:
    """Map a ``HumanMessage`` to Anthropic ``content``.

    Plain text → a string. With ``image_ref`` blocks (J.6 Path A) → a
    block list whose images are resolved to base64 ``image`` blocks. A
    missing resolver drops the images with a warning so a text-only
    deployment never crashes on an image-bearing message.
    """
    text, image_refs = split_human_content(msg.content)
    if not image_refs:
        return text
    if resolver is None:
        logger.warning("anthropic_adapter.image_dropped_no_resolver count=%d", len(image_refs))
        return text
    blocks: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for ref in image_refs:
        resolved = await resolver.resolve(ref)
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": resolved.media_type,
                    "data": resolved.base64_data,
                },
            }
        )
    return blocks


def _to_anthropic_tool(spec: ToolSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "description": spec.description,
        "input_schema": dict(spec.parameters) or {"type": "object", "properties": {}},
    }


def _from_anthropic_response(body: Mapping[str, Any]) -> AIMessage:
    """Decode Anthropic's content-blocks array into a LangChain AIMessage.

    Stream L.L1 — when the upstream returns cache token counters in
    ``usage`` (``cache_creation_input_tokens`` / ``cache_read_input_tokens``)
    they ride along on ``AIMessage.usage_metadata`` so dashboards /
    middleware (E.5 langfuse) can observe cache hit rate. The decoder
    is lenient: any of the fields may be missing on older API versions
    or non-cached requests.
    """
    blocks = body.get("content") or []
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, Mapping):
                continue
            block_type = block.get("type")
            if block_type == "text":
                text = block.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
            elif block_type == "tool_use":
                tool_calls.append(
                    {
                        "id": str(block.get("id") or ""),
                        "name": str(block.get("name") or ""),
                        "args": dict(block.get("input") or {}),
                        "type": "tool_call",
                    }
                )

    usage_metadata = _extract_usage_metadata(body)
    if usage_metadata:
        return AIMessage(
            content="".join(text_parts),
            tool_calls=tool_calls,
            usage_metadata=usage_metadata,
        )
    return AIMessage(content="".join(text_parts), tool_calls=tool_calls)


def _extract_usage_metadata(body: Mapping[str, Any]) -> dict[str, Any] | None:
    """Stream L.L1 — pull cache-aware token counters out of the body.

    Anthropic returns ``usage`` with ``input_tokens`` /
    ``output_tokens`` plus the L1-specific
    ``cache_creation_input_tokens`` / ``cache_read_input_tokens`` when
    caching is active. The LangChain ``usage_metadata`` shape carries
    the standard counters in ``input_tokens`` / ``output_tokens`` /
    ``total_tokens``; cache counters land in
    ``input_token_details`` so downstream observability can split out
    the cached portion without crashing on older shapes.
    """
    usage_raw = body.get("usage")
    if not isinstance(usage_raw, Mapping):
        return None
    input_tokens = _coerce_int(usage_raw.get("input_tokens"))
    output_tokens = _coerce_int(usage_raw.get("output_tokens"))
    cache_creation = _coerce_int(usage_raw.get("cache_creation_input_tokens"))
    cache_read = _coerce_int(usage_raw.get("cache_read_input_tokens"))
    if (
        input_tokens is None
        and output_tokens is None
        and cache_creation is None
        and cache_read is None
    ):
        return None
    metadata: dict[str, Any] = {
        "input_tokens": input_tokens or 0,
        "output_tokens": output_tokens or 0,
        "total_tokens": (input_tokens or 0) + (output_tokens or 0),
    }
    cache_details: dict[str, int] = {}
    if cache_creation is not None:
        cache_details["cache_creation"] = cache_creation
    if cache_read is not None:
        cache_details["cache_read"] = cache_read
    if cache_details:
        metadata["input_token_details"] = cache_details
    return metadata


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):  # bool is a subclass of int — reject explicitly
        return None
    if isinstance(value, int):
        return value
    return None
