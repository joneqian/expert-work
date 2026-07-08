"""Guard-rail: every table with a ``tenant_id`` column must have an RLS policy.

RLS is the enforced tenant-isolation backstop (see the RLS enforcement spec). A
new tenant table shipped without a policy would silently rejoin the leak surface;
this test fails CI when that happens.
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


# Tables with a ``tenant_id`` column that are intentionally NOT per-tenant
# RLS-scoped (platform-global / NULL-tenant). Keep this list tiny and justified.
_ALLOWED_WITHOUT_POLICY: frozenset[str] = frozenset()


def test_every_tenant_table_has_rls_policy(postgres_container: PostgresContainer) -> None:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")
    engine = create_engine(_sync_dsn(postgres_container), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            rows = (
                conn.execute(
                    text(
                        """
                        SELECT c.relname
                        FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        JOIN pg_attribute a
                          ON a.attrelid = c.oid AND a.attname = 'tenant_id'
                          AND NOT a.attisdropped
                        WHERE n.nspname = 'public' AND c.relkind = 'r'
                          AND NOT EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid)
                        ORDER BY c.relname
                        """
                    )
                )
                .scalars()
                .all()
            )
        missing = sorted(set(rows) - _ALLOWED_WITHOUT_POLICY)
        assert not missing, f"tenant_id tables without an RLS policy: {missing}"
    finally:
        engine.dispose()
