# PR1 软删生命周期闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让软删数据有确定的物理清除时刻:记忆/工作区归档/tenant_user 满 90 天物理清除,purge 用户不留孤儿 blob,feedback 随会话删除,retention worker 授权补齐。

**Architecture:** persistence 层给 5 个 store 补硬删/清扫方法(SQL + in-memory 双实现,谓词逐字节一致);retention-cleanup-job 新增 3 个夜扫 pass;purge_user 补 blob 删除与 feedback 步;一个 grants migration 补历史欠账。

**Tech Stack:** Python 3.12 / SQLAlchemy 2 async / Alembic / pytest(testcontainers 集成测试)。

**Spec:** `docs/superpowers/specs/2026-07-24-deletion-hygiene-pr1-design.md`

## Global Constraints

- 分支 `fix-deletion-hygiene-pr1`,基于 main。
- **SQL 与 in-memory 双实现的过滤谓词必须逐字节等价**(历史 I-1 命门);每个新方法都要有"双实现同 fixture 同断言"的平价测试。
- 集成测试前置:`export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock`(本机 Docker Desktop 无默认 sock)。
- 宽限期默认值:记忆 90 天、工作区归档 90 天、tenant_user 90 天(用户拍板 D1/D4);软删图片下次夜扫即清(D3)。
- retention job **不引入审计管线**,清扫结果只进 `CleanupReport` 结构化日志(spec §H)。
- purge_user 维持 best-effort + 幂等语义;所有新步骤走既有 `_step` 容错模式。
- purge_user 的 memory 步**不改**(软删 = 90 天恢复窗,设计本意)。
- 新 store 方法一律带 `limit` 批量上限。
- 提交信息:conventional commits,中文描述(仓库惯例)。
- 完成每个任务后本地跑该任务的测试;最终门:`uv run ruff check .`、`uv run ruff format --check .`、CI 同款 mypy 范围、相关 pytest。

---

### Task 1: MemoryStore.hard_delete_expired

**Files:**
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/memory/base.py`(delete_all_for_user 抽象方法之后,~L211)
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/memory/sql.py`(delete_all_for_user 实现之后,~L574)
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/memory/memory.py`(delete_all_for_user 实现之后,~L393)
- Modify: `services/control-plane/src/control_plane/api/memory.py`(文件头 docstring ~L17-19:"a future retention sweep hard-deletes 30+ days after" 改为 "the retention sweep hard-deletes 90 days after (`retention-cleanup-job`)")
- Test: `packages/expert-work-persistence/tests/test_sql_memory_store.py`(追加)+ in-memory 对应测试文件(`ls packages/expert-work-persistence/tests | grep -i memory` 找到既有文件追加)

**Interfaces:**
- Produces: `async def hard_delete_expired(self, *, before: datetime, limit: int = 1000) -> int` — 物理 DELETE `deleted_at IS NOT NULL AND deleted_at < before` 的行,按 `deleted_at` 升序取前 `limit` 行,跨租户(无 tenant 谓词),返回删除行数。Task 7 的 retention pass 消费。

- [ ] **Step 1: 写失败测试**(SQL 侧写进 test_sql_memory_store.py,镜像该文件既有 fixture 风格;in-memory 侧同断言)

```python
async def test_hard_delete_expired_only_reaps_old_soft_deleted(store):
    # 三行:活跃 / 软删 100 天 / 软删 10 天
    ...写入三条 MemoryItem(复用文件内既有写入 helper)...
    await store.soft_delete(memory_id=old_id, tenant_id=t, user_id=u)   # 然后把 deleted_at 回拨 100 天
    await store.soft_delete(memory_id=recent_id, tenant_id=t, user_id=u)  # 回拨 10 天
    cutoff = datetime.now(UTC) - timedelta(days=90)
    assert await store.hard_delete_expired(before=cutoff, limit=100) == 1
    # 活跃行还在、10 天软删行还在(list_for_user include_deleted 或直查行数核对)
