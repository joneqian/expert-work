"""Qwen run prep — OpenAI-compat client, embedding cache, concurrency.

Stream CM-N5 baseline-run hardening: the real run uses the user's Qwen
stack end to end, the 5-arm matrix must not re-embed the corpus per
arm, and a 23k-call end-to-end pass needs bounded concurrency. All
three are covered here without network: payload rendering is pure, the
cache is exercised against a counting backend, and the concurrent paths
are asserted byte-equal to the sequential ones on the fixtures.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from longmem.adapter import load_longmemeval
from longmem.embedders import CachedEmbedder, KeywordEmbedder
from longmem.endtoend import EndToEndConfig, run_end_to_end
from longmem.judge import ScriptedTextJudge
from longmem.openai_client import render_chat_payload
from longmem.retrieval import AblationConfig, evaluate_retrieval

FIXTURES = Path(__file__).parent / "datasets" / "longmem_fixture"
_NOW = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# OpenAI-compatible payload rendering
# ---------------------------------------------------------------------------


def test_render_chat_payload_roles_and_temperature() -> None:
    payload = render_chat_payload(
        [
            SystemMessage(content="sys"),
            HumanMessage(content="hi"),
            AIMessage(content="reply"),
        ],
        model="qwen-plus",
        max_tokens=128,
    )
    assert payload["model"] == "qwen-plus"
    assert payload["temperature"] == 0.0
    assert [(m["role"], m["content"]) for m in payload["messages"]] == [
        ("system", "sys"),
        ("user", "hi"),
        ("assistant", "reply"),
    ]


# ---------------------------------------------------------------------------
# CachedEmbedder
# ---------------------------------------------------------------------------


class _CountingBackend:
    def __init__(self) -> None:
        self.calls: list[int] = []
        self._inner = KeywordEmbedder()

    async def embed(self, texts: Sequence[str], *, tenant_id: UUID) -> list[tuple[float, ...]]:
        self.calls.append(len(texts))
        return await self._inner.embed(texts, tenant_id=tenant_id)


@pytest.mark.asyncio
async def test_cache_serves_hits_without_backend(tmp_path: Path) -> None:
    backend = _CountingBackend()
    cached = CachedEmbedder(backend, model_key="m1", db_path=tmp_path / "emb.sqlite")
    first = await cached.embed(["alpha", "beta"], tenant_id=uuid4())
    assert sum(backend.calls) == 2
    second = await cached.embed(["alpha", "beta"], tenant_id=uuid4())
    assert sum(backend.calls) == 2  # no new backend traffic
    assert first == second


@pytest.mark.asyncio
async def test_cache_persists_across_instances(tmp_path: Path) -> None:
    db = tmp_path / "emb.sqlite"
    backend = _CountingBackend()
    await CachedEmbedder(backend, model_key="m1", db_path=db).embed(["gamma"], tenant_id=uuid4())
    backend2 = _CountingBackend()
    vectors = await CachedEmbedder(backend2, model_key="m1", db_path=db).embed(
        ["gamma"], tenant_id=uuid4()
    )
    assert backend2.calls == []
    assert len(vectors) == 1


@pytest.mark.asyncio
async def test_cache_is_keyed_by_model(tmp_path: Path) -> None:
    db = tmp_path / "emb.sqlite"
    backend = _CountingBackend()
    await CachedEmbedder(backend, model_key="m1", db_path=db).embed(["x"], tenant_id=uuid4())
    await CachedEmbedder(backend, model_key="m2", db_path=db).embed(["x"], tenant_id=uuid4())
    assert sum(backend.calls) == 2  # same text, different model -> re-fetch


@pytest.mark.asyncio
async def test_cache_respects_backend_batch_cap(tmp_path: Path) -> None:
    """DashScope compatible-mode caps embedding batches at 10 inputs."""
    backend = _CountingBackend()
    cached = CachedEmbedder(
        backend, model_key="m1", db_path=tmp_path / "emb.sqlite", backend_batch=10
    )
    texts = [f"text-{i}" for i in range(23)]
    vectors = await cached.embed(texts, tenant_id=uuid4())
    assert len(vectors) == 23
    assert max(backend.calls) <= 10
    assert sum(backend.calls) == 23
    # Mixed hit/miss keeps input order.
    again = await cached.embed(["text-5", "brand-new", "text-7"], tenant_id=uuid4())
    assert again[0] == vectors[5]
    assert again[2] == vectors[7]


# ---------------------------------------------------------------------------
# Concurrency — concurrent results must equal sequential results
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieval_concurrency_matches_sequential() -> None:
    instances = load_longmemeval(FIXTURES / "longmemeval_mini.json")
    sequential = await evaluate_retrieval(
        instances, embedder=KeywordEmbedder(), config=AblationConfig(mmr=False), now=_NOW
    )
    concurrent = await evaluate_retrieval(
        instances,
        embedder=KeywordEmbedder(),
        config=AblationConfig(mmr=False),
        now=_NOW,
        concurrency=4,
    )
    assert concurrent.per_instance == sequential.per_instance
    assert concurrent.mean_mrr == sequential.mean_mrr


@pytest.mark.asyncio
async def test_endtoend_concurrency_matches_sequential() -> None:
    from test_longmem_endtoend import _ScriptedCaller

    instances = load_longmemeval(FIXTURES / "longmemeval_mini.json")
    judge = ScriptedTextJudge(
        {"The user visited Kyoto.": "yes", "Their favorite editor is helix.": "yes"}
    )
    sequential = await run_end_to_end(
        instances,
        benchmark="longmemeval",
        embedder=KeywordEmbedder(),
        llm_caller=_ScriptedCaller(),
        judge=judge,
        config=EndToEndConfig(reconcile=False),
    )
    concurrent = await run_end_to_end(
        instances,
        benchmark="longmemeval",
        embedder=KeywordEmbedder(),
        llm_caller=_ScriptedCaller(),
        judge=judge,
        config=EndToEndConfig(reconcile=False),
        concurrency=4,
    )
    assert concurrent.results == sequential.results
    assert concurrent.accuracy == sequential.accuracy == 1.0
    assert concurrent.memories_written == sequential.memories_written


@pytest.mark.asyncio
async def test_cache_truncates_oversized_inputs(tmp_path: Path) -> None:
    """DashScope caps one embedding input at 8192 tokens — LongMemEval_S
    turns above the cap 400'd a real baseline run (2026-06-10)."""
    backend = _CountingBackend()
    seen: list[int] = []

    class _LenSpy(_CountingBackend):
        async def embed(self, texts: Sequence[str], *, tenant_id: UUID) -> list[tuple[float, ...]]:
            seen.extend(len(t) for t in texts)
            return await super().embed(texts, tenant_id=tenant_id)

    cached = CachedEmbedder(
        _LenSpy(), model_key="m1", db_path=tmp_path / "emb.sqlite", max_text_chars=100
    )
    del backend
    vectors = await cached.embed(["x" * 5000, "short"], tenant_id=uuid4())
    assert len(vectors) == 2
    assert max(seen) <= 100
    # Same oversized text again hits the cache (key = truncated text).
    again = await cached.embed(["x" * 5000], tenant_id=uuid4())
    assert again[0] == vectors[0]
    assert len(seen) == 2  # no new backend traffic


