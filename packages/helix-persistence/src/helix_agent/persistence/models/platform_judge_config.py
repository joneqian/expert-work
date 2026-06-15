"""Platform judge-model config ORM model — Stream PI-3-A1.

A single-row (``id == "singleton"``) table storing the platform's chosen
output/action **judge** provider+model. Non-secret config (provider/model
names only — keys live in ``platform_provider_secret``).

Platform-global, tenant-less (like ``platform_embedding_config``) — no RLS
policy; all access goes through ``bypass_rls_session()``. An absent row means
"not configured" → the judge falls back to each agent's own model.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from helix_agent.persistence.base import Base


class PlatformJudgeConfigRow(Base):
    """The single platform judge-model selection row."""

    __tablename__ = "platform_judge_config"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    judge_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    judge_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_by: Mapped[str | None] = mapped_column(Text, nullable=True)