```

回拨 `deleted_at`:SQL 侧直接 `UPDATE memory_item SET deleted_at = ...`(session fixture 执行);in-memory 侧改行对象字段。再加一条 limit 测试:两行过期,`limit=1` 只删 1。

- [ ] **Step 2: 跑测试确认失败**(`DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock uv run pytest packages/expert-work-persistence/tests/test_sql_memory_store.py -k hard_delete -x`,预期 AttributeError/abstract 报错)

- [ ] **Step 3: 实现**。base.py 抽象方法(docstring 说明跨租户 + retention job 专用);sql.py:

```python
async def hard_delete_expired(self, *, before: datetime, limit: int = 1000) -> int:
    subq = (
        select(MemoryItemRow.id)
        .where(
            MemoryItemRow.deleted_at.is_not(None),
            MemoryItemRow.deleted_at < before,
        )
        .order_by(MemoryItemRow.deleted_at.asc())
        .limit(limit)
    )
    stmt = delete(MemoryItemRow).where(MemoryItemRow.id.in_(subq))
    async with self._sf() as session:
        result = await session.execute(stmt)
        await session.commit()
    return int(getattr(result, "rowcount", 0) or 0)
```

memory.py(in-memory)照 delete_all_for_user 的容器结构,谓词 `r.deleted_at is not None and r.deleted_at < before`,按 `deleted_at` 升序截 `limit`,从容器物理移除,返回个数。

- [ ] **Step 4: 跑测试确认通过**(同 Step 2 命令 + in-memory 文件)
- [ ] **Step 5: 变异自验**:临时把 SQL 谓词的 `is_not(None)` 删掉跑测试确认变红,恢复。
- [ ] **Step 6: Commit** `feat(persistence): MemoryStore.hard_delete_expired 记忆 90 天物理清扫地基`

---

### Task 2: FeedbackStore.delete_for_threads

**Files:**
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/feedback_store.py`(抽象 + InMemory + Db 三处)
- Test: feedback 既有测试文件(`ls packages/expert-work-persistence/tests services/control-plane/tests | grep -i feedback` 定位;若无 store 级测试文件则新建 `packages/expert-work-persistence/tests/test_feedback_store_delete.py`)

**Interfaces:**
- Produces: `async def delete_for_threads(self, *, tenant_id: UUID, thread_ids: Sequence[UUID]) -> int` — 物理 DELETE 指定租户下这些 thread 的全部 feedback 行,空列表返回 0。Task 8 的 purge 消费。

- [ ] **Step 1: 写失败测试**:插 3 行(t1/threadA ×2、t1/threadB ×1)+ 异租户同 thread_id 1 行;`delete_for_threads(tenant_id=t1, thread_ids=[threadA])` 返回 2,threadB 与异租户行仍可 `list_for_thread` 查到;空列表返回 0。
- [ ] **Step 2: 跑测试失败**(`uv run pytest <测试文件> -k delete_for_threads -x`)
- [ ] **Step 3: 实现**。Db 侧(500 一片防超长 IN):

```python
async def delete_for_threads(self, *, tenant_id: UUID, thread_ids: Sequence[UUID]) -> int:
    if not thread_ids:
        return 0
    ids = list(thread_ids)
    total = 0
    async with self._sf() as session:
        for i in range(0, len(ids), 500):
            result = await session.execute(
                delete(FeedbackRow).where(
                    FeedbackRow.tenant_id == tenant_id,
                    FeedbackRow.thread_id.in_(ids[i : i + 500]),
                )
            )
            total += int(getattr(result, "rowcount", 0) or 0)
        await session.commit()
    return total
```

InMemory:`wanted = set(thread_ids)`,过滤 `not (r.tenant_id == tenant_id and r.thread_id in wanted)`,返回差值。

- [ ] **Step 4: 跑测试通过**
- [ ] **Step 5: Commit** `feat(persistence): FeedbackStore.delete_for_threads(purge 用户随会话清评论)`

---

### Task 3: ImageUploadStore.list_expired → list_reapable(谓词加 deleted_at)

