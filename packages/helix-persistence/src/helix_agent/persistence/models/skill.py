"""``skill`` + ``skill_version`` ORM models — Stream J.7a (Mini-ADR J-23).

Schema mirrors migration 0029_skill exactly. Tenant RLS is enforced at
the row level by the migration's policy; the application still passes
``tenant_id`` for clarity + so an in-memory backend can match semantics
without a Postgres GUC.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from helix_agent.persistence.base import Base


class SkillRow(Base):
    """One row of ``skill`` — the named bundle.

    ``latest_version`` points at the current published version row (or
    0 between create + first version insert). The orchestrator's skill
    loader uses ``status='active'`` rows for bare ``name`` references.
    """

    __tablename__ = "skill"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    latest_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("status IN ('draft', 'active', 'archived')", name="skill_status_check"),
        CheckConstraint("latest_version >= 0", name="skill_latest_version_nonneg"),
        UniqueConstraint("tenant_id", "name", name="skill_tenant_name_uq"),
        Index("ix_skill_tenant_id", "tenant_id"),
        Index(
            "ix_skill_status_active",
            "tenant_id",
            "name",
            postgresql_where=text("status = 'active'"),
        ),
    )


class SkillVersionRow(Base):
    """One row of ``skill_version`` — an immutable published version.

    ``(skill_id, version)`` is unique; ``version`` starts at 1 and the
    Store auto-increments on ``add_version``. ``tool_names`` /
    ``required_models`` land as JSONB arrays of strings.
    """

    __tablename__ = "skill_version"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    skill_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("skill.id", ondelete="CASCADE", name="skill_version_skill_id_fk"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_fragment: Mapped[str] = mapped_column(Text, nullable=False)
    tool_names: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_models: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    authored_by: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'human'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("version >= 1", name="skill_version_positive"),
        CheckConstraint(
            "authored_by IN ('human', 'agent')", name="skill_version_authored_by_check"
        ),
        UniqueConstraint("skill_id", "version", name="skill_version_skill_version_uq"),
        Index("ix_skill_version_tenant_id", "tenant_id"),
        Index("ix_skill_version_skill_id", "skill_id"),
    )
