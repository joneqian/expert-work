"""SQLAlchemy-backed Stream C.3 auth stores."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from helix_agent.persistence.auth.base import (
    ApiKeyStore,
    DuplicateApiKeyPrefixError,
    DuplicateRoleBindingError,
    DuplicateServiceAccountError,
    RoleBindingStore,
    ServiceAccountStore,
)
from helix_agent.persistence.models import ApiKeyRow, RoleBindingRow, ServiceAccountRow
from helix_agent.protocol import ApiKey, ApiKeyScope, Role, RoleBinding, ServiceAccount

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _row_to_service_account(row: ServiceAccountRow) -> ServiceAccount:
    return ServiceAccount(
        id=row.id,
        tenant_id=row.tenant_id,
        name=row.name,
        description=row.description,
        is_active=row.is_active,
        created_at=row.created_at,
        created_by=row.created_by,
    )


def _row_to_api_key(row: ApiKeyRow) -> ApiKey:
    return ApiKey(
        id=row.id,
        service_account_id=row.service_account_id,
        tenant_id=row.tenant_id,
        prefix=row.prefix,
        secret_hash=row.secret_hash,
        scopes=tuple(ApiKeyScope(s) for s in row.scopes),
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
        rotated_at=row.rotated_at,
        grace_period_s=row.grace_period_s,
        created_at=row.created_at,
        created_by=row.created_by,
    )


def _row_to_role_binding(row: RoleBindingRow) -> RoleBinding:
    return RoleBinding(
        id=row.id,
        subject_type=row.subject_type,
        subject_id=row.subject_id,
        tenant_id=row.tenant_id,
        role=Role(row.role),
        platform_scope=row.platform_scope,
        granted_by=row.granted_by,
        granted_at=row.granted_at,
    )


# ---------------------------------------------------------------------------
# ServiceAccount
# ---------------------------------------------------------------------------


class SqlServiceAccountStore(ServiceAccountStore):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create(
        self,
        *,
        tenant_id: UUID,
        name: str,
        description: str,
        created_by: str,
    ) -> ServiceAccount:
        row = ServiceAccountRow(
            tenant_id=tenant_id,
            name=name,
            description=description,
            created_by=created_by,
            is_active=True,
        )
        async with self._sf() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                raise DuplicateServiceAccountError(tenant_id=tenant_id, name=name) from exc
            await session.refresh(row)
            return _row_to_service_account(row)

    async def get(self, *, tenant_id: UUID, service_account_id: UUID) -> ServiceAccount | None:
        stmt = select(ServiceAccountRow).where(
            ServiceAccountRow.id == service_account_id,
            ServiceAccountRow.tenant_id == tenant_id,
        )
        async with self._sf() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
        return _row_to_service_account(row) if row else None

    async def list_by_tenant(
        self,
        *,
        tenant_id: UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ServiceAccount]:
        stmt = (
            select(ServiceAccountRow)
            .where(ServiceAccountRow.tenant_id == tenant_id)
            .order_by(ServiceAccountRow.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_service_account(r) for r in rows]

    async def list_all_tenants(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ServiceAccount]:
        # Stream N — no tenant filter; caller must wrap in bypass_rls_session().
        stmt = (
            select(ServiceAccountRow)
            .order_by(ServiceAccountRow.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_service_account(r) for r in rows]

    async def delete(self, *, tenant_id: UUID, service_account_id: UUID) -> bool:
        stmt = delete(ServiceAccountRow).where(
            ServiceAccountRow.id == service_account_id,
            ServiceAccountRow.tenant_id == tenant_id,
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            await session.commit()
        rowcount = getattr(result, "rowcount", 0) or 0
        return int(rowcount) > 0


# ---------------------------------------------------------------------------
# ApiKey
# ---------------------------------------------------------------------------


class SqlApiKeyStore(ApiKeyStore):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create(
        self,
        *,
        tenant_id: UUID,
        service_account_id: UUID,
        prefix: str,
        secret_hash: str,
        scopes: Sequence[ApiKeyScope],
        expires_at: datetime | None,
        created_by: str,
    ) -> ApiKey:
        row = ApiKeyRow(
            service_account_id=service_account_id,
            tenant_id=tenant_id,
            prefix=prefix,
            secret_hash=secret_hash,
            scopes=[s.value for s in scopes],
            expires_at=expires_at,
            created_by=created_by,
        )
        async with self._sf() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                raise DuplicateApiKeyPrefixError(prefix=prefix) from exc
            await session.refresh(row)
            return _row_to_api_key(row)

    async def get_by_prefix(self, *, prefix: str) -> ApiKey | None:
        stmt = select(ApiKeyRow).where(ApiKeyRow.prefix == prefix)
        async with self._sf() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
        return _row_to_api_key(row) if row else None

    async def list_by_service_account(
        self,
        *,
        tenant_id: UUID,
        service_account_id: UUID,
    ) -> list[ApiKey]:
        stmt = (
            select(ApiKeyRow)
            .where(
                ApiKeyRow.tenant_id == tenant_id,
                ApiKeyRow.service_account_id == service_account_id,
            )
            .order_by(ApiKeyRow.created_at.desc())
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_api_key(r) for r in rows]

    async def list_by_tenant(
        self,
        *,
        tenant_id: UUID,
        service_account_id: UUID | None = None,
    ) -> list[ApiKey]:
        stmt = (
            select(ApiKeyRow)
            .where(ApiKeyRow.tenant_id == tenant_id)
            .order_by(ApiKeyRow.created_at.desc())
        )
        if service_account_id is not None:
            stmt = stmt.where(ApiKeyRow.service_account_id == service_account_id)
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_api_key(r) for r in rows]

    async def list_all_tenants(
        self,
        *,
        service_account_id: UUID | None = None,
    ) -> list[ApiKey]:
        # Stream N — no tenant filter; caller must wrap in bypass_rls_session().
        stmt = select(ApiKeyRow).order_by(ApiKeyRow.created_at.desc())
        if service_account_id is not None:
            stmt = stmt.where(ApiKeyRow.service_account_id == service_account_id)
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_api_key(r) for r in rows]

    async def revoke(self, *, tenant_id: UUID, api_key_id: UUID) -> bool:
        now = datetime.now(UTC)
        stmt = (
            update(ApiKeyRow)
            .where(ApiKeyRow.id == api_key_id, ApiKeyRow.tenant_id == tenant_id)
            .values(revoked_at=now)
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            await session.commit()
        rowcount = getattr(result, "rowcount", 0) or 0
        return int(rowcount) > 0

    async def rotate(
        self,
        *,
        tenant_id: UUID,
        api_key_id: UUID,
        new_prefix: str,
        new_secret_hash: str,
        grace_period_s: int,
        rotated_at: datetime,
        actor_id: str,
    ) -> tuple[ApiKey, ApiKey] | None:
        async with self._sf() as session:
            old_row = (
                await session.execute(
                    select(ApiKeyRow).where(
                        ApiKeyRow.id == api_key_id,
                        ApiKeyRow.tenant_id == tenant_id,
                    )
                )
            ).scalar_one_or_none()
            if old_row is None:
                return None
            # Refuse to rotate an already-revoked or already-rotated key
            # — operators must pick one explicit action at a time so the
            # audit trail stays unambiguous.
            if old_row.revoked_at is not None or old_row.rotated_at is not None:
                return None

            old_row.rotated_at = rotated_at
            old_row.grace_period_s = grace_period_s

            new_row = ApiKeyRow(
                service_account_id=old_row.service_account_id,
                tenant_id=tenant_id,
                prefix=new_prefix,
                secret_hash=new_secret_hash,
                scopes=list(old_row.scopes),
                expires_at=old_row.expires_at,
                created_by=actor_id,
            )
            session.add(new_row)
            try:
                await session.commit()
            except IntegrityError as exc:
                raise DuplicateApiKeyPrefixError(prefix=new_prefix) from exc
            await session.refresh(old_row)
            await session.refresh(new_row)
            return _row_to_api_key(old_row), _row_to_api_key(new_row)

    async def touch_last_used(self, *, api_key_id: UUID, when: datetime) -> None:
        stmt = update(ApiKeyRow).where(ApiKeyRow.id == api_key_id).values(last_used_at=when)
        async with self._sf() as session:
            await session.execute(stmt)
            await session.commit()


# ---------------------------------------------------------------------------
# RoleBinding
# ---------------------------------------------------------------------------


class SqlRoleBindingStore(RoleBindingStore):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create(
        self,
        *,
        subject_type: str,
        subject_id: UUID,
        tenant_id: UUID | None,
        role: Role,
        granted_by: str,
        platform_scope: bool = False,
    ) -> RoleBinding:
        row = RoleBindingRow(
            subject_type=subject_type,
            subject_id=subject_id,
            tenant_id=tenant_id,
            role=role.value,
            platform_scope=platform_scope,
            granted_by=granted_by,
        )
        async with self._sf() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                raise DuplicateRoleBindingError(
                    subject_type=subject_type,
                    subject_id=subject_id,
                    tenant_id=tenant_id,
                    role=role,
                ) from exc
            await session.refresh(row)
            return _row_to_role_binding(row)

    async def list_for_subject(
        self,
        *,
        subject_type: str,
        subject_id: UUID,
        tenant_id: UUID | None = None,
    ) -> list[RoleBinding]:
        stmt = select(RoleBindingRow).where(
            RoleBindingRow.subject_type == subject_type,
            RoleBindingRow.subject_id == subject_id,
        )
        if tenant_id is not None:
            stmt = stmt.where(RoleBindingRow.tenant_id == tenant_id)
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_role_binding(r) for r in rows]

    async def list_for_tenant(self, *, tenant_id: UUID) -> list[RoleBinding]:
        # Excludes platform-scope rows (their tenant_id is NULL anyway,
        # but be explicit for readability).
        stmt = select(RoleBindingRow).where(
            RoleBindingRow.tenant_id == tenant_id,
            RoleBindingRow.platform_scope.is_(False),
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_role_binding(r) for r in rows]

    async def list_all_tenants(self) -> list[RoleBinding]:
        # Stream N — every row, both scopes; caller must wrap in
        # bypass_rls_session(). Used by the platform admin view.
        stmt = select(RoleBindingRow).order_by(RoleBindingRow.granted_at.desc())
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_role_binding(r) for r in rows]

    async def list_platform_scope(self) -> list[RoleBinding]:
        stmt = select(RoleBindingRow).where(RoleBindingRow.platform_scope.is_(True))
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_role_binding(r) for r in rows]

    async def get_platform_admin_for_subject(
        self,
        *,
        subject_type: str,
        subject_id: UUID,
    ) -> RoleBinding | None:
        stmt = select(RoleBindingRow).where(
            RoleBindingRow.platform_scope.is_(True),
            RoleBindingRow.subject_type == subject_type,
            RoleBindingRow.subject_id == subject_id,
        )
        async with self._sf() as session:
            row = (await session.execute(stmt)).scalars().first()
        return _row_to_role_binding(row) if row else None

    async def delete(
        self,
        *,
        tenant_id: UUID | None,
        role_binding_id: UUID,
    ) -> bool:
        stmt = delete(RoleBindingRow).where(RoleBindingRow.id == role_binding_id)
        if tenant_id is None:
            stmt = stmt.where(RoleBindingRow.platform_scope.is_(True))
        else:
            stmt = stmt.where(
                RoleBindingRow.tenant_id == tenant_id,
                RoleBindingRow.platform_scope.is_(False),
            )
        async with self._sf() as session:
            result = await session.execute(stmt)
            await session.commit()
        rowcount = getattr(result, "rowcount", 0) or 0
        return int(rowcount) > 0
