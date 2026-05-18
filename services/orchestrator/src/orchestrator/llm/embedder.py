"""Embedding client — Stream J.3 (long-term memory).

Turns text into vectors for long-term memory recall + write-back.
Targets the OpenAI-compatible ``/v1/embeddings`` endpoint, so the
domestic vendors helix already speaks (qwen DashScope compatible-mode,
…) work uniformly — same pattern as the chat providers (E.11.5).

The output dimension is whatever the embedding model returns; the
deployment must set ``HELIX_AGENT_EMBEDDING_DIM`` to match it (the
``memory_item.embedding`` column is fixed at that width).

:class:`FakeEmbedder` is a deterministic test double — same text always
maps to the same vector — so memory tests need no network.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx

#: OpenAI-compatible embeddings path — qwen DashScope compatible-mode,
#: DeepSeek, etc. all accept it.
DEFAULT_EMBEDDINGS_PATH = "/v1/embeddings"

#: qwen / Alibaba DashScope OpenAI-compatible base URL (mirrors the chat
#: provider's ``QWEN_BASE_URL``).
QWEN_EMBEDDING_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode"

_DEFAULT_TIMEOUT_S = 30.0
_UINT32_MAX = 0xFFFFFFFF


@runtime_checkable
class Embedder(Protocol):
    """Async callable that embeds text into vectors."""

    async def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        """Return one embedding vector per input text, in input order."""


@runtime_checkable
class EmbeddingClient(Protocol):
    """The embeddings HTTP surface — sized to the one endpoint we use so
    tests can fake it without mocking httpx."""

    async def embeddings(self, *, model: str, texts: Sequence[str]) -> Mapping[str, Any]:
        """POST the embeddings endpoint; return the parsed JSON body.

        Expected shape: ``{"data": [{"embedding": [...], "index": int}, ...]}``.
        """


@dataclass(frozen=True)
class HTTPEmbeddingClient:
    """httpx-backed :class:`EmbeddingClient` for an OpenAI-compatible API."""

    api_key: str
    base_url: str = QWEN_EMBEDDING_BASE_URL
    embeddings_path: str = DEFAULT_EMBEDDINGS_PATH
    transport: httpx.AsyncBaseTransport | None = None

    async def embeddings(self, *, model: str, texts: Sequence[str]) -> Mapping[str, Any]:
        async with httpx.AsyncClient(
            transport=self.transport, timeout=_DEFAULT_TIMEOUT_S
        ) as client:
            response = await client.post(
                f"{self.base_url}{self.embeddings_path}",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": model, "input": list(texts)},
            )
            response.raise_for_status()
            body: Mapping[str, Any] = response.json()
            return body


@dataclass(frozen=True)
class OpenAICompatibleEmbedder:
    """:class:`Embedder` over an OpenAI-compatible ``/v1/embeddings`` API."""

    client: EmbeddingClient
    model: str

    async def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        if not texts:
            return []
        body = await self.client.embeddings(model=self.model, texts=texts)
        # ``index`` orders the vectors back onto the inputs — the API may
        # return them out of order.
        rows = sorted(body["data"], key=lambda row: row["index"])
        return [tuple(float(value) for value in row["embedding"]) for row in rows]


@dataclass(frozen=True)
class FakeEmbedder:
    """Deterministic test double — hashes text to a fixed-width vector.

    Same text always maps to the same vector; different texts to
    different ones. No semantic meaning — enough for store / node tests.
    """

    dim: int = 1024

    async def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> tuple[float, ...]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        values: list[float] = []
        counter = 0
        while len(values) < self.dim:
            block = hashlib.sha256(digest + counter.to_bytes(4, "big")).digest()
            for offset in range(0, len(block), 4):
                values.append(int.from_bytes(block[offset : offset + 4], "big") / _UINT32_MAX)
            counter += 1
        return tuple(values[: self.dim])
