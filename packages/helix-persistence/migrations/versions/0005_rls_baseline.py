"""Stream C.4 — Row-level security baseline.

Revision ID: 0005_rls_baseline
Revises: 0004_authn_authz_tables
Create Date: 2026-05-13

Enables PostgreSQL ROW LEVEL SECURITY on every tenant-scoped table
shipped through M0 so far (4 from Stream A/B + 3 from Stream C.3). Each
table gets a single ``USING (tenant_id = current_setting('app.tenant_id', true)::uuid)``
policy plus a matching ``WITH CHECK``. The session variable is set by
the application's RLS sessionmaker wrapper (see
``helix_agent.persistence.rls``) immediately after ``BEGIN``; PgBouncer
transaction pooling is preserved because ``SET LOCAL`` only lives for
the lifetime of the current transaction.

Tables enabled:

* ``event_log`` / ``thread_meta`` / ``audit_log`` (migration 0001)
* ``agent_spec`` (migration 0003)
* ``service_account`` / ``api_key`` / ``role_binding`` (migration 0004)

Tables intentionally excluded:

* ``app_user`` — global identity, ``default_tenant`` is a hint, not an
  ownership column.
* ``jwt_blacklist`` — global revocation table keyed by ``jti``.
* ``backup_record`` (migration 0002) — operator-only table; no
  ``tenant_id`` column.

Roles:

* ``audit_reader`` is a ``NOLOGIN BYPASSRLS`` role that admin-scope
  read-only sessions ``SET ROLE`` to (see subsystems/15 § 4.3). Granted
  ``SELECT`` on the audit-style tables so dashboards can read across
  tenants without the application code having to learn an
  "if-admin-skip-filter" pattern.

The migration deliberately uses ``IF EXISTS`` / ``IF NOT EXISTS`` on
the role + grant statements: alembic stamping in an existing dev
database must not break on second apply.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005_rls_baseline"
down_revision: str | Sequence[str] | None = "0004_authn_authz_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


# Tables that get the canonical tenant_id-equality policy. Order is
# alphabetical so the migration diff is reviewable.
_TENANT_TABLES: tuple[str, ...] = (
    "agent_spec",
    "api_key",
    "audit_log",
    "event_log",
    "role_binding",
    "service_account",
    "thread_meta",
)

# Tables ``audit_reader`` may read across tenants. Mirrors what
# subsystems/15 § 4.3 calls out for the admin observability path.
_AUDIT_READER_TABLES: tuple[str, ...] = (
    "audit_log",
    "event_log",
    "thread_meta",
)


def upgrade() -> None:
    # The shared bypass role for admin reads. ``BYPASSRLS`` is a role
    # attribute; we keep the role NOLOGIN so it can only be assumed via
    # ``SET ROLE`` from an already-authenticated application connection.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audit_reader') THEN
                CREATE ROLE audit_reader NOLOGIN BYPASSRLS;
            END IF;
        END
        $$;
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO audit_reader;")

    for table in _AUDIT_READER_TABLES:
        op.execute(f"GRANT SELECT ON TABLE {table} TO audit_reader;")

    for table in _TENANT_TABLES:
        policy = f"{table}_tenant_isolation"
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table};")
        op.execute(
            f"""
            CREATE POLICY {policy} ON {table}
                USING      (tenant_id = current_setting('app.tenant_id', true)::uuid)
                WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
            """
        )


def downgrade() -> None:
    for table in _TENANT_TABLES:
        policy = f"{table}_tenant_isolation"
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table};")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")

    for table in _AUDIT_READER_TABLES:
        op.execute(f"REVOKE SELECT ON TABLE {table} FROM audit_reader;")  # noqa: S608
    op.execute("REVOKE USAGE ON SCHEMA public FROM audit_reader;")
    op.execute("DROP ROLE IF EXISTS audit_reader;")
