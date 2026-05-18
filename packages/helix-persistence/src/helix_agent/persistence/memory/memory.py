"""In-memory ``MemoryStore`` for unit tests."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Literal
from uuid import UUID

from helix_agent.persistence.memory.base import MemoryStore
from helix_agent.protocol import MemoryItem


def _cosine_distance(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine distance (0 = identical) — mirrors pgvector's ``<=>``."""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 1.0
    return 1.0 - dot / (norm_a * norm_b)


class InMemoryMemoryStore(MemoryStore):
    def __init__(self) -> None:
        self._rows: list[MemoryItem] = []

    async def write(self, items: Sequence[MemoryItem]) -> None:
        self._rows.extend(items)

    async def retrieve(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        query_embedding: Sequence[float],
        kind: Literal["fact", "episodic"] | None = None,
        limit: int = 5,
    ) -> list[MemoryItem]:
        candidates = [
            row
            for row in self._rows
            if row.tenant_id == tenant_id
            and row.user_id == user_id
            and (kind is None or row.kind == kind)
        ]
        candidates.sort(key=lambda row: _cosine_distance(query_embedding, row.embedding))
        return candidates[:limit]