**Files:**
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/image_upload/base.py:80`(重命名 + docstring)
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/image_upload/sql.py:119`
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/image_upload/memory.py:73`
- Modify: `services/retention-cleanup-job/src/retention_cleanup_job/job.py:247`(唯一生产调用点,已验证)
- Test: `packages/expert-work-persistence/tests/test_in_memory_image_upload_store.py` + SQL 对应文件追加;retention job 既有测试若引用 list_expired 同步改名

**Interfaces:**
- Produces: `async def list_reapable(self, *, before: datetime, limit: int = 1000) -> list[ImageUpload]` — 谓词 `(created_at < before) OR (deleted_at IS NOT NULL)`,按 created_at 升序。job `_delete_expired_images` 消费(该方法内改调 list_reapable,其余逻辑不动)。

- [ ] **Step 1: 写失败测试**:四行——新+活跃(不命中)、老+活跃(命中)、新+软删(命中,这是新行为)、老+软删(命中);断言返回集合与排序。
- [ ] **Step 2: 跑测试失败**
- [ ] **Step 3: 实现**:SQL 谓词 `or_(ImageUploadRow.created_at < before, ImageUploadRow.deleted_at.is_not(None))`;in-memory `r.created_at < before or r.deleted_at is not None`。全仓 `grep -rn "list_expired" --include="*.py" packages/expert-work-persistence/src/expert_work/persistence/image_upload services/retention-cleanup-job` 确认无残留旧名(quota/artifact 的同名方法**不动**)。
- [ ] **Step 4: 跑测试通过**(含 `uv run pytest services/retention-cleanup-job/tests -x`)
- [ ] **Step 5: Commit** `feat(persistence): 图片清扫谓词纳入软删行(list_reapable),手删图片下次夜扫即清`

---

### Task 4: UserWorkspaceStore.list_archived_expired + hard_delete

**Files:**
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/workspace/base.py`(list_active 之后追加两个抽象方法)
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/workspace/sql.py`(照 list_pending_archive/L148 的查询与 DTO 转换风格)
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/workspace/memory.py`
- Test: workspace 既有测试文件追加(`ls packages/expert-work-persistence/tests | grep -i workspace` 定位)

**Interfaces:**
- Produces:
  - `async def list_archived_expired(self, *, before: datetime, limit: int = 100) -> list[UserWorkspace]` — `deleted_at IS NOT NULL AND deleted_at < before AND archived_object_key IS NOT NULL`,deleted_at 升序。
  - `async def hard_delete(self, *, workspace_id: UUID) -> bool` — 物理 DELETE 行,返回是否删到。
  Task 7 的 workspace pass 消费。

- [ ] **Step 1: 写失败测试**:四行——活跃 / 软删 100 天+已归档(命中)/ 软删 100 天+未归档(不命中,归档卡住)/ 软删 10 天+已归档(不命中);`hard_delete` 对命中行返回 True、再删返回 False、行真没了(get 返回 None)。
- [ ] **Step 2: 跑测试失败**
- [ ] **Step 3: 实现**(SQL 查询照 Task 4 Interfaces 谓词逐条写;in-memory 谓词 `w.deleted_at is not None and w.deleted_at < before and w.archived_object_key is not None`)
- [ ] **Step 4: 跑测试通过**
- [ ] **Step 5: Commit** `feat(persistence): 工作区归档过期清单 + 行硬删(Phase 3b 地基)`

---

### Task 5: TenantUserStore.hard_delete_deactivated

