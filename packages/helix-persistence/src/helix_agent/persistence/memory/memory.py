"""In-memory ``MemoryStore`` for unit tests."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from helix_agent.common.search.rrf import rrf_fuse
from helix_agent.common.threat_patterns import ThreatFinding, scan_for_threats
from helix_agent.persistence.knowledge.text_search import tokenize_for_search
from helix_agent.persistence.memory.base import MemoryInjectionBlockedError, MemoryStore
from helix_agent.persistence.memory.hash import hash_content
from helix_agent.protocol import MemoryItem

#: Per-side recall depth fetched before RRF fusion — mirrors the J.5
#: knowledge subsystem so behavior is comparable across hybrid paths.
_HYBRID_RECALL_LIMIT = 20


def _keyword_rank(rows: Sequence[MemoryItem], *, query_text: str) -> list[MemoryItem]:
    """Capability Uplift Sprint #6 — InMemory keyword ranking.

    Approximates Postgres ``ts_rank`` with a token-overlap count on
    jieba-segmented content. The contract is "items whose content
    shares more search tokens with the query rank higher"; the absolute
    score is not part of the contract — the SQL implementation computes
    it differently (``ts_rank``) and that's fine, RRF normalizes by
    rank, not by raw score (Mini-ADR U-5 § 7.3.4).
    """
    query_tokens = set(tokenize_for_search(query_text).split())
    if not query_tokens:
        return []

    def score(row: MemoryItem) -> int:
        return len(query_tokens & set(tokenize_for_search(row.content).split()))

    scored = [(row, score(row)) for row in rows]
    scored = [(row, s) for row, s in scored if s > 0]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [row for row, _ in scored]


def _with_drift_flag(item: MemoryItem) -> MemoryItem:
    """Capability Uplift Sprint #2 (Mini-ADR U-4) — recompute the
    K.K7 content hash and stamp ``drift=True`` when it diverges from
    the stored ``content_hash``. The original content is preserved;
    the recall node redacts based on the flag."""
    stored = item.content_hash
    if stored is None:
        return item
    if hash_content(item.content) == stored:
        return item
    return item.model_copy(update={"drift": True})


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
        # Capability Uplift Sprint #2 (Mini-ADR U-3) — atomic strict scan.
        blocked: list[tuple[object, list[ThreatFinding]]] = []
        for item in items:
            findings = scan_for_threats(item.content, scope="strict")
            if findings:
                blocked.append((item.id, findings))
        if blocked:
            raise MemoryInjectionBlockedError(blocked)  # type: ignore[arg-type]
        for item in items:
            # Stream K.K7 — fill content_hash if the caller didn't, and
            # skip the row when an identical live entry already exists
            # for the same ``(tenant, user, content_hash)`` (mirrors the
            # SQL ON CONFLICT DO NOTHING path).
            content_hash = item.content_hash or hash_content(item.content)
            if any(
                r.tenant_id == item.tenant_id
                and r.user_id == item.user_id
                and r.deleted_at is None
                and (r.content_hash or hash_content(r.content)) == content_hash
                for r in self._rows
            ):
                continue
            self._rows.append(item.model_copy(update={"content_hash": content_hash}))

    async def retrieve(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        query_embedding: Sequence[float],
        query_text: str | None = None,
        kind: Literal["fact", "episodic"] | None = None,
        limit: int = 5,
    ) -> list[MemoryItem]:
        candidates = [
            row
            for row in self._rows
            if row.tenant_id == tenant_id
            and row.user_id == user_id
            and row.deleted_at is None  # Stream K.K6 — hide soft-deleted
            and (kind is None or row.kind == kind)
        ]
        # Vector recall — closest cosine distance first.
        vector_hits = sorted(
            candidates, key=lambda row: _cosine_distance(query_embedding, row.embedding)
        )

        # Capability Uplift Sprint #6 (Mini-ADR U-5) — hybrid path.
        if query_text is not None and query_text.strip():
            keyword_hits = _keyword_rank(candidates, query_text=query_text)
            fused = rrf_fuse(
                [vector_hits[:_HYBRID_RECALL_LIMIT], keyword_hits[:_HYBRID_RECALL_LIMIT]]
            )
            return [_with_drift_flag(row) for row in fused[:limit]]

        return [_with_drift_flag(row) for row in vector_hits[:limit]]

    async def list_for_user(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        kind: Literal["fact", "episodic"] | None = None,
        limit: int = 50,
    ) -> list[MemoryItem]:
        candidates = [
            row
            for row in self._rows
            if row.tenant_id == tenant_id
            and row.user_id == user_id
            and row.deleted_at is None
            and (kind is None or row.kind == kind)
        ]
        # newest-first matches the partial index ordering in migration 0024.
        candidates.sort(
            key=lambda row: row.created_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        return candidates[:limit]

    async def list_all_tenants(
        self,
        *,
        kind: Literal["fact", "episodic"] | None = None,
        limit: int = 50,
    ) -> list[MemoryItem]:
        candidates = [
            row
            for row in self._rows
            if row.deleted_at is None and (kind is None or row.kind == kind)
        ]
        candidates.sort(
            key=lambda row: row.created_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        return candidates[:limit]

    async def update_content(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        memory_id: UUID,
        content: str,
        embedding: Sequence[float],
        kind: Literal["fact", "episodic"] | None = None,
    ) -> MemoryItem | None:
        for idx, row in enumerate(self._rows):
            if (
                row.id == memory_id
                and row.tenant_id == tenant_id
                and row.user_id == user_id
                and row.deleted_at is None
            ):
                updated = row.model_copy(
                    update={
                        "content": content,
                        # K.K7 — keep dedup hash in sync with content
                        "content_hash": hash_content(content),
                        "embedding": tuple(float(v) for v in embedding),
                        "kind": kind if kind is not None else row.kind,
                    }
                )
                self._rows[idx] = updated
                return updated
        return None

    async def soft_delete(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        memory_id: UUID,
    ) -> bool:
        for idx, row in enumerate(self._rows):
            if row.id == memory_id and row.tenant_id == tenant_id and row.user_id == user_id:
                if row.deleted_at is not None:
                    return True  # idempotent
                self._rows[idx] = row.model_copy(update={"deleted_at": datetime.now(UTC)})
                return True
        return False
