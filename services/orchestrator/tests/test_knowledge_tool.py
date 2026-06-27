"""Unit tests for J.5 knowledge retrieval — retriever, reranker, tool."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessage

from helix_agent.common.search.rrf import rrf_fuse as _rrf_fuse
from helix_agent.persistence import InMemoryKnowledgeStore
from helix_agent.protocol import KnowledgeChunk, RetrievalMethod
from orchestrator.tools import (
    KnowledgeRetriever,
    KnowledgeSearchTool,
    LLMReranker,
    Reranker,
    Tool,
    ToolBlockedError,
    ToolContext,
)
from orchestrator.tools.knowledge import _parse_rerank_order

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FixedEmbedder:
    """Embedder returning one fixed vector for every text."""

    def __init__(self, vector: tuple[float, ...]) -> None:
        self._vector = vector

    async def embed(self, texts: Sequence[str], *, tenant_id: UUID) -> list[tuple[float, ...]]:
        del tenant_id
        return [self._vector for _ in texts]


class _ScriptedLLM:
    """LLMCaller returning one scripted reply."""

    def __init__(self, reply: str) -> None:
        self._reply = reply

    async def __call__(self, *, messages: Any, tools: Any) -> AIMessage:
        del messages, tools
        return AIMessage(content=self._reply)


class _FailingLLM:
    async def __call__(self, *, messages: Any, tools: Any) -> AIMessage:
        del messages, tools
        raise RuntimeError("llm down")


class _ReversingReranker:
    """Reranker that reverses the candidate order."""

    async def rerank(
        self, *, query: str, documents: Sequence[str], top_k: int, tenant_id: UUID
    ) -> list[int]:
        del tenant_id
        del query
        return list(reversed(range(len(documents))))[:top_k]


def _chunk(
    *, tenant: UUID, kb: UUID, doc: UUID, index: int, content: str, embedding: tuple[float, ...]
) -> KnowledgeChunk:
    return KnowledgeChunk(
        id=uuid4(),
        tenant_id=tenant,
        kb_id=kb,
        document_id=doc,
        chunk_index=index,
        content=content,
        embedding=embedding,
    )


async def _seed_store() -> tuple[InMemoryKnowledgeStore, UUID]:
    """A store with one tenant, base ``kb``, document ``doc.pdf``, two chunks:
    chunk 0 about deductibles (vector (1,0)), chunk 1 about vacation (0,1)."""
    store = InMemoryKnowledgeStore()
    tenant = uuid4()
    base = await store.create_base(tenant_id=tenant, name="kb")
    document = await store.upsert_document(tenant_id=tenant, kb_id=base.id, filename="doc.pdf")
    await store.replace_chunks(
        tenant_id=tenant,
        document_id=document.id,
        chunks=[
            _chunk(
                tenant=tenant,
                kb=base.id,
                doc=document.id,
                index=0,
                content="the deductible is 500 dollars",
                embedding=(1.0, 0.0),
            ),
            _chunk(
                tenant=tenant,
                kb=base.id,
                doc=document.id,
                index=1,
                content="vacation policy grants 20 days",
                embedding=(0.0, 1.0),
            ),
        ],
    )
    return store, tenant


# ---------------------------------------------------------------------------
# RRF fusion
# ---------------------------------------------------------------------------


def test_rrf_fuse_rewards_chunks_in_both_lists() -> None:
    tenant, kb, doc = uuid4(), uuid4(), uuid4()
    shared = _chunk(tenant=tenant, kb=kb, doc=doc, index=0, content="shared", embedding=(1.0,))
    vec_only = _chunk(tenant=tenant, kb=kb, doc=doc, index=1, content="vec", embedding=(1.0,))
    kw_only = _chunk(tenant=tenant, kb=kb, doc=doc, index=2, content="kw", embedding=(1.0,))
    fused = _rrf_fuse([[vec_only, shared], [kw_only, shared]])
    # ``shared`` appears in both lists → highest fused score.
    assert fused[0].id == shared.id
    assert {c.id for c in fused} == {shared.id, vec_only.id, kw_only.id}


# ---------------------------------------------------------------------------
# rerank-order parsing
# ---------------------------------------------------------------------------


def test_parse_rerank_order_json_array() -> None:
    assert _parse_rerank_order("[2, 1, 3]", 5) == [1, 0, 2]


def test_parse_rerank_order_bare_numbers() -> None:
    assert _parse_rerank_order("most relevant: 3 then 1", 5) == [2, 0]


def test_parse_rerank_order_drops_out_of_range_and_dups() -> None:
    assert _parse_rerank_order("[9, 1, 1, 2]", 3) == [0, 1]


def test_parse_rerank_order_garbage_yields_empty() -> None:
    assert _parse_rerank_order("no numbers here", 3) == []


# ---------------------------------------------------------------------------
# LLMReranker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_reranker_reorders() -> None:
    reranker = LLMReranker(llm_caller=_ScriptedLLM("[2, 1]"))
    order = await reranker.rerank(query="q", documents=["a", "b"], top_k=2, tenant_id=uuid4())
    assert order == [1, 0]


@pytest.mark.asyncio
async def test_llm_reranker_truncates_to_top_k() -> None:
    reranker = LLMReranker(llm_caller=_ScriptedLLM("[3, 2, 1]"))
    order = await reranker.rerank(query="q", documents=["a", "b", "c"], top_k=2, tenant_id=uuid4())
    assert order == [2, 1]


@pytest.mark.asyncio
async def test_llm_reranker_unparseable_reply_falls_back_to_input_order() -> None:
    reranker = LLMReranker(llm_caller=_ScriptedLLM("sorry, I cannot help"))
    order = await reranker.rerank(query="q", documents=["a", "b", "c"], top_k=3, tenant_id=uuid4())
    assert order == [0, 1, 2]


@pytest.mark.asyncio
async def test_llm_reranker_llm_failure_falls_back() -> None:
    reranker = LLMReranker(llm_caller=_FailingLLM())
    order = await reranker.rerank(query="q", documents=["a", "b"], top_k=2, tenant_id=uuid4())
    assert order == [0, 1]


# ---------------------------------------------------------------------------
# KnowledgeRetriever
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retriever_returns_attributed_chunks() -> None:
    store, tenant = await _seed_store()
    retriever = KnowledgeRetriever(store=store, embedder=_FixedEmbedder((1.0, 0.0)))
    results = await retriever.search(
        tenant_id=tenant, base_names=["kb"], query="deductible", limit=5
    )
    assert results
    top = results[0]
    assert "deductible" in top.content
    assert top.filename == "doc.pdf"
    assert top.chunk_index == 0


@pytest.mark.asyncio
async def test_retriever_keyword_recall_surfaces_a_vector_miss() -> None:
    # The query vector points at chunk 0, but "vacation" only matches
    # chunk 1 by keyword — hybrid retrieval still surfaces chunk 1.
    store, tenant = await _seed_store()
    retriever = KnowledgeRetriever(store=store, embedder=_FixedEmbedder((1.0, 0.0)))
    results = await retriever.search(tenant_id=tenant, base_names=["kb"], query="vacation", limit=5)
    assert any("vacation" in r.content for r in results)


@pytest.mark.asyncio
async def test_retriever_unknown_base_name_is_skipped() -> None:
    store, tenant = await _seed_store()
    retriever = KnowledgeRetriever(store=store, embedder=_FixedEmbedder((1.0, 0.0)))
    assert await retriever.search(tenant_id=tenant, base_names=["ghost"], query="x", limit=5) == []


@pytest.mark.asyncio
async def test_retriever_applies_reranker() -> None:
    store, tenant = await _seed_store()
    retriever = KnowledgeRetriever(
        store=store, embedder=_FixedEmbedder((1.0, 0.0)), reranker=_ReversingReranker()
    )
    plain = KnowledgeRetriever(store=store, embedder=_FixedEmbedder((1.0, 0.0)))
    reranked = await retriever.search(
        tenant_id=tenant, base_names=["kb"], query="deductible", limit=5
    )
    fused = await plain.search(tenant_id=tenant, base_names=["kb"], query="deductible", limit=5)
    assert [r.content for r in reranked] == [r.content for r in reversed(fused)]


@pytest.mark.asyncio
async def test_retriever_surfaces_score_and_recall_source() -> None:
    # query vector == chunk 0 → similarity ~1.0; "deductible" also matches it
    # by keyword → recall_source "both".
    store, tenant = await _seed_store()
    retriever = KnowledgeRetriever(store=store, embedder=_FixedEmbedder((1.0, 0.0)))
    results = await retriever.search(
        tenant_id=tenant, base_names=["kb"], query="deductible", limit=5
    )
    top = next(r for r in results if r.chunk_index == 0)
    assert top.score == pytest.approx(1.0)
    assert top.recall_source == "both"


@pytest.mark.asyncio
async def test_retriever_override_method_and_threshold_filters() -> None:
    # method=vector skips keyword; threshold 0.5 drops chunk 1 (similarity ~0).
    store, tenant = await _seed_store()
    retriever = KnowledgeRetriever(store=store, embedder=_FixedEmbedder((1.0, 0.0)))
    results = await retriever.search(
        tenant_id=tenant,
        base_names=["kb"],
        query="deductible",
        limit=5,
        method=RetrievalMethod.VECTOR,
        score_threshold=0.5,
    )
    assert [r.chunk_index for r in results] == [0]
    assert results[0].recall_source == "vector"


@pytest.mark.asyncio
async def test_retriever_reads_per_base_config_defaults() -> None:
    # No overrides — the base's own stored config (vector-only + threshold)
    # is applied, proving per-KB defaults are honoured.
    store, tenant = await _seed_store()
    base = await store.get_base(tenant_id=tenant, name="kb")
    assert base is not None
    await store.update_base(
        tenant_id=tenant,
        kb_id=base.id,
        retrieval_method=RetrievalMethod.VECTOR,
        retrieval_score_threshold=0.5,
    )
    retriever = KnowledgeRetriever(store=store, embedder=_FixedEmbedder((1.0, 0.0)))
    results = await retriever.search(tenant_id=tenant, base_names=["kb"], query="vacation", limit=5)
    # "vacation" matches chunk 1 by keyword, but keyword recall is off and
    # chunk 1's vector similarity (~0) is below the base threshold → only
    # chunk 0 survives.
    assert [r.chunk_index for r in results] == [0]


@pytest.mark.asyncio
async def test_retriever_keyword_only_method() -> None:
    store, tenant = await _seed_store()
    retriever = KnowledgeRetriever(store=store, embedder=_FixedEmbedder((1.0, 0.0)))
    results = await retriever.search(
        tenant_id=tenant,
        base_names=["kb"],
        query="vacation",
        limit=5,
        method=RetrievalMethod.KEYWORD,
    )
    assert [r.chunk_index for r in results] == [1]
    assert results[0].recall_source == "keyword"
    assert results[0].score is None  # keyword-only hits carry no [0,1] similarity


@pytest.mark.asyncio
async def test_retriever_rerank_override_disables() -> None:
    store, tenant = await _seed_store()
    retriever = KnowledgeRetriever(
        store=store, embedder=_FixedEmbedder((1.0, 0.0)), reranker=_ReversingReranker()
    )
    plain = KnowledgeRetriever(store=store, embedder=_FixedEmbedder((1.0, 0.0)))
    no_rerank = await retriever.search(
        tenant_id=tenant, base_names=["kb"], query="deductible", limit=5, rerank=False
    )
    fused = await plain.search(tenant_id=tenant, base_names=["kb"], query="deductible", limit=5)
    # rerank=False → the reranker is skipped, so the order matches plain fusion.
    assert [r.content for r in no_rerank] == [r.content for r in fused]


# ---------------------------------------------------------------------------
# KnowledgeSearchTool
# ---------------------------------------------------------------------------


def _tool(
    store: InMemoryKnowledgeStore, *, reranker: Reranker | None = None
) -> KnowledgeSearchTool:
    retriever = KnowledgeRetriever(
        store=store, embedder=_FixedEmbedder((1.0, 0.0)), reranker=reranker
    )
    return KnowledgeSearchTool(retriever=retriever, knowledge_base_refs=("kb",))


def test_knowledge_search_tool_satisfies_tool_protocol() -> None:
    assert isinstance(_tool(InMemoryKnowledgeStore()), Tool)


def test_knowledge_search_spec() -> None:
    spec = _tool(InMemoryKnowledgeStore()).spec
    assert spec.name == "knowledge_search"
    assert spec.parameters["required"] == ["query"]


@pytest.mark.asyncio
async def test_knowledge_search_tool_returns_attributed_block() -> None:
    store, tenant = await _seed_store()
    result = await _tool(store).call({"query": "deductible"}, ctx=ToolContext(tenant_id=tenant))
    assert "[doc.pdf#0]" in result.content
    assert "deductible" in result.content
    assert result.meta["hits"] >= 1


@pytest.mark.asyncio
async def test_knowledge_search_tool_requires_tenant() -> None:
    with pytest.raises(ToolBlockedError, match="tenant binding"):
        await _tool(InMemoryKnowledgeStore()).call({"query": "x"}, ctx=ToolContext())


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [{}, {"query": ""}, {"query": "   "}])
async def test_knowledge_search_tool_rejects_empty_query(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="non-empty 'query'"):
        await _tool(InMemoryKnowledgeStore()).call(bad, ctx=ToolContext(tenant_id=uuid4()))


@pytest.mark.asyncio
async def test_knowledge_search_tool_no_hits_message() -> None:
    store = InMemoryKnowledgeStore()
    tenant = uuid4()
    await store.create_base(tenant_id=tenant, name="kb")  # empty base
    result = await _tool(store).call({"query": "anything"}, ctx=ToolContext(tenant_id=tenant))
    assert "no relevant knowledge" in result.content
    assert result.meta["hits"] == 0
