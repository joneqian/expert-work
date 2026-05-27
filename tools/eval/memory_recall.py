"""Memory recall eval gate — Stream K.K12.

A small harness for grading the J.3 long-term memory ``retrieve`` path
against a curated golden set. The bulk of K12 is *infrastructure*: a
schema for cases, recall / MRR metric helpers, and a runner that
threads a configurable :class:`Embedder` + :class:`MemoryStore` through
the cases. The actual benchmark numbers (recall@5 ≥ 0.7 etc.) move
into ``slo.md`` so they survive embedder swaps.

Why this exists: J.3 ships an embedder + vector store + retrieve API,
but nothing in CI tells us "did we just regress recall quality?". The
audit (G2c) called that out as a (c)-class gap — capability without a
measurable bar is a weak version of the feature.

Cases are YAML, one set per language so the gate can score zh / en
separately::

    cases:
      - id: zh-001
        query: 我喜欢咖啡
        memories:
          - id: m1
            content: 用户偏好咖啡而不是茶
            kind: fact
          - id: m2
            content: 用户家有一只猫
            kind: fact
        expected_recall_ids: [m1]   # m1 is the right answer; m2 is a distractor

A case's ``memories`` are written into a fresh ``InMemoryMemoryStore``
for the case (every case is isolated). ``retrieve`` is called with the
case's ``query`` embedded by the same embedder; the returned ids are
compared against ``expected_recall_ids``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

import yaml

# ---------------------------------------------------------------------------
# Case + report dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CaseMemory:
    """One memory in a case's corpus."""

    id: str
    content: str
    kind: Literal["fact", "episodic"] = "fact"


@dataclass(frozen=True)
class RecallCase:
    """One eval case: a query + the corpus + the expected hits."""

    id: str
    query: str
    memories: tuple[CaseMemory, ...]
    expected_recall_ids: tuple[str, ...]
    language: Literal["zh", "en"] = "en"


@dataclass(frozen=True)
class CaseResult:
    """The metric outcome for one case."""

    case_id: str
    language: str
    recall_at_k: float
    mrr_at_k: float


