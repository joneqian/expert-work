"""Maximal Marginal Relevance selection — Stream CM-6 (Mini-ADR CM-G4).

Pure greedy MMR over cosine similarity, no persistence dependencies. The
memory recall pipeline runs it as the last re-ranking stage (after the
CM-4 cross-encoder rerank) to de-duplicate near-identical memories before
the top-k cut: ``score(c) = λ·sim(query, c) - (1-λ)·max sim(c, selected)``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

#: Relevance↔diversity trade-off (OpenClaw memory-search parity) —
#: λ=1 is pure relevance order, λ=0 is pure diversity.
DEFAULT_MMR_LAMBDA = 0.7


def _cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def mmr_select[T](
    *,
    query_embedding: Sequence[float],
    candidates: Sequence[tuple[T, Sequence[float]]],
    k: int,
    lambda_: float = DEFAULT_MMR_LAMBDA,
) -> list[T]:
    """Greedily select up to ``k`` items balancing relevance and diversity.

    ``candidates`` are ``(item, embedding)`` pairs, expected in the
    caller's relevance order (ties in the greedy argmax keep that order —
    ``>`` strictness means the earlier candidate wins). Candidates whose
    embedding is empty or dimension-mismatched against ``query_embedding``
    are skipped — the caller treats a thinned result as fewer-but-valid,
    never an error (Mini-ADR CM-G6 best-effort contract sits one level up).
    """
    if k <= 0:
        return []
    dim = len(query_embedding)
    pool = [(item, emb) for item, emb in candidates if len(emb) == dim and dim > 0]
    relevance = [_cosine_similarity(query_embedding, emb) for _item, emb in pool]

    selected: list[int] = []
    remaining = list(range(len(pool)))
    while remaining and len(selected) < k:
        best_idx: int | None = None
        best_score = -math.inf
        for idx in remaining:
            redundancy = max(
                (_cosine_similarity(pool[idx][1], pool[sel][1]) for sel in selected),
                default=0.0,
            )
            score = lambda_ * relevance[idx] - (1.0 - lambda_) * redundancy
            if score > best_score:
                best_score = score
                best_idx = idx
        if best_idx is None:  # pragma: no cover — remaining non-empty
            break
        selected.append(best_idx)
        remaining.remove(best_idx)
    return [pool[idx][0] for idx in selected]


__all__ = ["DEFAULT_MMR_LAMBDA", "mmr_select"]
