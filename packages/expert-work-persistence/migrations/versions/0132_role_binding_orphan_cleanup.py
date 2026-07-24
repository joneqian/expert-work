"""role_binding 存量孤儿清理 —— 已撤销/停用成员遗留的授权行.

Revision ID: 0132_role_binding_orphan_cleanup
Revises: 0131_retention_grants
Create Date: 2026-07-24

删除接口卫生修复第 2 批 Task 4。成员从 tenant_member 撤销/停用
(``status`` 变为 ``revoked``/``suspended``)不会级联清理其
``role_binding`` 行 —— 这些行在成员 JWT 有效期内曾是活权限,停用后
本该一并清除,但历史上没有做,导致存量孤儿累积。

本迁移一次性 DELETE 这批孤儿(仅动态 join 到已撤销/停用成员的
tenant-scope、subject_type='user' 的绑定;platform_scope 绑定与仍
active 成员的绑定不受影响)。之后的常规撤销/停用应由业务层同步清理
(不在本迁移范围内)。

join 键:``role_binding.subject_id::text = tenant_member.keycloak_user_id``
—— **不是** ``tenant_member.subject_id``(那是首登回填的另一 UUID 列)。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0132_role_binding_orphan_cleanup"
down_revision: str | Sequence[str] | None = "0131_retention_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM role_binding rb
        USING tenant_member tm
        WHERE tm.tenant_id = rb.tenant_id
          AND tm.keycloak_user_id = rb.subject_id::text
          AND rb.subject_type = 'user'
          AND rb.platform_scope = false
          AND tm.status IN ('revoked', 'suspended')
        """
    )


def downgrade() -> None:
    # No-op by design: the deleted role_binding rows represented stale
    # authorization for revoked/suspended members and should not exist.
    # Recreating them would re-grant access that was already invalid at
    # the time of cleanup — irreversibility here is intentional.
    pass
