"""``platform_agent_template`` ORM model — Stream Agent-Templates (M1).

Platform-curated catalog of official Agent templates (full base AgentSpec
manifests). RLS (NULL-tenant isolation) is declared in migration
``0095_platform_agent_template``, not here — the model is purely structural
(mirrors ``mcp_connector_catalog``). ``tenant_id`` is NULLABLE: NULL =
platform-global (the only shape today).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CHAR, Boolean, DateTime, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from expert_work.persistence.base import Base


class PlatformAgentTemplateRow(Base):
    """One platform-curated Agent template version."""

    __tablename__ = "platform_agent_template"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # NULL = platform-global (the only shape today); kept so future per-tenant
    # private template libraries are a non-migration change.
    tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    spec_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    spec_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    category: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'general'"))
    icon: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_tier: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'free'"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
