"""``model_rate_card`` ORM model — Stream Y (Mini-ADR Y-3) / 模型定价简化.

Platform-curated model pricing: one **cost price** per ``(provider, model)``,
in integer micro-元 / 百万 tokens. RLS (NULL-tenant isolation), CHECK
constraints, and the unique index are declared in the migrations, not here —
the model is purely structural (mirrors ``mcp_connector_catalog``).
``tenant_id`` is NULLABLE: NULL = platform-global (the only shape today).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from expert_work.persistence.base import Base


class ModelRateCardRow(Base):
    """One platform-curated pricing row (one current price per provider+model)."""

    __tablename__ = "model_rate_card"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # NULL = platform-global (the only shape today); kept so future per-tenant
    # private rate cards are a non-migration change.
    tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    # micro-元 per *million* tokens.
    input_per_mtok_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    output_per_mtok_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cache_creation_per_mtok_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    cache_read_per_mtok_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
