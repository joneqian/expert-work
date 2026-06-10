"""P0 retrieval-tier harness — Stream CM-N5 (Mini-ADR CM-K1/K4/K5).

Replays a benchmark corpus through the production recall shape — wide
``MemoryStore.retrieve`` (decay inside) -> optional cross-encoder rerank
-> MMR diversity cut — and scores the final top-k against the
benchmarks' own ground truth. Zero LLM calls: with a deterministic
embedder the whole tier is a mechanical regression gate.

The **ablation matrix** is the attribution mechanism (CM-K5); every arm
is a data- or parameter-level switch, never a monkeypatch:

- ``hybrid``   — pass ``query_text`` to ``retrieve`` (RRF fusion) or not
- ``decay``    — write docs with shifted timestamps (CM-K4) or none at
  all ("no timestamp decays nothing" is store semantics)
- ``rerank``   — thread a :class:`Reranker` through, or skip
- ``mmr``      — apply ``mmr_select`` over the wide order, or truncate

Stores are built **once per shared corpus** (LoCoMo's ~150 QA per
conversation share one corpus tuple), so real-embedder runs embed each
conversation once, not once per question.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from helix_agent.common.search.mmr import mmr_select
from helix_agent.common.threat_patterns import scan_for_threats
from helix_agent.persistence import InMemoryMemoryStore
from helix_agent.persistence.memory.base import MemoryStore
from helix_agent.protocol import MemoryItem
from longmem.adapter import MemoryDoc, RetrievalInstance
from longmem.metrics import mrr_at_k, ndcg_at_k, ordered_unique, recall_at_k

logger = logging.getLogger(__name__)

#: Fixed identities — the benchmark replay is single-tenant by nature.
EVAL_TENANT_ID = uuid5(NAMESPACE_URL, "helix-eval://longmem/tenant")
EVAL_USER_ID = uuid5(NAMESPACE_URL, "helix-eval://longmem/user")

#: Embedding batch size — keeps real-embedder requests reasonably sized.
_EMBED_BATCH = 64


class Embedder(Protocol):
    """Structural mirror of the orchestrator Embedder protocol."""

    async def embed(self, texts: Sequence[str], *, tenant_id: UUID) -> list[tuple[float, ...]]:
        """Return one embedding vector per input text, in input order."""


class Reranker(Protocol):
    """Structural mirror of the orchestrator Reranker protocol."""

    async def rerank(
        self, *, query: str, documents: Sequence[str], top_k: int, tenant_id: UUID
    ) -> list[int]:
        """Return indices of the ``top_k`` most relevant documents, best first."""


@dataclass(frozen=True)
class AblationConfig:
    """One arm of the CM-K5 matrix. Defaults = the production shape."""

    hybrid: bool = True
    decay: bool = True
    mmr: bool = True
    rerank: bool = False
    top_k: int = 5
    wide_limit: int = 20


@dataclass(frozen=True)
class InstanceResult:
    question_id: str
    question_type: str
    session_recall: float
    turn_recall: float
    ndcg: float
    mrr: float


@dataclass(frozen=True)
class RetrievalReport:
    """Aggregate over one dataset x one ablation arm."""

    config: AblationConfig
    n_instances: int
    blocked_writes: int
    deduped_writes: int
    mean_session_recall: float
    mean_turn_recall: float
    mean_ndcg: float
    mean_mrr: float
    by_question_type: dict[str, dict[str, float]] = field(default_factory=dict)
    per_instance: tuple[InstanceResult, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """YAML-ready projection (per-instance rows omitted — they go to jsonl)."""
        data = asdict(self)
        data.pop("per_instance")
        return data


def _doc_uuid(doc_id: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"helix-eval://longmem/doc/{doc_id}")


async def _build_store(
    docs: Sequence[MemoryDoc],
    *,
    embedder: Embedder,
    decay: bool,
    delta: timedelta,
) -> tuple[MemoryStore, dict[UUID, MemoryDoc], int, int]:
    """Embed + write one corpus into a fresh in-memory store.

    Returns ``(store, uuid->doc map, blocked_count, deduped_count)``.
    Docs tripping the strict threat scan are pre-filtered and counted
    (``write`` raises atomically, so filtering up front keeps the rest
    of the corpus; the count is surfaced on the report — benchmark text
    occasionally pattern-matches injection heuristics and silence would
    misread as full coverage). Duplicate contents are deduped by the
    store's content-hash guard; they are counted here so a ground-truth
    doc silently merged into a twin is visible in the report.
    """
    store = InMemoryMemoryStore()
    blocked = 0
    kept: list[MemoryDoc] = []
    for doc in docs:
        if scan_for_threats(doc.content, scope="strict"):
            blocked += 1
            continue
        kept.append(doc)

    embeddings: list[tuple[float, ...]] = []
    for start in range(0, len(kept), _EMBED_BATCH):
        batch = kept[start : start + _EMBED_BATCH]
        embeddings.extend(
            await embedder.embed([d.content for d in batch], tenant_id=EVAL_TENANT_ID)
        )

    by_uuid: dict[UUID, MemoryDoc] = {}
    items: list[MemoryItem] = []
    for doc, embedding in zip(kept, embeddings, strict=True):
        item_id = _doc_uuid(doc.doc_id)
        timestamp = doc.timestamp + delta if (decay and doc.timestamp is not None) else None
        items.append(
            MemoryItem(
                id=item_id,
                tenant_id=EVAL_TENANT_ID,
                user_id=EVAL_USER_ID,
                kind="episodic",
                content=doc.content,
                embedding=embedding,
                created_at=timestamp,
                last_used_at=timestamp,
            )
        )
        by_uuid[item_id] = doc
    await store.write(items)
    stored = len(
        await store.list_for_user(
            tenant_id=EVAL_TENANT_ID, user_id=EVAL_USER_ID, limit=len(items) + 1
        )
    )
    deduped = len(items) - stored
    return store, by_uuid, blocked, deduped


async def _final_top_k(
    *,
    store: MemoryStore,
    by_uuid: dict[UUID, MemoryDoc],
    question: str,
    query_embedding: tuple[float, ...],
    reranker: Reranker | None,
    config: AblationConfig,
) -> list[MemoryDoc]:
    """Wide retrieve -> optional full-order rerank -> MMR/truncate — the
    production pipeline shape (CM-4 §6 / CM-6 §8)."""
    wide = max(config.top_k, config.wide_limit)
    candidates = await store.retrieve(
        tenant_id=EVAL_TENANT_ID,
        user_id=EVAL_USER_ID,
        query_embedding=query_embedding,
        query_text=question if config.hybrid else None,
        limit=wide,
    )
    if config.rerank and reranker is not None and candidates:
        try:
            order = await reranker.rerank(
                query=question,
                documents=[m.content for m in candidates],
                top_k=len(candidates),
                tenant_id=EVAL_TENANT_ID,
            )
            reranked = [candidates[i] for i in order if 0 <= i < len(candidates)]
            if reranked:
                candidates = reranked
        except Exception:
            logger.warning("longmem.rerank_failed — keeping fused order", exc_info=True)
    if config.mmr:
        selected = mmr_select(
            query_embedding=query_embedding,
            candidates=[(m, m.embedding) for m in candidates],
            k=config.top_k,
        )
        final = selected if selected else candidates[: config.top_k]
    else:
        final = candidates[: config.top_k]
    return [by_uuid[m.id] for m in final if m.id in by_uuid]


async def evaluate_retrieval(
    instances: Sequence[RetrievalInstance],
    *,
    embedder: Embedder,
    config: AblationConfig | None = None,
    reranker: Reranker | None = None,
    now: datetime | None = None,
    concurrency: int = 1,
) -> RetrievalReport:
    """Score ``instances`` under one ablation arm.

    Instances sharing a corpus tuple **and** question date (every LoCoMo
    QA of one conversation) share one store build; LongMemEval instances
    each get their own. ``now`` is taken once at entry so the CM-K4 time
    shift is consistent across the whole run.

    ``concurrency`` bounds in-flight store builds / instance scorings
    via one shared semaphore; results stay input-ordered (``gather``
    preserves order), so ``concurrency=1`` is byte-identical to the
    sequential path and CI keeps deterministic numbers.
    """
    cfg = config or AblationConfig()
    if cfg.rerank and reranker is None:
        raise ValueError("config.rerank=True requires a reranker instance")
    wall_now = now or datetime.now(UTC)
    sem = asyncio.Semaphore(max(1, concurrency))

    groups: dict[tuple[int, datetime], list[RetrievalInstance]] = defaultdict(list)
    for instance in instances:
        groups[(id(instance.docs), instance.question_date)].append(instance)

    async def _score_instance(
        instance: RetrievalInstance,
        store: MemoryStore,
        by_uuid: dict[UUID, MemoryDoc],
    ) -> InstanceResult:
        async with sem:
            (query_embedding,) = await embedder.embed([instance.question], tenant_id=EVAL_TENANT_ID)
            final = await _final_top_k(
                store=store,
                by_uuid=by_uuid,
                question=instance.question,
                query_embedding=query_embedding,
                reranker=reranker,
                config=cfg,
            )
        doc_ids = [d.doc_id for d in final]
        session_ids = ordered_unique([d.session_id for d in final])
        return InstanceResult(
            question_id=instance.question_id,
            question_type=instance.question_type,
            session_recall=recall_at_k(session_ids, instance.answer_session_ids, cfg.top_k),
            turn_recall=recall_at_k(doc_ids, instance.answer_doc_ids, cfg.top_k),
            ndcg=ndcg_at_k(doc_ids, instance.answer_doc_ids, cfg.top_k),
            mrr=mrr_at_k(doc_ids, instance.answer_doc_ids, cfg.top_k),
        )

    async def _run_group(
        grouped: list[RetrievalInstance],
    ) -> tuple[list[InstanceResult], int, int]:
        first = grouped[0]
        delta = wall_now - first.question_date
        async with sem:
            store, by_uuid, blocked, deduped = await _build_store(
                first.docs, embedder=embedder, decay=cfg.decay, delta=delta
            )
        rows = await asyncio.gather(*[_score_instance(i, store, by_uuid) for i in grouped])
        return list(rows), blocked, deduped

    group_outcomes = await asyncio.gather(*[_run_group(g) for g in groups.values()])
    results: list[InstanceResult] = []
    blocked_total = 0
    deduped_total = 0
    for rows, blocked, deduped in group_outcomes:
        results.extend(rows)
        blocked_total += blocked
        deduped_total += deduped

    def _mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    by_type: dict[str, list[InstanceResult]] = defaultdict(list)
    for result in results:
        by_type[result.question_type].append(result)
    breakdown = {
        qtype: {
            "n": float(len(rows)),
            "session_recall": _mean([r.session_recall for r in rows]),
            "turn_recall": _mean([r.turn_recall for r in rows]),
            "ndcg": _mean([r.ndcg for r in rows]),
            "mrr": _mean([r.mrr for r in rows]),
        }
        for qtype, rows in sorted(by_type.items())
    }
    return RetrievalReport(
        config=cfg,
        n_instances=len(results),
        blocked_writes=blocked_total,
        deduped_writes=deduped_total,
        mean_session_recall=_mean([r.session_recall for r in results]),
        mean_turn_recall=_mean([r.turn_recall for r in results]),
        mean_ndcg=_mean([r.ndcg for r in results]),
        mean_mrr=_mean([r.mrr for r in results]),
        by_question_type=breakdown,
        per_instance=tuple(results),
    )
