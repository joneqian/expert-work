"""J.5 RAG eval — Stream J.13a (M0 baseline) closeout.

Drives :class:`~orchestrator.tools.knowledge.KnowledgeRetriever` against
scripted knowledge bases with a deterministic keyword embedder. Two
metrics roll up into the baseline (per Mini-ADR J-22 / J-37):

* ``pass_rate`` — fraction of cases whose every expected chunk appears
  in the top-k retrieval.
* ``recall_at_k`` — mean fraction of expected chunks recovered per case.

The retriever runs without an LLM reranker so the score is reproducible
under CI — the rerank LLM path is covered by orchestrator unit tests
(``test_knowledge_tool.py``). Per Mini-ADR J-37 the J.5 threshold is
``pass_rate ≥ 0.80`` AND ``recall@k ≥ 0.70`` (§ 18.3).
"""

from __future__ import annotations

import sys as _sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from pathlib import Path as _Path
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

import yaml

from helix_agent.persistence import InMemoryKnowledgeStore
from helix_agent.protocol import KnowledgeChunk

_EVAL_DIR = _Path(__file__).resolve().parent
if str(_EVAL_DIR) not in _sys.path:
    _sys.path.insert(0, str(_EVAL_DIR))

from _capability import (  # type: ignore[import-not-found]  # noqa: E402
    CapabilityCaseResult,
    CapabilityReport,
)

CAPABILITY = "J.5_rag"
METRIC_TYPE = "pass-rate+recall@k"
THRESHOLD = {"pass_rate": 0.80, "recall_at_k": 0.70}


# ---------------------------------------------------------------------------
# Per-case dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RagChunk:
    """One chunk of a document — content + a stable case-local id used by
    ``expected_chunk_ids`` to score retrieval."""

    id: str
    content: str


@dataclass(frozen=True)
class RagDocument:
    """One document in a knowledge base — a filename + ordered chunks."""

    filename: str
    chunks: tuple[RagChunk, ...]


@dataclass(frozen=True)
class RagKnowledgeBase:
    """One knowledge base populated for a case."""

    name: str
    documents: tuple[RagDocument, ...]


@dataclass(frozen=True)
class RagCase:
    """One J.5 capability case.

    The eval seeds a fresh :class:`InMemoryKnowledgeStore` with
    ``knowledge_bases`` (one tenant per case — no cross-case leakage),
    runs the retriever against ``query`` over every base in the case, and
    scores top-k against ``expected_chunk_ids``.

    ``language`` is metadata for diff readability (e.g. mixing en / zh
    cases); the eval logic is language-agnostic — the deterministic
    keyword embedder handles ASCII words + CJK bigrams identically.
    """

    case_id: str
    knowledge_bases: tuple[RagKnowledgeBase, ...]
    query_bases: tuple[str, ...]
    query: str
    expected_chunk_ids: tuple[str, ...]
    k: int = 5
    language: str = "en"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class _EmbedderLike(Protocol):
    async def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        """Embed each text and return one vector per input."""


async def _seed_store(
    case: RagCase,
    *,
    tenant_id: UUID,
    embedder: _EmbedderLike,
) -> tuple[InMemoryKnowledgeStore, dict[tuple[str, int], str]]:
    """Build a fresh store containing ``case``'s corpus.

    Returns the store plus ``locator`` — keyed by
    ``(filename, chunk_index)`` and projecting each chunk back to its
    case-local ``RagChunk.id``. ``RetrievedChunk`` carries the filename +
    chunk_index but not a stable id, so the scorer joins on that tuple
    instead of poking the store's internals.
    """
    store = InMemoryKnowledgeStore()
    # Embed every chunk content in one batch — same call shape the
    # production ingestion runner uses (chunks → embedder.embed → store).
    all_chunks: list[tuple[RagKnowledgeBase, RagDocument, RagChunk]] = []
    for kb in case.knowledge_bases:
        for doc in kb.documents:
            for chunk in doc.chunks:
                all_chunks.append((kb, doc, chunk))
    if not all_chunks:
        return store, {}
    vectors = await embedder.embed([c.content for *_, c in all_chunks])

    locator: dict[tuple[str, int], str] = {}
    by_kb: dict[str, UUID] = {}
    by_doc: dict[tuple[str, str], UUID] = {}
    chunks_by_doc: dict[UUID, list[KnowledgeChunk]] = {}

    for (kb, doc, chunk), vector in zip(all_chunks, vectors, strict=True):
        kb_id = by_kb.get(kb.name)
        if kb_id is None:
            base = await store.create_base(tenant_id=tenant_id, name=kb.name)
            kb_id = base.id
            by_kb[kb.name] = kb_id
        doc_key = (kb.name, doc.filename)
        doc_id = by_doc.get(doc_key)
        if doc_id is None:
            document = await store.upsert_document(
                tenant_id=tenant_id,
                kb_id=kb_id,
                filename=doc.filename,
            )
            doc_id = document.id
            by_doc[doc_key] = doc_id
            chunks_by_doc[doc_id] = []
        chunk_index = len(chunks_by_doc[doc_id])
        locator[(doc.filename, chunk_index)] = chunk.id
        chunks_by_doc[doc_id].append(
            KnowledgeChunk(
                id=uuid4(),
                tenant_id=tenant_id,
                kb_id=kb_id,
                document_id=doc_id,
                chunk_index=chunk_index,
                content=chunk.content,
                embedding=tuple(vector),
            )
        )

    for doc_id, chunks in chunks_by_doc.items():
        await store.replace_chunks(
            tenant_id=tenant_id,
            document_id=doc_id,
            chunks=chunks,
        )
    return store, locator


