"""``token_budget_ledger`` ORM model — Stream C.5."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import BigInteger, Date, DateTime, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from expert_work.persistence.base import Base


class TokenBudgetLedgerRow(Base):
    """Monthly used / reserved token accounting per ``(tenant_id, month)``."""

    __tablename__ = "token_budget_ledger"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    month: Mapped[date] = mapped_column(Date, nullable=False)
    budget_total: Mapped[int] = mapped_column(BigInteger, nullable=False)
    used_total: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    reserved_total: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "month", name="token_budget_ledger_tenant_month_uniq"),
    )
