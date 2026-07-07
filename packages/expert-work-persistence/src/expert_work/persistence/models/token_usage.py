"""ORM model for the ``token_usage`` table — Stream G.9.

See migration ``0036_token_usage`` and STREAM-G-DESIGN § 9.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Index, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from expert_work.persistence.base import Base


class TokenUsageRow(Base):
    """One row per LLM call — fine-grained accounting for G.9 dashboards."""

    __tablename__ = "token_usage"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    agent_name: Mapped[str] = mapped_column(Text, nullable=False)
    agent_version: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    # Stream Y-3 — additive nullable provider so Y4 can price by
    # ``(provider, model)``. New rows are populated by the token-usage
    # middleware (the ModelSpec carries the provider); legacy rows stay NULL
    # and Y4 reverse-looks-up the provider via MODEL_CATALOG.
    provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Stream Agent-Templates (M1-5a) — end-user (tenant_user.id) this call ran for,
    # for per-user cost attribution. NULL for runs with no user context / legacy rows.
    user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # SE-16 (SE-A43) — what spent the tokens: 'conversation' (per-run LLM
    # calls, the historical shape) vs 'skill_evolution' (flywheel aux +
    # replay). Added in migration 0110; legacy rows backfill via default.
    usage_kind: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'conversation'"), default="conversation"
    )
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    cache_creation_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    cache_read_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("token_usage_tenant_time_idx", "tenant_id", text("observed_at DESC")),
        Index(
            "token_usage_tenant_agent_model_time_idx",
            "tenant_id",
            "agent_name",
            "model",
            text("observed_at DESC"),
        ),
        # Per-user cost rollup (M1-5a).
        Index(
            "token_usage_tenant_user_time_idx",
            "tenant_id",
            "user_id",
            text("observed_at DESC"),
        ),
    )
