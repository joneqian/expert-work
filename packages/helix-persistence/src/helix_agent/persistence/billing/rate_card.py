"""Platform model rate-card store — Stream Y (Mini-ADR Y-3) / 模型定价简化.

CRUD + resolution over the platform-curated model pricing table. Every row is
platform-global (``tenant_id`` is NULL), so SQL callers MUST be inside
``bypass_rls_session()`` — there is no per-tenant RLS scope to satisfy, exactly
like :class:`SqlMcpConnectorCatalogStore` / :class:`SqlPlatformSecretStore`. The
store layer itself is transparent: it does not import bypass; the control-plane
caller applies it.

One price per ``(provider, model)`` — ``resolve`` is a plain lookup (no plan
tier, no temporal window). Repricing edits the row in place.
"""

from __future__ import annotations

import abc
import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from helix_agent.persistence.models import ModelRateCardRow
from helix_agent.protocol import (
    ModelRateCardPatch,
    ModelRateCardRecord,
    ModelRateCardUpsert,
)


class ModelRateCardConflictError(Exception):
    """A platform pricing row already exists for ``(provider, model)``.

    Surfaced as a 409 by the control-plane POST handler.
    """

    def __init__(self, *, provider: str, model: str) -> None:
        super().__init__(f"model_rate_card already exists: provider={provider!r} model={model!r}")
        self.provider = provider
        self.model = model


class ModelRateCardNotFoundError(Exception):
    """No ``model_rate_card`` row for the requested id."""

    def __init__(self, *, rate_card_id: UUID) -> None:
        super().__init__(f"model_rate_card not found: id={rate_card_id}")
        self.rate_card_id = rate_card_id


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _resolve(
    rows: list[ModelRateCardRecord],
    *,
    provider: str,
    model: str,
) -> ModelRateCardRecord | None:
    """Return the single price row for ``(provider, model)``, or ``None``."""
    return next((r for r in rows if r.provider == provider and r.model == model), None)


