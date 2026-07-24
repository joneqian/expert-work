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
