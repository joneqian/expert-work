"""``tenant_billing_ledger`` ORM model — Stream Y (Mini-ADR Y-4).

Derived per-tenant monthly billing buckets: one row per
``(tenant_id, month, provider, model, agent_name)``. Produced by the Y4 rollup
job by pricing ``token_usage`` rows with the rate effective at each row's
``observed_at``. Cost is stored as the ``base`` / ``markup`` / ``billed`` split
(integer micro-USD); tenants see only ``billed`` (Stream Z exposure control),
the split is retained for system_admin chargeback.

Tenant-scoped (NOT the NULL-tenant catalog shape): RLS, the unique constraint
on the bucket key, and the ``>= 0`` CHECKs are declared in migration
``0060_tenant_billing_ledger`` — this model is purely structural. This is a
**separate** table from the C.5 ``token_budget_ledger`` (different semantics:
that one is coarse budget counters; this is derived cost).
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from expert_work.persistence.base import Base


class TenantBillingLedgerRow(Base):
    """One derived monthly billing bucket for a tenant."""

    __tablename__ = "tenant_billing_ledger"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    # First-of-month convention.
    month: Mapped[date] = mapped_column(Date, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    agent_name: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    cache_creation_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    cache_read_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    base_cost_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    markup_cost_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    billed_cost_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    priced: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    rate_card_priced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
