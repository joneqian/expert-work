"""OpenAI Chat Completions adapter — Stream E.11.

Translates the orchestrator's :class:`BaseMessage` history +
:class:`ToolSpec` catalogue into OpenAI's Chat Completions wire format,
posts via :class:`OpenAIClient` (httpx in production, recording client
in tests), and maps the response back to a LangChain :class:`AIMessage`
with ``tool_calls`` populated.

Wire-format mapping (M0 minimal):

- ``SystemMessage`` → ``{"role": "system", "content": <text>}``.
- ``HumanMessage`` → ``{"role": "user", "content": <text>}``.
- ``AIMessage`` (text only) → ``{"role": "assistant", "content": <text>}``.
- ``AIMessage`` with ``tool_calls`` → ``{"role": "assistant", "content":
  null, "tool_calls": [{"id": ..., "type": "function", "function":
  {"name": ..., "arguments": <json-string>}}]}``.
- ``ToolMessage`` → ``{"role": "tool", "tool_call_id": ..., "content":
  <text>}``.

Differences from :mod:`anthropic` adapter (kept in sync with vendor docs):

- OpenAI puts ``system`` in the messages array, Anthropic at the top
  level — adapter responsibility.
- OpenAI ``arguments`` is a JSON-encoded string; we ``json.dumps`` the
  ``args`` dict on the way out and ``json.loads`` on the way in.

Error mapping per :class:`LLMError` hierarchy (E.4):

- HTTP 429 → :class:`LLMRateLimitError`
- HTTP 4xx other → :class:`LLMClientError` (NOT retried, NOT fallback)
- HTTP 5xx → :class:`LLMServerError`
- :class:`httpx.HTTPError` (network / TLS) → :class:`LLMNetworkError`
"""

from __future__ import annotations

import json
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

_DEFAULT_BASE_URL = "https://api.openai.com"
_DEFAULT_TIMEOUT_S = 60.0
_ERROR_BODY_CHAR_CAP = 500


@runtime_checkable
class OpenAIClient(Protocol):
    """Sized to the one Chat Completions endpoint we use.

    Both :class:`HTTPOpenAIClient` and :class:`RecordingOpenAIClient`
    implement this. Adapters raise :class:`LLMError` subclasses rather
    than letting transport exceptions leak.
    """

    async def chat_completions(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> Mapping[str, Any]:
        """POST ``/v1/chat/completions`` and return the parsed JSON body."""


@dataclass
class HTTPOpenAIClient:
    """Production :class:`OpenAIClient` — talks to the real API.

    ``transport`` is an optional injection point for tests; see
    :class:`~orchestrator.llm.providers.anthropic.HTTPAnthropicClient`
    for the rationale.
    """

    api_key: str
    base_url: str = _DEFAULT_BASE_URL
    timeout_s: float = _DEFAULT_TIMEOUT_S
    transport: httpx.AsyncBaseTransport | None = None

    async def chat_completions(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> Mapping[str, Any]:
        body: dict[str, Any] = {"model": model, "messages": messages}
        if tools:
            body["tools"] = tools

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_s, transport=self.transport
            ) as client:
                response = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    headers={
                        "authorization": f"Bearer {self.api_key}",
                        "content-type": "application/json",
                    },
                    json=body,
                )
        except httpx.HTTPError as exc:
            raise LLMNetworkError(f"openai: {exc}") from exc

        status = response.status_code
        if status == 429:
            raise LLMRateLimitError(f"openai 429: {_truncate(response.text)}")
        if 400 <= status < 500:
            raise LLMClientError(f"openai {status}: {_truncate(response.text)}")
        if status >= 500:
            raise LLMServerError(f"openai {status}: {_truncate(response.text)}")

        data = response.json()
        if not isinstance(data, Mapping):
            raise LLMServerError(f"openai returned non-object body: {type(data).__name__}")
        return data


