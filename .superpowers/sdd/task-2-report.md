# Task 2 报告 — FeedbackStore.delete_for_threads

## 状态
DONE

## 改动文件
- `packages/expert-work-persistence/src/expert_work/persistence/feedback_store.py`
  - `sqlalchemy` import 加 `delete`。
  - 抽象类 `FeedbackStore` 新增 `delete_for_threads(*, tenant_id: UUID, thread_ids: Sequence[UUID]) -> int` 抽象方法(docstring 说明 Task 8 purge_user 消费 + 空列表短路语义)。
  - `InMemoryFeedbackStore.delete_for_threads`:`wanted = set(thread_ids)`,过滤保留 `not (r.tenant_id == tenant_id and r.thread_id in wanted)`,用 before/after 行数差返回删除计数;空列表提前返回 0。
  - `DbFeedbackStore.delete_for_threads`:与 brief 给定实现逐字节一致 —— 500 一片分批 `DELETE ... WHERE tenant_id == :t AND thread_id IN (:chunk)`,累加各批 `result.rowcount`,末尾统一 `commit()`;空列表提前返回 0。

- `packages/expert-work-persistence/tests/test_feedback_store_delete.py`(新建,`ls tests | grep -i feedback` 未命中既有 store 级测试文件,按 brief 指定文件名新建):
  - 共享 `_seed()` 助手:插 t1/threadA×2、t1/threadB×1、t2/threadA×1(异租户同 thread_id)。
  - InMemory 两个用例:`delete_for_threads(tenant_id=t1, thread_ids=[threadA])` 返回 2 + threadB(t1)与 threadA(t2 行)均仍可 `list_for_thread` 查到;空列表返回 0。
  - Db(Postgres testcontainers)侧同一场景两个用例,`sql_store` fixture 照搬 `test_sql_memory_store.py` 的容器 fixture 风格(`postgres_container` 会话级 fixture + alembic upgrade head + `create_async_engine_from_config`/`create_async_session_factory`,非 RLS-role 版本 —— 因为 `delete_for_threads` 的租户隔离由显式 `WHERE tenant_id ==` 断言,不依赖 RLS 策略,故不需要 `test_rls_integration.py` 那套非超级用户角色配置的重基建)。

## 偏离及理由
1. **除 brief Step1 描述的 InMemory 场景外,额外加了 Postgres 集成测试(2 个用例)**,覆盖同一场景跑在真实 `DbFeedbackStore` 上。理由:brief 给出的 Db 实现含分批 `DELETE...IN` + `rowcount` 累加逻辑,是本任务的新增复杂度来源,InMemory 测试完全测不到(SQLAlchemy `rowcount`/异步引擎实际行为需要真容器验证);且同一 PR1 的 Task 1(`MemoryStore.hard_delete_expired`)已在姊妹文件里同时加了 in-memory + `test_sql_memory_store.py` 两侧测试,视为本轮"硬删类方法双测"的既定先例。未新造容器基建,复用根 `conftest.py` 的 `postgres_container` 会话级 fixture,写法照抄 `test_sql_memory_store.py`。
2. **发现但未使用 `test_rls_integration.py` 里既有的 `feedback_rls_store` fixture**(它已经在跑 `DbFeedbackStore`,但走的是非超级用户 RLS 角色 + 全套 APP_ROLE 授权配置)。未复用理由:`delete_for_threads` 的隔离靠显式 SQL `WHERE tenant_id ==`,与 RLS 策略无关,不需要那套更重的角色基建;用 `test_sql_memory_store.py` 的轻量容器风格(超级用户连接,RLS 天然旁路但查询本身已过滤)已足够验证该方法的真实行为,且更贴合 brief「不新造基建、照 test_sql_* 系列文件的容器 fixture 风格写」的指示。

## 测试证据
```
$ uv run pytest packages/expert-work-persistence/tests/test_feedback_store_delete.py -k in_memory -q
..                                                                       [100%]
2 passed, 2 deselected in 0.47s

$ export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock
$ uv run pytest packages/expert-work-persistence/tests/test_feedback_store_delete.py -q
....                                                                     [100%]
4 passed, 2 warnings in 9.89s   # (第二次热跑 3.10s,warning 为 alembic path_separator 弃用提示,与本改动无关)

$ uv run ruff check packages/expert-work-persistence
All checks passed!            # 首轮曾报 2 处 RUF002(docstring `×` 歧义符),已改成 ASCII `x` 后清零

$ uv run ruff format --check packages/expert-work-persistence
449 files already formatted

$ uv run mypy packages/expert-work-persistence/src/expert_work/persistence/feedback_store.py
Success: no issues found in 1 source file

$ uv run pytest packages/expert-work-persistence/tests -k feedback -m "not integration" -q
2 passed, 865 deselected in 2.18s
```

TDD 红→绿:实现前跑同一测试文件,`test_in_memory_delete_for_threads_scopes_by_tenant_and_thread` 以
`AttributeError: 'InMemoryFeedbackStore' object has no attribute 'delete_for_threads'` 失败,确认真红后再实现。

## 自审
- `git diff` 过了 `feedback_store.py` 全部改动,三处新增方法均未触碰既有方法/其余代码。
- 抽象方法签名 `async def delete_for_threads(self, *, tenant_id: UUID, thread_ids: Sequence[UUID]) -> int` 与 brief「Produces」条目逐字节一致。
- Db 实现与 brief 给定代码块逐字节一致(500 分片、`getattr(result, "rowcount", 0) or 0`、统一 commit)。
- grep 确认 `FeedbackStore` 仅有 `InMemoryFeedbackStore`/`DbFeedbackStore` 两个子类,无第三方实现遗漏新抽象方法。
- 空列表路径两侧实现均提前 `return 0`,不进入插叙/DELETE 语句。

