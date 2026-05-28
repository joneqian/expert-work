"""``skill`` + ``skill_version`` ORM models — Stream J.7a (Mini-ADR J-23).

Schema mirrors migration 0029_skill exactly. Tenant RLS is enforced at
the row level by the migration's policy; the application still passes
``tenant_id`` for clarity + so an in-memory backend can match semantics
without a Postgres GUC.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
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
    # Capability Uplift Sprint #3 — migration 0042.
    # supporting_files: {"reference/foo.md": {"content": b64, "size": int, "mime": str}, ...}
    # Mini-ADR U-16 caps total bytes at 5 MB via DB CHECK; per-file 1 MB
    # + per-skill 64 entries enforced at the API layer.
    supporting_files: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Mini-ADR U-15: progressive disclosure opt-in. Default false keeps
    # existing eager body injection so deployed agents do not regress.
    lazy_load: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # Mini-ADR U-21: blake2b-32 of canonicalized (prompt_fragment,
    # supporting_files). Recomputed at skill_view time to catch drift
    # (SQL injection / internal actor writing past the strict scan).
    content_hash: Mapped[bytes] = mapped_column(
        LargeBinary, nullable=False, server_default=text("''::bytea")
    )
    # Mini-ADR U-24: high-risk publish gate. Computed at write time when
    # tool_names ∩ HIGH_RISK_TOOLS ≠ ∅ or any supporting_files path
    # starts with "scripts/". M0 transparent (all writes are admin);
    # M1-K J.7b-1 agent-self-authored skills get gated.
    high_risk: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("version >= 1", name="skill_version_positive"),
        CheckConstraint(
            "authored_by IN ('human', 'agent')", name="skill_version_authored_by_check"
        ),
        CheckConstraint(
            "octet_length(supporting_files::text) <= 5242880",
            name="skill_version_supporting_files_size_ck",
        ),
        UniqueConstraint("skill_id", "version", name="skill_version_skill_version_uq"),
        Index("ix_skill_version_tenant_id", "tenant_id"),
        Index("ix_skill_version_skill_id", "skill_id"),
    )