@dataclass(frozen=True)
class EvalReport:
    """Aggregate report across a whole set."""

    k: int
    n_cases: int
    mean_recall_at_k: float
    mean_mrr_at_k: float
    per_case: tuple[CaseResult, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Metric functions — pure, easy to unit-test
# ---------------------------------------------------------------------------


def recall_at_k(retrieved_ids: Sequence[str], expected_ids: Sequence[str], k: int) -> float:
    """Fraction of expected ids found in the top-``k`` retrieved.

    Identical to the SLI used by IR systems — ``|expected ∩ top_k| /
    |expected|``. Returns ``1.0`` when ``expected_ids`` is empty so an
    empty expectation isn't counted as a failure (the case is a no-op).
    """
    if not expected_ids:
        return 1.0
    if k <= 0:
        return 0.0
    top = set(retrieved_ids[:k])
    hits = sum(1 for eid in expected_ids if eid in top)
    return hits / len(expected_ids)


def mrr_at_k(retrieved_ids: Sequence[str], expected_ids: Sequence[str], k: int) -> float:
    """Mean reciprocal rank of the *first* expected id in the top-``k``.

    Rewards ranking the right answer high. Returns ``0.0`` when no
    expected id appears in the top-``k``; ``1.0`` when the very first
    retrieved id is in ``expected_ids``. Useful sibling to recall —
    catches "the right doc is in top-k but it's at rank 5".
    """
    if not expected_ids or k <= 0:
        return 0.0
    expected_set = set(expected_ids)
    for rank, doc_id in enumerate(retrieved_ids[:k], start=1):
        if doc_id in expected_set:
            return 1.0 / rank
    return 0.0


# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------


def load_cases(path: Path) -> list[RecallCase]:
    """Read an eval set from disk.

    The YAML shape is a top-level ``cases:`` list; each case has
    ``id`` / ``query`` / ``memories`` / ``expected_recall_ids`` and
    an optional ``language``. See this module's docstring for an
    example.
    """
    raw = yaml.safe_load(path.read_text())
    cases_raw = raw.get("cases", [])
    if not isinstance(cases_raw, list):
        msg = f"'cases' must be a list, got {type(cases_raw).__name__}"
        raise ValueError(msg)
    out: list[RecallCase] = []
    for entry in cases_raw:
        out.append(_parse_case(entry))
    return out


def _parse_case(entry: Any) -> RecallCase:
    if not isinstance(entry, dict):
        msg = "case entries must be mappings"
        raise ValueError(msg)
    memories = tuple(
        CaseMemory(
            id=str(m["id"]),
            content=str(m["content"]),
            kind=m.get("kind", "fact"),
        )
        for m in entry.get("memories", [])
    )
    return RecallCase(
        id=str(entry["id"]),
        query=str(entry["query"]),
        memories=memories,
        expected_recall_ids=tuple(str(x) for x in entry.get("expected_recall_ids", ())),
        language=entry.get("language", "en"),
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class _EmbedderLike(Protocol):
    async def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        """Embed each text and return one vector per input."""


async def evaluate_case(
    case: RecallCase,
    *,
    embedder: _EmbedderLike,
    k: int = 5,
    mode: Literal["vector", "hybrid"] = "vector",
) -> CaseResult:
    """Score one case.

    The case's corpus is loaded into a fresh in-memory store so the
    metric reflects retrieval against exactly the memories the case
    declared — no cross-case contamination, no order dependence.

    Capability Uplift Sprint #6 (Mini-ADR U-5): when ``mode='hybrid'``
    the query text is forwarded to ``MemoryStore.retrieve(query_text=)``
    so the keyword side runs alongside the vector side. ``mode='vector'``
    keeps the pre-Sprint-#6 pure-vector baseline (the default — used
    when a caller wants the older comparison point).
    """
    # Import inside the function so this module's import surface stays
    # narrow — the runner is the only path that touches helix-persistence.
    from helix_agent.persistence import InMemoryMemoryStore
    from helix_agent.protocol import MemoryItem

    tenant_id = uuid4()
    user_id = uuid4()
    store = InMemoryMemoryStore()

    # Embed every memory + the query in one batch so the embedder gets
    # the cheapest call shape on the production path too.
    texts = [m.content for m in case.memories] + [case.query]
    vectors = await embedder.embed(texts)
    memory_vectors = vectors[:-1]
    query_vector = vectors[-1]

    # ``id_map`` lets us project the store's UUIDs back to the case's
    # string ids when we score.
    id_map: dict[UUID, str] = {}
    items: list[MemoryItem] = []
    for case_memory, vector in zip(case.memories, memory_vectors, strict=True):
        memory_uuid = uuid4()
        id_map[memory_uuid] = case_memory.id
        items.append(
            MemoryItem(
                id=memory_uuid,
                tenant_id=tenant_id,
                user_id=user_id,
                kind=case_memory.kind,
                content=case_memory.content,
                embedding=tuple(vector),
            )
        )
    await store.write(items)

    retrieved = await store.retrieve(
        tenant_id=tenant_id,
        user_id=user_id,
        query_embedding=query_vector,
        query_text=case.query if mode == "hybrid" else None,
        limit=max(k, len(case.expected_recall_ids)),
    )
    retrieved_ids = [id_map[item.id] for item in retrieved]
    return CaseResult(
        case_id=case.id,
        language=case.language,
        recall_at_k=recall_at_k(retrieved_ids, case.expected_recall_ids, k),
        mrr_at_k=mrr_at_k(retrieved_ids, case.expected_recall_ids, k),
    )


async def evaluate_set(
    cases: Sequence[RecallCase],
    *,
    embedder: _EmbedderLike,
    k: int = 5,
    mode: Literal["vector", "hybrid"] = "vector",
) -> EvalReport:
    """Score a whole set and return aggregate + per-case results."""
    per_case = [await evaluate_case(case, embedder=embedder, k=k, mode=mode) for case in cases]
    if not per_case:
        return EvalReport(k=k, n_cases=0, mean_recall_at_k=0.0, mean_mrr_at_k=0.0)
    mean_recall = sum(r.recall_at_k for r in per_case) / len(per_case)
    mean_mrr = sum(r.mrr_at_k for r in per_case) / len(per_case)
    return EvalReport(
        k=k,
        n_cases=len(per_case),
        mean_recall_at_k=mean_recall,
        mean_mrr_at_k=mean_mrr,
        per_case=tuple(per_case),
    )


__all__ = [
    "CaseMemory",
    "CaseResult",
    "EvalReport",
    "RecallCase",
    "evaluate_case",
    "evaluate_set",
    "load_cases",
    "mrr_at_k",
    "recall_at_k",
]
