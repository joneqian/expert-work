"""``audit_log`` ORM model — see ADR-0002 §audit_log and subsystems/17-audit-log."""

from __future__ import annotations

from datetime import datetime
from ipaddress import IPv4Address, IPv6Address
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Text, false, func, text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from helix_agent.persistence.base import Base


class AuditLogRow(Base):
    """Admin / compliance operation audit log.

    Append-only at both the application contract and the DB layer
    (D.1a: ``audit_writer`` role + REVOKE UPDATE/DELETE/TRUNCATE FROM
    PUBLIC). WORM backup to ObjectStore with Object Lock retention is
    added in Stream D.1c — the ``backup_acked`` / ``backup_acked_at``
    columns are the worker's progress marker.

    Schema follows ADR-0002 §audit_log + subsystems/17-audit-log §3.1.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    actor_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str] = mapped_column(Text, nullable=False)
    on_behalf_of: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str] = mapped_column(Text, nullable=False)
    resource_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip: Mapped[IPv4Address | IPv6Address | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    backup_acked: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=false(),
        default=False,
    )
    backup_acked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        Index("audit_log_tenant_time_idx", "tenant_id", "occurred_at"),
        Index("audit_log_actor_idx", "tenant_id", "actor_type", "actor_id", "occurred_at"),
        Index("audit_log_resource_idx", "tenant_id", "resource_type", "resource_id", "occurred_at"),
        Index("audit_log_action_idx", "tenant_id", "action", "occurred_at"),
        Index("audit_log_request_idx", "request_id"),
        Index(
            "audit_log_backup_pending_idx",
            "occurred_at",
            postgresql_where=text("backup_acked = false"),
        ),
    )