async def evaluate_case(case: RagCase, *, embedder: _EmbedderLike) -> CapabilityCaseResult:
    """Score one case.

    Builds the case's corpus into a fresh store, runs the retriever
    against ``case.query``, and compares the retrieved chunks' case-local
    ids against ``case.expected_chunk_ids``.
    """
    # Import inside the function so this module's top-level import surface
    # mirrors ``memory_recall.py`` — orchestrator is only touched at runtime.
    from orchestrator.tools.knowledge import KnowledgeRetriever

    tenant_id = uuid4()
    store, locator = await _seed_store(case, tenant_id=tenant_id, embedder=embedder)

    retriever = KnowledgeRetriever(store=store, embedder=cast(Any, embedder), reranker=None)
    retrieved = await retriever.search(
        tenant_id=tenant_id,
        base_names=list(case.query_bases),
        query=case.query,
        limit=case.k,
    )

    retrieved_ids: list[str] = []
    for hit in retrieved:
        case_id = locator.get((hit.filename, hit.chunk_index))
        if case_id is not None:
            retrieved_ids.append(case_id)

    expected = set(case.expected_chunk_ids)
    if not expected:
        # An expected-empty case passes when retrieval finds nothing (or
        # nothing matching the case's chunks — both are valid negatives).
        passed = len(retrieved_ids) == 0
        recall = 1.0 if passed else 0.0
        return CapabilityCaseResult(
            case_id=case.case_id,
            passed=passed,
            scores={"recall_at_k": recall},
            notes=() if passed else (f"expected empty retrieval; got {retrieved_ids}",),
        )

    hit_set = set(retrieved_ids[: case.k]) & expected
    recall = len(hit_set) / len(expected)
    passed = recall >= 1.0  # Hard pass: every expected chunk must land in top-k.

    notes: tuple[str, ...] = ()
    if not passed:
        missing = expected - hit_set
        notes = (f"missing expected ids: {sorted(missing)}; got top-{case.k} {retrieved_ids}",)
    return CapabilityCaseResult(
        case_id=case.case_id,
        passed=passed,
        scores={"recall_at_k": recall},
        notes=notes,
    )


async def evaluate_set(
    cases: Sequence[RagCase],
    *,
    embedder: _EmbedderLike,
) -> CapabilityReport:
    """Score a whole set and return the J.5 :class:`CapabilityReport`."""
    per_case: list[CapabilityCaseResult] = []
    for case in cases:
        per_case.append(await evaluate_case(case, embedder=embedder))
    sample = len(per_case)
    pass_rate = sum(1 for r in per_case if r.passed) / sample if sample else 0.0
    recall = sum(r.scores.get("recall_at_k", 0.0) for r in per_case) / sample if sample else 0.0
    status = (
        "PASS"
        if pass_rate >= THRESHOLD["pass_rate"] and recall >= THRESHOLD["recall_at_k"]
        else "FAIL"
    )
    return CapabilityReport(
        capability=CAPABILITY,
        metric_type=METRIC_TYPE,
        sample_size=sample,
        threshold=THRESHOLD,
        aggregate_score={"pass_rate": pass_rate, "recall_at_k": recall},
        status=cast(Any, status),
        per_case=tuple(per_case),
    )


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_cases(path: Path) -> list[RagCase]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [_parse_case(entry) for entry in raw.get("cases", [])]


def _parse_case(entry: dict[str, Any]) -> RagCase:
    bases = tuple(_parse_base(b) for b in entry.get("knowledge_bases", []))
    query_bases = tuple(str(name) for name in entry.get("query_bases", ()))
    return RagCase(
        case_id=str(entry["id"]),
        knowledge_bases=bases,
        query_bases=query_bases,
        query=str(entry["query"]),
        expected_chunk_ids=tuple(str(x) for x in entry.get("expected_chunk_ids", ())),
        k=int(entry.get("k", 5)),
        language=str(entry.get("language", "en")),
    )


def _parse_base(entry: dict[str, Any]) -> RagKnowledgeBase:
    docs = tuple(_parse_doc(d) for d in entry.get("documents", []))
    return RagKnowledgeBase(name=str(entry["name"]), documents=docs)


def _parse_doc(entry: dict[str, Any]) -> RagDocument:
    chunks = tuple(
        RagChunk(id=str(c["id"]), content=str(c["content"])) for c in entry.get("chunks", [])
    )
    return RagDocument(filename=str(entry["filename"]), chunks=chunks)


__all__ = [
    "CAPABILITY",
    "METRIC_TYPE",
    "THRESHOLD",
    "RagCase",
    "RagChunk",
    "RagDocument",
    "RagKnowledgeBase",
    "evaluate_case",
    "evaluate_set",
    "load_cases",
]
