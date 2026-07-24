"""Postgres-backed :class:`McpOAuthConnectionStore` — Stream MCP-OAUTH (OA-1b)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from expert_work.persistence.mcp_oauth_connection.base import (
    McpOAuthConnectionAlreadyExistsError,
    McpOAuthConnectionNotFoundError,
    McpOAuthConnectionStore,
)
from expert_work.persistence.models import McpOAuthConnectionRow
from expert_work.protocol import McpOAuthConnectionPatch, McpOAuthConnectionRecord


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _row_to_record(row: McpOAuthConnectionRow) -> McpOAuthConnectionRecord:
    return McpOAuthConnectionRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        catalog_id=row.catalog_id,
        name=row.name,
        status=row.status,  # type: ignore[arg-type]
        resolved_url=row.resolved_url,
        scopes=row.scopes,
        redirect_uri=row.redirect_uri,
        access_token_ref=row.access_token_ref,
        refresh_token_ref=row.refresh_token_ref,
        token_expires_at=row.token_expires_at,
        oauth_state=row.oauth_state,
        pkce_verifier=row.pkce_verifier,
        last_refresh_at=row.last_refresh_at,
        last_error=row.last_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlMcpOAuthConnectionStore(McpOAuthConnectionStore):
    """Postgres-backed per-user OAuth connection store (RLS-scoped sessions)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create(
        self,
        *,
        tenant_id: UUID,
        user_id: str,
        catalog_id: UUID,
        name: str,
        resolved_url: str,
        scopes: str = "",
        oauth_state: str | None = None,
        pkce_verifier: str | None = None,
        redirect_uri: str | None = None,
    ) -> McpOAuthConnectionRecord:
        now = _utc_now()
        stmt = (
            pg_insert(McpOAuthConnectionRow)
            .values(
                tenant_id=tenant_id,
                user_id=user_id,
                catalog_id=catalog_id,
                name=name,
                status="pending",
                resolved_url=resolved_url,
                scopes=scopes,
                oauth_state=oauth_state,
                pkce_verifier=pkce_verifier,
                redirect_uri=redirect_uri,
                created_at=now,
                updated_at=now,
            )
            .returning(McpOAuthConnectionRow)
        )
        async with self._sf() as session:
            try:
                row = (await session.execute(stmt)).scalar_one()
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise McpOAuthConnectionAlreadyExistsError(
                    tenant_id=tenant_id, user_id=user_id, catalog_id=catalog_id
                ) from exc
            await session.refresh(row)
            return _row_to_record(row)

    async def get(
        self, *, connection_id: UUID, tenant_id: UUID, user_id: str
    ) -> McpOAuthConnectionRecord | None:
        stmt = select(McpOAuthConnectionRow).where(
            McpOAuthConnectionRow.id == connection_id,
            McpOAuthConnectionRow.tenant_id == tenant_id,
            McpOAuthConnectionRow.user_id == user_id,
        )
        async with self._sf() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
        return _row_to_record(row) if row is not None else None

    async def get_for_connector(
        self, *, tenant_id: UUID, user_id: str, catalog_id: UUID
    ) -> McpOAuthConnectionRecord | None:
        stmt = select(McpOAuthConnectionRow).where(
            McpOAuthConnectionRow.tenant_id == tenant_id,
            McpOAuthConnectionRow.user_id == user_id,
            McpOAuthConnectionRow.catalog_id == catalog_id,
        )
        async with self._sf() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
        return _row_to_record(row) if row is not None else None

    async def get_by_state(
        self, *, tenant_id: UUID, user_id: str, oauth_state: str
    ) -> McpOAuthConnectionRecord | None:
        stmt = select(McpOAuthConnectionRow).where(
            McpOAuthConnectionRow.tenant_id == tenant_id,
            McpOAuthConnectionRow.user_id == user_id,
            McpOAuthConnectionRow.oauth_state == oauth_state,
        )
        async with self._sf() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
        return _row_to_record(row) if row is not None else None

    async def list_for_user(
        self, *, tenant_id: UUID, user_id: str
    ) -> list[McpOAuthConnectionRecord]:
        stmt = (
            select(McpOAuthConnectionRow)
            .where(
                McpOAuthConnectionRow.tenant_id == tenant_id,
                McpOAuthConnectionRow.user_id == user_id,
            )
            .order_by(McpOAuthConnectionRow.name)
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_record(r) for r in rows]

    async def update(
        self, *, connection_id: UUID, tenant_id: UUID, user_id: str, patch: McpOAuthConnectionPatch
    ) -> McpOAuthConnectionRecord:
        async with self._sf() as session:
            stmt = select(McpOAuthConnectionRow).where(
                McpOAuthConnectionRow.id == connection_id,
                McpOAuthConnectionRow.tenant_id == tenant_id,
                McpOAuthConnectionRow.user_id == user_id,
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if existing is None:
                raise McpOAuthConnectionNotFoundError(connection_id=connection_id)
            if patch.status is not None:
                existing.status = patch.status
            if patch.access_token_ref is not None:
                existing.access_token_ref = patch.access_token_ref
            if patch.refresh_token_ref is not None:
                existing.refresh_token_ref = patch.refresh_token_ref
            if patch.token_expires_at is not None:
                existing.token_expires_at = patch.token_expires_at
            if patch.scopes is not None:
                existing.scopes = patch.scopes
            if patch.last_refresh_at is not None:
                existing.last_refresh_at = patch.last_refresh_at
            if patch.last_error is not None:
                existing.last_error = patch.last_error
            if patch.clear_flow_state:
                existing.oauth_state = None
                existing.pkce_verifier = None
            if patch.clear_last_error:
                existing.last_error = None
            existing.updated_at = _utc_now()
            # Validate the prospective record before commit (parity with the
            # in-memory store): a violated invariant rolls back, no corrupt row.
            record = _row_to_record(existing)
            await session.commit()
            return record

    async def delete(self, *, connection_id: UUID, tenant_id: UUID, user_id: str) -> None:
        stmt = (
            sa_delete(McpOAuthConnectionRow)
            .where(
                McpOAuthConnectionRow.id == connection_id,
                McpOAuthConnectionRow.tenant_id == tenant_id,
                McpOAuthConnectionRow.user_id == user_id,
            )
            .returning(McpOAuthConnectionRow.id)
        )
        async with self._sf() as session:
            deleted = (await session.execute(stmt)).scalar_one_or_none()
            await session.commit()
        if deleted is None:
            raise McpOAuthConnectionNotFoundError(connection_id=connection_id)

    async def delete_all_for_user(self, *, tenant_id: UUID, user_id: str) -> int:
        stmt = sa_delete(McpOAuthConnectionRow).where(
            McpOAuthConnectionRow.tenant_id == tenant_id,
            McpOAuthConnectionRow.user_id == user_id,
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            await session.commit()
        return int(getattr(result, "rowcount", 0) or 0)

    async def count_for_catalog(self, *, catalog_id: UUID) -> int:
        # Platform-scope, cross-tenant by design — no tenant_id predicate.
        # No bypass-RLS session precedent on this store; the caller must run
        # on a platform-scope session (superuser/BYPASSRLS) or RLS filters
        # every row out. See base.py docstring.
        stmt = (
            select(func.count())
            .select_from(McpOAuthConnectionRow)
            .where(McpOAuthConnectionRow.catalog_id == catalog_id)
        )
        async with self._sf() as session:
            return int((await session.execute(stmt)).scalar_one())

    async def list_for_catalog(
        self, *, catalog_id: UUID, limit: int = 1000
    ) -> list[McpOAuthConnectionRecord]:
        # Platform-scope, cross-tenant by design — see count_for_catalog.
        stmt = (
            select(McpOAuthConnectionRow)
            .where(McpOAuthConnectionRow.catalog_id == catalog_id)
            .order_by(McpOAuthConnectionRow.created_at)
            .limit(limit)
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_record(r) for r in rows]

    async def delete_for_catalog(self, *, catalog_id: UUID) -> int:
        # Platform-scope, cross-tenant by design — see count_for_catalog.
        stmt = sa_delete(McpOAuthConnectionRow).where(
            McpOAuthConnectionRow.catalog_id == catalog_id
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            await session.commit()
        return int(getattr(result, "rowcount", 0) or 0)
