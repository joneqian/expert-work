"""``tenant_mcp_server`` ORM model — Stream V.

Tenant-registered remote MCP servers. RLS (tenant isolation) is declared in
migration ``0054_tenant_mcp_server``, not here — the model is purely
structural (mirrors ``tenant_config`` / ``tenant_member``).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import ARRAY, Boolean, DateTime, Float, Text, func, text
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
    # Custom HTTP headers (M1). Values can be secrets (e.g. X-API-Key), so the
    # whole {name: value} map is stored as one encrypted SecretStore blob and
    # only its ``secret://`` ref lives here; the (non-secret) header names are
    # kept separately so the UI can list configured headers without decrypting.
    # Both NULL = no custom headers. Migration 0090.
    custom_headers_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    custom_header_names: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    # SSE read timeout override (M1) — NULL keeps the SDK default. Migration 0090.
    sse_read_timeout_s: Mapped[float | None] = mapped_column(Float, nullable=True)
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
    # Connectivity health (#2) — result of the most recent probe; all NULL until
    # first probed. CHECK constraint on last_probe_status declared in migration
    # 0064. Health is observational; it never gates tool assembly.
    last_probe_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_probe_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_probe_error: Mapped[str | None] = mapped_column(Text, nullable=True)