class ModelRateCardStore(abc.ABC):
    """CRUD + resolution for the platform-curated model pricing table.

    Platform table: every row is NULL-tenant, so SQL callers MUST drive these
    methods inside ``bypass_rls_session()``.
    """

    @abc.abstractmethod
    async def create(self, *, upsert: ModelRateCardUpsert, actor_id: str) -> ModelRateCardRecord:
        """Insert a new platform (NULL-tenant) pricing row. Raises
        :class:`ModelRateCardConflictError` on a duplicate ``(provider, model)``."""

    @abc.abstractmethod
    async def get(self, rate_card_id: UUID) -> ModelRateCardRecord | None:
        """Return the row, or None if absent."""

    @abc.abstractmethod
    async def list(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> list[ModelRateCardRecord]:
        """Return rows ordered by ``(provider, model)``."""

    @abc.abstractmethod
    async def patch(self, *, rate_card_id: UUID, patch: ModelRateCardPatch) -> ModelRateCardRecord:
        """Apply a partial update. Raises
        :class:`ModelRateCardNotFoundError` if absent."""

    @abc.abstractmethod
    async def delete(self, rate_card_id: UUID) -> None:
        """Delete the row. Raises
        :class:`ModelRateCardNotFoundError` if absent."""

    @abc.abstractmethod
    async def resolve(self, *, provider: str, model: str) -> ModelRateCardRecord | None:
        """Resolve the current price for ``(provider, model)``, or ``None``."""


class InMemoryModelRateCardStore(ModelRateCardStore):
    """Dict-backed rate-card store keyed by ``id``; lock-guarded."""

    def __init__(self) -> None:
        self._rows: dict[UUID, ModelRateCardRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self, *, upsert: ModelRateCardUpsert, actor_id: str) -> ModelRateCardRecord:
        async with self._lock:
            if any(
                r.provider == upsert.provider and r.model == upsert.model
                for r in self._rows.values()
            ):
                raise ModelRateCardConflictError(provider=upsert.provider, model=upsert.model)
            now = _utc_now()
            record = ModelRateCardRecord(
                id=uuid4(),
                tenant_id=None,
                provider=upsert.provider,
                model=upsert.model,
                input_per_mtok_micros=upsert.input_per_mtok_micros,
                output_per_mtok_micros=upsert.output_per_mtok_micros,
                cache_creation_per_mtok_micros=upsert.cache_creation_per_mtok_micros,
                cache_read_per_mtok_micros=upsert.cache_read_per_mtok_micros,
                created_at=now,
                updated_at=now,
            )
            self._rows[record.id] = record
            return record

    async def get(self, rate_card_id: UUID) -> ModelRateCardRecord | None:
        async with self._lock:
            return self._rows.get(rate_card_id)

    async def list(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> list[ModelRateCardRecord]:
        async with self._lock:
            rows = [
                r
                for r in self._rows.values()
                if (provider is None or r.provider == provider)
                and (model is None or r.model == model)
            ]
        rows.sort(key=lambda r: (r.provider, r.model))
        return rows

    async def patch(self, *, rate_card_id: UUID, patch: ModelRateCardPatch) -> ModelRateCardRecord:
        async with self._lock:
            existing = self._rows.get(rate_card_id)
            if existing is None:
                raise ModelRateCardNotFoundError(rate_card_id=rate_card_id)
            # patch field == None means "leave unchanged"; provider/model are
            # immutable identity (reprice edits prices in place).
            changes: dict[str, object] = {"updated_at": _utc_now()}
            if patch.input_per_mtok_micros is not None:
                changes["input_per_mtok_micros"] = patch.input_per_mtok_micros
            if patch.output_per_mtok_micros is not None:
                changes["output_per_mtok_micros"] = patch.output_per_mtok_micros
            if patch.cache_creation_per_mtok_micros is not None:
                changes["cache_creation_per_mtok_micros"] = patch.cache_creation_per_mtok_micros
            if patch.cache_read_per_mtok_micros is not None:
                changes["cache_read_per_mtok_micros"] = patch.cache_read_per_mtok_micros
            # Re-validate the merged row (model_copy doesn't run validators) so a
            # patch can't slip a cross-field-invalid record (parity with SQL).
            updated = ModelRateCardRecord.model_validate(
                existing.model_copy(update=changes).model_dump()
            )
            self._rows[rate_card_id] = updated
            return updated

    async def delete(self, rate_card_id: UUID) -> None:
        async with self._lock:
            if rate_card_id not in self._rows:
                raise ModelRateCardNotFoundError(rate_card_id=rate_card_id)
            del self._rows[rate_card_id]

    async def resolve(self, *, provider: str, model: str) -> ModelRateCardRecord | None:
        async with self._lock:
            rows = list(self._rows.values())
        return _resolve(rows, provider=provider, model=model)


def _row_to_record(row: ModelRateCardRow) -> ModelRateCardRecord:
    return ModelRateCardRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        provider=row.provider,
        model=row.model,
        input_per_mtok_micros=row.input_per_mtok_micros,
        output_per_mtok_micros=row.output_per_mtok_micros,
        cache_creation_per_mtok_micros=row.cache_creation_per_mtok_micros,
        cache_read_per_mtok_micros=row.cache_read_per_mtok_micros,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class DbModelRateCardStore(ModelRateCardStore):
    """Postgres-backed platform model pricing table (bypass-RLS sessions)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create(self, *, upsert: ModelRateCardUpsert, actor_id: str) -> ModelRateCardRecord:
        now = _utc_now()
        stmt = (
            pg_insert(ModelRateCardRow)
            .values(
                tenant_id=None,
                provider=upsert.provider,
                model=upsert.model,
                input_per_mtok_micros=upsert.input_per_mtok_micros,
                output_per_mtok_micros=upsert.output_per_mtok_micros,
                cache_creation_per_mtok_micros=upsert.cache_creation_per_mtok_micros,
                cache_read_per_mtok_micros=upsert.cache_read_per_mtok_micros,
                created_at=now,
                updated_at=now,
            )
            .returning(ModelRateCardRow)
        )
        async with self._sf() as session:
            try:
                row = (await session.execute(stmt)).scalar_one()
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ModelRateCardConflictError(
                    provider=upsert.provider, model=upsert.model
                ) from exc
            await session.refresh(row)
            return _row_to_record(row)

    async def get(self, rate_card_id: UUID) -> ModelRateCardRecord | None:
        async with self._sf() as session:
            row = await session.get(ModelRateCardRow, rate_card_id)
        return _row_to_record(row) if row is not None else None

    async def list(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> list[ModelRateCardRecord]:
        stmt = select(ModelRateCardRow).order_by(
            ModelRateCardRow.provider,
            ModelRateCardRow.model,
        )
        if provider is not None:
            stmt = stmt.where(ModelRateCardRow.provider == provider)
        if model is not None:
            stmt = stmt.where(ModelRateCardRow.model == model)
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_record(r) for r in rows]

    async def patch(self, *, rate_card_id: UUID, patch: ModelRateCardPatch) -> ModelRateCardRecord:
        async with self._sf() as session:
            existing = await session.get(ModelRateCardRow, rate_card_id)
            if existing is None:
                raise ModelRateCardNotFoundError(rate_card_id=rate_card_id)
            if patch.input_per_mtok_micros is not None:
                existing.input_per_mtok_micros = patch.input_per_mtok_micros
            if patch.output_per_mtok_micros is not None:
                existing.output_per_mtok_micros = patch.output_per_mtok_micros
            if patch.cache_creation_per_mtok_micros is not None:
                existing.cache_creation_per_mtok_micros = patch.cache_creation_per_mtok_micros
            if patch.cache_read_per_mtok_micros is not None:
                existing.cache_read_per_mtok_micros = patch.cache_read_per_mtok_micros
            existing.updated_at = _utc_now()
            # Validate the prospective record BEFORE commit: if the merged row
            # violates an invariant, _row_to_record raises and the context
            # manager rolls back — no corrupt row is persisted.
            record = _row_to_record(existing)
            await session.commit()
            return record

    async def delete(self, rate_card_id: UUID) -> None:
        stmt = (
            sa_delete(ModelRateCardRow)
            .where(ModelRateCardRow.id == rate_card_id)
            .returning(ModelRateCardRow.id)
        )
        async with self._sf() as session:
            deleted = (await session.execute(stmt)).scalar_one_or_none()
            if deleted is None:
                await session.rollback()
                raise ModelRateCardNotFoundError(rate_card_id=rate_card_id)
            await session.commit()

    async def resolve(self, *, provider: str, model: str) -> ModelRateCardRecord | None:
        stmt = select(ModelRateCardRow).where(
            ModelRateCardRow.provider == provider,
            ModelRateCardRow.model == model,
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        records = [_row_to_record(r) for r in rows]
        return _resolve(records, provider=provider, model=model)
