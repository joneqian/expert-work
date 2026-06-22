"""Read-side store for ``sandbox_egress_audit`` (sandbox-egress §3.1 Phase 3).

The credential-proxy writes egress-connection rows (``DbEgressAuditStore``);
this is the query side the control-plane's admin audit endpoint reads. Keyset
pagination on the ``id`` BigSerial (newest-first), matching the audit_log
pattern. The table has no RLS, so tenant isolation is enforced here by the
``tenant_id`` filter (``"*"`` = all tenants, gated upstream to system_admin).
"""

from __future__ import annotations

import abc
import base64
import binascii
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from helix_agent.persistence.models import SandboxEgressAuditRow

_CURSOR_PREFIX = "egr1:"


def encode_cursor(row_id: int) -> str:
    raw = f"{_CURSOR_PREFIX}{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> int:
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded).decode("ascii")
    except (binascii.Error, UnicodeDecodeError) as exc:
        msg = f"malformed cursor: {cursor!r}"
        raise ValueError(msg) from exc
    if not raw.startswith(_CURSOR_PREFIX):
        msg = f"unknown cursor format: {cursor!r}"
        raise ValueError(msg)
    try:
        return int(raw[len(_CURSOR_PREFIX) :])
    except ValueError as exc:
        msg = f"cursor payload is not an integer: {cursor!r}"
        raise ValueError(msg) from exc


@dataclass(frozen=True)
class EgressAuditRecord:
    """One ``sandbox_egress_audit`` row, as the read API returns it."""

    id: int
    tenant_id: UUID
    agent_name: str | None
    agent_version: str | None
    sandbox_id: str | None
    target_host: str
    target_port: int
    verdict: str
    bytes_up: int
    bytes_down: int
    duration_ms: int | None
    error_msg: str | None
    occurred_at: datetime


@dataclass(frozen=True)
class EgressAuditQuery:
    """Filters for an egress-audit page. ``tenant_id='*'`` = all tenants."""

    tenant_id: UUID | Literal["*"]
    agent_name: str | None = None
    verdict: str | None = None
    target_host: str | None = None
    limit: int = 100
    cursor: str | None = None


@dataclass(frozen=True)
class EgressAuditPage:
    entries: list[EgressAuditRecord] = field(default_factory=list)
    next_cursor: str | None = None


def _to_record(row: SandboxEgressAuditRow) -> EgressAuditRecord:
    return EgressAuditRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        agent_name=row.agent_name,
        agent_version=row.agent_version,
        sandbox_id=row.sandbox_id,
        target_host=row.target_host,
        target_port=row.target_port,
        verdict=row.verdict,
        bytes_up=row.bytes_up,
        bytes_down=row.bytes_down,
        duration_ms=row.duration_ms,
        error_msg=row.error_msg,
        occurred_at=row.occurred_at,
    )


class SandboxEgressAuditStore(abc.ABC):
    """Query side of ``sandbox_egress_audit`` (read-only, newest-first)."""

    @abc.abstractmethod
    async def query(self, q: EgressAuditQuery) -> EgressAuditPage:
        """Paginated query, newest-first. ``tenant_id='*'`` spans all tenants —
        the caller must have verified system_admin before that path."""


class SqlSandboxEgressAuditStore(SandboxEgressAuditStore):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def query(self, q: EgressAuditQuery) -> EgressAuditPage:
        stmt = select(SandboxEgressAuditRow).order_by(SandboxEgressAuditRow.id.desc())
        if q.tenant_id != "*":
            stmt = stmt.where(SandboxEgressAuditRow.tenant_id == q.tenant_id)
        if q.agent_name is not None:
            stmt = stmt.where(SandboxEgressAuditRow.agent_name == q.agent_name)
        if q.verdict is not None:
            stmt = stmt.where(SandboxEgressAuditRow.verdict == q.verdict)
        if q.target_host is not None:
            stmt = stmt.where(SandboxEgressAuditRow.target_host == q.target_host)
        if q.cursor is not None:
            stmt = stmt.where(SandboxEgressAuditRow.id < decode_cursor(q.cursor))
        stmt = stmt.limit(q.limit + 1)
        async with self._sf() as session:
            rows = list((await session.execute(stmt)).scalars().all())
        return _paginate(rows, q.limit)


class InMemorySandboxEgressAuditStore(SandboxEgressAuditStore):
    """Test double — an in-memory list of records (newest-first by id)."""

    def __init__(self) -> None:
        self.records: list[EgressAuditRecord] = []

    async def query(self, q: EgressAuditQuery) -> EgressAuditPage:
        rows = sorted(self.records, key=lambda r: r.id, reverse=True)
        if q.tenant_id != "*":
            rows = [r for r in rows if r.tenant_id == q.tenant_id]
        if q.agent_name is not None:
            rows = [r for r in rows if r.agent_name == q.agent_name]
        if q.verdict is not None:
            rows = [r for r in rows if r.verdict == q.verdict]
        if q.target_host is not None:
            rows = [r for r in rows if r.target_host == q.target_host]
        if q.cursor is not None:
            cutoff = decode_cursor(q.cursor)
            rows = [r for r in rows if r.id < cutoff]
        return _paginate(rows[: q.limit + 1], q.limit)


def _paginate(
    rows: list[EgressAuditRecord] | list[SandboxEgressAuditRow], limit: int
) -> EgressAuditPage:
    records = [r if isinstance(r, EgressAuditRecord) else _to_record(r) for r in rows]
    if len(records) > limit:
        page = records[:limit]
        return EgressAuditPage(entries=page, next_cursor=encode_cursor(page[-1].id))
    return EgressAuditPage(entries=records, next_cursor=None)
