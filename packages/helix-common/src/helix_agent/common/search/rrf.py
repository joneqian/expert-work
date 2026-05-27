"""Reciprocal Rank Fusion — Mini-ADR U-6.

Pure algorithm extracted from ``orchestrator/tools/knowledge.py`` so it
can be shared between J.5 RAG hybrid retrieval and the Sprint #6 memory
hybrid recall path.

Reciprocal Rank Fusion combines several ranked lists by scoring each
item with ``Σ 1/(k + rank)`` across the lists it appears in. An item
ranked well by every list rises to the top; ``k`` (default 60, the
canonical value from Cormack/Clarke/Buettcher 2009) dampens the weight
of the very top ranks so a single list cannot dominate the fusion.

The function is generic over the item type — any hashable type works
(``MemoryItem`` / ``KnowledgeChunk`` / ``UUID`` / dataclasses with
``frozen=True``). Callers keep ownership of comparison semantics.
"""

from __future__ import annotations

from collections.abc import Hashable, Sequence

#: Canonical RRF dampener — Cormack/Clarke/Buettcher 2009 baseline.
#: helix locks this for both J.5 and Sprint #6 so the algorithm behavior
#: is comparable across subsystems. Per-recall-path tuning is M1.
DEFAULT_K = 60


def rrf_fuse[T: Hashable](rankings: Sequence[Sequence[T]], *, k: int = DEFAULT_K) -> list[T]:
    """Fuse several ranked lists by Reciprocal Rank Fusion.

    An item's fused score is the sum over all input lists of
    ``1 / (k + rank)`` (rank is 0-indexed). Items appearing high in
    multiple lists rise to the top. Items appearing in only one list
    are still kept, ranked by their position in that list.

    ``k`` should match across callers that compare fused outputs;
    defaults to :data:`DEFAULT_K`.

    Returns the fused items most-relevant first. An empty input
    returns ``[]``. Duplicate items inside one input list are
    collapsed to the first occurrence (the higher rank).
    """
    if not rankings:
        return []
    scores: dict[T, float] = {}
    for ranking in rankings:
        seen: set[T] = set()
        for rank, item in enumerate(ranking):
            if item in seen:
                continue
            seen.add(item)
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.keys(), key=lambda t: scores[t], reverse=True)


__all__ = ["DEFAULT_K", "rrf_fuse"]