**Files:**
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/tenant_user/base.py`(deactivate 之后)
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/tenant_user/sql.py`(deactivate/L129 之后,子查询限批模式同 Task 1)
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/tenant_user/memory.py`
- Test: `packages/expert-work-persistence/tests/test_sql_tenant_user_store.py` + in-memory 对应文件追加

**Interfaces:**
- Produces: `async def hard_delete_deactivated(self, *, before: datetime, limit: int = 1000) -> int` — 物理 DELETE `deleted_at IS NOT NULL AND deleted_at < before`,跨租户,deleted_at 升序限批。Task 7 消费。

- [ ] **Step 1: 写失败测试**:活跃行不动;deactivate 后回拨 100 天的行被删(get 返回 None);回拨 10 天的行保留;**复活场景**——deactivate 后再 `resolve()` 同 subject(deleted_at 清空)不命中清扫。
- [ ] **Step 2: 跑测试失败**
- [ ] **Step 3: 实现**(SQL 与 Task 1 同构:`select(TenantUserRow.id).where(deleted_at.is_not(None), deleted_at < before).order_by(...).limit(limit)` 套 `delete(...).where(id.in_(subq))`)
- [ ] **Step 4: 跑测试通过**
- [ ] **Step 5: Commit** `feat(persistence): tenant_user 停用满期物理硬删(90 天恢复窗到期收尾)`

---

### Task 6: grants 迁移 0131

**Files:**
- Create: `packages/expert-work-persistence/migrations/versions/0131_retention_grants.py`(`down_revision = "0130_trigger_user_scope"`;先 `ls` 确认 0130 仍是链尾,若主干有新迁移则顺延编号)
- Test: 集成测试(migrations 全量跑通即验证;若仓库有 grants 断言测试先例——`grep -rn "has_table_privilege" packages/expert-work-persistence/tests` ——照写一条)

**Interfaces:**
- Produces: `retention_cleanup_worker` 角色获得本批 + 历史欠账所有 sweep 表权限。

- [ ] **Step 1: 写迁移**(照 0010 逐表单语句风格,upgrade 全部 GRANT、downgrade 对称 REVOKE):

```python
def upgrade() -> None:
    for stmt in (
        # 本批新增 pass
        "GRANT SELECT, DELETE ON TABLE memory_item TO retention_cleanup_worker;",
        "GRANT SELECT, DELETE ON TABLE user_workspace TO retention_cleanup_worker;",
        "GRANT SELECT, DELETE ON TABLE tenant_user TO retention_cleanup_worker;",
        # 历史欠账(image/artifact/approval pass 上线时未授权)
        "GRANT SELECT, DELETE ON TABLE image_upload TO retention_cleanup_worker;",
        "GRANT SELECT, UPDATE, DELETE ON TABLE artifact TO retention_cleanup_worker;",
        "GRANT SELECT, DELETE ON TABLE artifact_version TO retention_cleanup_worker;",
        "GRANT SELECT, UPDATE ON TABLE agent_approval TO retention_cleanup_worker;",
    ):
        op.execute(stmt)
```

- [ ] **Step 2: 本地容器跑全量迁移验证**(`DOCKER_HOST=... uv run pytest packages/expert-work-persistence/tests/test_rls_integration.py -x` 或仓库惯用的迁移冒烟测试)
- [ ] **Step 3: Commit** `feat(persistence): retention worker 清扫表授权补齐(0131,含 image/artifact/approval 历史欠账)`

---

### Task 7: retention job 三个新 pass + settings + 接线

**Files:**
- Modify: `services/retention-cleanup-job/src/retention_cleanup_job/settings.py`(三个新 knob)
- Modify: `services/retention-cleanup-job/src/retention_cleanup_job/job.py`(构造参数、CleanupReport 字段、run_once、三个 `_sweep_*`)
- Modify: `services/retention-cleanup-job/src/retention_cleanup_job/main.py`(构造 SqlMemoryStore/SqlUserWorkspaceStore/SqlTenantUserStore,传参,done 日志加字段)
- Test: `services/retention-cleanup-job/tests/test_job_unit.py`(追加,in-memory store)

**Interfaces:**
- Consumes: Task 1/3/4/5 的 store 方法。
- Produces: `CleanupReport` 新字段 `memory_hard_deleted / workspaces_hard_deleted / workspace_archives_removed / workspace_archives_failed / workspaces_pending_archive / tenant_users_hard_deleted`。

- [ ] **Step 1: settings 加 knob**(照 artifact 先例):

```python
memory_hard_delete_grace_days: int = Field(default=90, ge=1, le=3650)
workspace_archive_retention_days: int = Field(default=90, ge=1, le=3650)
tenant_user_hard_delete_grace_days: int = Field(default=90, ge=1, le=3650)
```

- [ ] **Step 2: 写失败单测**(in-memory store 直构 job):
  - memory pass:软删 100 天行被清、10 天行保留、store 未接线时 0。
  - tenant_user pass:同构三断言。
  - workspace pass:已归档过期行 → ObjectStore key 被删 + 行硬删;**key 删失败(mock delete 抛异常)→ 行保留、failed 计数**;key 不存在(抛 KeyError/FileNotFoundError)→ 视为已清,行照删;未归档卡住行 → 只进 pending 计数;object_store 缺失时 no-op。
- [ ] **Step 3: 跑测试失败**
- [ ] **Step 4: 实现**。job 构造参数(全部默认 None/90,向后兼容既有调用);run_once 依次追加三个 pass;核心:

```python
async def _sweep_memory(self) -> int:
    if self._memory_store is None:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=self._memory_grace_days)
    return await self._memory_store.hard_delete_expired(before=cutoff, limit=self._batch_size)