def _throttle_error() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://example.invalid/v1/embeddings")
    response = httpx.Response(
        400,
        text='{"error":{"message":"Too many requests. throttled","type":"ServiceUnavailable"}}',
        request=request,
    )
    return httpx.HTTPStatusError("400", request=request, response=response)


@pytest.mark.asyncio
async def test_cache_retries_throttle_shaped_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DashScope reports capacity throttling as HTTP 400 — must self-heal."""
    import asyncio

    sleeps: list[float] = []

    async def _fast_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    # asyncio is a singleton module — patching it here patches the
    # CachedEmbedder retry loop's view of it too.
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)

    class _FlakyBackend(_CountingBackend):
        failures = 2

        async def embed(self, texts: Sequence[str], *, tenant_id: UUID) -> list[tuple[float, ...]]:
            if type(self).failures > 0:
                type(self).failures -= 1
                raise _throttle_error()
            return await super().embed(texts, tenant_id=tenant_id)

    cached = CachedEmbedder(_FlakyBackend(), model_key="m1", db_path=tmp_path / "emb.sqlite")
    vectors = await cached.embed(["hello"], tenant_id=uuid4())
    assert len(vectors) == 1
    assert sleeps == [2.0, 4.0]


@pytest.mark.asyncio
async def test_cache_does_not_retry_genuine_400(tmp_path: Path) -> None:
    request = httpx.Request("POST", "http://example.invalid/v1/embeddings")
    response = httpx.Response(400, text='{"error":{"message":"invalid input"}}', request=request)
    error = httpx.HTTPStatusError("400", request=request, response=response)

    class _BadInputBackend(_CountingBackend):
        attempts = 0

        async def embed(self, texts: Sequence[str], *, tenant_id: UUID) -> list[tuple[float, ...]]:
            type(self).attempts += 1
            raise error

    cached = CachedEmbedder(_BadInputBackend(), model_key="m1", db_path=tmp_path / "emb.sqlite")
    with pytest.raises(httpx.HTTPStatusError):
        await cached.embed(["hello"], tenant_id=uuid4())
    assert _BadInputBackend.attempts == 1  # no retry on genuine 400
