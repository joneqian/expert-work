"""``mcp_connector_catalog`` ORM model — Stream W (Mini-ADR W-1).

Platform-curated catalog of MCP connector *types*. RLS (NULL-tenant isolation)
is declared in migration ``0055_mcp_connector_catalog``, not here — the model is
purely structural (mirrors ``tenant_mcp_server`` / ``encrypted_secret``).
``tenant_id`` is NULLABLE: NULL = platform-global (the only shape today).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from helix_agent.persistence.base import Base


class McpConnectorCatalogRow(Base):
    """One platform-curated MCP connector type."""

    __tablename__ = "mcp_connector_catalog"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # NULL = platform-global (the only shape today); kept so future per-tenant
    # private catalogs are a non-migration change.
    tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    category: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'general'"))
    icon: Mapped[str | None] = mapped_column(Text, nullable=True)
    transport: Mapped[str] = mapped_column(Text, nullable=False)
    url_template: Mapped[str] = mapped_column(Text, nullable=False)
    auth_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'none'"))
    auth_schema: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    required_tier: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'free'"))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_by: Mapped[str] = mapped_column(Text, nullable=False)
