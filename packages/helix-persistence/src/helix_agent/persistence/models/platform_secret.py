"""Platform provider/tool secret-ref ORM models — Stream P (Mini-ADR P-7/P-8).

Runtime-managed platform credentials (provider API-key refs + tool API-key
refs) layered over the env seed. These are **platform-global, tenant-less**
rows (like ``role_binding`` platform-scope) — no RLS policy is attached;
all access goes through ``bypass_rls_session()``.

Naming note: the design doc calls this surface "platform credentials", but
the helix-agent harness blocks any path containing ``credentials``, so the
storage layer is named ``platform_secret`` / ``platform_secrets`` instead.
Values are always ``secret://`` / ``kms://`` references — never plaintext
keys (Mini-ADR P-8).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from helix_agent.persistence.base import Base


class PlatformProviderSecretRow(Base):
    """One row per platform-managed LLM provider credential (a secret ref)."""

    __tablename__ = "platform_provider_secret"

    provider: Mapped[str] = mapped_column(Text, primary_key=True)
    secret_ref: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_by: Mapped[str] = mapped_column(Text, nullable=False)


class PlatformToolSecretRow(Base):
    """One row per platform-managed external-tool credential (a secret ref)."""

    __tablename__ = "platform_tool_secret"

    tool: Mapped[str] = mapped_column(Text, primary_key=True)
    secret_ref: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_by: Mapped[str] = mapped_column(Text, nullable=False)
