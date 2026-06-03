"""``tenant_mcp_server`` ORM model — Stream V.

Tenant-registered remote MCP servers. RLS (tenant isolation) is declared in
migration ``0054_tenant_mcp_server``, not here — the model is purely
structural (mirrors ``tenant_config`` / ``tenant_member``).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Float, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from helix_agent.persistence.base import Base


class TenantMcpServerRow(Base):
    """One tenant-registered remote MCP server."""

    __tablename__ = "tenant_mcp_server"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    transport: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    auth_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'none'"))
    token_secret_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Stream W — Mini-ADR W-2. NULL = off-catalog custom row (all Stream V rows);
    # non-NULL = instance of a platform catalog entry. FK + ON DELETE RESTRICT
    # declared in migration 0056.
    catalog_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    timeout_s: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("30"))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
