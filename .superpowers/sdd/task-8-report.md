# Task 8 报告(删除接口卫生 PR2):mcp_catalog 409+force 级联 + 0133 FK RESTRICT

## 状态:完成

## worktree / 分支

- worktree 路径:`/Users/mac/src/github/jone_qian/expert-work/.claude/worktrees/agent-a61c05d4224cbe93f`
- 分支:`worktree-agent-a61c05d4224cbe93f`(本地分支)
- 起手 `git merge --ff-only fix-deletion-hygiene-pr2`(tip `b630cf7a`)成功,fast-forward,拿到
  `SecretStore.delete`(T2)、`McpOAuthConnectionStore` 三方法 `count_for_catalog` /
  `list_for_catalog` / `delete_for_catalog`(T3,base/sql/memory 均已实现)、0132 迁移(T4)。

## 改动文件

- `services/control-plane/src/control_plane/api/mcp_catalog.py`(delete 端点)
  - 新增 `force: bool = False` query 参数 + `_get_oauth_store` 依赖注入(照 `_get_catalog_store`/
    `_get_secret_store` 先例,`request.app.state.mcp_oauth_connection_store`)。
  - 顺序:① 404 解析既有行(不变)→ ② `oauth_store.count_for_catalog` > 0 且未 `force` → 409
    `CATALOG_HAS_OAUTH_CONNECTIONS` + `count`(连接行/密文均未动)→ ③ `force` 且有连接:
    `list_for_catalog` 逐条对 `access_token_ref`/`refresh_token_ref` best-effort
    `secret_store.delete(parse_secret_ref(ref))`(计 `secrets_removed`/`secrets_failed`,失败
    `logger.warning` 不阻断)→ `delete_for_catalog` 批量删连接行(计 `connections_removed`)→
    ④ 既有 `store.delete(catalog_id)` try/except(`McpConnectorCatalogNotFoundError` → 404、
    `McpConnectorCatalogInUseError` → 409 `CATALOG_IN_USE`)不变,**位置仍在最后**——`force` 只级联
    清理 OAuth 连接,从不绕过 tenant_mcp_server 的 RESTRICT 闸(实例必须先删,即使带 `force`)。
  - 审计 `details` 新增 `connections_removed`/`secrets_removed`/`secrets_failed`(无级联时均为 0)。
- `packages/expert-work-persistence/migrations/versions/0133_mcp_oauth_fk_restrict.py`(新建,
  `down_revision = "0132_role_binding_orphan_cleanup"`)
  - `mcp_oauth_connection.catalog_id` FK 由 0063 inline `ON DELETE CASCADE` 改 `RESTRICT`。0063
    无显式约束名,**未赌 auto-name**——`upgrade()` 用 `information_schema.table_constraints` join
    `key_column_usage`/`constraint_column_usage` 动态查真实约束名再 `drop_constraint`,`create_foreign_key`
    显式命名 `mcp_oauth_connection_catalog_id_fkey`。`downgrade()` 对称改回 CASCADE(用本迁移的显式
    名,无需再查)。
  - **revision id 长度坑**:`alembic_version.version_num` 是 `varchar(32)`;最初取名
    `0133_mcp_oauth_catalog_fk_restrict`(34 字符)在真容器跑迁移时报
    `StringDataRightTruncation`——改短为 `0133_mcp_oauth_fk_restrict`(26 字符,文件名同步改名)。
    `0132_role_binding_orphan_cleanup` 恰好是 32(临界值),这是隐性长度闸,后续迁移命名要留意。
- `packages/expert-work-persistence/src/expert_work/persistence/mcp_connector_catalog/sql.py`
  (`delete` 方法,**非计划内但 Task 8 必需的 bug fix**——见下方"意外发现")。

## 意外发现:既有 `IntegrityError` 捕获位置错误(此前从未被真容器测试驱动过)

brief 假定"store 层 `delete(catalog_id)` 的 `IntegrityError → McpConnectorCatalogInUseError` 捕获
已有,改 RESTRICT 后自动兜 OAuth 残留"。写 FK 集成测试(Step 5 场景 ⑤)时发现这个假设不成立:

