"""Stream F.5 — credential-proxy tables.

Revision ID: 0013_credential_proxy
Revises: 0012_sandbox_instance
Create Date: 2026-05-16

Two tables for the Credential Proxy (subsystems/11 § 3.1):

* ``secret_allowlist`` — the ``(tenant, agent, version, secret_ref)``
  four-tuples a manifest is permitted to reference. The proxy refuses
  any ``X-Helix-Secret-Ref`` not on this list (403).

* ``credential_proxy_audit`` — one row per injection attempt. Records
  the ref + tenant + target host + status; **never** the secret value.
  A dedicated table (not ``audit_log``) — same table family, separate
  source, per subsystems/11 § 3.1.

``tenant_id`` is ``UUID`` here (subsystems/11 writes ``TEXT``) to match
the rest of the Helix schema.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0013_credential_proxy"
down_revision: str | Sequence[str] | None = "0012_sandbox_instance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.create_table(
        "secret_allowlist",
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("agent_name", sa.Text(), nullable=False),
        sa.Column("agent_version", sa.Text(), nullable=False),
        sa.Column("secret_ref", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "agent_name",
            "agent_version",
            "secret_ref",
            name="secret_allowlist_pkey",
        ),
    )
    op.create_index(
        "secret_allowlist_lookup_idx",
        "secret_allowlist",
        ["tenant_id", "agent_name", "agent_version"],
    )

    op.create_table(
        "credential_proxy_audit",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("agent_name", sa.Text(), nullable=True),
        sa.Column("agent_version", sa.Text(), nullable=True),
        sa.Column("session_id", UUID(as_uuid=True), nullable=True),
        sa.Column("sandbox_id", sa.Text(), nullable=True),
        sa.Column("secret_ref", sa.Text(), nullable=True),
        sa.Column("target_host", sa.Text(), nullable=False),
        # header / query / body — the injection site.
        sa.Column("inject_kind", sa.Text(), nullable=True),
        # ok / denied / secret_miss / cached.
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "credential_proxy_audit_tenant_time_idx",
        "credential_proxy_audit",
        ["tenant_id", sa.text("occurred_at DESC")],
    )
    op.create_index(
        "credential_proxy_audit_session_idx",
        "credential_proxy_audit",
        ["session_id"],
    )


def downgrade() -> None:
    op.drop_index("credential_proxy_audit_session_idx", table_name="credential_proxy_audit")
    op.drop_index("credential_proxy_audit_tenant_time_idx", table_name="credential_proxy_audit")
    op.drop_table("credential_proxy_audit")
    op.drop_index("secret_allowlist_lookup_idx", table_name="secret_allowlist")
    op.drop_table("secret_allowlist")
