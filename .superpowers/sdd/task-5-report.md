# Task 5 报告:members revoke/suspend 接线删授权

## 状态:完成

## 需求来源
`/Users/mac/src/github/jone_qian/expert-work/.superpowers/sdd/task-5-brief.md`(唯一需求源)。

先执行 `git merge --ff-only fix-deletion-hygiene-pr2`(拿到波 1 的 `RoleBindingStore.delete_for_subject`,
merge tip `b630cf7a`)——fast-forward 成功,带入 T1-T4 的既有改动(role_binding orphan cleanup、
mcp_oauth_connection store、encrypted secret store 等)。

## 实现

**`services/control-plane/src/control_plane/api/members.py`**(`revoke` 端点,`DELETE /v1/members/{member_id}`):

1. handler 签名新增 `role_binding_repo: Annotated[RoleBindingStore, Depends(_get_role_binding_repo)]`
   —— dep getter `_get_role_binding_repo` 该文件已有(invite/resend 端点在用),直接复用。
2. 两分支(`invited→revoked` 删 KC 账号 / `active→suspended` 禁用账号)状态转移**成功后**(即两个
   `if/elif` 分支跑完、`action` 已确定,emit 审计**之前**),统一执行:
   ```python
   removed = 0
   if member.keycloak_user_id is not None:
       try:
           removed = await role_binding_repo.delete_for_subject(
               subject_type="user",
               subject_id=UUID(member.keycloak_user_id),
               tenant_id=principal.tenant_id,
           )
       except Exception:
           logger.warning("member_revoke.role_binding_cleanup_failed", exc_info=True)
   ```
   `keycloak_user_id is None` 直接跳过(`removed` 留 0),不炸;删除失败仅 warning,**不回滚**已提交的状态转移
   (与 brief 一致,和既有 KC 调用失败的 warning-only 风格对齐)。
3. 审计 `emit(...)` 的 `details` 增加 `role_bindings_removed: removed`(原有 `email` / `from_status` 不动)。

## 测试(TDD 红→绿)

`services/control-plane/tests/test_members_api.py`:

- 新增 `audit_store` fixture(`InMemoryAuditLogStore`),`admin_app` fixture 改为把
  `audit_logger=build_default_audit_logger(audit_store)` 传给 `create_app(...)`(照 `test_admin_api.py`
  的既有审计断言风格:`audit_store.query(AuditQuery(tenant_id=...))` → 按 `action` 过滤 → 断言 `.details[...]`)。
- 3 个新用例(布在 `test_revoke_missing_member_404` 之后):
  1. `test_revoke_invited_member_removes_role_binding` —— invite 后先用
     `role_binding_repo.list_for_subject(...)` 断言 binding 已建(invite 流程自带),revoke 后断言
     `list_for_subject` 为空,审计 `MEMBER_REVOKE` 一条、`role_bindings_removed == 1`。
  2. `test_suspend_active_member_removes_role_binding` —— invite 后用
     `tenant_member_repo.transition(..., to="active", ...)` 推进到 active(合法前驱,照
     `transitions.py` 的状态机),revoke(走 suspend 分支)后同样断言 binding 清空、
     `MEMBER_SUSPEND` 审计 `role_bindings_removed == 1`。
  3. `test_revoke_member_without_keycloak_user_id_skips_cleanup` —— 直插
     `tenant_member_repo.create(..., keycloak_user_id=None)`,revoke 不炸(200→204)、状态转 `revoked`、
     审计 `role_bindings_removed == 0`。
- RED 验证:临时 `git stash` 掉 `members.py` 的改动、只跑这 3 个新测试 → 3 个全部失败
  (`KeyError: 'role_bindings_removed'`),确认测试先于实现写、且真的在验证新行为;`git stash pop`
  恢复后转绿。

## 验证结果

- `uv run pytest services/control-plane/tests/test_members_api.py -q` → **18 passed**(既有 15 + 新增 3)
- `uv run ruff check services/control-plane` → All checks passed!
- `uv run ruff format --check services/control-plane` → 395 files already formatted

## 改动文件清单(仅本任务)

