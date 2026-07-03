"""OpenAI Chat Completions adapter — Stream E.11.

Translates the orchestrator's :class:`BaseMessage` history +
:class:`ToolSpec` catalogue into OpenAI's Chat Completions wire format,
posts via :class:`OpenAIClient` (httpx in production, recording client
in tests), and maps the response back to a LangChain :class:`AIMessage`
with ``tool_calls`` populated.

Wire-format mapping (M0 minimal):

- ``SystemMessage`` → ``{"role": "system", "content": <text>}``. Stream
  RT-2 (RT-ADR-5): mid-conversation SystemMessages are coalesced into
  the leading system entry before mapping — strict OpenAI-compatible
  backends reject a non-leading ``system`` role with 400. See
  :mod:`orchestrator.llm.coalesce`.
- ``HumanMessage`` → ``{"role": "user", "content": <text>}``; when the
  message carries ``image_ref`` blocks (J.6 Path A) ``content`` becomes a
  block list with ``{"type": "image_url", ...}`` entries, the images
  resolved to data URIs via an :class:`ImageResolver`.
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
    LLMServerError,
)
from orchestrator.llm.coalesce import coalesce_system_messages
from orchestrator.llm.providers._errors import classify_http_error
from orchestrator.llm.providers._metrics import disclosure_fallback_total
from orchestrator.multimodal import ImageResolver, split_human_content
from orchestrator.tools.registry import ToolSpec

logger = logging.getLogger(__name__)


_DEFAULT_BASE_URL = "https://api.openai.com"
DEFAULT_CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
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
        temperature: float | None = None,
        extra_body: dict[str, Any] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        """POST ``/v1/chat/completions`` and return the parsed JSON body.

        ``extra_body`` (Stream CM-10, Mini-ADR CM-L3) merges vendor
        extension fields into the top-level request body — the thinking
        controls of the OpenAI-compatible vendors (``enable_thinking``,
        ``thinking``, ``reasoning_effort``) are all top-level
        non-standard fields, so one channel covers every vendor."""


@dataclass
class HTTPOpenAIClient:
    """Production :class:`OpenAIClient` — talks to the real API.

    ``transport`` is an optional injection point for tests; see
    :class:`~orchestrator.llm.providers.anthropic.HTTPAnthropicClient`
    for the rationale.

    ``chat_completions_path`` lets OpenAI-compatible vendors override the
    default ``/v1/chat/completions`` suffix — most (DeepSeek, Moonshot,
    DashScope compatible-mode) keep ``/v1/chat/completions``, but Zhipu
    GLM uses ``/api/paas/v4/chat/completions`` and Volcengine ARK
    (Doubao) uses ``/api/v3/chat/completions``. See
    :mod:`orchestrator.llm.providers.openai_compatible` for the
    pre-configured factory functions.
    """

    api_key: str
    base_url: str = _DEFAULT_BASE_URL
    timeout_s: float = _DEFAULT_TIMEOUT_S
    transport: httpx.AsyncBaseTransport | None = None
    chat_completions_path: str = DEFAULT_CHAT_COMPLETIONS_PATH
    #: Auth header name + value prefix. OpenAI and the compatible
    #: regional vendors use ``Authorization: Bearer <key>``; Azure
    #: OpenAI uses ``api-key: <key>`` (prefix empty).
    api_key_header: str = "authorization"
    api_key_prefix: str = "Bearer "

    async def chat_completions(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        temperature: float | None = None,
        extra_body: dict[str, Any] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        body: dict[str, Any] = {"model": model, "messages": messages}
        if tools:
            body["tools"] = tools
        if tool_choice is not None:
            # Stream HX-13 — the allowed_tools subset constraint.
            body["tool_choice"] = tool_choice
        if temperature is not None:
            body["temperature"] = temperature
        if extra_body:
            # Stream CM-10 — vendor thinking controls, merged last so the
            # translated payload is exactly what goes on the wire.
            body.update(extra_body)

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_s, transport=self.transport
            ) as client:
                response = await client.post(
                    f"{self.base_url}{self.chat_completions_path}",
                    headers={
                        self.api_key_header: f"{self.api_key_prefix}{self.api_key}",
                        "content-type": "application/json",
                    },
                    json=body,
                )
        except httpx.HTTPError as exc:
            raise LLMNetworkError(f"openai: {exc}") from exc

        status = response.status_code
        if status >= 400:
            # Stream Y-MK — shared classifier splits account/key-level failures
            # (402 / quota / billing → LLMKeyUnavailableError, router tries a
            # sibling key) from plain 429 rate-limits, malformed 4xx, and 5xx.
            # 401 stays an LLMUnauthorizedError for the L.L8 OAuth refresh path.
            raise classify_http_error("openai", status, _truncate(response.text))

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
        temperature: float | None = None,
        extra_body: dict[str, Any] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "tools": tools,
                "temperature": temperature,
                "extra_body": extra_body,
                "tool_choice": tool_choice,
            }
        )
        if self.raise_with is not None:
            raise self.raise_with
        return self.response


@dataclass
class OpenAIProvider:
    """:class:`LLMProvider` for OpenAI Chat Completions.

    ``temperature`` is the manifest's sampling temperature
    (``ModelSpec.temperature``). ``None`` omits it from the request so
    the provider applies its own default.
    """

    client: OpenAIClient
    model: str
    temperature: float | None = None
    #: Resolves ``image_ref`` content blocks to bytes at call time (J.6
    #: Path A). ``None`` → image blocks are dropped with a warning.
    image_resolver: ImageResolver | None = None
    #: Stream CM-10 (Mini-ADR CM-L3) — pre-translated vendor thinking
    #: payload (``_thinking_payload``), merged into the request body.
    #: ``None`` (every untouched manifest) keeps the body byte-identical.
    thinking_payload: dict[str, Any] | None = None
    #: Stream HX-13 (Mini-ADR HX-J4) — set after the allowed_tools
    #: constraint is rejected once: this provider instance falls back to
    #: the application tier for its remaining lifetime (restart retries).
    _allowed_tools_disabled: bool = field(default=False, init=False, repr=False)

    async def complete(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
    ) -> AIMessage:
        # Stream RT-2 (RT-ADR-5) — strict OpenAI-compatible backends 400 on
        # a non-leading ``system`` role (the L2 ``<context-summary>`` lands
        # mid-list); fold mid-conversation SystemMessages into the leading
        # system before mapping. Per-request only; input never mutated.
        messages = coalesce_system_messages(messages)
        mapped = await _to_openai_messages(messages, self.image_resolver)
        # Stream HX-13 — defer markers ride the specs (agent_node sets them
        # on the allowed_tools tier): the FULL schema set stays on the wire
        # (prompt-cache friendly) and the marked tools are excluded from
        # the allowed subset until promoted.
        use_allowed = any(spec.defer_loading for spec in tools) and not self._allowed_tools_disabled
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

        try:
            body = await self.client.chat_completions(
                model=self.model,
                messages=mapped,
                tools=tool_payload,
                temperature=self.temperature,
                extra_body=self.thinking_payload,
                tool_choice=tool_choice,
            )
        except LLMClientError:
            if not use_allowed:
                raise
            # Stream HX-13 (Mini-ADR HX-J4) — the allowed_tools constraint
            # was rejected. Fail open: drop to the application tier for
            # this provider instance and resend once without it.
            self._allowed_tools_disabled = True
            disclosure_fallback_total.labels(provider="openai").inc()
            logger.warning("openai.allowed_tools_rejected — falling back to app tier")
            body = await self.client.chat_completions(
                model=self.model,
                messages=mapped,
                tools=tool_payload,
                temperature=self.temperature,
                extra_body=self.thinking_payload,
                tool_choice=None,
            )

        return _from_openai_response(body)


def _truncate(text: str) -> str:
    if len(text) <= _ERROR_BODY_CHAR_CAP:
        return text
    return text[:_ERROR_BODY_CHAR_CAP] + "...[truncated]"


async def _to_openai_messages(
    messages: Sequence[BaseMessage], resolver: ImageResolver | None
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            out.append({"role": "system", "content": _message_text(msg)})
        elif isinstance(msg, HumanMessage):
            out.append({"role": "user", "content": await _human_content(msg, resolver)})
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


async def _human_content(
    msg: HumanMessage, resolver: ImageResolver | None
) -> str | list[dict[str, Any]]:
    """Map a ``HumanMessage`` to OpenAI ``content``.

    Plain text → a string. With ``image_ref`` blocks (J.6 Path A) → a
    block list whose images are resolved to ``image_url`` data URIs. A
    missing resolver drops the images with a warning so a text-only
    deployment never crashes on an image-bearing message.
    """
    text, image_refs = split_human_content(msg.content)
    if not image_refs:
        return text
    if resolver is None:
        logger.warning("openai_adapter.image_dropped_no_resolver count=%d", len(image_refs))
        return text
    blocks: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for ref in image_refs:
        resolved = await resolver.resolve(ref)
        blocks.append({"type": "image_url", "image_url": {"url": resolved.data_uri}})
    return blocks


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

    # Event-stream enrichment — surface the fields the OpenAI-compatible
    # vendors return but the M0 minimal decoder dropped:
    #   * ``usage_metadata`` (body.usage) — REQUIRED for token metering
    #     (TokenUsageMiddleware reads AIMessage.usage_metadata; without it
    #     every compat-vendor turn meters zero) + langfuse cost observability.
    #   * ``additional_kwargs.reasoning_content`` — the thinking trace
    #     (DeepSeek / Qwen / Doubao return ``message.reasoning_content``).
    #   * ``response_metadata`` — finish_reason / model / system_fingerprint.
    additional_kwargs: dict[str, Any] = {}
    reasoning = message.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning:
        additional_kwargs["reasoning_content"] = reasoning

    return AIMessage(
        content=text,
        tool_calls=tool_calls,
        additional_kwargs=additional_kwargs,
        response_metadata=_extract_response_metadata(body, first),
        usage_metadata=_extract_usage_metadata(body),
    )


def _extract_usage_metadata(body: Mapping[str, Any]) -> dict[str, Any] | None:
    """Map OpenAI ``usage`` → the LangChain ``usage_metadata`` shape.

    OpenAI returns ``prompt_tokens`` / ``completion_tokens`` / ``total_tokens``
    plus optional ``prompt_tokens_details.cached_tokens`` and
    ``completion_tokens_details.reasoning_tokens``. LangChain carries the
    standard counters in ``input_tokens`` / ``output_tokens`` / ``total_tokens``
    with cache/reasoning splits in ``input_token_details`` /
    ``output_token_details``. Lenient: ``None`` when no usable counter is
    present (older / streaming-only shapes).
    """
    usage_raw = body.get("usage")
    if not isinstance(usage_raw, Mapping):
        return None
    input_tokens = _coerce_int(usage_raw.get("prompt_tokens"))
    output_tokens = _coerce_int(usage_raw.get("completion_tokens"))
    total = _coerce_int(usage_raw.get("total_tokens"))
    if input_tokens is None and output_tokens is None and total is None:
        return None
    metadata: dict[str, Any] = {
        "input_tokens": input_tokens or 0,
        "output_tokens": output_tokens or 0,
        "total_tokens": total if total is not None else (input_tokens or 0) + (output_tokens or 0),
    }
    prompt_details = usage_raw.get("prompt_tokens_details")
    if isinstance(prompt_details, Mapping):
        cached = _coerce_int(prompt_details.get("cached_tokens"))
        if cached is not None:
            metadata["input_token_details"] = {"cache_read": cached}
    completion_details = usage_raw.get("completion_tokens_details")
    if isinstance(completion_details, Mapping):
        reasoning = _coerce_int(completion_details.get("reasoning_tokens"))
        if reasoning is not None:
            metadata["output_token_details"] = {"reasoning": reasoning}
    return metadata


def _extract_response_metadata(
    body: Mapping[str, Any], choice: Mapping[str, Any]
) -> dict[str, Any]:
    """finish_reason / model / system_fingerprint, omitting absent keys."""
    metadata: dict[str, Any] = {}
    finish_reason = choice.get("finish_reason")
    if isinstance(finish_reason, str) and finish_reason:
        metadata["finish_reason"] = finish_reason
    model = body.get("model")
    if isinstance(model, str) and model:
        metadata["model_name"] = model
    fingerprint = body.get("system_fingerprint")
    if isinstance(fingerprint, str) and fingerprint:
        metadata["system_fingerprint"] = fingerprint
    return metadata


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):  # bool is an int subclass — reject explicitly
        return None
    if isinstance(value, int):
        return value
    return None


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