- 原代码只在 `await session.commit()` 外包 `try/except IntegrityError`,注释称"the constraint fires
  on commit (not on the DELETE statement)"。
- 实测(真 Postgres,`0133` 迁移后 `mcp_oauth_connection.catalog_id` 与既有
  `tenant_mcp_server.catalog_id` 都是非 DEFERRABLE 的 `RESTRICT`)：Postgres 对不可延迟的 FK 约束是
  **语句执行期**立即检查,不是等到 `COMMIT`。异常在 `await session.execute(stmt)` 这一行就抛出
  (`sqlalchemy.exc.IntegrityError` / `asyncpg.exceptions.ForeignKeyViolationError`),原 try/except
  完全没包住这一行,直接从 `store.delete()` 冒出未捕获的 `IntegrityError`。
- 全仓搜索确认:此前没有任何 SQL 层集成测试真正验证过"RESTRICT 命中 → `McpConnectorCatalogInUseError`"
  这条路径(`test_delete_in_use_409` 只用了一个手写 fake store 直接抛该异常,从未触达真实 FK)。这是一
  个此前存在、但从未被测试驱动过的潜伏 bug——不只影响本 Task 新增的 OAuth RESTRICT,**也一直影响既有的
  tenant_mcp_server RESTRICT**(那条闸在真容器下同样从未真正被 catch 住)。
- 修法(`mcp_connector_catalog/sql.py::delete`):把 `session.execute(stmt)` 也纳入同一
  `try/except IntegrityError`(与 `commit()` 分成两段各自 try,`execute` 段是主捕获点,`commit` 段留作
  未来若某个 FK 被声明 `DEFERRABLE INITIALLY DEFERRED` 时的兜底)。这是 Task 8 场景 ⑤ 测试能通过的必要
  前提,已在报告的验证部分交叉验证(既有 `test_delete_in_use_409` 仍绿,证明修复未改变 fake-store 路径
  行为)。

## 测试新增

- `services/control-plane/tests/test_mcp_catalog_api.py` 追加 4 个测试(+1 个新增 helper
  `_seed_oauth_connection`,直接调用 in-memory `mcp_oauth_connection_store`/`secret_store` 造
  `connected` 状态连接,绕过 OAuth 授权 HTTP 回调流程):
  1. `test_delete_no_oauth_connections_deletes_normally` —— 无连接 204(与既有
     `test_create_get_list_patch_delete` 里的裸删场景互补,单独显式覆盖 brief 场景 ①)。
  2. `test_delete_with_oauth_connections_409_without_force` —— 两租户各一条连接,不带 force → 409
     `CATALOG_HAS_OAUTH_CONNECTIONS` + `count == 2`;连接行 + 目录行均未被动(`count_for_catalog`
     仍 2、GET 目录仍 200)。
  3. `test_delete_force_cascades_connections_and_secrets` —— `?force=true`:204;
     `count_for_catalog == 0`;4 个密文(2 连接 × access+refresh)全部 `SecretNotFoundError`;审计
     `details` 三计数 `connections_removed=2`/`secrets_removed=4`/`secrets_failed=0`。
  4. `test_delete_tenant_mcp_server_in_use_blocks_even_with_force` —— 沿用既有
     `test_delete_in_use_409` 的 fake-store 手法,`?force=true` 仍被 409 `CATALOG_IN_USE` 拦下
     (0 个 OAuth 连接场景下,验证 force 不绕过 tenant_mcp_server 闸)。
- `packages/expert-work-persistence/tests/test_mcp_catalog_oauth_fk_restrict.py`(新建,
  `pytest.mark.integration`,真容器 + alembic 到 head):直调
  `SqlMcpConnectorCatalogStore.delete()`(绕过 app 层闸),oauth 连接仍在 → 断言抛
  `McpConnectorCatalogInUseError`、目录行存活;删掉阻塞连接后重试 → 成功、目录行消失。
