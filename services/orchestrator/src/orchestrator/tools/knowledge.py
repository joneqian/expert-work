"""Knowledge retrieval — Stream J.5 RAG.

The ``knowledge_search`` tool an agent calls to ground its answers in
the tenant's uploaded documents. Retrieval is **hybrid**:

1. **vector recall** — the query is embedded and matched by cosine
   similarity against ``knowledge_chunk`` embeddings;
2. **keyword recall** — the query is matched by Postgres full-text
   relevance (jieba-segmented, so it is correct for CJK);
3. the two ranked lists are fused by **Reciprocal Rank Fusion**; and
4. an optional **LLM reranker** reorders the fused candidates by
   judged relevance before the top-k is returned.

Vector recall alone misses exact-term matches (error codes, names);
keyword recall alone misses paraphrases — hybrid covers both, and the
rerank pass fixes ordering precision. See ``STREAM-J-DESIGN.md`` § 12.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage

from helix_agent.persistence import KnowledgeStore
from helix_agent.protocol import KnowledgeChunk
from orchestrator.tools.registry import ToolBlockedError, ToolContext, ToolResult, ToolSpec

if TYPE_CHECKING:
    # Imported under TYPE_CHECKING only — a runtime import of
    # ``orchestrator.llm`` here would cycle (llm → tools.registry → tools).
    from orchestrator.llm import Embedder, LLMCaller

logger = logging.getLogger(__name__)

#: Reciprocal Rank Fusion constant — the standard 60. Dampens the weight
#: of any single list's top ranks so the two recall lists combine fairly.
_RRF_K = 60
#: Per-side recall depth fetched before fusion / rerank — wider than the
#: final top-k so fusion and the reranker have candidates to work with.
_DEFAULT_RECALL_LIMIT = 20
#: Each candidate is truncated to this many characters in the rerank
#: prompt — enough to judge relevance without an enormous prompt.
_RERANK_DOC_CHARS = 600
#: ``knowledge_search`` result-count default + hard cap.
_DEFAULT_LIMIT = 5
_MAX_LIMIT = 20


@runtime_checkable
class Reranker(Protocol):
    """Reorders retrieval candidates by judged relevance to a query."""

    async def rerank(self, *, query: str, documents: Sequence[str], top_k: int) -> list[int]:
        """Return the indices of the ``top_k`` most relevant ``documents``,
        most relevant first. Indices are positions in ``documents``."""


@dataclass(frozen=True)
class RetrievedChunk:
    """A retrieved chunk with its source document attribution."""

    content: str
    filename: str
    chunk_index: int


@dataclass(frozen=True)
class LLMReranker:
    """LLM-backed :class:`Reranker` — reuses an :class:`LLMCaller`.

    A cheap, dependency-free reranker: the LLM is asked to rank the
    candidates and reply with their numbers. A response it cannot parse
    falls back to the input order, so a flaky rerank never breaks search.
    """

    llm_caller: LLMCaller

    async def rerank(self, *, query: str, documents: Sequence[str], top_k: int) -> list[int]:
        if not documents:
            return []
        listing = "\n\n".join(
            f"[{i + 1}] {doc[:_RERANK_DOC_CHARS]}" for i, doc in enumerate(documents)
        )
        prompt = [
            SystemMessage(
                content=(
                    "You rank documents by relevance to a query. Reply with "
                    "ONLY a JSON array of the document numbers, most relevant "
                    "first, e.g. [3, 1, 5]. Include only genuinely relevant "
                    "documents."
                )
            ),
            HumanMessage(content=f"Query: {query}\n\nDocuments:\n{listing}"),
        ]
        try:
            response = await self.llm_caller(messages=prompt, tools=[])
            order = _parse_rerank_order(_message_text(response), len(documents))
        except Exception:
            logger.warning("knowledge.rerank_failed — using fused order", exc_info=True)
            order = []
        if not order:
            order = list(range(len(documents)))
        return order[:top_k]


@dataclass(frozen=True)
class KnowledgeRetriever:
    """Hybrid retrieval over a tenant's knowledge bases — Stream J.5.

    Bundles the :class:`KnowledgeStore`, the query :class:`Embedder`, and
    an optional :class:`Reranker`. Injected into the orchestrator's
    ``ToolEnv`` by the control-plane (which configures the embedder /
    rerank LLM). With no reranker the fused order is returned directly.
    """

    store: KnowledgeStore
    embedder: Embedder
    reranker: Reranker | None = None
    recall_limit: int = field(default=_DEFAULT_RECALL_LIMIT)

    async def search(
        self,
        *,
        tenant_id: UUID,
        base_names: Sequence[str],
        query: str,
        limit: int,
    ) -> list[RetrievedChunk]:
        """Hybrid-search ``base_names`` for ``query``, returning the
        ``limit`` most relevant chunks with source attribution. Base
        names that do not resolve to a knowledge base are skipped."""
        kb_ids = await self._resolve_base_ids(tenant_id, base_names)
        if not kb_ids:
            return []
        query_embedding = (await self.embedder.embed([query]))[0]
        vector_hits = await self.store.search(
            tenant_id=tenant_id,
            kb_ids=kb_ids,
            query_embedding=query_embedding,
            limit=self.recall_limit,
        )
        keyword_hits = await self.store.keyword_search(
            tenant_id=tenant_id, kb_ids=kb_ids, query=query, limit=self.recall_limit
        )
        fused = _rrf_fuse([vector_hits, keyword_hits])
        if not fused:
            return []
        chunks = await self._rerank(query, fused, limit)
        return await self._attribute(tenant_id, chunks)

    async def _resolve_base_ids(self, tenant_id: UUID, base_names: Sequence[str]) -> list[UUID]:
        ids: list[UUID] = []
        for name in base_names:
            base = await self.store.get_base(tenant_id=tenant_id, name=name)
            if base is not None:
                ids.append(base.id)
        return ids

    async def _rerank(
        self, query: str, fused: list[KnowledgeChunk], limit: int
    ) -> list[KnowledgeChunk]:
        if self.reranker is None:
            return fused[:limit]
        candidates = fused[: self.recall_limit]
        order = await self.reranker.rerank(
            query=query, documents=[chunk.content for chunk in candidates], top_k=limit
        )
        return [candidates[i] for i in order]

    async def _attribute(
        self, tenant_id: UUID, chunks: Sequence[KnowledgeChunk]
    ) -> list[RetrievedChunk]:
        filenames: dict[UUID, str] = {}
        for document_id in {chunk.document_id for chunk in chunks}:
            document = await self.store.get_document(tenant_id=tenant_id, document_id=document_id)
            filenames[document_id] = document.filename if document else "(unknown)"
        return [
            RetrievedChunk(
                content=chunk.content,
                filename=filenames[chunk.document_id],
                chunk_index=chunk.chunk_index,
            )
            for chunk in chunks
        ]


@dataclass(frozen=True)
class KnowledgeSearchTool:
    """The ``knowledge_search`` tool — Stream J.5.

    One instance per agent whose manifest declares a ``knowledge:``
    block; ``knowledge_base_refs`` are the base names it may query. The
    LLM calls it with a natural-language ``query``; the result is a
    formatted block of relevant chunks, each tagged with its source.
    """

    retriever: KnowledgeRetriever
    knowledge_base_refs: tuple[str, ...]

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="knowledge_search",
            description=(
                "Search the tenant's knowledge base for information relevant "
                "to a query. Use it to ground answers in internal documents "
                "rather than guessing."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language search query.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": _MAX_LIMIT,
                        "description": f"Max results (default {_DEFAULT_LIMIT}).",
                    },
                },
                "required": ["query"],
            },
        )

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        if ctx.tenant_id is None:
            msg = "knowledge_search requires a tenant binding"
            raise ToolBlockedError(msg)
        query = self._require_query(args)
        limit = _coerce_limit(args.get("limit"))
        results = await self.retriever.search(
            tenant_id=ctx.tenant_id,
            base_names=self.knowledge_base_refs,
            query=query,
            limit=limit,
        )
        if not results:
            return ToolResult(
                content="[no relevant knowledge found for the query]",
                meta={"hits": 0},
            )
        return ToolResult(content=_format_results(results), meta={"hits": len(results)})

    def _require_query(self, args: Mapping[str, Any]) -> str:
        raw = args.get("query")
        if not isinstance(raw, str) or not raw.strip():
            msg = "knowledge_search requires a non-empty 'query' string"
            raise ValueError(msg)
        return raw.strip()


def _rrf_fuse(rankings: Sequence[Sequence[KnowledgeChunk]]) -> list[KnowledgeChunk]:
    """Reciprocal Rank Fusion of several ranked chunk lists.

    A chunk's score is ``Σ 1/(K + rank)`` over the lists it appears in;
    a chunk ranked well by *both* recall paths rises to the top.
    """
    scores: dict[UUID, float] = {}
    by_id: dict[UUID, KnowledgeChunk] = {}
    for ranking in rankings:
        for rank, chunk in enumerate(ranking):
            scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (_RRF_K + rank + 1)
            by_id[chunk.id] = chunk
    return sorted(by_id.values(), key=lambda chunk: scores[chunk.id], reverse=True)


def _parse_rerank_order(text: str, count: int) -> list[int]:
    """Parse an LLM rerank reply into 0-based, in-range, de-duplicated
    indices. An unparseable reply yields ``[]`` (caller falls back)."""
    numbers: list[int] = []
    match = re.search(r"\[[^\]]*\]", text)
    if match is not None:
        try:
            parsed = json.loads(match.group())
            numbers = [int(n) for n in parsed if isinstance(n, int | float)]
        except (ValueError, TypeError):
            numbers = []
    if not numbers:
        numbers = [int(n) for n in re.findall(r"\d+", text)]
    seen: set[int] = set()
    order: list[int] = []
    for one_based in numbers:
        index = one_based - 1
        if 0 <= index < count and index not in seen:
            seen.add(index)
            order.append(index)
    return order


def _message_text(message: object) -> str:
    content = getattr(message, "content", "")
    return content if isinstance(content, str) else str(content)


def _coerce_limit(raw: object) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool):
        return _DEFAULT_LIMIT
    return max(1, min(raw, _MAX_LIMIT))


def _format_results(results: Sequence[RetrievedChunk]) -> str:
    blocks = [f"[{result.filename}#{result.chunk_index}]\n{result.content}" for result in results]
    return "\n\n---\n\n".join(blocks)
