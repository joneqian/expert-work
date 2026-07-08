"""Create the ``app_user`` role — RLS enforcement (Phase 0).

Non-superuser, non-BYPASSRLS role that runtime tenant sessions ``SET LOCAL ROLE``
to, so the FORCE-RLS policies actually apply (the app connects as superuser
``expert_work``, which bypasses RLS). Created but UNUSED here — enforcement is
enabled later in ``rls.py``. Idempotent, reversible, no table locks.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0121_app_user_role"
down_revision: str | Sequence[str] | None = "0120_supporting_files_cap"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') THEN
                CREATE ROLE app_user NOLOGIN NOSUPERUSER NOBYPASSRLS;
            END IF;
        END
        $$;
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO app_user;")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_user;")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user;")
    # Future tables/sequences created by the migration role auto-grant to app_user,
    # else a new table is invisible to app_user (fail-closed) after cutover.
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_user;"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO app_user;"
    )
    # Membership → audit/billing paths' SET LOCAL ROLE audit_reader|writer works
    # from an app_user session. BYPASSRLS is NOT inherited ambiently (only on SET ROLE).
    op.execute("GRANT audit_reader, audit_writer TO app_user;")


def downgrade() -> None:
    # Revoke default privileges + grants BEFORE dropping the role (DROP ROLE fails
    # while dependent privileges exist).
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM app_user;"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE USAGE, SELECT ON SEQUENCES FROM app_user;"
    )
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM app_user;")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM app_user;")
    op.execute("REVOKE USAGE ON SCHEMA public FROM app_user;")
    op.execute("REVOKE audit_reader, audit_writer FROM app_user;")
    op.execute("DROP ROLE IF EXISTS app_user;")
