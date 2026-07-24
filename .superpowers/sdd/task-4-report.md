# Task 4 报告:迁移 0132 存量孤儿 role_binding 清理

## 状态:完成

## worktree / 分支

- worktree 路径:`/Users/mac/src/github/jone_qian/expert-work/.claude/worktrees/agent-a1230d7d38323ea69`
- 分支:`worktree-agent-a1230d7d38323ea69`(本地分支)
- 起手 `git merge --ff-only fix-deletion-hygiene-pr2` 成功(fast-forward,新增两份 PR2 计划/设计文档,无冲突)。

## 改动文件

- `packages/expert-work-persistence/migrations/versions/0132_role_binding_orphan_cleanup.py`(新建)
  - `down_revision = "0131_retention_grants"`(唯一 head,`alembic heads` 确认无分叉)。
  - `upgrade()`:brief §A3 原文逐字 DELETE SQL(join 键
    `rb.subject_id::text = tm.keycloak_user_id`,谓词 `subject_type='user'` +
    `platform_scope=false` + `status IN ('revoked','suspended')`)。
  - `downgrade()`:no-op(`pass`),docstring 说明删除的授权本不该存在、不可逆是设计意图,不重建。
  - 模块结构(`from __future__ import annotations` / `Sequence` 类型标注 / `__all__`)照 0131 先例。

## 测试新增

- `packages/expert-work-persistence/tests/test_role_binding_orphan_cleanup.py`(新建,
  `pytest.mark.integration`,真容器 + alembic)
  - **写法偏离 brief 给的第一选项,采用了 brief 明确认可的后备方案**,原因见文件顶部 docstring:
    `postgres_container` 是 session 级共享容器,被仓库内所有 integration 测试文件复用。0132 是"一次性
    数据清理"迁移而非纯 schema 变更 —— 若照 `test_x2_migration_safe_preexisting_tenant_skill` 的
    "迁到 0131 → 插孤儿数据 → upgrade head" 写法,一旦容器已被更早跑的测试文件先迁到 head(0132 的
    DELETE 早已在彼时的空数据上跑过、`command.upgrade` 对已应用版本是 no-op、不会重跑),之后插入的
    孤儿数据永远不会被清理,断言会因测试执行顺序而假性失败,是脆的写法。而 test_x2 那类先例之所以能
    在共享容器下稳定通过,是因为它验证的是"列默认值"(与迁移是否重跑无关),跟本场景不可比。
    故照 brief 授权的后备方案:先把容器全量 `upgrade(cfg, "head")`(幂等、保证 schema 就绪),手工插入
    孤儿数据,再直接执行 `_ORPHAN_CLEANUP_SQL`(与迁移文件 `upgrade()` 里的 SQL 逐字节一致)做等价断言。
  - 数据矩阵(按 brief Step 2):revoked 成员 + 其孤儿 tenant-scope binding(删)、active 成员的同形态
    binding(留)、同一 revoked 主体的 platform_scope binding(留,验证 `platform_scope=false` 谓词生效,
    虽然该场景下 CHECK 约束也顺带保证 tenant_id NULL 使 join 天然不命中——两道防线叠加,不可分离测试)。
  - 断言:`SELECT id FROM role_binding WHERE id = ANY(...)` 三个已知 id 里,孤儿 id 不在结果集,另两个在。

## 变异自验

把测试文件里的 `_ORPHAN_CLEANUP_SQL` join 键从 `tm.keycloak_user_id = rb.subject_id::text` 改成
`tm.subject_id::text = rb.subject_id::text`(即 brief 警告的错误列)→ 重跑测试 → **变红**(`1 failed`,
孤儿绑定未被删除,因为测试数据里 `tenant_member.subject_id` 恒为 NULL,`NULL::text` 永不等于任何
`rb.subject_id::text`)。改回后重跑 → **复绿**(`1 passed`)。

自验对象是测试里内嵌的 SQL 常量(与迁移文件的 SQL 逐字节相同),而非直接调用迁移模块的
`upgrade()`——因为选用的是后备写法(未走 alembic 版本重放),这点已在测试 docstring 里说明。

## 测试结果

```
export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock

uv run pytest packages/expert-work-persistence/tests/test_role_binding_orphan_cleanup.py -x -q
→ 1 passed

uv run pytest packages/expert-work-persistence/tests/test_retention_grants.py packages/expert-work-persistence/tests/test_role_binding_orphan_cleanup.py -q
→ 4 passed（0131 + 0132 两份 integration 测试同容器下均绿，交叉验证链尾无冲突）

cd packages/expert-work-persistence && uv run alembic heads
→ 0132_role_binding_orphan_cleanup (head)（单一 head，无分叉）

uv run ruff check packages/expert-work-persistence
→ All checks passed!

uv run ruff format --check packages/expert-work-persistence
→ 454 files already formatted
```

## Commit

- 待创建（本报告写完后随迁移文件 + 测试文件一并提交，`.superpowers/sdd/task-4-report.md` 用
  `git add -f` 强制加入，因该目录被 `.superpowers/sdd/.gitignore` 挡住 —— 照 task-3/5/6 报告先例）。

## Concerns

1. **共享 session 容器下的测试写法权衡**:如上"测试新增"一节所述，为规避跨测试文件执行顺序导致的假性
   失败，本测试选用了 brief 明确授权的"后备方案"（内嵌 SQL 副本 + 手工插入 + 直接执行），而非"迁到中间
   版本"的写法。代价是测试不直接调用迁移文件的 `upgrade()` 函数本体，理论上如果两处 SQL 文本出现漂移
   （有人改了迁移文件却忘了同步测试里的 `_ORPHAN_CLEANUP_SQL`），测试会继续验证"旧的"谓词而非"新的"。
   缓解手段：两处 SQL 逐字节相同（已核对），且迁移文件本身是一次性清理、上线后预期不再改动。如果团队
   更看重"测试即文档、不可能漂移"，可以考虑后续用 `alembic.runtime.migration.MigrationContext` +
   `alembic.operations.Operations.context()` 手动绑定 `op` 直接调用迁移模块的 `upgrade()`——但仓库测试
   套件里没有这个模式的先例，且 brief 已明确认可当前的后备写法，故未引入这一更复杂的新模式（避免不必要
   的抽象）。
2. **数据层面的存量影响未知**:本迁移会在生产库真实执行 DELETE，此前没有对存量数据量做统计/预估（不在
   本 task 范围内，brief 也未要求）。如果存量孤儿行数很大，上线时可能有短暂锁等待，建议上线前用只读
   `SELECT COUNT(*)`（同一 SQL 的计数版本）预估影响面。