async def _sweep_tenant_users(self) -> int:
    if self._tenant_user_store is None:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=self._tenant_user_grace_days)
    return await self._tenant_user_store.hard_delete_deactivated(before=cutoff, limit=self._batch_size)

async def _sweep_workspaces(self) -> tuple[int, int, int, int]:
    """返回 (rows_hard_deleted, keys_removed, keys_failed, pending_archive)。

    key 删失败当次保留行(行是找回 key 的唯一线索,下次夜扫重试)——
    与 image pass 的取舍相反,理由见 spec §E。key 已不存在视为删除成功。
    """
    if self._workspace_store is None or self._object_store is None:
        return 0, 0, 0, 0
    cutoff = datetime.now(UTC) - timedelta(days=self._workspace_retention_days)
    pending = [
        w for w in await self._workspace_store.list_pending_archive()
        if w.deleted_at is not None and w.deleted_at < cutoff
    ]
    rows = await self._workspace_store.list_archived_expired(before=cutoff, limit=self._batch_size)
    hard = keys_ok = keys_failed = 0
    for ws in rows:
        assert ws.archived_object_key is not None  # list_archived_expired 谓词保证
        try:
            await self._object_store.delete(ws.archived_object_key)
            keys_ok += 1
        except (KeyError, FileNotFoundError):
            keys_ok += 1  # 归档已不在——目标态达成,行照删
        except Exception:
            keys_failed += 1
            logger.exception(
                "retention.workspace_archive_delete_failed key=%s", ws.archived_object_key
            )
            continue
        if await self._workspace_store.hard_delete(workspace_id=ws.id):
            hard += 1
    return hard, keys_ok, keys_failed, len(pending)
