"""Platform embedding/rerank config ORM model — Stream T (PR B).

A single-row (``id == "singleton"``) table storing the platform's chosen
embedding / rerank provider+model selection. This is **non-secret** config
(provider/model names only — actual API keys live in ``platform_provider_secret``).

Platform-global, tenant-less (like ``platform_provider_secret`` /
``role_binding`` platform-scope rows) — no RLS policy is attached; all access
goes through ``bypass_rls_session()``. An absent row means "not configured".
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from expert_work.persistence.base import Base


class PlatformEmbeddingConfigRow(Base):
    """The single platform embedding/rerank selection row."""

    __tablename__ = "platform_embedding_config"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    embedding_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    rerank_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    rerank_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_by: Mapped[str | None] = mapped_column(Text, nullable=True)
