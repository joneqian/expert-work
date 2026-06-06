"""``mcp_oauth_connection`` ORM model — Stream MCP-OAUTH (OA-1b).

Per-user OAuth 2.1 connection to a hosted MCP connector. RLS (tenant isolation)
is declared in migration ``0063_mcp_oauth_connection``; user-level scoping is
enforced in the store (every query filters ``user_id``). Token *values* live in
the encrypted secret store — only ``secret://`` refs are columns here.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from helix_agent.persistence.base import Base


class McpOAuthConnectionRow(Base):
    """One per-user OAuth connection to a hosted MCP connector."""

    __tablename__ = "mcp_oauth_connection"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    catalog_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("mcp_connector_catalog.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    resolved_url: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    access_token_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    oauth_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    pkce_verifier: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
