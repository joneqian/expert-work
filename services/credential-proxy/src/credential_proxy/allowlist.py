"""The ``secret_allowlist`` — which secret refs a manifest may inject.

An :class:`AllowlistStore` Protocol keeps the proxy logic unit-testable
with an in-memory fake; :class:`DbAllowlistStore` is the SQL impl.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from credential_proxy.domain import AllowlistKey
from expert_work.persistence.models import SecretAllowlistRow


class AllowlistStore(Protocol):
    """The ``secret_allowlist`` operations the proxy + admin API need."""

    async def is_allowed(self, key: AllowlistKey) -> bool:
        """Whether ``(tenant, agent, version, secret_ref)`` is registered."""

    async def add(self, key: AllowlistKey) -> None:
        """Register a four-tuple — idempotent (re-register updates purpose)."""

    async def remove_agent_version(
        self, tenant_id: UUID, agent_name: str, agent_version: str
    ) -> int:
        """Revoke every ref of one agent version; return the row count."""


class DbAllowlistStore:
    """SQL-backed :class:`AllowlistStore` over the ``secret_allowlist`` table."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def is_allowed(self, key: AllowlistKey) -> bool:
        async with self._sf() as session:
            result = await session.execute(
                select(SecretAllowlistRow.secret_ref).where(
                    SecretAllowlistRow.tenant_id == key.tenant_id,
                    SecretAllowlistRow.agent_name == key.agent_name,
                    SecretAllowlistRow.agent_version == key.agent_version,
                    SecretAllowlistRow.secret_ref == key.secret_ref,
                )
            )
            return result.first() is not None

    async def add(self, key: AllowlistKey) -> None:
        async with self._sf() as session:
            await session.execute(
                insert(SecretAllowlistRow)
                .values(
                    tenant_id=key.tenant_id,
                    agent_name=key.agent_name,
                    agent_version=key.agent_version,
                    secret_ref=key.secret_ref,
                    purpose=key.purpose,
                )
                .on_conflict_do_update(
                    constraint="secret_allowlist_pkey",
                    set_={"purpose": key.purpose},
                )
            )
            await session.commit()

    async def remove_agent_version(
        self, tenant_id: UUID, agent_name: str, agent_version: str
    ) -> int:
        async with self._sf() as session:
            result = await session.execute(
                delete(SecretAllowlistRow)
                .where(
                    SecretAllowlistRow.tenant_id == tenant_id,
                    SecretAllowlistRow.agent_name == agent_name,
                    SecretAllowlistRow.agent_version == agent_version,
                )
                .returning(SecretAllowlistRow.secret_ref)
            )
            removed = len(result.fetchall())
            await session.commit()
            return removed
