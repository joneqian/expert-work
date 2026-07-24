"""mcp_oauth_connection.catalog_id FK CASCADE → RESTRICT —— 目录删除 DB 级兜底.

Revision ID: 0133_mcp_oauth_fk_restrict
Revises: 0132_role_binding_orphan_cleanup
Create Date: 2026-07-24

删除接口卫生修复第 2 批 Task 8。``mcp_connector_catalog → mcp_oauth_connection``
的 FK 原是 ``ON DELETE CASCADE``(0063 inline 定义,无显式约束名)—— 删目录会
静默清空所有用户的 OAuth 连接与其密文引用,用户无感知。app 层已在
``mcp_catalog.py`` 的 DELETE 端点加了 409 防护(默认拦 + ``?force=true`` 显式
级联),本迁移把 FK 同步改成 ``RESTRICT`` 作为 DB 级兜底 —— 防止未来代码绕过
app 闸时仍然静默级联。

0063 的 FK 是内联 ``sa.ForeignKey(...)`` 定义,PostgreSQL 会分配一个
auto-generated 约束名(通常是 ``mcp_oauth_connection_catalog_id_fkey``,但**不
赌 auto-name**)—— upgrade 里先用 ``information_schema`` 查真实约束名再 drop。

downgrade 对称改回 CASCADE(用本迁移显式命名的 ``mcp_oauth_connection_catalog_id_fkey``,
drop 时无需再查名)。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0133_mcp_oauth_fk_restrict"
down_revision: str | Sequence[str] | None = "0132_role_binding_orphan_cleanup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_TABLE = "mcp_oauth_connection"
_COLUMN = "catalog_id"
_REFERENCED_TABLE = "mcp_connector_catalog"
_NEW_FK_NAME = "mcp_oauth_connection_catalog_id_fkey"

_FIND_FK_NAME_SQL = """
    SELECT tc.constraint_name
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON tc.constraint_name = kcu.constraint_name
     AND tc.table_schema = kcu.table_schema
    JOIN information_schema.constraint_column_usage ccu
      ON tc.constraint_name = ccu.constraint_name
     AND tc.table_schema = ccu.table_schema
    WHERE tc.table_name = :table
      AND tc.table_schema = current_schema()
      AND tc.constraint_type = 'FOREIGN KEY'
      AND kcu.column_name = :column
      AND ccu.table_name = :referenced_table
"""


def upgrade() -> None:
    bind = op.get_bind()
    fk_name = bind.execute(
        text(_FIND_FK_NAME_SQL),
        {"table": _TABLE, "column": _COLUMN, "referenced_table": _REFERENCED_TABLE},
    ).scalar()
    if fk_name is None:
        msg = (
            f"could not locate the FK from {_TABLE}.{_COLUMN} to {_REFERENCED_TABLE} "
            "— 0063 must have changed shape; update this migration"
        )
        raise RuntimeError(msg)
    op.drop_constraint(fk_name, _TABLE, type_="foreignkey")
    op.create_foreign_key(
        _NEW_FK_NAME,
        _TABLE,
        _REFERENCED_TABLE,
        [_COLUMN],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(_NEW_FK_NAME, _TABLE, type_="foreignkey")
    op.create_foreign_key(
        _NEW_FK_NAME,
        _TABLE,
        _REFERENCED_TABLE,
        [_COLUMN],
        ["id"],
        ondelete="CASCADE",
    )
