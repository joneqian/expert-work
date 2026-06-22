"""Make sandbox_egress_audit.tenant_id nullable (audit-eval Phase 4).

Revision ID: 0088_egress_audit_null_tenant
Revises: 0087_sandbox_egress_audit
Create Date: 2026-06-22

A pre-identity egress rejection (a missing / invalid / expired proxy token →
``blocked_auth``) has no trustworthy tenant to attribute it to. Allow a NULL
``tenant_id`` so the rejection is still recorded as a platform-level anomaly
(visible only in the cross-tenant audit view). All authenticated rows continue
to carry a tenant_id.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0088_egress_audit_null_tenant"
down_revision: str | Sequence[str] | None = "0087_sandbox_egress_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.alter_column(
        "sandbox_egress_audit",
        "tenant_id",
        existing_type=UUID(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    # NULL rows (blocked_auth) must go before re-imposing NOT NULL.
    op.execute(sa.text("DELETE FROM sandbox_egress_audit WHERE tenant_id IS NULL"))
    op.alter_column(
        "sandbox_egress_audit",
        "tenant_id",
        existing_type=UUID(as_uuid=True),
        nullable=False,
    )
