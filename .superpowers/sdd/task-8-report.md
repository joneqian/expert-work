# Task 8 报告 —— purge_user 补 blob / feedback / summary + ImageUploadStore.list_for_user

## 做了什么

1. **分支对齐**:`git merge --ff-only fix-deletion-hygiene-pr1`(纯快进,拿到 Task 2 的
   `FeedbackStore.delete_for_threads` 与 Task 3 的 `list_reapable` 软删纳入行为)。

2. **`ImageUploadStore.list_for_user`**(base/sql/memory 三处新增,`ImageUpload` 已含
   `object_key`):
   - `base.py`:抽象方法 + docstring —— 不过滤 `deleted_at`(软删行也返回,purge 要连软删行
     的 blob 一起清),tenant + user 双域,`created_at` 升序。
   - `sql.py`:`select(ImageUploadRow).where(tenant_id==, user_id==).order_by(created_at.asc()).limit(limit)`。
   - `memory.py`:同谓词(`tenant_id == and user_id ==`,不判 `deleted_at`),`created_at` 升序 + `[:limit]`。
     两侧谓词逐字节等价(均无 `deleted_at` 过滤)。

3. **`user_purge.py`**:
   - `PurgeUserDeps` 新增 `feedback: FeedbackStore`、`object_store: ObjectStore | None`
     (import `expert_work.persistence.feedback_store.FeedbackStore` +
     `expert_work.runtime.storage.ObjectStore`)。
   - `PurgeSummary` 新增 `image_blobs_removed`/`image_blobs_failed`/`image_blobs_skipped`
     (默认 0),同步进 `as_dict()`。
   - 新 helper `_purge_images`:先 `list_for_user` 取行(含软删),`object_store is None` 时整
     批计入 `image_blobs_skipped`,否则逐行 `object_store.delete` 累加 removed/failed(单行失败
     不阻断,`logger.warning` + best-effort,同 brief 给的代码骨架);无论哪种分支都跑
     `delete_all_for_user` 落 `deleted["image_upload"]`(孤儿 object-store key 好过卡住的行,
     行删除不因 blob 清理失败而跳过)。原来的
     `summary.deleted["image_upload"] = await _step(..., deps.image_uploads.delete_all_for_user(...))`
     调用点改为 `await _step(summary, "image_upload", _purge_images(...), default=None)`。
   - `_purge_threads`:枚举完 `thread_ids`、进入逐 thread 删除循环**之前**插入
     `deps.feedback.delete_for_threads(tenant_id=, thread_ids=)` → `summary.deleted["feedback"]`,
     失败走与 `_step` 同格式的 `summary.failures["feedback"]`(不用 `_step` 本身,因为这段代码
     嵌在 `_purge_threads` 内部、不是顶层 await 链上的一个独立协程 —— brief 骨架即如此写)。
     顺序理由:thread 删除中途失败不留"会话没了评论还在"的中间态。

4. **`agent_users.py`** `_build_purge_deps`:加 `feedback=state.feedback_store`、
   `object_store=getattr(state, "object_store", None)`(grep 确认 `app.py` 分别在
   `app.state.feedback_store`/`app.state.object_store` 挂载,后者用 `getattr` 兜底未来可能不挂
   object store 的部署形态,与既有 `volume_backup_dlq`/`supervisor` 兜底手法一致),并更新了
   `_build_purge_deps` docstring 里"可选依赖"清单加入 object store。

5. **测试**:
   - `test_user_purge.py`:主 cascade 测试补种 —— 给用户 A 的线程插一条 image_upload 行 +
     把对应 blob 塞进新增的 `InMemoryObjectStore`,插一条 A 线程的 feedback;给用户 B 建一条独立
     线程 + feedback(隔离对照组)。purge 后断言:blob 从 object store 消失
     (`ObjectNotFoundError`)、`image_blobs_removed == 1`、A 线程 feedback 清空、B 线程 feedback
     保留、`deleted["feedback"] == 1`。幂等重跑段补 4 条断言(`deleted["feedback"]`/
     `deleted["image_upload"]`/三个 image_blobs_* 均为 0,不炸)。
   - 新增独立测试 `test_purge_user_image_blobs_skipped_without_object_store`:`object_store=None`
     时行照删(`deleted["image_upload"] == 1`)、`image_blobs_skipped == 1`、removed/failed 均 0、
     `"image_upload" not in summary.failures`。
   - `test_in_memory_image_upload_store.py` / `test_sql_image_upload_store.py`:各加
     `list_for_user` 测试 —— 软删行入选、同租户异用户/同用户异租户排除、`created_at` 升序、
     `limit` 生效(in-memory 侧额外测 limit)。

