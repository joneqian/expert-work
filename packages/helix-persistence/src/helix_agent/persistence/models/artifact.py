"""``artifact`` / ``artifact_version`` ORM models — Stream J.9.

A logical :class:`ArtifactRow` (a named file) owns one or more
:class:`ArtifactVersionRow` revisions. Content lives in the user's J.15
persistent workspace volume; these rows carry only metadata. See
migration ``0019_artifact`` and STREAM-J-DESIGN § 10.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Integer, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from helix_agent.persistence.base import Base


class ArtifactRow(Base):
    """A logical artifact — a named file, tenant- and user-scoped (J.9).

    RLS (migration ``0019``) enforces both ``app.tenant_id`` and
    ``app.user_id``.
    """

    __tablename__ = "artifact"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    #: document / code / data / other — declared by the agent.
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    #: Version number of the newest ``artifact_version`` revision.
    latest_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "name", name="artifact_identity_uniq"),
    )


class ArtifactVersionRow(Base):
    """One saved revision of an artifact (J.9).

    ``artifact_id`` is a bare UUID column — no FK. The parent
    ``artifact`` table is ``FORCE`` RLS, where FK referential-integrity
    checks are a known footgun (Mini-ADR J-1a). ``tenant_id`` /
    ``user_id`` are denormalised here so this table carries the same
    combined RLS policy.
    """

    __tablename__ = "artifact_version"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    artifact_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Path of the file inside the user's persistent workspace volume.
    path_in_workspace: Mapped[str] = mapped_column(Text, nullable=False)
    #: Filled lazily on the first content read — NULL until then.
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sha256: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_in_thread: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("artifact_id", "version", name="artifact_version_identity_uniq"),
    )
