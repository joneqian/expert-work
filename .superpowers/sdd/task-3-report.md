# Task 3 报告(PR2)—— McpOAuthConnectionStore 按目录三方法

## 状态
DONE

## worktree / 分支
- worktree 路径:`/Users/mac/src/github/jone_qian/expert-work/.claude/worktrees/agent-a0263004833995af1`
- 分支:`worktree-agent-a0263004833995af1`
- 起手 `git merge --ff-only fix-deletion-hygiene-pr2`(fast-forward,`621c53f9..bef52752`),带入 PR2 的设计/计划文档,工作树无改动被丢弃。

**已知细节**:本 worktree 的 `.superpowers/sdd/` 目录下已有 `task-2/5/6/7/8-report.md`,`git log` 确认这些文件全部来自已合并的 `621c53f9`(**PR1** "删除接口卫生 PR1"的报告,历史遗留),不是本 PR2 sibling 任务的产物。`task-3-report.md`(本文件)同样是 PR1 遗留(内容是 `ImageUploadStore.list_expired → list_reapable`,与本任务无关)。按编排指令原样覆盖为 PR2 Task 3 的报告——PR1 版本仍完整保留在 git 历史的 `621c53f9` 提交里,不受影响。

## 需求源
`/Users/mac/src/github/jone_qian/expert-work/.superpowers/sdd/task-3-brief.md`(主仓库,唯一需求源)。

## 改动文件

- `packages/expert-work-persistence/src/expert_work/persistence/mcp_oauth_connection/base.py`
  - 新增 3 个抽象方法:`count_for_catalog(*, catalog_id) -> int`、`list_for_catalog(*, catalog_id, limit=1000) -> list[McpOAuthConnectionRecord]`、`delete_for_catalog(*, catalog_id) -> int`。
  - docstring 明确标注 "Platform-scope caller only"——**无先例**(全表扫描 grep 未发现该 store 内既有跨租户 bypass-RLS 方法),按 brief 指示走 docstring 注明路线,并指向 `agent_spec/sql.py::list_all_tenants` 作为同库其他 store 里"无 tenant_id 谓词 + 需 platform-scope 调用方"的既有先例写法。
- `packages/expert-work-persistence/src/expert_work/persistence/mcp_oauth_connection/memory.py`
  - 三方法均以 `catalog_id` 过滤 `self._rows`(不带 tenant_id/user_id 谓词,天然跨租户);`list_for_catalog` 按 `created_at` 升序 + `limit` 截断;`delete_for_catalog` 复用既有 `delete_all_for_user` 的"收集受害 id → 逐个 del"模式。
- `packages/expert-work-persistence/src/expert_work/persistence/mcp_oauth_connection/sql.py`
  - `sqlalchemy` import 加 `func`。
  - 三方法均**不带 tenant_id WHERE**(平台治理视角,跨租户):`count_for_catalog` 用 `select(func.count()).select_from(...).where(catalog_id==...)`;`list_for_catalog` 用 `select(...).where(catalog_id==...).order_by(created_at).limit(...)`;`delete_for_catalog` 用裸 `sa_delete(...).where(catalog_id==...)` + `result.rowcount`。谓词与 in-memory 版本逐字节等价(同一 `catalog_id ==` 单条件)。

### RLS 调查结论(为何选 docstring 注明而非造 bypass session)
- `mcp_oauth_connection` 表(migration `0063_mcp_oauth_connection`)`FORCE ROW LEVEL SECURITY` + 策略是**严格等值**(`tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid`,非 `IS NOT DISTINCT FROM`),这与 `mcp_connector_catalog`(NULL-tenant 平台表,策略允许 NULL 匹配)不同,没法靠"不设租户上下文"天然放行。
- 全库 grep `bypass_rls_var` 在本 store 内**零命中**;唯一同类先例是 `agent_spec/sql.py::list_all_tenants`(同样无 tenant_id 谓词,注释写"caller MUST have bypass_rls_var=True or RLS filters everything out"),本次三方法照其写法加了等价 docstring/注释。
- 结合既有记忆 `rls-inert-runtime-superuser`:运行期 DB 连接本身是 Postgres 超级用户(`rolsuper=t, rolbypassrls=t`),`FORCE ROW LEVEL SECURITY` 对超级用户无条件失效——生产路径上这三个方法天然会跨租户可见,不需要额外的 bypass session 包装。真正的"platform-scope caller only"契约靠调用方(Task 8 的目录删除守卫,system_admin-only 端点)保证,不靠 SQL 层。

## 测试改动 / 新增

- `packages/expert-work-persistence/tests/test_memory_mcp_oauth_connection_store.py`
  - 新增 `test_count_list_delete_for_catalog_cross_tenant`:两租户各挂 1 条 catalogA + 1 条 catalogB;`count(A)==2`;`list(A)` 按创建顺序返回两条且 `tenant_id` 集合覆盖两租户;`delete(A)==2` 后两条 A 记录均不可 `get`,同时 `count(B)==2`/`list(B)` 两条不受影响。
  - 新增 `test_count_list_delete_for_catalog_empty`:空 catalog 三方法分别返回 `0`/`[]`/`0`。
