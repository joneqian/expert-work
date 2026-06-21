"""ORM models for the Credential Proxy tables — Stream F.5.

See migration ``0013_credential_proxy`` and subsystems/11 § 3.1.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from helix_agent.persistence.base import Base


class SecretAllowlistRow(Base):
    """A ``(tenant, agent, version, secret_ref)`` the proxy may inject."""

    __tablename__ = "secret_allowlist"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    agent_name: Mapped[str] = mapped_column(Text, primary_key=True)
    agent_version: Mapped[str] = mapped_column(Text, primary_key=True)
    secret_ref: Mapped[str] = mapped_column(Text, primary_key=True)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "secret_allowlist_lookup_idx",
            "tenant_id",
            "agent_name",
            "agent_version",
        ),
    )


class CredentialProxyAuditRow(Base):
    """One secret-injection attempt — the ref + host + status, never the value."""

    __tablename__ = "credential_proxy_audit"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    agent_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    session_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    sandbox_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    secret_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_host: Mapped[str] = mapped_column(Text, nullable=False)
    inject_kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: ok / denied / secret_miss / cached.
    status: Mapped[str] = mapped_column(Text, nullable=False)
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "credential_proxy_audit_tenant_time_idx",
            "tenant_id",
            text("occurred_at DESC"),
        ),
        Index("credential_proxy_audit_session_idx", "session_id"),
    )


class SandboxEgressAuditRow(Base):
    """One sandbox→internet connection through the transparent egress proxy.

    Records host + port + byte volumes + verdict — never payload (HTTPS is
    tunnelled, the proxy never sees plaintext). The audit-over-blocking record
    (sandbox-egress design §3.1).
    """

    __tablename__ = "sandbox_egress_audit"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    agent_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    sandbox_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_host: Mapped[str] = mapped_column(Text, nullable=False)
    target_port: Mapped[int] = mapped_column(Integer, nullable=False)
    #: allowed / blocked_ssrf / blocked_allowlist / blocked_auth / upstream_error.
    verdict: Mapped[str] = mapped_column(Text, nullable=False)
    bytes_up: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    bytes_down: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "sandbox_egress_audit_tenant_time_idx",
            "tenant_id",
            text("occurred_at DESC"),
        ),
        Index("sandbox_egress_audit_host_idx", "target_host"),
    )
