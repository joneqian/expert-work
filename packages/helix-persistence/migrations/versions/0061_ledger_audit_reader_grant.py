"""GRANT SELECT on tenant_billing_ledger to audit_reader — Stream Z (Z-2).

The Z2 chargeback API reads ``tenant_billing_ledger`` across every tenant.
That table is FORCE ROW LEVEL SECURITY (migration 0060), and the
application's main connection role is NOT BYPASSRLS, so the cross-tenant
read assumes the shared ``audit_reader`` BYPASSRLS role
(``DbTenantBillingLedgerStore.list_for_month_all_tenants`` does
``SET LOCAL ROLE audit_reader``) — exactly mirroring the audit
cross-tenant read precedent.

``audit_reader`` (NOLOGIN BYPASSRLS, created in migration 0005) was only
GRANTed SELECT on ``audit_log`` / ``event_log`` / ``thread_meta``. This
migration extends it to ``tenant_billing_ledger`` so the role can actually
read the new table. BYPASSRLS lets it cross the RLS policy; this GRANT is
the *table-level* SELECT privilege it still needs.

Membership (``GRANT audit_reader TO <app_role>``) is provisioned per
deployment, exactly as it already is for ``audit_writer`` — not encoded
here, because the application LOGIN role name is environment-specific.

Revision ID: 0061_ledger_audit_reader_grant
Revises: 0060_tenant_billing_ledger
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0061_ledger_audit_reader_grant"
down_revision: str | Sequence[str] | None = "0060_tenant_billing_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_TABLE = "tenant_billing_ledger"
_READER_ROLE = "audit_reader"


def upgrade() -> None:
    op.execute(f"GRANT SELECT ON TABLE {_TABLE} TO {_READER_ROLE};")


def downgrade() -> None:
    op.execute(f"REVOKE SELECT ON TABLE {_TABLE} FROM {_READER_ROLE};")  # noqa: S608
