"""Integration: the ``app_user`` role exists with the right attributes/grants.

RLS enforcement Task 1 (Phase 0): pins that migration 0121 provisions a
non-superuser, non-BYPASSRLS, NOLOGIN role with DML on tenant tables and
membership in the pre-existing ``audit_reader``/``audit_writer`` BYPASSRLS
roles (0005/0008). The role is created but UNUSED here — Task 6 wires
``SET LOCAL ROLE app_user`` into the runtime session path.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from testcontainers.postgres import PostgresContainer

pytestmark = pytest.mark.integration
ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _sync_dsn(c: PostgresContainer) -> str:
    url = str(c.get_connection_url())
    return url.replace("+psycopg2", "+psycopg").replace("postgresql://", "postgresql+psycopg://", 1)


def test_app_user_role_provisioned(postgres_container: PostgresContainer) -> None:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")
    engine = create_engine(_sync_dsn(postgres_container), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            attrs = conn.execute(
                text(
                    "SELECT rolsuper, rolbypassrls, rolcanlogin "
                    "FROM pg_roles WHERE rolname='app_user'"
                )
            ).one_or_none()
            assert attrs is not None, "app_user role missing"
            assert attrs == (False, False, False)  # NOSUPERUSER, NOBYPASSRLS, NOLOGIN
            # DML granted on a representative tenant table.
            has_select = conn.execute(
                text("SELECT has_table_privilege('app_user', 'tenant_config', 'SELECT')")
            ).scalar()
            assert has_select is True
            # Member of audit_reader (so audit paths' SET ROLE works from app_user).
            is_member = conn.execute(
                text("SELECT pg_has_role('app_user', 'audit_reader', 'MEMBER')")
            ).scalar()
            assert is_member is True
    finally:
        engine.dispose()


def test_app_user_role_downgrade_drops_role(postgres_container: PostgresContainer) -> None:
    """Downgrading past 0121 revokes grants first, then drops the role cleanly."""
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")
    engine = create_engine(_sync_dsn(postgres_container), isolation_level="AUTOCOMMIT")
    try:
        command.downgrade(cfg, "0120_supporting_files_cap")
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname='app_user'")
            ).one_or_none()
            assert row is None, "app_user role still present after downgrade"
    finally:
        command.upgrade(cfg, "head")  # restore shared session-scoped container
        engine.dispose()
