# Task 1 报告 — RoleBindingStore.delete_for_subject

## 状态
DONE

## 改动文件
- `packages/expert-work-persistence/src/expert_work/persistence/auth/base.py`
  - `RoleBindingStore` 抽象类在 `delete` 之后新增 `delete_for_subject(*, subject_type: str, subject_id: UUID, tenant_id: UUID) -> int` 抽象方法,docstring 说明只删非 platform-scope 行 + 幂等语义 + T5 消费方。
- `packages/expert-work-persistence/src/expert_work/persistence/auth/sql.py`
  - `SqlRoleBindingStore.delete_for_subject`:与 brief 给定实现逐字节一致 —— `delete(RoleBindingRow).where(subject_type ==, subject_id ==, tenant_id ==, platform_scope.is_(False))`,单会话 `execute` + `commit`,返回 `int(getattr(result, "rowcount", 0) or 0)`。
- `packages/expert-work-persistence/src/expert_work/persistence/auth/memory.py`
  - `InMemoryRoleBindingStore.delete_for_subject`:同一把 `self._lock` 下先收集匹配 `subject_type`/`subject_id`/`tenant_id` 相等 + `not row.platform_scope` 的行 id,再逐个 `del self._rows[id]`,返回删除计数——谓词与 SQL 侧逐字节对应(四个条件、顺序一致)。
- `packages/expert-work-persistence/tests/test_sql_auth_store.py`
  - 新增 `test_role_binding_delete_for_subject`(真容器):同 subject 两行(ADMIN/OPERATOR,同租户)+ 异租户同 subject 一行(VIEWER)+ 同租户 platform_scope=true 一行(SYSTEM_ADMIN)。调用后返回 2;`list_for_subject` 断言剩余集合恰为 {异租户行, platform_scope 行};再调返回 0(幂等)。
- `packages/expert-work-persistence/tests/test_role_binding_platform_scope.py`
  - 新增 `test_inmem_delete_for_subject`,同一场景 + 同一断言,跑在 `InMemoryRoleBindingStore` 上(双实现同断言,按 brief 要求)。

## 偏离及理由
无。测试矩阵、SQL 实现代码块、抽象方法签名均与 brief 逐字节/逐语义对齐。测试文件选点:该仓库对 auth 的 in-memory 测试历来独立成 `test_role_binding_platform_scope.py`(而非与 SQL 测试同文件参数化),故新测试加进该既有文件而非新建文件,遵循先例。

## 测试证据
```
$ uv run pytest packages/expert-work-persistence/tests/test_role_binding_platform_scope.py -k delete_for_subject -x
# 实现前:AttributeError: 'InMemoryRoleBindingStore' object has no attribute 'delete_for_subject' — 确认真红

$ export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock
$ uv run pytest packages/expert-work-persistence/tests/test_sql_auth_store.py -k delete_for_subject -x
# 实现前同样真红(AttributeError)

# 实现后:
$ uv run pytest packages/expert-work-persistence/tests/test_role_binding_platform_scope.py packages/expert-work-persistence/tests/test_sql_auth_store.py -v
======================= 38 passed, 18 warnings in 3.79s ========================

$ uv run pytest packages/expert-work-persistence -q -m "not integration"
596 passed, 305 deselected in 2.73s

$ uv run ruff check packages/expert-work-persistence
All checks passed!

$ uv run ruff format --check packages/expert-work-persistence
452 files already formatted

$ uv run mypy packages/expert-work-persistence
Success: no issues found in 452 source files
```

## 变异自验（含一处真实发现，非阻塞）
按 brief 要求,临时去掉两侧实现的 `platform_scope IS FALSE` 谓词(SQL 删掉 `.is_(False)` 子句、in-memory 删掉 `and not row.platform_scope`),重跑 `-k delete_for_subject`。

**结果:两个测试仍然全绿,没有变红。** 深挖原因:`RoleBinding` 的 DTO validator + DB CHECK 约束
(`role_binding_scope_triple_ck`,test_dto_platform_scope_with_tenant_id_rejected /
test_db_check_rejects_platform_scope_with_wrong_role 已锁死)强制 `platform_scope=True ⟹ tenant_id IS NULL`。
`delete_for_subject` 的 `tenant_id` 参数是必填的具体 `UUID`(非 `UUID | None`),而 platform-scope 行的
`tenant_id` 恒为 `NULL`——`tenant_id == <某租户 UUID>` 这一个谓词本身就已经把所有合法的 platform-scope 行排除在外,
`platform_scope IS FALSE` 谓词对任何满足 schema 不变式的数据都是逻辑冗余(SQL 与 in-memory 两侧皆然)。
这与既有 `list_for_tenant` 的注释("Excludes platform-scope rows (their tenant_id is NULL anyway, but be explicit
for readability)")是同一套"防御性显式谓词"风格——我保留了该谓词(严格照 brief/spec 硬编码要求),因为:
1) spec 明确要求"严禁删",是有意的纵深防御,防止未来 schema 不变式被破坏或此方法被复用到别处；
2) 与仓库既有同类谓词(`list_for_tenant`)的写法一致,不引入不一致风格。
把这一发现记为 concern 而非阻塞项——两个测试文件本身的断言(platform_scope 行在 `list_for_subject` 里原样保留)
是正确且有效的回归锁,只是这条特定谓词在当前 schema 不变式下无法被单元测试的"删谓词"变异揪出来,这是数据模型
本身的性质,不是测试写法的缺陷。恢复原谓词后重跑,38/38 全绿。

## 自审
- `git diff` 过了 base.py/sql.py/memory.py 三处改动,均为新增方法追加在文件末尾/`delete` 之后,未触碰既有方法。
- SQL 实现与 brief 给定代码块逐字节一致(变量名、谓词顺序、`getattr(result, "rowcount", 0) or 0` 写法全同)。
- grep 确认全仓 `RoleBindingStore` 只有 `SqlRoleBindingStore`/`InMemoryRoleBindingStore` 两个子类,无第三方实现遗漏新抽象方法。
- SQL 与 in-memory 谓词四条逐一对应:`subject_type ==` / `subject_id ==` / `tenant_id ==` / `platform_scope is False`,顺序、语义一致。
- 两次调用幂等性(第二次返回 0)在双实现测试中均已覆盖。

## Concerns
- 见上方"变异自验"小节:`platform_scope IS FALSE` 谓词在当前 schema 不变式(platform_scope ⟺ tenant_id IS NULL,
  DB CHECK 强制)下对本方法而言是逻辑冗余,常规变异测试无法证伪它。已按 spec 原样保留(防御性/风格一致性考量),
  仅记录该结构性事实供后续任务(T5 等)或代码评审知悉,不影响本任务功能正确性。
