"""Unit tests for the embedding client — Stream J.3."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from orchestrator.llm import (
    FakeEmbedder,
    HTTPEmbeddingClient,
    OpenAICompatibleEmbedder,
)

# ---------------------------------------------------------------------------
# FakeEmbedder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_embedder_is_deterministic_and_correct_width() -> None:
    embedder = FakeEmbedder(dim=64)
    first = await embedder.embed(["hello", "world"])
    again = await embedder.embed(["hello", "world"])

    assert [len(v) for v in first] == [64, 64]
    # Same text → same vector; different text → different vector.
    assert first == again
    assert first[0] != first[1]


@pytest.mark.asyncio
async def test_fake_embedder_empty_input() -> None:
    assert await FakeEmbedder(dim=8).embed([]) == []


# ---------------------------------------------------------------------------
# OpenAICompatibleEmbedder
# ---------------------------------------------------------------------------


@dataclass
class _ScriptedEmbeddingClient:
    """EmbeddingClient stub returning a fixed body, recording the call."""

    body: Mapping[str, Any]
    calls: list[tuple[str, list[str]]] = field(default_factory=list)

    async def embeddings(self, *, model: str, texts: Sequence[str]) -> Mapping[str, Any]:
        self.calls.append((model, list(texts)))
        return self.body


@pytest.mark.asyncio
async def test_openai_compatible_embedder_extracts_vectors() -> None:
    client = _ScriptedEmbeddingClient(
        body={
            "data": [
                {"index": 0, "embedding": [0.1, 0.2]},
                {"index": 1, "embedding": [0.3, 0.4]},
            ]
        }
    )
    embedder = OpenAICompatibleEmbedder(client=client, model="text-embedding-v4")
    vectors = await embedder.embed(["a", "b"])

    assert vectors == [(0.1, 0.2), (0.3, 0.4)]
    assert client.calls == [("text-embedding-v4", ["a", "b"])]


@pytest.mark.asyncio
async def test_openai_compatible_embedder_reorders_by_index() -> None:
    """The API may return rows out of order — ``index`` puts them back."""
    client = _ScriptedEmbeddingClient(
        body={
            "data": [
                {"index": 1, "embedding": [0.3, 0.4]},
                {"index": 0, "embedding": [0.1, 0.2]},
            ]
        }
    )
    embedder = OpenAICompatibleEmbedder(client=client, model="m")
    assert await embedder.embed(["a", "b"]) == [(0.1, 0.2), (0.3, 0.4)]


@pytest.mark.asyncio
async def test_openai_compatible_embedder_empty_input_skips_call() -> None:
    client = _ScriptedEmbeddingClient(body={"data": []})
    embedder = OpenAICompatibleEmbedder(client=client, model="m")
    assert await embedder.embed([]) == []
    assert client.calls == []


# ---------------------------------------------------------------------------
# HTTPEmbeddingClient
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_embedding_client_posts_and_parses() -> None:
    seen: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        import json

        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 2.0]}]})

    client = HTTPEmbeddingClient(api_key="sk-test", transport=httpx.MockTransport(_handler))
    body = await client.embeddings(model="text-embedding-v4", texts=["hi"])

    assert body["data"][0]["embedding"] == [1.0, 2.0]
    assert seen["url"].endswith("/v1/embeddings")
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"] == {"model": "text-embedding-v4", "input": ["hi"]}


@pytest.mark.asyncio
async def test_http_embedding_client_raises_on_http_error() -> None:
    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key"})

    client = HTTPEmbeddingClient(api_key="bad", transport=httpx.MockTransport(_handler))
    with pytest.raises(httpx.HTTPStatusError):
        await client.embeddings(model="m", texts=["x"])
