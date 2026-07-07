"""Anthropic transport for real eval runs — Stream CM-N5 P1.

Mirrors the repo's ``_judge.py`` HTTP shape (raw httpx against
``/v1/messages``, version ``2023-06-01``) instead of pulling the full
orchestrator provider stack into a dev tool. Two adapters share one
client:

- :class:`AnthropicCaller` — the ``LLMCaller`` shape
  (``(messages, tools) -> AIMessage``) used by ingestion extraction,
  reconcile, reading, and the optional LLM reranker arm. Tools are
  ignored (every eval call is text-only).
- :class:`AnthropicTextJudge` — the ``TextJudge`` shape for the
  benchmark verdicts.

``temperature=0.0`` is sent only when the model's catalog entry says it
supports sampling (opus-4-7+ rejects it with a 400 — Stream CM-9
finding); off-catalog models get it, matching the factory's
"operator's responsibility" stance.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage

from expert_work.protocol import catalog_entry
from longmem.transient import with_retries

_ANTHROPIC_API = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"

#: J-39 precedent — the repo's fixed judge/eval workhorse model.
DEFAULT_EVAL_MODEL = "claude-haiku-4-5-20251001"


def render_payload(
    messages: Sequence[BaseMessage],
    *,
    model: str,
    max_tokens: int,
) -> dict[str, Any]:
    """LangChain messages -> Anthropic Messages API body.

    ``SystemMessage`` lands in the top-level ``system`` field; human /
    AI messages alternate as user / assistant turns. Deterministic
    grading wants ``temperature=0`` wherever the model accepts it.
    """
    system_parts: list[str] = []
    turns: list[dict[str, str]] = []
    for message in messages:
        content = message.content if isinstance(message.content, str) else str(message.content)
        if isinstance(message, SystemMessage):
            system_parts.append(content)
        elif isinstance(message, AIMessage):
            turns.append({"role": "assistant", "content": content})
        else:
            turns.append({"role": "user", "content": content})
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": turns,
    }
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)
    catalog_model = model.removesuffix("-20251001")
    entry = catalog_entry("anthropic", model) or catalog_entry("anthropic", catalog_model)
    if entry is None or entry.sampling:
        payload["temperature"] = 0.0
    return payload


class _Transport:
    def __init__(self, *, api_key: str, http_client: httpx.AsyncClient | None = None) -> None:
        self._api_key = api_key
        self._http = http_client

    async def post(self, payload: dict[str, Any]) -> str:
        # Same transient policy as the OpenAI-compat transport — an
        # hours-long run must out-wait transport drops and throttling.
        return await with_retries(lambda: self._post_once(payload))

    async def _post_once(self, payload: dict[str, Any]) -> str:
        client = self._http or httpx.AsyncClient(timeout=120.0)
        try:
            response = await client.post(
                _ANTHROPIC_API,
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": _ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                json=payload,
            )
        finally:
            if self._http is None:
                await client.aclose()
        response.raise_for_status()
        body = response.json()
        blocks = body.get("content") or []
        texts = [
            b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "".join(texts)


class AnthropicCaller:
    """``LLMCaller``-shaped adapter for ingestion / reading / rerank."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_EVAL_MODEL,
        max_tokens: int = 2048,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._transport = _Transport(api_key=api_key, http_client=http_client)
        self._model = model
        self._max_tokens = max_tokens

    async def __call__(self, *, messages: Sequence[BaseMessage], tools: Sequence[Any]) -> AIMessage:
        del tools  # text-only eval calls
        text = await self._transport.post(
            render_payload(messages, model=self._model, max_tokens=self._max_tokens)
        )
        return AIMessage(content=text)


class AnthropicTextJudge:
    """``TextJudge``-shaped adapter for the benchmark verdict calls."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_EVAL_MODEL,
        max_tokens: int = 256,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._transport = _Transport(api_key=api_key, http_client=http_client)
        self._model = model
        self._max_tokens = max_tokens

    async def complete(self, *, prompt: str) -> str:
        from langchain_core.messages import HumanMessage

        return await self._transport.post(
            render_payload(
                [HumanMessage(content=prompt)], model=self._model, max_tokens=self._max_tokens
            )
        )
