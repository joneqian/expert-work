"""Anthropic Messages API adapter — Stream E.11.

Translates the orchestrator's :class:`BaseMessage` history +
:class:`ToolSpec` catalogue into Anthropic's Messages API wire format,
posts via :class:`AnthropicClient` (httpx in production, recording
client in tests), and maps the response back to a LangChain
:class:`AIMessage` with ``tool_calls`` populated.

Wire-format mapping (covered, deliberately minimal for M0):

- ``SystemMessage`` → top-level ``system`` field. Multiple system
  messages are concatenated with ``\\n\\n`` separators.
- ``HumanMessage`` → ``{"role": "user", "content": <text>}``.
- ``AIMessage`` with text only → ``{"role": "assistant", "content": <text>}``.
- ``AIMessage`` with ``tool_calls`` → ``{"role": "assistant", "content":
  [{"type": "text", ...}, {"type": "tool_use", "id": ..., "name": ...,
  "input": ...}, ...]}``.
- ``ToolMessage`` → ``{"role": "user", "content": [{"type":
  "tool_result", "tool_use_id": ..., "content": <text>}]}``.

Out of scope for M0 (deferred to M1-D hardening):

- Multimodal content (images, PDF). Anthropic supports them; we don't
  surface them in :class:`BaseMessage` yet.
- Streaming responses (``stream=true``). Routed through E.14 SSE later.
- Cache control and tool ``cache_control`` blocks.

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

from helix_agent.runtime.middleware import (
    LLMClientError,
    LLMNetworkError,
    LLMRateLimitError,
    LLMServerError,
)
from orchestrator.tools.registry import ToolSpec

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://api.anthropic.com"
_DEFAULT_TIMEOUT_S = 60.0
DEFAULT_MAX_TOKENS = 4096
_ANTHROPIC_VERSION = "2023-06-01"
_ERROR_BODY_CHAR_CAP = 500


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
        system: str | None,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
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
        system: str | None,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
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
        system: str | None,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
    ) -> Mapping[str, Any]:
        self.calls.append(
            {
                "model": model,
                "system": system,
                "messages": messages,
                "tools": tools,
                "max_tokens": max_tokens,
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

    async def complete(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
    ) -> AIMessage:
        system, mapped = _to_anthropic_messages(messages)
        tool_payload = [_to_anthropic_tool(spec) for spec in tools] if tools else None

        body = await self.client.messages(
            model=self.model,
            system=system,
            messages=mapped,
            tools=tool_payload,
            max_tokens=self.max_tokens,
        )

        return _from_anthropic_response(body)


def _truncate(text: str) -> str:
    if len(text) <= _ERROR_BODY_CHAR_CAP:
        return text
    return text[:_ERROR_BODY_CHAR_CAP] + "...[truncated]"


def _to_anthropic_messages(
    messages: Sequence[BaseMessage],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Split system content from the message list and map the rest.

    Anthropic places system prompts in a top-level ``system`` field; we
    concatenate any :class:`SystemMessage` instances we find rather
    than emitting an unsupported ``role: "system"`` message.
    """
    system_parts: list[str] = []
    mapped: list[dict[str, Any]] = []

    for msg in messages:
        if isinstance(msg, SystemMessage):
            system_parts.append(_message_text(msg))
        elif isinstance(msg, HumanMessage):
            mapped.append({"role": "user", "content": _message_text(msg)})
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

    system = "\n\n".join(p for p in system_parts if p) or None
    return system, mapped


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


def _to_anthropic_tool(spec: ToolSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "description": spec.description,
        "input_schema": dict(spec.parameters) or {"type": "object", "properties": {}},
    }


def _from_anthropic_response(body: Mapping[str, Any]) -> AIMessage:
    """Decode Anthropic's content-blocks array into a LangChain AIMessage."""
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

    return AIMessage(content="".join(text_parts), tool_calls=tool_calls)
