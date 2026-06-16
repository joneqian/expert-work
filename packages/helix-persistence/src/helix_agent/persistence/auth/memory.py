"""In-memory implementations of the Stream C.3 auth stores."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from helix_agent.persistence.auth.base import (
    ApiKeyStore,
    DuplicateApiKeyPrefixError,
    DuplicateRoleBindingError,
    DuplicateServiceAccountError,
    RoleBindingStore,
    ServiceAccountStore,
)
from helix_agent.protocol import (
    ApiKey,
    ApiKeyScope,
    BindingConditions,
    Role,
    RoleBinding,
    ServiceAccount,
)


def _now() -> datetime:
    return datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# ServiceAccount
# ---------------------------------------------------------------------------


class InMemoryServiceAccountStore(ServiceAccountStore):
    def __init__(self) -> None:
        self._rows: dict[UUID, ServiceAccount] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        *,
        tenant_id: UUID,
        name: str,
        description: str,
        created_by: str,
    ) -> ServiceAccount:
        async with self._lock:
            for row in self._rows.values():
                if row.tenant_id == tenant_id and row.name == name:
                    raise DuplicateServiceAccountError(tenant_id=tenant_id, name=name)
            account = ServiceAccount(
                id=uuid4(),
                tenant_id=tenant_id,
                name=name,
                description=description,
                created_at=_now(),
                created_by=created_by,
            )
            self._rows[account.id] = account
            return account

    async def get(self, *, tenant_id: UUID, service_account_id: UUID) -> ServiceAccount | None:
        async with self._lock:
            row = self._rows.get(service_account_id)
            if row is None or row.tenant_id != tenant_id:
                return None
            return row

    async def list_by_tenant(
        self,
        *,
        tenant_id: UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ServiceAccount]:
        async with self._lock:
            ordered = sorted(
                (r for r in self._rows.values() if r.tenant_id == tenant_id),
                key=lambda r: r.created_at,
                reverse=True,
            )
            return ordered[offset : offset + limit]

    async def list_all_tenants(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ServiceAccount]:
        async with self._lock:
            ordered = sorted(self._rows.values(), key=lambda r: r.created_at, reverse=True)
            return ordered[offset : offset + limit]

    async def delete(self, *, tenant_id: UUID, service_account_id: UUID) -> bool:
        async with self._lock:
            row = self._rows.get(service_account_id)
            if row is None or row.tenant_id != tenant_id:
                return False
            del self._rows[service_account_id]
            return True


# ---------------------------------------------------------------------------
# ApiKey
# ---------------------------------------------------------------------------


class InMemoryApiKeyStore(ApiKeyStore):
    def __init__(self) -> None:
        self._rows: dict[UUID, ApiKey] = {}
        self._by_prefix: dict[str, UUID] = {}
        self._lock = asyncio.Lock()

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
        async with self._lock:
            if prefix in self._by_prefix:
                raise DuplicateApiKeyPrefixError(prefix=prefix)
            key = ApiKey(
                id=uuid4(),
                service_account_id=service_account_id,
                tenant_id=tenant_id,
                prefix=prefix,
                secret_hash=secret_hash,
                scopes=tuple(scopes),
                expires_at=expires_at,
                created_at=_now(),
                created_by=created_by,
            )
            self._rows[key.id] = key
            self._by_prefix[prefix] = key.id
            return key

    async def get_by_prefix(self, *, prefix: str) -> ApiKey | None:
        async with self._lock:
            key_id = self._by_prefix.get(prefix)
            if key_id is None:
                return None
            return self._rows.get(key_id)

    async def list_by_service_account(
        self,
        *,
        tenant_id: UUID,
        service_account_id: UUID,
    ) -> list[ApiKey]:
        async with self._lock:
            return sorted(
                (
                    row
                    for row in self._rows.values()
                    if row.tenant_id == tenant_id and row.service_account_id == service_account_id
                ),
                key=lambda r: r.created_at,
                reverse=True,
            )

    async def list_by_tenant(
        self,
        *,
        tenant_id: UUID,
        service_account_id: UUID | None = None,
    ) -> list[ApiKey]:
        async with self._lock:
            return sorted(
                (
                    row
                    for row in self._rows.values()
                    if row.tenant_id == tenant_id
                    and (service_account_id is None or row.service_account_id == service_account_id)
                ),
                key=lambda r: r.created_at,
                reverse=True,
            )

    async def list_all_tenants(
        self,
        *,
        service_account_id: UUID | None = None,
    ) -> list[ApiKey]:
        async with self._lock:
            return sorted(
                (
                    row
                    for row in self._rows.values()
                    if service_account_id is None or row.service_account_id == service_account_id
                ),
                key=lambda r: r.created_at,
                reverse=True,
            )

    async def revoke(self, *, tenant_id: UUID, api_key_id: UUID) -> bool:
        async with self._lock:
            row = self._rows.get(api_key_id)
            if row is None or row.tenant_id != tenant_id:
                return False
            if row.revoked_at is not None:
                return True  # idempotent
            self._rows[api_key_id] = row.model_copy(update={"revoked_at": _now()})
            return True

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
        async with self._lock:
            old = self._rows.get(api_key_id)
            if old is None or old.tenant_id != tenant_id:
                return None
            if old.revoked_at is not None or old.rotated_at is not None:
                return None
            if new_prefix in self._by_prefix:
                raise DuplicateApiKeyPrefixError(prefix=new_prefix)

            rotated_old = old.model_copy(
                update={"rotated_at": rotated_at, "grace_period_s": grace_period_s}
            )
            new = ApiKey(
                id=uuid4(),
                service_account_id=old.service_account_id,
                tenant_id=tenant_id,
                prefix=new_prefix,
                secret_hash=new_secret_hash,
                scopes=old.scopes,
                expires_at=old.expires_at,
                created_at=_now(),
                created_by=actor_id,
            )
            self._rows[api_key_id] = rotated_old
            self._rows[new.id] = new
            self._by_prefix[new_prefix] = new.id
            return rotated_old, new

    async def touch_last_used(self, *, api_key_id: UUID, when: datetime) -> None:
        async with self._lock:
            row = self._rows.get(api_key_id)
            if row is not None:
                self._rows[api_key_id] = row.model_copy(update={"last_used_at": when})


# ---------------------------------------------------------------------------
# RoleBinding
# ---------------------------------------------------------------------------


class InMemoryRoleBindingStore(RoleBindingStore):
    def __init__(self) -> None:
        self._rows: dict[UUID, RoleBinding] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        *,
        subject_type: str,
        subject_id: UUID,
        tenant_id: UUID | None,
        role: Role,
        granted_by: str,
        platform_scope: bool = False,
        conditions: BindingConditions | None = None,
    ) -> RoleBinding:
        async with self._lock:
            for row in self._rows.values():
                # Tenant-scope duplicate:same (subject, tenant, role).
                if (
                    not platform_scope
                    and not row.platform_scope
                    and row.subject_type == subject_type
                    and row.subject_id == subject_id
                    and row.tenant_id == tenant_id
                    and row.role == role
                ):
                    raise DuplicateRoleBindingError(
                        subject_type=subject_type,
                        subject_id=subject_id,
                        tenant_id=tenant_id,
                        role=role,
                    )
                # Platform-scope duplicate:one platform binding per subject.
                if (
                    platform_scope
                    and row.platform_scope
                    and row.subject_type == subject_type
                    and row.subject_id == subject_id
                ):
                    raise DuplicateRoleBindingError(
                        subject_type=subject_type,
                        subject_id=subject_id,
                        tenant_id=None,
                        role=role,
                    )
            # The DTO's model_validator enforces the (platform_scope,
            # tenant_id, role) triple; mismatched calls raise ValueError here.
            binding = RoleBinding(
                id=uuid4(),
                subject_type=subject_type,
                subject_id=subject_id,
                tenant_id=tenant_id,
                role=role,
                platform_scope=platform_scope,
                conditions=conditions,
                granted_by=granted_by,
                granted_at=_now(),
            )
            self._rows[binding.id] = binding
            return binding

    async def list_for_subject(
        self,
        *,
        subject_type: str,
        subject_id: UUID,
        tenant_id: UUID | None = None,
    ) -> list[RoleBinding]:
        async with self._lock:
            return [
                row
                for row in self._rows.values()
                if row.subject_type == subject_type
                and row.subject_id == subject_id
                and (tenant_id is None or row.tenant_id == tenant_id)
            ]

    async def list_for_tenant(self, *, tenant_id: UUID) -> list[RoleBinding]:
        # Excludes platform-scope rows (they have ``tenant_id IS NULL``).
        async with self._lock:
            return [
                row
                for row in self._rows.values()
                if not row.platform_scope and row.tenant_id == tenant_id
            ]

    async def list_all_tenants(self) -> list[RoleBinding]:
        async with self._lock:
            return list(self._rows.values())

    async def list_platform_scope(self) -> list[RoleBinding]:
        async with self._lock:
            return [row for row in self._rows.values() if row.platform_scope]

    async def get_platform_admin_for_subject(
        self,
        *,
        subject_type: str,
        subject_id: UUID,
    ) -> RoleBinding | None:
        async with self._lock:
            for row in self._rows.values():
                if (
                    row.platform_scope
                    and row.subject_type == subject_type
                    and row.subject_id == subject_id
                ):
                    return row
            return None

    async def delete(
        self,
        *,
        tenant_id: UUID | None,
        role_binding_id: UUID,
    ) -> bool:
        async with self._lock:
            row = self._rows.get(role_binding_id)
            if row is None:
                return False
            if tenant_id is None:
                # Targeting a platform-scope binding.
                if not row.platform_scope:
                    return False
            else:
                # Targeting a tenant-scope binding.
                if row.platform_scope or row.tenant_id != tenant_id:
                    return False
            del self._rows[role_binding_id]
            return True
