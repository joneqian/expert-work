"""Integration: 0132 role_binding 存量孤儿清理 —— DELETE 谓词等价断言.

删除接口卫生修复第 2 批 Task 4:0132 一次性 DELETE 已撤销/停用成员遗留的
``role_binding`` 孤儿(join 键 ``rb.subject_id::text = tm.keycloak_user_id``)。

测试策略说明(为什么不照 ``test_x2_migration_safe_preexisting_tenant_skill``
那样"迁到中间版本 → 插数据 → upgrade head"写):``postgres_container`` 是
session 级共享容器,被仓库内其它 integration 测试文件复用,无法保证本文件
是第一个把容器迁到 head 的测试。0132 是"一次性数据清理"迁移而非纯 schema
变更 —— 若容器已被更早跑的测试迁到 head(0132 的 DELETE 已经在彼时的空
数据上执行过、不会重跑),本测试之后插入的孤儿数据就永远不会被清理,断言
会假性失败,写法本身是脆的。故照 task brief 指引的后备方案:先把容器全量
upgrade 到 head 保证 schema 就绪,手工插入孤儿数据,再直接执行 upgrade()
里的 DELETE SQL 语句本体(``_ORPHAN_CLEANUP_SQL``,与迁移文件的 SQL 逐字
一致)做等价断言。

变异自验(手工执行,未固化进 CI):把 ``_ORPHAN_CLEANUP_SQL`` 的 join 键从
``tm.keycloak_user_id = rb.subject_id::text`` 改成
``tm.subject_id::text = rb.subject_id::text`` 重跑 —— 测试变红(orphan 绑定
未被删除,因为 ``tenant_member.subject_id`` 在本测试数据里为 NULL,永不等于
任何 ``rb.subject_id::text``);改回后复绿。
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from testcontainers.postgres import PostgresContainer

pytestmark = pytest.mark.integration
ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

# Must stay byte-identical to the `upgrade()` body in
# `migrations/versions/0132_role_binding_orphan_cleanup.py`.
_ORPHAN_CLEANUP_SQL = """
    DELETE FROM role_binding rb
    USING tenant_member tm
    WHERE tm.tenant_id = rb.tenant_id
      AND tm.keycloak_user_id = rb.subject_id::text
      AND rb.subject_type = 'user'
      AND rb.platform_scope = false
      AND tm.status IN ('revoked', 'suspended')
"""


def _sync_dsn(c: PostgresContainer) -> str:
    url = str(c.get_connection_url())
    return url.replace("+psycopg2", "+psycopg").replace("postgresql://", "postgresql+psycopg://", 1)


def test_orphan_role_binding_removed_active_and_platform_scope_kept(
    postgres_container: PostgresContainer,
) -> None:
    """已撤销成员的 tenant-scope 绑定被删;active 成员 / platform_scope 绑定保留。"""
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")

    tenant_id = uuid4()
    revoked_subject = uuid4()
    active_subject = uuid4()
    orphan_binding_id = uuid4()
    active_binding_id = uuid4()
    platform_binding_id = uuid4()

    engine = create_engine(_sync_dsn(postgres_container), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            # Revoked member — owns the orphan binding.
            conn.execute(
                text(
                    "INSERT INTO tenant_member "
                    "(id, tenant_id, email, role, status, keycloak_user_id, invited_by) "
                    "VALUES (gen_random_uuid(), :tid, :email, 'operator', 'revoked',"
                    " :kc, 'test-setup')"
                ),
                {
                    "tid": tenant_id,
                    "email": f"revoked-{uuid4().hex[:8]}@example.com",
                    "kc": str(revoked_subject),
                },
            )
            # Active member — its binding must survive.
            conn.execute(
                text(
                    "INSERT INTO tenant_member "
                    "(id, tenant_id, email, role, status, keycloak_user_id, invited_by,"
                    " activated_at) "
                    "VALUES (gen_random_uuid(), :tid, :email, 'operator', 'active',"
                    " :kc, 'test-setup', now())"
                ),
                {
                    "tid": tenant_id,
                    "email": f"active-{uuid4().hex[:8]}@example.com",
                    "kc": str(active_subject),
                },
            )
            # Orphan: tenant-scope, subject_type=user, joins to the revoked member — deleted.
            conn.execute(
                text(
                    "INSERT INTO role_binding "
                    "(id, subject_type, subject_id, tenant_id, role, platform_scope,"
                    " granted_by, granted_at) "
                    "VALUES (:id, 'user', :sid, :tid, 'operator', false, 'test-setup', now())"
                ),
                {"id": orphan_binding_id, "sid": revoked_subject, "tid": tenant_id},
            )
            # Active member's binding — same shape, must NOT be deleted.
            conn.execute(
                text(
                    "INSERT INTO role_binding "
                    "(id, subject_type, subject_id, tenant_id, role, platform_scope,"
                    " granted_by, granted_at) "
                    "VALUES (:id, 'user', :sid, :tid, 'operator', false, 'test-setup', now())"
                ),
                {"id": active_binding_id, "sid": active_subject, "tid": tenant_id},
            )
            # Platform-scope binding for the SAME revoked subject — platform_scope=true
            # excludes it even though the person is revoked. tenant_id must be NULL
            # (CHECK constraint) — the join can never match a non-NULL tm.tenant_id.
            conn.execute(
                text(
                    "INSERT INTO role_binding "
                    "(id, subject_type, subject_id, tenant_id, role, platform_scope,"
                    " granted_by, granted_at) "
                    "VALUES (:id, 'user', :sid, NULL, 'system_admin', true, 'test-setup', now())"
                ),
                {"id": platform_binding_id, "sid": revoked_subject},
            )

            conn.execute(text(_ORPHAN_CLEANUP_SQL))

            remaining = {
                row[0]
                for row in conn.execute(
                    text("SELECT id FROM role_binding WHERE id = ANY(:ids)"),
                    {"ids": [orphan_binding_id, active_binding_id, platform_binding_id]},
                )
            }
    finally:
        engine.dispose()

    assert orphan_binding_id not in remaining, "orphan role_binding survived cleanup"
    assert active_binding_id in remaining, "active member's role_binding was wrongly deleted"
    assert platform_binding_id in remaining, "platform_scope role_binding was wrongly deleted"
