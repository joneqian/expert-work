"""OpenAI-compatible LLM transport (Qwen/DashScope) — Stream CM-N5 run prep.

The P1 tier originally shipped Anthropic-only; real baseline runs use
the user's Qwen stack end to end (extraction, reading, judge), which is
both cheaper and closer to how their tenants actually deploy helix.
Same two adapter shapes as ``anthropic_client``:

- :class:`OpenAICompatCaller` — the ``LLMCaller`` shape.
- :class:`OpenAICompatTextJudge` — the ``TextJudge`` shape.

Judge-model divergence from the upstream protocols (gpt-4o family) is
already a stated CM-K6 caveat for the Anthropic judge; a Qwen judge is
the same situation and lands in the baseline fingerprint the same way.

Endpoint defaults to DashScope's compatible-mode URL; override with
``HELIX_EVAL_LLM_BASE_URL`` for any other OpenAI-compatible gateway.
``temperature=0`` always (these gateways accept it).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

DASHSCOPE_COMPAT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def render_chat_payload(
    messages: Sequence[BaseMessage],
    *,
    model: str,
    max_tokens: int,
) -> dict[str, Any]:
    """LangChain messages -> OpenAI ``/chat/completions`` body."""
    turns: list[dict[str, str]] = []
    for message in messages:
        content = message.content if isinstance(message.content, str) else str(message.content)
        if isinstance(message, SystemMessage):
            turns.append({"role": "system", "content": content})
        elif isinstance(message, AIMessage):
            turns.append({"role": "assistant", "content": content})
        else:
            turns.append({"role": "user", "content": content})
    return {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "messages": turns,
    }


class _ChatTransport:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DASHSCOPE_COMPAT_BASE_URL,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._http = http_client

    async def post(self, payload: dict[str, Any]) -> str:
        client = self._http or httpx.AsyncClient(timeout=120.0)
        try:
            response = await client.post(
                self._url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "content-type": "application/json",
                },
                json=payload,
            )
        finally:
            if self._http is None:
                await client.aclose()
        response.raise_for_status()
        body = response.json()
        choices = body.get("choices") or []
        if not choices:
            return ""
        content = (choices[0].get("message") or {}).get("content")
        return content if isinstance(content, str) else ""


class OpenAICompatCaller:
    """``LLMCaller``-shaped adapter (extraction / reconcile / reading)."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = DASHSCOPE_COMPAT_BASE_URL,
        max_tokens: int = 2048,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._transport = _ChatTransport(
            api_key=api_key, base_url=base_url, http_client=http_client
        )
        self._model = model
        self._max_tokens = max_tokens

    async def __call__(self, *, messages: Sequence[BaseMessage], tools: Sequence[Any]) -> AIMessage:
        del tools  # text-only eval calls
        text = await self._transport.post(
            render_chat_payload(messages, model=self._model, max_tokens=self._max_tokens)
        )
        return AIMessage(content=text)


class OpenAICompatTextJudge:
    """``TextJudge``-shaped adapter for the benchmark verdict calls."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = DASHSCOPE_COMPAT_BASE_URL,
        max_tokens: int = 256,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._transport = _ChatTransport(
            api_key=api_key, base_url=base_url, http_client=http_client
        )
        self._model = model
        self._max_tokens = max_tokens

    async def complete(self, *, prompt: str) -> str:
        return await self._transport.post(
            render_chat_payload(
                [HumanMessage(content=prompt)], model=self._model, max_tokens=self._max_tokens
            )
        )