## Concerns
无阻塞性遗留问题。Task 8(purge_user 消费方)尚未接入本方法,属于后续任务范围,brief 已明确本任务只交付 store 方法本身。

---

## 追加 — T2 review 两处发现修复(2026-07-24)

### 状态
DONE

### 背景
T2 review 指出上面「偏离及理由 §2」的判断站不住:`sql_store` fixture 走的是 testcontainers 超级用户连接,`feedback` 表的 FORCE RLS 被超级用户身份无条件绕过——而 `DbFeedbackStore` 类文档明确要求生产路径必须是 RLS-wrapped sessionmaker。既有测试锁的只是分片 `DELETE...IN` + `rowcount` 累加逻辑,从未验证过"RLS GUC 与显式 `tenant_id` 参数一致"这条生产契约,是真实覆盖缺口,不是可以照抄 `test_sql_memory_store.py` 轻量风格带过的选择题。

### 改动
1. **`packages/expert-work-persistence/tests/test_feedback_store_delete.py`**(新增,不改动既有测试/fixture):
   - import 新增 `urllib.parse.{urlparse,urlunparse}`、`sqlalchemy.{create_engine,text}`、`expert_work.persistence.rls.{build_rls_sessionmaker,current_tenant_id_var}`。
   - 新增 `APP_ROLE`/`APP_PASSWORD` 模块常量(取名 `expert_work_app_feedback_delete`,与 `test_rls_integration.py`/`test_billing_ledger_rls_integration.py` 等共享同一 session-scoped `postgres_container` 的其余 RLS 集成测试文件区分角色名,避免授权冲突)。
   - 新增 `_rewrite_credentials` / `_provision_app_role` 两个 helper——原样照抄 `test_rls_integration.py:74-118` 的非超级用户角色配置模式(该仓库对每个 RLS 集成测试文件都是各自复制这套 helper,非集中在 conftest,`test_billing_ledger_rls_integration.py` 同款先例)。
   - 新增 `feedback_rls_store` fixture:`alembic upgrade head` → `_provision_app_role` → 用 `APP_ROLE` 重写 DSN 建 engine → `build_rls_sessionmaker(create_async_session_factory(engine))` 包装 sessionmaker → `yield DbFeedbackStore(session_factory), engine`。对齐 brief 指定的 `test_rls_integration.py:249-260` 版本。
   - 新增 autouse fixture `reset_rls_context`:每测试前后重置 `current_tenant_id_var`,避免跨测试 GUC 泄漏。
   - 新增集成测试 `test_sql_delete_for_threads_rls_scoped_to_session_guc`:tenant A/B 各自在自己的 RLS 作用域下插入一条共享 `thread_id` 的 feedback 行;GUC 固定在 tenant A 后,①`delete_for_threads(tenant_id=tenant_a, ...)` 删掉 A 自己那行(`deleted_own == 1`);②同一会话不切 GUC,改传 `tenant_id=tenant_b` 再删——RLS 在显式 `WHERE` 生效前就已把可见/可删行集裁剪到 GUC=A,所以匹配 0 行(`deleted_other == 0`);③切 GUC 到 B 读回,证明 B 的行是真的还在(不是仅仅从 A 的视角不可见)。既有超级用户 `sql_store`/`test_sql_delete_for_threads_scopes_by_tenant_and_thread` 两个用例原样保留——锁的是分片/rowcount 逻辑,brief 明确不删。

2. **`packages/expert-work-persistence/src/expert_work/persistence/feedback_store.py`**:
   - `FeedbackStore.delete_for_threads` 抽象方法 docstring 追加一段,照 `mark_processed`("A *write* — must run under the row's own tenant RLS scope...")同款写法,补上 RLS 范围契约:方法期望在 tenant-scoped RLS session 内调用,`tenant_id` 参数须与会话 GUC 一致,不一致时删 0 行而非误删他租户数据。

### 自审(变异验证,非只跑绿)
用 scratchpad 里的副本临时把 `feedback_rls_store` fixture 的 `build_rls_sessionmaker(create_async_session_factory(engine))` 换回裸 `create_async_session_factory(engine)`(即退回无 RLS 包装的 sessionmaker,模拟"测试其实没走 RLS"这一被指出的缺口),单独跑新测试:
```
$ uv run pytest packages/expert-work-persistence/tests/test_feedback_store_delete.py -q -k rls_scoped
FAILED ...test_sql_delete_for_threads_rls_scoped_to_session_guc
1 failed, 4 deselected
```
`deleted_other` 变成 1(B 的行被跨租户删掉)——证明新测试确实在断言 RLS 生效,不是摆设。改回真实文件后 5/5 全绿(见下方证据),确认改动未残留。

### 测试证据(改动后,真实文件)
```
$ export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock
$ uv run pytest packages/expert-work-persistence/tests/test_feedback_store_delete.py -q
.....                                                                    [100%]
5 passed, 3 warnings in 3.30s   # warning 为既有 alembic path_separator 弃用提示,与本改动无关

$ uv run ruff check packages/expert-work-persistence
All checks passed!

$ uv run ruff format --check packages/expert-work-persistence
452 files already formatted   # 首轮曾报新测试文件需要 reformat(行宽换行),`ruff format` 后清零
```

### Concerns
无阻塞性遗留问题。既有超级用户测试与新 RLS-scoped 测试并存,分别锁定"分片/rowcount 机制"与"RLS GUC×显式参数一致性契约"两条不重叠的回归线。
