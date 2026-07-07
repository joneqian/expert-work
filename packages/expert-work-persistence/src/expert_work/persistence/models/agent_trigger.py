"""``agent_trigger`` + ``trigger_run`` ORM models — Stream J.10 (Mini-ADR J-26 / J-42).

Schema mirrors migration 0033_agent_trigger exactly. Tenant RLS is
enforced at the row level by the migration's policies; the application
still passes ``tenant_id`` so an in-memory backend can match semantics
without a Postgres GUC.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from expert_work.persistence.base import Base

_KIND_VALUES = "('cron', 'webhook')"
_SOURCE_VALUES = "('manifest', 'api')"
_RUN_STATUS_VALUES = "('fired', 'succeeded', 'failed', 'retrying', 'dead_letter')"


class AgentTriggerRow(Base):
    """One registered cron / webhook trigger."""

    __tablename__ = "agent_trigger"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    agent_name: Mapped[str] = mapped_column(Text, nullable=False)
    agent_version: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    source: Mapped[str] = mapped_column(Text, nullable=False)
    webhook_secret_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(f"kind IN {_KIND_VALUES}", name="agent_trigger_kind_valid"),
        CheckConstraint(f"source IN {_SOURCE_VALUES}", name="agent_trigger_source_valid"),
        UniqueConstraint("tenant_id", "agent_name", "name", name="agent_trigger_name_uniq"),
        Index("ix_agent_trigger_tenant_id", "tenant_id"),
        Index(
            "ix_agent_trigger_cron_enabled",
            "kind",
            "enabled",
            postgresql_where=text("kind = 'cron' AND enabled = true"),
        ),
    )


class TriggerRunRow(Base):
    """One firing of a trigger — links to the ``agent_run`` it started."""

    __tablename__ = "trigger_run"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    trigger_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    run_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'fired'"))
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(f"status IN {_RUN_STATUS_VALUES}", name="trigger_run_status_valid"),
        Index("ix_trigger_run_tenant_id", "tenant_id"),
        Index("ix_trigger_run_trigger_id", "trigger_id"),
        Index(
            "ix_trigger_run_retrying",
            "next_retry_at",
            postgresql_where=text("status = 'retrying'"),
        ),
    )