## 验证

```
uv run pytest packages/expert-work-persistence/tests/test_in_memory_image_upload_store.py -x -q
# 8 passed

export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock
uv run pytest packages/expert-work-persistence/tests/test_sql_image_upload_store.py -x -q -m integration
# 3 passed(含新 list_for_user 集成测试)

uv run pytest services/control-plane/tests/test_user_purge.py -x -q
# 7 passed(5 endpoint + cascade + 新 object_store=None 用例)

uv run pytest services/control-plane/tests/test_agent_users_api.py -x -q
# 13 passed(未受 PurgeUserDeps 新增字段影响)

uv run pytest packages/expert-work-persistence/tests -q -m "not integration"
# 595 passed(全量单元回归,确认 base.py 新增抽象方法未漏掉任何 ImageUploadStore 实现)

uv run pytest services/control-plane/tests -q --collect-only
# 2059 tests collected, 0 import error(确认无其它 PurgeUserDeps(...) 构造点漏改)

uv run ruff check packages/expert-work-persistence services/control-plane
# All checks passed!

uv run ruff format --check packages/expert-work-persistence services/control-plane
# 初次 1 file (memory.py) 需重排,`ruff format` 就地修正后复跑全绿(847 files already formatted)

uv run mypy packages/expert-work-persistence/src/expert_work/persistence/image_upload \
  services/control-plane/src/control_plane/purge \
  services/control-plane/src/control_plane/api/agent_users.py
# Success: no issues found in 7 source files
```

## Concerns

- 无阻塞项。`PurgeUserDeps(` 构造点全库只有两处(`test_user_purge.py` 主测试 +
  `agent_users.py` 的 `_build_purge_deps`),均已同步新增字段;`--collect-only` 2059 测试零导入
  错误佐证没有第三处遗漏。
- `object_store.delete` 对不存在的 key 是幂等的(不抛异常,ObjectStore 协议文档明确写了),
  `_purge_images` 里的 `except Exception` 主要兜网络/后端层失败,不是"key 已不存在"这类正常
  幂等路径——重跑时 `list_for_user` 本身返回空列表(行已在首次 purge 删光),所以幂等靠"没有
  行可循环"而不是靠 delete 的幂等语义,两者共同保证重跑三个 image_blobs_* 计数都是 0。

## T8 review Minor 补测(2026-07-24)

**问题**:review 指出 `_purge_images` 的 `object_store.delete` 抛异常分支
(`image_blobs_failed` 计数,行照删不阻断)没有被任何测试驱动过——既有三处
`image_blobs_failed` 断言全是 `== 0`。

**修法**:`test_user_purge.py` 新增 `_FailingObjectStore`(继承
`InMemoryObjectStore`,`delete` 恒抛 `RuntimeError`)+
`test_purge_user_image_blob_delete_failure_still_deletes_rows`——用户 A 挂 2 张
image_upload 行,object_store 用 `_FailingObjectStore`,purge 后断言:
`image_blobs_failed == 2`、`image_blobs_removed == 0`、
`deleted["image_upload"] == 2`(行照删)、`"image_upload" not in summary.failures`
(不算步骤失败)、`image_uploads.list_for_user` 真的空。

**变异自验**:临时把 `_purge_images` 循环里 `except Exception: summary.image_blobs_failed
+= 1; logger.warning(...)` 改成 `except Exception: raise`(让异常穿透到 `_step`
把整个 `image_upload` 步骤标记失败,`delete_all_for_user` 不会被跑到)→
`pytest -k blob_delete_failure` 变红(`AssertionError`:`deleted["image_upload"]`
不存在 + `"image_upload" in summary.failures`)→ 改回原样 → `git diff` 确认源码
无残留改动 → 全量 8 个测试复跑绿。

**验证**:
```
uv run pytest services/control-plane/tests/test_user_purge.py -q
# 8 passed

uv run ruff check services/control-plane
# All checks passed!
```
