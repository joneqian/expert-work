"""迁移 0130 —— agent_trigger user 维度唯一 + 投递路由列。"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"
_PRE = "0129_tenant_cfg_predictive"
_MIGRATION = "0130_trigger_user_scope"


def _sync_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql+psycopg://")


@pytest.mark.integration
def test_migration_0130_schema() -> None:
    with PostgresContainer("pgvector/pgvector:pg16") as container:
        dsn = _sync_dsn(str(container.get_connection_url()))
        cfg = Config(str(ALEMBIC_INI))
        cfg.set_main_option("sqlalchemy.url", dsn)
        command.upgrade(cfg, _MIGRATION)

        engine = sa.create_engine(dsn)
        with engine.connect() as conn:
            cols = {
                r[0]
                for r in conn.execute(
                    sa.text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'agent_trigger'"
                    )
                )
            }
            assert "originating_thread_id" in cols
            assert "context_mode" in cols

            indexes = {
                r[0]
                for r in conn.execute(
                    sa.text("SELECT indexname FROM pg_indexes WHERE tablename = 'agent_trigger'")
                )
            }
            assert "ix_agent_trigger_user_name_uniq" in indexes
            assert "ix_agent_trigger_null_user_name_uniq" in indexes

            constraints = {
                r[0]
                for r in conn.execute(
                    sa.text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conrelid = 'agent_trigger'::regclass"
                    )
                )
            }
            assert "agent_trigger_name_uniq" not in constraints  # 旧唯一约束已删
        engine.dispose()
