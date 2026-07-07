"""``dr_drill`` ORM model — see subsystems/22-disaster-recovery § 3.2."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Integer, Text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from expert_work.persistence.base import Base


class DrDrillRow(Base):
    """One row per DR drill exercise.

    M0 inserts one row per quarterly manual drill. M1+ ``DrillRunner``
    writes programmatically. Schema matches the Pydantic ``DrillRecord``
    field-for-field.
    """

    __tablename__ = "dr_drill"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    drill_type: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    rpo_actual_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rto_actual_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_rpo_s: Mapped[int] = mapped_column(Integer, nullable=False)
    target_rto_s: Mapped[int] = mapped_column(Integer, nullable=False)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
