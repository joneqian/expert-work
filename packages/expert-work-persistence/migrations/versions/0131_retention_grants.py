"""retention_cleanup_worker 清扫表授权补齐(含 image/artifact/approval 历史欠账).

Revision ID: 0131_retention_grants
Revises: 0130_trigger_user_scope
Create Date: 2026-07-24

删除接口卫生修复第 1 批。``retention_cleanup_worker`` 角色(0010 建)在
后续几批物理清扫上线时未跟着补授权,导致清扫作业对这些表拿不到
SELECT/DELETE:

* 本批新增 pass:``memory_item`` / ``user_workspace`` / ``tenant_user``。
* 历史欠账(image/artifact/approval pass 上线时漏授权):
  ``image_upload`` / ``artifact`` / ``artifact_version`` / ``agent_approval``。
  ``artifact`` / ``agent_approval`` 额外要 UPDATE(软删打标,非物理删除)。

角色本身及 schema USAGE 已由 0010 授予,这里只补表级 GRANT。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0131_retention_grants"
down_revision: str | Sequence[str] | None = "0130_trigger_user_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    for stmt in (
        # 本批新增 pass
        "GRANT SELECT, DELETE ON TABLE memory_item TO retention_cleanup_worker;",
        "GRANT SELECT, DELETE ON TABLE user_workspace TO retention_cleanup_worker;",
        "GRANT SELECT, DELETE ON TABLE tenant_user TO retention_cleanup_worker;",
        # 历史欠账(image/artifact/approval pass 上线时未授权)
        "GRANT SELECT, DELETE ON TABLE image_upload TO retention_cleanup_worker;",
        "GRANT SELECT, UPDATE, DELETE ON TABLE artifact TO retention_cleanup_worker;",
        "GRANT SELECT, DELETE ON TABLE artifact_version TO retention_cleanup_worker;",
        "GRANT SELECT, UPDATE ON TABLE agent_approval TO retention_cleanup_worker;",
    ):
        op.execute(stmt)


def downgrade() -> None:
    for stmt in (
        "REVOKE SELECT, UPDATE ON TABLE agent_approval FROM retention_cleanup_worker;",
        "REVOKE SELECT, DELETE ON TABLE artifact_version FROM retention_cleanup_worker;",
        "REVOKE SELECT, UPDATE, DELETE ON TABLE artifact FROM retention_cleanup_worker;",
        "REVOKE SELECT, DELETE ON TABLE image_upload FROM retention_cleanup_worker;",
        "REVOKE SELECT, DELETE ON TABLE tenant_user FROM retention_cleanup_worker;",
        "REVOKE SELECT, DELETE ON TABLE user_workspace FROM retention_cleanup_worker;",
        "REVOKE SELECT, DELETE ON TABLE memory_item FROM retention_cleanup_worker;",
    ):
        op.execute(stmt)
