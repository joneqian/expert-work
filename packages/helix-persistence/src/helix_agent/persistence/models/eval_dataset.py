"""``eval_dataset`` + ``curation_candidate`` ORM models — Stream J.12.

Mini-ADR J-19 / J-27 / J-43 (STREAM-J-DESIGN § 17). Schema mirrors
migration 0034_eval_dataset exactly. Tenant RLS is enforced at the row
level by the migration's policies; the application still passes
``tenant_id`` so an in-memory backend can match semantics without a
Postgres GUC.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from helix_agent.persistence.base import Base

_SOURCE_VALUES = "('golden', 'trajectory', 'regression')"
_OUTCOME_VALUES = "('success', 'failed', 'max_steps', 'cancelled')"
_SIGNAL_VALUES = "('negative_feedback', 'failed_outcome', 'positive_feedback')"
_RATING_VALUES = "('up', 'down')"
_STATUS_VALUES = "('pending', 'promoted', 'dismissed')"


class EvalDatasetRow(Base):
    """One curated eval case — shared with J.13.

    A "dataset" is the set of rows sharing ``(tenant_id, agent_name,
    name)``; ``name`` is not unique.
    """

    __tablename__ = "eval_dataset"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    agent_name: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    input: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    expected: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_trajectory_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(f"source IN {_SOURCE_VALUES}", name="eval_dataset_source_valid"),
        Index("ix_eval_dataset_tenant_id", "tenant_id"),
        Index("ix_eval_dataset_agent", "tenant_id", "agent_name"),
    )


class CurationCandidateRow(Base):
    """One trajectory the curation worker flagged for human review.

    Unique per ``(tenant_id, trajectory_key)`` — the worker upserts
    insert-if-absent so a trajectory becomes a candidate at most once.
    """

    __tablename__ = "curation_candidate"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    agent_name: Mapped[str] = mapped_column(Text, nullable=False)
    agent_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    thread_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    trajectory_key: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    signal: Mapped[str] = mapped_column(Text, nullable=False)
    feedback_rating: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    eval_dataset_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(f"outcome IN {_OUTCOME_VALUES}", name="curation_candidate_outcome_valid"),
        CheckConstraint(f"signal IN {_SIGNAL_VALUES}", name="curation_candidate_signal_valid"),
        CheckConstraint(
            f"feedback_rating IN {_RATING_VALUES}", name="curation_candidate_rating_valid"
        ),
        CheckConstraint(f"status IN {_STATUS_VALUES}", name="curation_candidate_status_valid"),
        UniqueConstraint("tenant_id", "trajectory_key", name="curation_candidate_trajectory_uniq"),
        Index("ix_curation_candidate_tenant_id", "tenant_id"),
        Index("ix_curation_candidate_agent", "tenant_id", "agent_name"),
        Index(
            "ix_curation_candidate_pending",
            "tenant_id",
            "agent_name",
            postgresql_where=text("status = 'pending'"),
        ),
    )