- `packages/expert-work-persistence/tests/test_sql_mcp_oauth_connection_store.py`(新建,该 store 此前无 SQL 集成测试文件)
  - fixture 风格照抄 `test_sql_agent_spec_store.py`:session factory 直连 testcontainers 超级用户 DSN(不重写到 `APP_ROLE`、不套 `build_rls_sessionmaker`),对应"该表既有 SQL 测试的会话 fixture"先例(该 store 此前无 SQL 测试,故取同库风格最接近的 `agent_spec` 先例,也是 brief 指定的 fallback 路径)。
  - `mcp_oauth_connection.catalog_id` 对 `mcp_connector_catalog.id` 有 FK(`ondelete=CASCADE`),测试内先用 `SqlMcpConnectorCatalogStore.create()` 建两条真实 catalog 行满足 FK,再各插入跨租户连接行。
  - `test_count_list_delete_for_catalog_cross_tenant` / `test_count_list_delete_for_catalog_empty`:与 in-memory 版本同一场景,验证 SQL 谓词与 in-memory 谓词行为一致(真 Postgres)。

## 测试结果

```
uv run pytest packages/expert-work-persistence/tests/test_memory_mcp_oauth_connection_store.py -q
→ 10 passed

export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock
uv run pytest packages/expert-work-persistence/tests/test_sql_mcp_oauth_connection_store.py -m integration -q
→ 2 passed

uv run pytest packages/expert-work-persistence/tests/test_memory_mcp_oauth_connection_store.py \
  packages/expert-work-persistence/tests/test_sql_mcp_oauth_connection_store.py \
  packages/expert-work-persistence/tests/test_sql_mcp_connector_catalog_store.py -q
→ 19 passed（三文件混跑无跨测试污染）

uv run ruff check packages/expert-work-persistence
→ All checks passed!

uv run ruff format --check packages/expert-work-persistence
→ 453 files already formatted
```

## TDD 记录
先写 in-memory 测试矩阵（两条正例 + 一条空 catalog）并跑绿——实现（base/memory/sql 三方法）是在同一轮内一并写就后运行验证的，未额外插入人为 RED 步骤截图；跑测试前手动核对过若漏掉任一方法会触发 `AttributeError`（三个 store 类均为具体子类,少一个抽象方法实现会在类定义时因未满足 `abc.ABC` 直接报 `TypeError: Can't instantiate abstract class`,已用该失败模式替代显式先跑红）。SQL 侧因需真容器,先建表 fixture + FK 满足逻辑,再跑绿确认。

## 自审
- `git diff` 过了 base/memory/sql 三个文件的全部改动,新增方法均未触碰既有 8 个方法的实现。
- 三方法签名与 brief「Produces」条目逐字节一致（`count_for_catalog(*, catalog_id: UUID) -> int` / `list_for_catalog(*, catalog_id: UUID, limit: int = 1000) -> list[McpOAuthConnectionRecord]` / `delete_for_catalog(*, catalog_id: UUID) -> int`）。
- grep 确认 `McpOAuthConnectionStore` 仅有 `InMemoryMcpOAuthConnectionStore` / `SqlMcpOAuthConnectionStore` 两个子类,均已补齐三方法,无第三方实现遗漏。
- SQL 与 in-memory 谓词均为单一 `catalog_id ==` 相等比较,无额外隐藏过滤条件,逐字节等价。
- `list_for_catalog` 排序键（`created_at` 升序）SQL/in-memory 一致；`limit` 语义一致（SQL `.limit()`、in-memory `[:limit]`）。

## Commit
（见最终回复；只 `git add` 本任务改动的 5 个文件 + 本报告，均显式路径加入,未用 `-A`/`.`）

## Concerns
1. **RLS 无 bypass session 先例**:本 store 内没有任何既有跨租户方法可抄"真 RLS bypass"写法,选择了 brief 明确允许的 fallback（docstring 注明 + 超级用户会话测试）。如果未来 `rls-project-parked-phase0` 那条 backlog（启用非超级用户 `app_user` 角色 + 真 `SET LOCAL ROLE`）落地,这三个方法在生产环境会立刻从"能看见跨租户行"退化为"看不见任何行"（严格等值策略、无 tenant GUC → 全部拒绝）,到时需要补上真正的 `bypass_rls_var=True` 或等价的 BYPASSRLS 会话包装。这是全库共性问题（见 `agent_spec.list_all_tenants` 同款风险),不是本任务独有的新洞,未在本任务范围内处理。
2. **`test_sql_mcp_oauth_connection_store.py` 为该 store 新建的首个 SQL 集成测试文件**,只覆盖本任务新增的三方法,未补齐既有 8 个方法（create/get/...）的 SQL 集成测试——不在 brief 范围内,如后续想要更完整的 SQL 层回归覆盖是一处 follow-up。
3. Task 8（目录删除守卫,本三方法的消费方）尚未接入,brief 明确本任务只交付 store 方法本身。
