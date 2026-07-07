"""``backup_record`` ORM model — see subsystems/22-disaster-recovery § 3.2."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Index, SmallInteger, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from expert_work.persistence.base import Base


class BackupRecordRow(Base):
    """One row per backup attempt across all asset types.

    ``UNIQUE (asset_type, asset_ref)`` lets reruns overwrite the same
    physical artifact slot rather than accumulating duplicate rows.
    """

    __tablename__ = "backup_record"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    asset_type: Mapped[str] = mapped_column(Text, nullable=False)
    asset_ref: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sha256: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    region: Mapped[str] = mapped_column(Text, nullable=False)
    tier: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint("asset_type", "asset_ref", name="backup_record_asset_unique"),
        Index("backup_record_asset_time_idx", "asset_type", "started_at"),
    )
