# Task 5 报告:TenantUserStore.hard_delete_deactivated

## 状态:完成

## 需求来源
`.superpowers/sdd/task-5-brief.md`(注:该目录被 `.superpowers/sdd/.gitignore` 忽略,不随分支同步;worktree 起初停在
`fix-deletion-hygiene-pr1` 的合并基点 `69181a73`,缺 Task 1 的提交与 brief 文件本身——先用
`git merge --ff-only fix-deletion-hygiene-pr1` 快进补齐,再开始本任务)。

## 实现

三处改动,均照抄 Task 1 `MemoryStore.hard_delete_expired`(`packages/expert-work-persistence/src/expert_work/persistence/memory/sql.py`)的子查询限批模式:

1. **`packages/expert-work-persistence/src/expert_work/persistence/tenant_user/base.py`**
   `deactivate` 之后新增抽象方法 `hard_delete_deactivated(self, *, before: datetime, limit: int = 1000) -> int`,docstring 说明跨租户(无 tenant 谓词)、按 `deleted_at` 升序限批、只删已停用行、复活(re-`resolve`)的行不受影响。

2. **`packages/expert-work-persistence/src/expert_work/persistence/tenant_user/sql.py`**
   `deactivate` 之后(原 L129 附近)实现:
   ```python
   async def hard_delete_deactivated(self, *, before: datetime, limit: int = 1000) -> int:
       subq = (
           select(TenantUserRow.id)
           .where(TenantUserRow.deleted_at.is_not(None), TenantUserRow.deleted_at < before)
           .order_by(TenantUserRow.deleted_at.asc())
           .limit(limit)
       )
       stmt = delete(TenantUserRow).where(TenantUserRow.id.in_(subq))
       async with self._sf() as session:
           result = await session.execute(stmt)
           await session.commit()
       return int(getattr(result, "rowcount", 0) or 0)
   ```
   顶部 `sqlalchemy` import 加 `delete`。

3. **`packages/expert-work-persistence/src/expert_work/persistence/tenant_user/memory.py`**
   同构的 in-memory 实现:按 `deleted_at` 排序取前 `limit` 条已停用行,从 `self._rows` 字典删除,返回删除数。

## 测试(TDD 红→绿)

先写 4 个失败测试(`AttributeError: no attribute 'hard_delete_deactivated'`),确认 RED,再实现转 GREEN。

**`packages/expert-work-persistence/tests/test_in_memory_tenant_user_store.py`**(+4 用例):
- `test_hard_delete_deactivated_only_reaps_old_deactivated` — 活跃行不动 / 停用满 100 天被删(`get` 返回 `None`)/ 停用仅 10 天保留
- `test_hard_delete_deactivated_respects_limit` — 两行都过期,`limit=1` 只删最旧一条(`deleted_at` 升序)
- `test_hard_delete_deactivated_sweeps_across_tenants` — 跨租户两行一次清 2(无 tenant 谓词验证)
- `test_hard_delete_deactivated_revival_not_swept` — **复活场景**:`deactivate(now=100天前)` 后再 `resolve()` 同 subject 清空 `deleted_at`,扫描返回 0、行仍在

**`packages/expert-work-persistence/tests/test_sql_tenant_user_store.py`**(+4 对应集成用例,针对真 Postgres):同上四个场景,直接用 `deactivate(..., now=<回拨时间>)` 参数回拨(该方法本就接受调用方传入的 `now`,无需像 Task 1 memory 测试那样另开连接 `UPDATE ... deleted_at`)。

## 验证结果

- `uv run pytest packages/expert-work-persistence/tests/test_in_memory_tenant_user_store.py` → 13 passed(含既有 9 + 新增 4)
- `DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock uv run pytest packages/expert-work-persistence/tests/test_sql_tenant_user_store.py` → 10 passed(含既有 6 + 新增 4)
- `uv run ruff check packages/expert-work-persistence` → All checks passed!
- `uv run ruff format --check packages/expert-work-persistence` → 448 files already formatted

## 改动文件清单(仅本任务)

- `packages/expert-work-persistence/src/expert_work/persistence/tenant_user/base.py`
- `packages/expert-work-persistence/src/expert_work/persistence/tenant_user/sql.py`
- `packages/expert-work-persistence/src/expert_work/persistence/tenant_user/memory.py`
- `packages/expert-work-persistence/tests/test_sql_tenant_user_store.py`
- `packages/expert-work-persistence/tests/test_in_memory_tenant_user_store.py`

## Concerns / 后续

- `TenantUserStore` 目前只有 `SqlTenantUserStore` / `InMemoryTenantUserStore` 两个实现(已 grep 确认无第三方子类),接口新增抽象方法不会破坏其他实现。
- 本任务不接入 retention-cleanup-job(Task 7 消费此方法),`hard_delete_deactivated` 目前无任何调用方——符合 brief 范围(仅地基)。
- 未跑 mypy(brief 未要求);ruff check/format 均绿。
