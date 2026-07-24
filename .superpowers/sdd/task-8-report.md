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

---

# 追加:T8 review 修复轮(I-1 + M-1/M-2/M-3)

## 状态:完成

## 分支

`fix-deletion-hygiene-pr2`(主工作树直接改,非 worktree)。

## I-1(顺序修复,主项)

**问题**:`mcp_catalog.py` 的 DELETE 端点里,`force` 级联(删 OAuth 连接行 + 其密文)跑在
`store.delete(catalog_id)` 的 `IntegrityError → McpConnectorCatalogInUseError` 映射**之前**。
重叠态下(目录既被某租户 `tenant_mcp_server` 实例化,又有其他租户/用户的 OAuth 连接)——
`?force=true` 会先把真实用户的 OAuth 连接行 + 密文销毁,然后才因为 `tenant_mcp_server` 的
RESTRICT 约束在 `store.delete` 里炸出 409。销毁发生在 `emit(audit, ...)` 之前,所以这次真实数据
销毁**零审计记录**。

**修法**:

- `TenantMcpServerStore` 新增 `count_for_catalog(*, catalog_id: UUID) -> int`——三处实现:
  - `base.py`:抽象方法 + docstring(照 `McpOAuthConnectionStore.count_for_catalog` 先例——
    platform-scope、跨租户、无 `tenant_id` 谓词,调用方须自带 bypass-RLS/superuser 会话)。
  - `sql.py`:`select(func.count()).select_from(TenantMcpServerRow).where(TenantMcpServerRow.catalog_id == catalog_id)`
    (新增 `func` import)。
  - `memory.py`:`sum(1 for r in self._rows.values() if r.catalog_id == catalog_id)`(锁内)。
  - 两实现谓词逐字节等价(`catalog_id ==` 单一条件),无 dedup/filter 分歧风险。
- `mcp_catalog.py` 的 `delete_catalog_entry`:
  - 新增 `_get_tenant_mcp_server_store` 依赖注入(照 `_get_oauth_store` 先例,读
    `request.app.state.tenant_mcp_server_store`),端点签名加
    `tenant_mcp_server_store: Annotated[TenantMcpServerStore, Depends(_get_tenant_mcp_server_store)]`。
  - 顺序改为:① 404 解析既有行(不变)→ **② 新增:`bypass_rls_session()` 内
    `tenant_mcp_server_store.count_for_catalog(catalog_id) > 0` → 立即 409 `CATALOG_IN_USE`
    (不看 `force`,不碰任何 OAuth 数据)** → ③ OAuth 连接计数 + 未 `force` 时 409
    `CATALOG_HAS_OAUTH_CONNECTIONS`(不变)→ ④ `force` 且有连接时才跑级联删连接/密文(不变,
    但此时已保证 tenant_mcp_server 未实例化,级联不会白做)→ ⑤ `store.delete()` 的
    `IntegrityError → McpConnectorCatalogInUseError` 映射保留原位,作**竞态兜底**(本次检查和
    真正 delete 之间理论上仍可能有并发实例化插入)。
  - 409 响应体沿用既有 `McpConnectorCatalogInUseError` 分支的同一 `code`/`message`
    (`CATALOG_IN_USE` / "catalog entry is instantiated by one or more tenants"),前端无需区分
    "新前置检查命中" vs "DB 兜底命中"。

## M-1(`list_for_catalog` 默认 limit=1000)

`force` 级联段的 `oauth_store.list_for_catalog(catalog_id=catalog_id)` 改传
`limit=max(connection_count, 1)`——`connection_count` 已在上一步 `count_for_catalog` 拿到,
`max(..., 1)` 避免 `limit=0`(理论上 `connection_count>0` 才会进这个分支,`max` 是防御性写法,
不依赖分支条件的隐式保证)。这样 >1000 连接的目录 force 删除时不会有密文孤儿或
`connections_removed`/`secrets_removed` 计数少算。

## M-2(0133 迁移 FK 查名加 schema 过滤)

`0133_mcp_oauth_fk_restrict.py` 的 `_FIND_FK_NAME_SQL` 加了一行
`AND tc.table_schema = current_schema()`——原查询虽然在 `kcu`/`ccu` 的 JOIN 条件上都带了
`tc.table_schema = kcu.table_schema` / `tc.table_schema = ccu.table_schema`,但对 `tc.table_schema`
本身没有过滤,多 schema(如同名表存在于另一个 schema)时可能查到错误 schema 的约束名。跑
`test_migrations_create_all_tables` 等既有迁移回归确认改动不破坏单 schema(默认 `public`)下的
正常路径。

## M-3(secrets_failed 分支变异测试)

照 `test_mcp_servers_api.py`(commit `788216c0`)的 `_DeleteAlwaysFailsSecretStore` 手法,在
`test_mcp_catalog_api.py` 新增同名 wrapper class(`get`/`put` 透传,`delete` 恒抛
`RuntimeError`),配 `test_delete_force_secrets_failed_branch_does_not_abort`:两条 OAuth 连接
(4 个密文引用)、`secret_store` 换成恒失败包装后 `?force=true` 删除 → 仍 204、
`oauth_store.count_for_catalog == 0`(连接行删除不受密文失败影响)、审计
`connections_removed == 2` / `secrets_removed == 0` / `secrets_failed == 4`。此前这个分支
(`except Exception: secrets_failed += 1`)只是"合理但从未被覆盖"的代码路径,现在有真实
mutation-killing 回归。

## 新增/改动测试清单

