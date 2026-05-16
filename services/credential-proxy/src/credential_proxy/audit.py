"""The ``credential_proxy_audit`` write path.

A :class:`ProxyAuditStore` Protocol keeps the proxy logic testable with
a recording fake; :class:`DbProxyAuditStore` is the SQL impl. Audit
rows record the ref + host + status — **never** the secret value.
"""

from __future__ import annotations

import logging
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from credential_proxy.domain import ProxyAuditEntry
from helix_agent.persistence.models import CredentialProxyAuditRow

logger = logging.getLogger(__name__)


class ProxyAuditStore(Protocol):
    """Sink for ``credential_proxy_audit`` rows."""

    async def record(self, entry: ProxyAuditEntry) -> None:
        """Persist one injection-attempt audit row."""


class DbProxyAuditStore:
    """SQL-backed :class:`ProxyAuditStore` over ``credential_proxy_audit``.

    Best-effort: a write failure is logged, never raised — an audit-side
    fault must not fail the caller's forwarded request.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def record(self, entry: ProxyAuditEntry) -> None:
        try:
            async with self._sf() as session:
                session.add(
                    CredentialProxyAuditRow(
                        tenant_id=entry.tenant_id,
                        agent_name=entry.agent_name,
                        agent_version=entry.agent_version,
                        session_id=entry.session_id,
                        sandbox_id=entry.sandbox_id,
                        secret_ref=entry.secret_ref,
                        target_host=entry.target_host,
                        inject_kind=entry.inject_kind,
                        status=entry.status,
                        error_msg=entry.error_msg,
                        duration_ms=entry.duration_ms,
                    )
                )
                await session.commit()
        except Exception:
            logger.exception(
                "credential_proxy_audit.write_failed tenant=%s status=%s",
                entry.tenant_id,
                entry.status,
            )
