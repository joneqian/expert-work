"""ORM model for the ``feedback`` table — Stream G.6.

See migration ``0014_feedback`` and STREAM-G-DESIGN § 2.3.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Index, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from helix_agent.persistence.base import Base


class FeedbackRow(Base):
    """One 👍/👎 a user left on an agent session or a specific turn."""

    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    thread_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    #: ``event_log.seq`` of the rated turn; NULL = whole-session feedback.
    turn_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: W3C trace id — correlates the feedback to its trace in Tempo.
    trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 'up' | 'down'.
    rating: Mapped[str] = mapped_column(Text, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_id: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: Stream HX-2 (Mini-ADR HX-B1) -- FeedbackConsumerWorker stamp.
    #: NULL = a row not yet consumed by the learning loop.
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("feedback_tenant_thread_idx", "tenant_id", "thread_id"),
        Index("feedback_tenant_time_idx", "tenant_id", text("created_at DESC")),
    )