- `services/control-plane/src/control_plane/api/members.py`
- `services/control-plane/tests/test_members_api.py`

## Concerns / 后续

- 删除失败(`delete_for_subject` 抛异常)只 warning、不回滚状态转移——与 brief 要求及既有 KC 调用失败的
  处理风格一致,但意味着极端情况下(store 层异常)一次撤销后 binding 可能残留,需靠下一次撤销/人工
  role_bindings 管理页兜底;brief 范围内未要求补偿重试,未做。
- 未接触 `resend` 端点——resend 只对 `invited` 状态生效,不涉及撤销,binding 是"容忍重复"(`DuplicateRoleBindingError`
  吞掉),与本任务无交集。
- `admin_app` fixture 改动(新增 `audit_store` 参数、传入自定义 `audit_logger`)是本文件级别的改动,
  影响该文件全部 18 个测试的 app 构造路径,但只是把默认 in-memory audit store 换成显式可查询的同款
  store,行为等价,其余 15 个既有测试原样通过。

## Review follow-up(T5 三发现修复)

针对 T5 review 的 [I-1]/[可见性]/[M-1] 三条发现,在 `fix-deletion-hygiene-pr2` 分支主工作树直接修:

1. **[I-1] `moved` 门禁**:`role_binding_repo.delete_for_subject` 的调用条件从
   `if member.keycloak_user_id is not None:` 改为 `if moved and member.keycloak_user_id is not None:`,
   与同函数 `:307`/`:318` 两处 Keycloak 调用的门禁风格对齐——`moved` 是 `member_repo.transition(...)`
   的返回值,`False` 表示并发竞态下这次请求丢了(状态未真正转移,例如另一并发请求已先转移)。
   门禁前:即便 `moved=False`,依然会真删 role binding、并在审计里记一条与实际状态转移不符的
   “删除记录”(幽灵审计——binding 被删了,但这次请求并没有真的完成 revoke/suspend)。门禁后:未真正
   转移状态的请求不再动 role binding。
2. **[可见性] 审计失败标记**:`delete_for_subject` 的 `except Exception` 分支新增 `cleanup_failed = True`
   (原来只有 `logger.warning(...)`),审计 `emit(...)` 的 `details` 增加 `role_bindings_cleanup_failed: bool`
   (正常路径为 `False`)。理由:`revoked`/`suspended` 都是终态,没有针对 binding 清理失败的重跑路径,
   操作员必须能从审计页(而非翻应用日志)发现清理失败,并去 role_bindings 管理页手动补删。
3. **[M-1] 失败分支测试**:`test_members_api.py` 新增两例:
   - `test_revoke_role_binding_cleanup_failure_does_not_fail_request`——`monkeypatch` 把
     `app.state.role_binding_repo.delete_for_subject` 换成一个恒抛 `RuntimeError` 的协程,断言:
     revoke 仍 `204`(异常不外抛)、`tenant_member_repo` 里状态已转 `revoked`(不回滚)、审计
     `MEMBER_REVOKE` 一条,`role_bindings_removed == 0` 且 `role_bindings_cleanup_failed is True`。
   - `test_revoke_skips_cleanup_when_transition_loses_race`——`monkeypatch` 把
     `app.state.tenant_member_repo.transition` 换成恒返回 `False` 的协程(模拟并发竞态丢单),
     再给 `role_binding_repo.delete_for_subject` 挂一个仅记录“是否被调用”的桩,断言 revoke 仍
     `204`、且该桩从未被调用(`moved=False` 门禁生效,不误删 binding)。
   - 顺带给既有 `test_revoke_invited_member_removes_role_binding`(正常路径)追加一条断言
     `role_bindings_cleanup_failed is False`。

### 验证结果(review follow-up)

- `uv run pytest services/control-plane/tests/test_members_api.py -q` → **20 passed**(18 + 新增 2)
- `uv run ruff check services/control-plane` → All checks passed!
- `uv run ruff format --check services/control-plane` → 395 files already formatted

### 改动文件清单(review follow-up)

- `services/control-plane/src/control_plane/api/members.py`
- `services/control-plane/tests/test_members_api.py`
- `.superpowers/sdd/task-5-report.md`(本节)