```

(实现前先确认 `ObjectStore.delete` 对缺失 key 的真实行为——`grep -n "async def delete" packages/expert-work-runtime/src/expert_work/runtime/storage/*.py`——若它本身静默容忍缺失,去掉 KeyError 分支,勿写死代码。)

main.py:`memory_store=SqlMemoryStore(session_factory)` 等三个无条件接线(照 artifact_store 先例,均 metadata-only,workspace pass 自身有 object_store 门),knob 透传,done 日志加新字段。

- [ ] **Step 5: 跑测试通过**(`uv run pytest services/retention-cleanup-job/tests -x`)
- [ ] **Step 6: Commit** `feat(retention): 记忆/工作区归档/tenant_user 三个 90 天物理清扫 pass`

---

### Task 8: purge_user 补 blob / feedback / summary + ImageUploadStore.list_for_user

**Files:**
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/image_upload/base.py` + `sql.py` + `memory.py`(新增 `list_for_user`)
- Modify: `services/control-plane/src/control_plane/purge/user_purge.py`(deps 两字段、_purge_threads 加 feedback、image 步改造、PurgeSummary 三字段)
- Modify: `services/control-plane/src/control_plane/api/agent_users.py`(deps 组装处接 `app.state.object_store` 与 `app.state.feedback_store`;先 grep 该文件 `PurgeUserDeps(` 定位)
- Test: `services/control-plane/tests/test_user_purge.py`(追加)+ image store 测试追加

**Interfaces:**
- Consumes: Task 2 `delete_for_threads`。
- Produces:
  - `ImageUploadStore.list_for_user(*, tenant_id: UUID, user_id: UUID, limit: int = 10000) -> list[ImageUpload]`(含 object_key,软删行也返回——purge 要连软删行的 blob 一起清)。
  - `PurgeUserDeps` 新字段:`feedback: FeedbackStore`、`object_store: ObjectStore | None`。
  - `PurgeSummary` 新字段:`image_blobs_removed: int = 0`、`image_blobs_failed: int = 0`、`image_blobs_skipped: int = 0`,`as_dict()` 同步;feedback 计数进 `deleted["feedback"]`。

- [ ] **Step 1: 写失败测试**(test_user_purge.py 既有 fixture 上追加):
  - purge 后 in-memory ObjectStore 里该用户图片 key 消失,`image_blobs_removed` 计数正确;
  - `object_store=None` 时行照删、`image_blobs_skipped` = 行数、无失败;
  - 用户 thread 的 feedback 行消失、异 thread 行保留、`deleted["feedback"]` 计数;
  - 幂等:重跑 purge 各计数为 0 不炸。
- [ ] **Step 2: 跑测试失败**(`uv run pytest services/control-plane/tests/test_user_purge.py -x`)
- [ ] **Step 3: 实现**:
  - `list_for_user` SQL:`select(ImageUploadRow).where(tenant_id==, user_id==).order_by(created_at.asc()).limit(limit)`(不过滤 deleted_at);in-memory 同谓词。
  - user_purge image 步改为独立 helper:

```python
async def _purge_images(
    deps: PurgeUserDeps, summary: PurgeSummary, *, tenant_id: UUID, user_id: UUID
) -> None:
    rows = await deps.image_uploads.list_for_user(tenant_id=tenant_id, user_id=user_id)
    if deps.object_store is None:
        summary.image_blobs_skipped = len(rows)
    else:
        for row in rows:
            try:
                await deps.object_store.delete(row.object_key)
                summary.image_blobs_removed += 1
            except Exception:  # best-effort:孤儿 key 好过卡住整个 purge
                summary.image_blobs_failed += 1
                logger.warning("purge_user.image_blob_failed", exc_info=True)
    summary.deleted["image_upload"] = await deps.image_uploads.delete_all_for_user(
        tenant_id=tenant_id, user_id=user_id
    )
```

  原 `summary.deleted["image_upload"] = await _step(...)` 调用点改为 `await _step(summary, "image_upload", _purge_images(...), default=None)`。
  - `_purge_threads` 在枚举完 `thread_ids`、进入逐 thread 删除**之前**:

```python
    try:
        summary.deleted["feedback"] = await deps.feedback.delete_for_threads(
            tenant_id=tenant_id, thread_ids=thread_ids
        )
    except Exception as exc:  # best-effort,与 _step 同格式落 failures
        logger.warning("purge_user.feedback_failed", exc_info=True)
        summary.failures["feedback"] = f"{type(exc).__name__}: {exc}"
```

  - agent_users.py deps 组装加 `feedback=state.feedback_store`、`object_store=getattr(state, "object_store", None)`(命名以该文件实际取 state 的写法为准)。
- [ ] **Step 4: 跑测试通过**(purge 全文件 + `uv run pytest services/control-plane/tests/test_agent_users_api.py -x`)
- [ ] **Step 5: Commit** `feat(control-plane): purge_user 清图片 blob + feedback 随会话硬删`

---

### Task 9(终门): 全量校验

- [ ] `uv run ruff check .` + `uv run ruff format --check .`
- [ ] CI 同款 mypy 范围(照 `.github/workflows` 里 mypy 步骤的目标跑)
- [ ] `uv run pytest packages/expert-work-persistence/tests services/retention-cleanup-job/tests services/control-plane/tests/test_user_purge.py services/control-plane/tests/test_agent_users_api.py services/control-plane/tests/test_memory_api.py`(memory API docstring 改动不碰逻辑,但一并回归)
- [ ] 集成测试(真容器):`DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock uv run pytest packages/expert-work-persistence/tests -k "sql" -x -q` 范围内新增用例全绿
- [ ] Commit(如有残留)+ push + PR