- `packages/expert-work-persistence/tests/test_in_memory_tenant_mcp_server_store.py`:
  `test_count_for_catalog_cross_tenant` / `test_count_for_catalog_empty`。
- `packages/expert-work-persistence/tests/test_sql_tenant_mcp_server_store.py`:新增
  `tenant_mcp_server_platform_scope` fixture(superuser DSN、无 RLS 包裹、无 APP_ROLE 改写——
  照 `test_sql_mcp_oauth_connection_store.py` 的 `sql_store` fixture 先例,platform-scope
  跨租户方法需要一个真能看见所有租户行的会话)+ `_make_catalog_entry` helper +
  `test_count_for_catalog_cross_tenant` / `test_count_for_catalog_empty`。
- `services/control-plane/tests/test_mcp_catalog_api.py`:
  - `test_delete_force_overlapping_instantiation_and_oauth_leaves_both_intact`(I-1 核心场景)——
    直插一行真实 `tenant_mcp_server`(经 `ctx.app.state.tenant_mcp_server_store.create(...)`,
    `catalog_id` 指向目标目录)+ 一条真实 OAuth 连接(`_seed_oauth_connection`),
    `?force=true` → 断言 409 `CATALOG_IN_USE`、OAuth 连接行仍在(`count_for_catalog == 1`)、
    两个密文仍可读取(`secret_store.get` 不抛)、审计里**没有**新增
    `mcp_catalog:delete` 记录(端点在 `emit()` 之前就返回了)。
  - `_DeleteAlwaysFailsSecretStore` class + `test_delete_force_secrets_failed_branch_does_not_abort`
    (M-3,见上)。

## 测试结果

```
export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock

uv run pytest services/control-plane/tests/test_mcp_catalog_api.py -q
→ 24 passed(22 既有 + 2 新增)

uv run pytest services/control-plane/tests/test_mcp_catalog_instantiation.py -q
→ 2 passed(回归)

uv run pytest packages/expert-work-persistence/tests/test_mcp_catalog_oauth_fk_restrict.py -q
→ 1 passed(回归)

uv run pytest services/control-plane/tests/test_mcp_catalog_api.py \
  services/control-plane/tests/test_mcp_catalog_instantiation.py \
  packages/expert-work-persistence/tests/test_mcp_catalog_oauth_fk_restrict.py -q
→ 27 passed

uv run pytest packages/expert-work-persistence/tests/test_in_memory_tenant_mcp_server_store.py \
  packages/expert-work-persistence/tests/test_sql_tenant_mcp_server_store.py -q
→ 32 passed(新 store 方法双实现测试,含既有回归)

uv run pytest packages/expert-work-persistence/tests -k "catalog or mcp_oauth or tenant_mcp_server" -q
→ 70 passed

uv run pytest services/control-plane/tests/test_mcp_catalog_api.py \
  services/control-plane/tests/test_mcp_catalog_instantiation.py \
  services/control-plane/tests/test_mcp_servers_api.py \
  services/control-plane/tests/test_mcp_oauth_api.py \
  services/control-plane/tests/test_mcp_oauth.py -q
→ 97 passed(mcp 相关面全量回归)

uv run pytest packages/expert-work-persistence/tests -q
→ 907 passed, 1 failed, 3 errors —— 失败/错误项(test_rls_detect.py 一例、
  test_pgbouncer_integration.py 三例)与本次改动前基线(上一轮报告记录的 903 passed / 同样
  1 failed 3 errors)一致,只是新增的 4 个 store 方法测试把总数从 903 抬到 907;失败项本身与
  catalog/oauth/tenant_mcp_server 无关,非本轮改动引入。

uv run ruff check services/control-plane packages/expert-work-persistence
→ All checks passed!

uv run ruff format --check services/control-plane packages/expert-work-persistence
→ 852 files already formatted(初次 mcp_catalog.py 需重排,ruff format 就地修正后复跑全绿)
```

## Concerns

1. **mypy**(未列入本轮验收命令,仅信息记录):`uv run mypy packages/expert-work-persistence
   services/control-plane/src` 报 81 处既有错误(`platform_config.py`/`runs.py`/`app.py` 等一堆
   与本次改动无关的既有告警,以及 `mcp_catalog.py`/`mcp_servers.py`/`mcp_oauth_api.py` 里一批
   `Unused "type: ignore" comment`)。用 `git stash` 交叉验证:`mcp_catalog.py` 的两处
   unused-ignore 在改动前就存在(行号从 70/74 平移到 75/79,内容是 `_get_catalog_store`/
   `_get_oauth_store` 既有的 `# type: ignore[no-any-return]`,不是本轮新增的
   `_get_tenant_mcp_server_store` 那一处)。照上一轮报告记录的先例,CI mypy 范围本就不含
   `services/control-plane`(只扫 `packages` + 几个 job 服务),这批告警与本轮改动无关,未修。
2. **I-1 修复后 `force` 的语义收紧**:`force=true` 现在只能级联清理 OAuth 连接,永远不能绕过
   `tenant_mcp_server` 实例化(修复前的实现在"重叠态"下技术上也做不到真正绕过——最终仍是 409——
   但会在拒绝前先销毁数据;修复后是"先判无实例化,才谈 force 级联"),这与 brief 的既定语义
   ("`force` 只级联清理 OAuth 连接,从不绕过 tenant_mcp_server 的 RESTRICT 闸")完全一致,只是本轮
   把"从不绕过"落实到了"从不哪怕短暂地销毁数据后再拒绝"。