@dataclass
class RecordingOpenAIClient:
    """In-memory :class:`OpenAIClient` for dev / tests.

    Behaviour mirrors
    :class:`~orchestrator.llm.providers.anthropic.RecordingAnthropicClient`:
    canned response, recorded calls, optional injected exception.
    """

    response: Mapping[str, Any] = field(default_factory=dict)
    raise_with: BaseException | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def chat_completions(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> Mapping[str, Any]:
        self.calls.append({"model": model, "messages": messages, "tools": tools})
        if self.raise_with is not None:
            raise self.raise_with
        return self.response


@dataclass
class OpenAIProvider:
    """:class:`LLMProvider` for OpenAI Chat Completions."""

    client: OpenAIClient
    model: str

    async def complete(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
    ) -> AIMessage:
        mapped = _to_openai_messages(messages)
        tool_payload = [_to_openai_tool(spec) for spec in tools] if tools else None

        body = await self.client.chat_completions(
            model=self.model,
            messages=mapped,
            tools=tool_payload,
        )

        return _from_openai_response(body)


def _truncate(text: str) -> str:
    if len(text) <= _ERROR_BODY_CHAR_CAP:
        return text
    return text[:_ERROR_BODY_CHAR_CAP] + "...[truncated]"


def _to_openai_messages(messages: Sequence[BaseMessage]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            out.append({"role": "system", "content": _message_text(msg)})
        elif isinstance(msg, HumanMessage):
            out.append({"role": "user", "content": _message_text(msg)})
        elif isinstance(msg, AIMessage):
            out.append(_ai_message_to_openai(msg))
        elif isinstance(msg, ToolMessage):
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": str(getattr(msg, "tool_call_id", "") or ""),
                    "content": _message_text(msg),
                }
            )
        else:
            logger.warning(
                "openai_adapter.unknown_message_type type=%s",
                type(msg).__name__,
            )
            out.append({"role": "user", "content": _message_text(msg)})
    return out


def _ai_message_to_openai(msg: AIMessage) -> dict[str, Any]:
    tool_calls = list(getattr(msg, "tool_calls", None) or [])
    text = _message_text(msg)

    if not tool_calls:
        return {"role": "assistant", "content": text}

    return {
        "role": "assistant",
        "content": text or None,
        "tool_calls": [
            {
                "id": str(tc.get("id") or ""),
                "type": "function",
                "function": {
                    "name": str(tc.get("name") or ""),
                    "arguments": json.dumps(tc.get("args") or {}),
                },
            }
            for tc in tool_calls
        ],
    }


def _message_text(msg: BaseMessage) -> str:
    """See :func:`orchestrator.llm.providers.anthropic._message_text`."""
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


def _to_openai_tool(spec: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": dict(spec.parameters) or {"type": "object", "properties": {}},
        },
    }


def _from_openai_response(body: Mapping[str, Any]) -> AIMessage:
    """Decode OpenAI choice[0].message into a LangChain AIMessage.

    Tolerant of empty / malformed responses — returns an empty
    :class:`AIMessage` rather than raising so the router can let the
    LLM "say nothing" gracefully. Real production errors come from
    :class:`LLMError` raised by the client, not from parse.
    """
    choices = body.get("choices") or []
    if not isinstance(choices, list) or not choices:
        return AIMessage(content="")

    first = choices[0]
    if not isinstance(first, Mapping):
        return AIMessage(content="")
    message = first.get("message")
    if not isinstance(message, Mapping):
        return AIMessage(content="")

    raw_content = message.get("content")
    text = raw_content if isinstance(raw_content, str) else ""
    raw_tool_calls = message.get("tool_calls") or []

    tool_calls: list[dict[str, Any]] = []
    if isinstance(raw_tool_calls, list):
        for tc in raw_tool_calls:
            if not isinstance(tc, Mapping):
                continue
            fn = tc.get("function") or {}
            if not isinstance(fn, Mapping):
                continue
            tool_calls.append(
                {
                    "id": str(tc.get("id") or ""),
                    "name": str(fn.get("name") or ""),
                    "args": _parse_arguments(fn.get("arguments")),
                    "type": "tool_call",
                }
            )

    return AIMessage(content=text, tool_calls=tool_calls)


def _parse_arguments(raw: Any) -> dict[str, Any]:
    """OpenAI sends ``arguments`` as a JSON string; tolerate dict too."""
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, Mapping):
            return dict(parsed)
    return {}
