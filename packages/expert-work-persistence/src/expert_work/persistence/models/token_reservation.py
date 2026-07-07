"""``token_reservation`` ORM model — Stream C.5."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Index, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from expert_work.persistence.base import Base


class TokenReservationRow(Base):
    """Outstanding token reservation: ``RESERVED → COMMITTED/RELEASED/EXPIRED``."""

    __tablename__ = "token_reservation"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    agent_name: Mapped[str] = mapped_column(Text, nullable=False)
    thread_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    parent_thread_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    estimated: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actual: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    reserved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("token_reservation_state_reserved_at_idx", "state", "reserved_at"),
        Index("token_reservation_tenant_idx", "tenant_id"),
    )