- `test_mcp_catalog_instantiation.py`:未改动,作为回归跑(未使用真实
  `mcp_oauth_connection_store`,不受影响)。

## 测试结果

```
export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock

uv run pytest services/control-plane/tests/test_mcp_catalog_api.py -q
→ 22 passed(18 既有 + 4 新增)

uv run pytest services/control-plane/tests/test_mcp_catalog_instantiation.py -q
→ 2 passed(回归)

uv run pytest packages/expert-work-persistence/tests/test_mcp_catalog_oauth_fk_restrict.py -q
→ 1 passed

uv run pytest packages/expert-work-persistence/tests -k "catalog or mcp_oauth or role_binding" -q
→ 69 passed(store 双实现平价 + FK 集成 + 既有回归)

uv run pytest packages/expert-work-persistence/tests -q
→ 903 passed, 1 failed, 3 errors —— 失败/错误项(test_rls_detect.py 一例、
  test_pgbouncer_integration.py 三例)经 git stash 交叉验证,在合并 Task 8 改动之前(即 main +
  fix-deletion-hygiene-pr2 基线)就已存在,与本 task 无关(pgbouncer 容器环境缺失 + 一个与
  RLS-detect 相关的既有 flaky 用例,均非 catalog/oauth 相关文件)。

uv run pytest services/control-plane/tests/test_mcp_catalog_api.py \
  services/control-plane/tests/test_mcp_catalog_instantiation.py \
  services/control-plane/tests/test_mcp_servers_api.py \
  services/control-plane/tests/test_mcp_oauth_api.py \
  services/control-plane/tests/test_mcp_oauth.py -q
→ 93 passed(mcp 相关面回归全绿;mcp_oauth_api.py 本身未改动,属 Task 7 范围)

uv run ruff check services/control-plane packages/expert-work-persistence
→ All checks passed!

uv run ruff format --check services/control-plane packages/expert-work-persistence
→ 852 files already formatted(初次 1 个新测试文件需重排,ruff format 就地修正后复跑全绿)

uv run mypy packages services/audit-backup-worker/src services/billing-rollup-job/src \
  services/event-log-archive-job/src services/orchestrator/src services/retention-cleanup-job/src
→ Success: no issues found in 777 source files
```

## Concerns

1. **`mcp_connector_catalog/sql.py::delete` 的 bug fix 超出 brief 字面范围**,但没有它 Task 8 的
   FK-RESTRICT 兜底(场景 ⑤,以及未来若 app 层闸被绕过时的真实 500→409 语义)完全不生效——`store.delete`
   会把裸 `IntegrityError` 泄漏给调用方(端点里只 catch `McpConnectorCatalogInUseError`,泄漏会变成未
   处理异常 500,而不是 409)。判断这是 Task 8 目标("DB 级兜底")必需的修复,不是范围蔓延;改动只加宽了
   已有 try/except 的覆盖面,未改变异常类型/契约。
2. **secrets_failed 未被真正触发过**:`LocalDevSecretStore.delete` 是幂等 pop,不会抛异常,测试环境下
   `secrets_failed` 恒为 0(与 brief "计 removed/failed" 的 best-effort 语义一致,但矩阵测试没有覆盖
   "某个密文删除失败"分支)。生产 KMS 后端理论上可能失败,该分支代码路径合理但未被集成测试覆盖到——与
   `mcp_oauth_api.py` disconnect 的既有 best-effort 覆写分支同样未覆盖失败路径,是本仓库既有取舍,非本
   task 引入的新缺口。
3. **task-8-report.md 文件名冲突**:本 worktree 起手时该路径已存在 PR1(#1048)遗留的"Task 8:purge_user
   补 blob/feedback/summary"报告(与本 PR2 Task 8 无关,PR1/PR2 各自独立编号 1-8/1-9,文件名恰好撞车)。
   已按指示直接覆盖为本报告;PR1 内容已在 git 历史(commit `621c53f9`)中可查,未丢失。
