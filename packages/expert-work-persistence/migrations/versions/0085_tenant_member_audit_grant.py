"""GRANT SELECT on tenant_member to audit_reader — Stream ACCT.

The platform-admin member view (``GET /v1/members?tenant_id=*``) reads
``tenant_member`` across every tenant. That table is FORCE ROW LEVEL
SECURITY (migration 0051), and the application's main connection role is
NOT BYPASSRLS, so the cross-tenant read assumes the shared ``audit_reader``
BYPASSRLS role (``SqlTenantMemberStore.list_all_tenants`` does
``SET LOCAL ROLE audit_reader``) — exactly mirroring the ledger / feedback
cross-tenant read precedent (``0061_ledger_audit_reader_grant``,
``0071_hx2_feedback_loop``).

``audit_reader`` (NOLOGIN BYPASSRLS, created in migration 0005) needs the
table-level SELECT privilege in addition to BYPASSRLS; BYPASSRLS lets it
cross the RLS policy, this GRANT lets it read the table at all.

Membership (``GRANT audit_reader TO <app_role>``) is provisioned per
deployment, as for the existing audit/ledger readers — not encoded here,
because the application LOGIN role name is environment-specific.

Revision ID: 0085_tenant_member_audit_grant
Revises: 0084_provider_secret_multikey
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0085_tenant_member_audit_grant"
down_revision: str | Sequence[str] | None = "0084_provider_secret_multikey"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_TABLE = "tenant_member"
_READER_ROLE = "audit_reader"


def upgrade() -> None:
    op.execute(f"GRANT SELECT ON TABLE {_TABLE} TO {_READER_ROLE};")


def downgrade() -> None:
    op.execute(f"REVOKE SELECT ON TABLE {_TABLE} FROM {_READER_ROLE};")  # noqa: S608
