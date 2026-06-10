"""SQLAlchemy + pgvector ``MemoryStore`` (Postgres / asyncpg)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import ColumnElement, and_, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from helix_agent.common.search.decay import temporal_decay_factor
from helix_agent.common.search.rrf import rrf_fuse_scored
from helix_agent.common.threat_patterns import ThreatFinding, scan_for_threats
from helix_agent.persistence.knowledge.text_search import tokenize_for_search
from helix_agent.persistence.memory.base import MemoryInjectionBlockedError, MemoryStore
from helix_agent.persistence.memory.hash import hash_content
from helix_agent.persistence.models import MemoryItemRow
from helix_agent.protocol import MemoryItem

#: Postgres ``tsvector`` configuration — ``simple`` so app-side jieba
#: segmentation is what controls tokenization (mirrors J.5 knowledge).
_TS_CONFIG = "simple"

#: Per-side recall depth fetched before RRF fusion — mirrors J.5.
_HYBRID_RECALL_LIMIT = 20


def _cosine_distance_value(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(float(x) * float(y) for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(float(x) * float(x) for x in a))
    norm_b = math.sqrt(sum(float(y) * float(y) for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 1.0
    return 1.0 - dot / (norm_a * norm_b)


def _decay_for(
    last_used_at: datetime | None, created_at: datetime | None, *, now: datetime
) -> float:
    """Stream CM-6 (Mini-ADR CM-G2/G3) — recency weight for one row.

    Anchored on ``last_used_at`` (use keeps a memory fresh), falling back
    to ``created_at``; no timestamp at all decays nothing.
    """
    anchor = last_used_at or created_at
    if anchor is None:
        return 1.0
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=UTC)
    return temporal_decay_factor(age=now - anchor)


def _row_to_item(row: MemoryItemRow) -> MemoryItem:
    item = MemoryItem(
        id=row.id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        kind=row.kind,  # type: ignore[arg-type]
        content=row.content,
        content_hash=row.content_hash,
        embedding=tuple(float(value) for value in row.embedding),
        source_thread_id=row.source_thread_id,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        deleted_at=row.deleted_at,
        # Capability Uplift Sprint #7 (Mini-ADR U-33) — lifecycle fields.
        status=row.status,  # type: ignore[arg-type]
        consolidated_into=row.consolidated_into,
        consolidated_from=tuple(UUID(str(uid)) for uid in row.consolidated_from),
        last_reviewed_at=row.last_reviewed_at,
    )
    # Capability Uplift Sprint #2 (Mini-ADR U-4) — drift detection.
    if row.content_hash and hash_content(row.content) != row.content_hash:
        return item.model_copy(update={"drift": True})
    return item


# Capability Uplift Sprint #7 (Mini-ADR U-33) — default retrieve filter
# applied to every code path that returns "what the agent should see".
# Skips ``archived`` outright and skips raw transient items that have
# been superseded by a consolidated parent (the parent is returned in
# their place when relevant).
def _retrieve_filter() -> list[ColumnElement[bool]]:
    return [
        MemoryItemRow.status != "archived",
        or_(
            MemoryItemRow.status == "consolidated",
            MemoryItemRow.consolidated_into.is_(None),
        ),
    ]


class SqlMemoryStore(MemoryStore):
    """Postgres-backed long-term memory repository (pgvector)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def write(self, items: Sequence[MemoryItem]) -> None:
        if not items:
            return
        # Capability Uplift Sprint #2 (Mini-ADR U-3) — atomic strict scan.
        blocked: list[tuple[UUID, list[ThreatFinding]]] = []
        for item in items:
            findings = scan_for_threats(item.content, scope="strict")
            if findings:
                blocked.append((item.id, findings))
        if blocked:
            raise MemoryInjectionBlockedError(blocked)
        # Stream K.K7 — fill content_hash here so callers do not need
        # to import the hash helper, and use ON CONFLICT DO NOTHING
        # against the (tenant_id, user_id, content_hash) partial unique
        # index so a re-run that re-extracts the same memory is a no-op
        # instead of a duplicate row.
        payload = [
            {
                "id": item.id,
                "tenant_id": item.tenant_id,
                "user_id": item.user_id,
                "kind": item.kind,
                "content": item.content,
                "content_hash": item.content_hash or hash_content(item.content),
                "embedding": list(item.embedding),
                "source_thread_id": item.source_thread_id,
                # Capability Uplift Sprint #6 — populate the tsvector
                # column from jieba-segmented content. ``func.to_tsvector``
                # is evaluated server-side so the value lands as a real
                # tsvector, not a string cast.
                "content_tsv": func.to_tsvector(_TS_CONFIG, tokenize_for_search(item.content)),
            }
            for item in items
        ]
        stmt = pg_insert(MemoryItemRow).values(payload).on_conflict_do_nothing()
        async with self._sf() as session:
            await session.execute(stmt)
            await session.commit()

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
        # Capability Uplift Sprint #6 (Mini-ADR U-5) — hybrid path.
        if query_text is not None and query_text.strip():
            return await self._hybrid_retrieve(
                tenant_id=tenant_id,
                user_id=user_id,
                query_embedding=query_embedding,
                query_text=query_text,
                kind=kind,
                limit=limit,
            )
        return await self._vector_retrieve(
            tenant_id=tenant_id,
            user_id=user_id,
            query_embedding=query_embedding,
            kind=kind,
            limit=limit,
        )

    async def _vector_retrieve(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        query_embedding: Sequence[float],
        kind: Literal["fact", "episodic"] | None,
        limit: int,
    ) -> list[MemoryItem]:
        stmt = select(MemoryItemRow).where(
            MemoryItemRow.tenant_id == tenant_id,
            MemoryItemRow.user_id == user_id,
            MemoryItemRow.deleted_at.is_(None),  # Stream K.K6 — exclude soft-deleted
            *_retrieve_filter(),  # Sprint #7 lifecycle filter
        )
        if kind is not None:
            stmt = stmt.where(MemoryItemRow.kind == kind)
        # pgvector cosine distance (``<=>``); HNSW index backs the sort.
        stmt = stmt.order_by(MemoryItemRow.embedding.cosine_distance(list(query_embedding))).limit(
            limit
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        # Stream CM-6 (Mini-ADR CM-G2) — temporal decay re-ranks inside the
        # recall window: similarity (1 - distance/2 ∈ [0,1]) weighted by
        # recency of use. The window itself is unchanged.
        now = datetime.now(UTC)
        weighted = sorted(
            rows,
            key=lambda row: (
                (1.0 - _cosine_distance_value(query_embedding, row.embedding) / 2.0)
                * _decay_for(row.last_used_at, row.created_at, now=now)
            ),
            reverse=True,
        )
        return [_row_to_item(row) for row in weighted]

    async def _hybrid_retrieve(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        query_embedding: Sequence[float],
        query_text: str,
        kind: Literal["fact", "episodic"] | None,
        limit: int,
    ) -> list[MemoryItem]:
        tokenized = tokenize_for_search(query_text)
        if not tokenized:
            return await self._vector_retrieve(
                tenant_id=tenant_id,
                user_id=user_id,
                query_embedding=query_embedding,
                kind=kind,
                limit=limit,
            )
        # Two parallel selects under the same RLS-scoped session; fuse
        # in Python (cheaper than a SQL UNION + window function for the
        # small recall_limit window we work with).
        ts_query = func.plainto_tsquery(_TS_CONFIG, tokenized)
        base_where: list[ColumnElement[bool]] = [
            MemoryItemRow.tenant_id == tenant_id,
            MemoryItemRow.user_id == user_id,
            MemoryItemRow.deleted_at.is_(None),
            *_retrieve_filter(),  # Sprint #7 lifecycle filter
        ]
        if kind is not None:
            base_where.append(MemoryItemRow.kind == kind)

        vector_stmt = (
            select(MemoryItemRow)
            .where(*base_where)
            .order_by(MemoryItemRow.embedding.cosine_distance(list(query_embedding)))
            .limit(_HYBRID_RECALL_LIMIT)
        )
        keyword_stmt = (
            select(MemoryItemRow)
            .where(*base_where, MemoryItemRow.content_tsv.op("@@")(ts_query))
            .order_by(func.ts_rank(MemoryItemRow.content_tsv, ts_query).desc())
            .limit(_HYBRID_RECALL_LIMIT)
        )
        async with self._sf() as session:
            vector_rows = (await session.execute(vector_stmt)).scalars().all()
            keyword_rows = (await session.execute(keyword_stmt)).scalars().all()
        # RRF on the row IDs (hashable); resolve back to rows after fusion.
        # Stream CM-6 (Mini-ADR CM-G2) — temporal decay re-weights the
        # fused scores before the final cut so recently-used memories win
        # same-relevance ties inside the recall window.
        by_id = {row.id: row for row in list(vector_rows) + list(keyword_rows)}
        scored = rrf_fuse_scored([[r.id for r in vector_rows], [r.id for r in keyword_rows]])
        now = datetime.now(UTC)
        weighted = sorted(
            (
                (mid, score * _decay_for(by_id[mid].last_used_at, by_id[mid].created_at, now=now))
                for mid, score in scored
            ),
            key=lambda pair: pair[1],
            reverse=True,
        )
        return [_row_to_item(by_id[mid]) for mid, _score in weighted[:limit]]

    async def list_for_user(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        kind: Literal["fact", "episodic"] | None = None,
        limit: int = 50,
    ) -> list[MemoryItem]:
        stmt = select(MemoryItemRow).where(
            MemoryItemRow.tenant_id == tenant_id,
            MemoryItemRow.user_id == user_id,
            MemoryItemRow.deleted_at.is_(None),
        )
        if kind is not None:
            stmt = stmt.where(MemoryItemRow.kind == kind)
        # newest first; ``memory_item_live_user_idx`` (migration 0024) is
        # a partial index on (user_id, created_at DESC) WHERE
        # deleted_at IS NULL — query shape matches.
        stmt = stmt.order_by(MemoryItemRow.created_at.desc()).limit(limit)
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_item(row) for row in rows]

    async def list_all_tenants(
        self,
        *,
        kind: Literal["fact", "episodic"] | None = None,
        limit: int = 50,
    ) -> list[MemoryItem]:
        # Stream N — no tenant / user filter; caller must wrap in bypass_rls_session().
        stmt = select(MemoryItemRow).where(MemoryItemRow.deleted_at.is_(None))
        if kind is not None:
            stmt = stmt.where(MemoryItemRow.kind == kind)
        stmt = stmt.order_by(MemoryItemRow.created_at.desc()).limit(limit)
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_item(row) for row in rows]

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
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(MemoryItemRow).where(
                        MemoryItemRow.id == memory_id,
                        MemoryItemRow.tenant_id == tenant_id,
                        MemoryItemRow.user_id == user_id,
                        MemoryItemRow.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            row.content = content
            row.content_hash = hash_content(content)  # K.K7 — keep dedup hash in sync
            # Capability Uplift Sprint #6 — keep keyword search vector in
            # sync with the new content.
            row.content_tsv = func.to_tsvector(_TS_CONFIG, tokenize_for_search(content))
            row.embedding = list(embedding)
            if kind is not None:
                row.kind = kind
            await session.commit()
            await session.refresh(row)
            return _row_to_item(row)

    async def soft_delete(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        memory_id: UUID,
    ) -> bool:
        now = datetime.now(UTC)
        stmt = (
            update(MemoryItemRow)
            .where(
                MemoryItemRow.id == memory_id,
                MemoryItemRow.tenant_id == tenant_id,
                MemoryItemRow.user_id == user_id,
                MemoryItemRow.deleted_at.is_(None),
            )
            .values(deleted_at=now)
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            await session.commit()
        if int(getattr(result, "rowcount", 0) or 0) > 0:
            return True
        # Either truly missing or already deleted. Differentiate by a
        # cheap existence check so the caller gets idempotent semantics
        # on a second forget but a clean 404 on an unknown id.
        async with self._sf() as session:
            exists = (
                await session.execute(
                    select(MemoryItemRow.id).where(
                        MemoryItemRow.id == memory_id,
                        MemoryItemRow.tenant_id == tenant_id,
                        MemoryItemRow.user_id == user_id,
                    )
                )
            ).first()
        return exists is not None

    # ------------------------------------------------------------------
    # Capability Uplift Sprint #7 — MemoryConsolidator interface
    # ------------------------------------------------------------------

    async def consolidator_distinct_tenant_ids(self) -> list[UUID]:
        stmt = (
            select(MemoryItemRow.tenant_id)
            .where(
                MemoryItemRow.deleted_at.is_(None),
                MemoryItemRow.status == "transient",
            )
            .distinct()
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return list(rows)

    async def distinct_users(self, *, tenant_id: UUID) -> list[UUID]:
        stmt = (
            select(MemoryItemRow.user_id)
            .where(
                MemoryItemRow.tenant_id == tenant_id,
                MemoryItemRow.deleted_at.is_(None),
                MemoryItemRow.status == "transient",
            )
            .distinct()
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return list(rows)

    async def list_transient(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        max_age_days: int,
        limit: int,
    ) -> list[MemoryItem]:
        cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
        stmt = (
            select(MemoryItemRow)
            .where(
                MemoryItemRow.tenant_id == tenant_id,
                MemoryItemRow.user_id == user_id,
                MemoryItemRow.deleted_at.is_(None),
                MemoryItemRow.status == "transient",
                MemoryItemRow.consolidated_into.is_(None),
                MemoryItemRow.created_at >= cutoff,
            )
            .order_by(MemoryItemRow.created_at.asc())
            .limit(limit)
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_item(row) for row in rows]

    async def vector_neighbors(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        embedding: Sequence[float],
        cosine_max: float,
        limit: int,
    ) -> list[MemoryItem]:
        # pgvector cosine distance via the existing HNSW index. We sort
        # by distance ASC then filter to those within ``cosine_max``.
        stmt = (
            select(MemoryItemRow)
            .where(
                MemoryItemRow.tenant_id == tenant_id,
                MemoryItemRow.user_id == user_id,
                MemoryItemRow.deleted_at.is_(None),
                MemoryItemRow.status == "transient",
                MemoryItemRow.consolidated_into.is_(None),
                MemoryItemRow.embedding.cosine_distance(list(embedding)) <= cosine_max,
            )
            .order_by(MemoryItemRow.embedding.cosine_distance(list(embedding)))
            .limit(limit)
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_item(row) for row in rows]

    async def write_consolidated(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        content: str,
        embedding: Sequence[float],
        source_ids: Sequence[UUID],
    ) -> MemoryItem:
        now = datetime.now(UTC)
        new_id = uuid4()
        content_hash = hash_content(content)
        async with self._sf() as session:
            # Idempotency guard — if any source has consolidated_into set
            # already, abort cleanly so the worker can skip on the next
            # tick. The link-update below is a 2-statement transaction so
            # this guard removes most of the practical race window.
            already = (
                await session.execute(
                    select(MemoryItemRow.id, MemoryItemRow.consolidated_into).where(
                        MemoryItemRow.id.in_(list(source_ids)),
                        MemoryItemRow.consolidated_into.is_not(None),
                    )
                )
            ).first()
            if already is not None:
                msg = (
                    f"memory item {already[0]} already consolidated_into {already[1]}; "
                    "skipping cluster"
                )
                raise RuntimeError(msg)
            # Insert the consolidated parent.
            new_row = MemoryItemRow(
                id=new_id,
                tenant_id=tenant_id,
                user_id=user_id,
                kind="fact",
                content=content,
                content_hash=content_hash,
                embedding=list(embedding),
                created_at=now,
                last_used_at=now,
                content_tsv=func.to_tsvector(_TS_CONFIG, tokenize_for_search(content)),
                status="consolidated",
                consolidated_from=[str(sid) for sid in source_ids],
            )
            session.add(new_row)
            # Atomically link sources back to the new parent.
            await session.execute(
                update(MemoryItemRow)
                .where(MemoryItemRow.id.in_(list(source_ids)))
                .values(consolidated_into=new_id)
            )
            await session.commit()
            await session.refresh(new_row)
            return _row_to_item(new_row)

    async def list_purge_candidates(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        min_age_days: int,
        limit: int,
    ) -> list[MemoryItem]:
        cutoff = datetime.now(UTC) - timedelta(days=min_age_days)
        # "Never retrieved" approximated by
        # ``last_used_at <= created_at + 1 minute`` — writeback bump
        # can be ~ms after insert, the minute slack soaks up clock skew
        # without admitting "actually used" rows (Mini-ADR U-37).
        never_used = MemoryItemRow.last_used_at <= (
            MemoryItemRow.created_at + text("INTERVAL '1 minute'")
        )
        stmt = (
            select(MemoryItemRow)
            .where(
                MemoryItemRow.tenant_id == tenant_id,
                MemoryItemRow.user_id == user_id,
                MemoryItemRow.deleted_at.is_(None),
                MemoryItemRow.status == "transient",
                MemoryItemRow.consolidated_into.is_(None),
                MemoryItemRow.last_reviewed_at.is_(None),
                MemoryItemRow.created_at < cutoff,
                and_(never_used),
            )
            .order_by(MemoryItemRow.created_at.asc())
            .limit(limit)
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_item(row) for row in rows]

    async def mark_reviewed(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        memory_id: UUID,
    ) -> bool:
        now = datetime.now(UTC)
        stmt = (
            update(MemoryItemRow)
            .where(
                MemoryItemRow.id == memory_id,
                MemoryItemRow.tenant_id == tenant_id,
                MemoryItemRow.user_id == user_id,
                MemoryItemRow.deleted_at.is_(None),
            )
            .values(last_reviewed_at=now)
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            await session.commit()
        return int(getattr(result, "rowcount", 0) or 0) > 0

    async def archive(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        memory_id: UUID,
    ) -> bool:
        # Capability Uplift Sprint #7 (Mini-ADR U-40) — reserved for
        # M2-C archive pipeline. Raised loud rather than no-op so the
        # M2-C implementer gets a clear "do me" signal.
        msg = (
            "MemoryStore.archive() is reserved for M2-C; Sprint #7 only "
            "lands the interface + status='archived' retrieve filter."
        )
        raise NotImplementedError(msg)
