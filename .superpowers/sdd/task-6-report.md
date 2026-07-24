# Task 6 报告 —— grants 迁移 0131

## 做了什么

1. **worktree 对齐**:接手时该 worktree 的分支(`worktree-agent-af85c7b8c311801ca`)停在
   `69181a73`,并未如任务描述那样已切到 `fix-deletion-hygiene-pr1`。确认
   `69181a73` 是 `fix-deletion-hygiene-pr1`(尖端 `9cbd8e5c`)的祖先后,执行
   `git merge --ff-only fix-deletion-hygiene-pr1` 做纯快进合并(无冲突、无新提交),
   使本 worktree 拿到 Task 6 所需的迁移目录状态与 `.superpowers/sdd/task-6-brief.md`。

2. **新迁移** `packages/expert-work-persistence/migrations/versions/0131_retention_grants.py`:
   - `down_revision = "0130_trigger_user_scope"`(确认合并后 0130 仍是链尾)。
   - 模块结构(revision/down_revision/branch_labels/depends_on/`__all__`/docstring)照
     0010_retention_cleanup.py 和 0130_trigger_user_scope.py 先例。
   - `upgrade()` 逐表单语句 GRANT(与 brief 清单逐字一致):
     - 本批新增 pass:`memory_item`(SELECT, DELETE)、`user_workspace`(SELECT, DELETE)、
       `tenant_user`(SELECT, DELETE)。
     - 历史欠账:`image_upload`(SELECT, DELETE)、`artifact`(SELECT, UPDATE, DELETE)、
       `artifact_version`(SELECT, DELETE)、`agent_approval`(SELECT, UPDATE)。
   - `downgrade()` 对称 REVOKE,顺序与 upgrade 相反。
   - 角色本体 + `GRANT USAGE ON SCHEMA public` 已在 0010 授予,本迁移只补表级 GRANT,未重建角色。

3. **新测试** `packages/expert-work-persistence/tests/test_retention_grants.py`:
   - 先例来源:`grep -rn "has_table_privilege" packages/expert-work-persistence/tests` 命中
     `test_sql_app_user_role.py`(唯一先例),照其 `postgres_container` + `command.upgrade(cfg, "head")` +
     `has_table_privilege(...)` 断言模式写。
   - `@pytest.mark.parametrize("table_name", ["memory_item", "user_workspace", "tenant_user"])`,
     对每张表断言 `retention_cleanup_worker` 同时拿到 SELECT 和 DELETE(brief 明确要求这三张表)。
   - 历史欠账四张表(image_upload/artifact/artifact_version/agent_approval)未额外写参数化断言
     ——brief 原话只要求"针对 memory_item/user_workspace/tenant_user 的"一条,遵循"最小改动"未扩大范围;
     这四张表的授权正确性由 `upgrade()`/`downgrade()` 语句本身 + 全量迁移跑通(无 SQL 报错)覆盖。

## 验证

```
export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock

uv run pytest packages/expert-work-persistence/tests/test_retention_grants.py \
  packages/expert-work-persistence/tests/test_sql_app_user_role.py -x -q
# 5 passed(3 个新 grants 断言 + 2 个既有 app_user 角色测试,确认 0131 加入链后
# 全量 alembic upgrade head 仍跑通、downgrade 路径不受影响)

uv run pytest packages/expert-work-persistence/tests/test_rls_integration.py -x -q
# 9 passed(全量迁移到 head 后的既有 RLS 集成回归,进一步确认 0131 不破坏迁移链)

uv run ruff check packages/expert-work-persistence
# All checks passed!

uv run ruff format --check packages/expert-work-persistence
# 初次跑出 1 file would be reformatted(新测试文件的两行 SQL text() 换行不合 ruff 风格),
# 用 `uv run ruff format` 就地格式化后复跑 --check 全绿(450 files already formatted)。
```

## Concerns

- 无阻塞项。角色/schema USAGE 复用 0010 既有基建,未重复创建;GRANT 清单与 brief 逐字对齐。
- 历史欠账四张表只靠迁移执行不报错 + 既有集成测试跑通验证,没有像三张新表那样逐表写
  `has_table_privilege` 断言——如果后续要更强覆盖,可比照本文件的参数化模式把
  `image_upload`/`artifact`/`artifact_version`/`agent_approval` 也加进去(含 artifact 的 UPDATE、
  agent_approval 的 UPDATE 而非 DELETE)。当前按 brief 字面要求收敛,未做范围外扩展。
