"""``memory_writeback_dlq`` ORM model — Stream K.K7.

Dead-letter queue for memory writebacks that the orchestrator's
``memory_writeback`` node could not complete (LLM extraction failure,
embed error, transient DB hiccup). The retention-cleanup-job's K7
retry sweep drains it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from expert_work.persistence.base import Base


class MemoryWritebackDLQRow(Base):
    """One pending memory writeback that failed to land."""

    __tablename__ = "memory_writeback_dlq"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    source_thread_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Stream P5b — the run (agent_run.id, stringified) that produced these
    #: extracted memories. Carried through so a retried writeback keeps run
    #: provenance. NULL for legacy rows.
    source_run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: List of ``[kind, content]`` pairs (kind ∈ {"fact","episodic"}).
    extracted: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    next_retry_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
