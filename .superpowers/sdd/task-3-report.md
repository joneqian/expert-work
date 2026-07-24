# Task 3 报告:ImageUploadStore.list_expired → list_reapable(谓词加 deleted_at)

## 状态:完成

## worktree / 分支

- worktree 路径:`/Users/mac/src/github/jone_qian/expert-work/.claude/worktrees/agent-a83f57d812b3c25dc`
- 分支:`worktree-agent-a83f57d812b3c25dc`(本地分支)

**前置纠偏**:任务下发时说明"worktree 已从分支 fix-deletion-hygiene-pr1 切出",但实际检出时该 worktree 停在
`69181a73`(`fix-deletion-hygiene-pr1` 的合并基点上游),缺少该分支后续 4 个 commit(`80a898bf`
`a9af4d62` `856567c9` `9cbd8e5c`,即 Task 1/2 的产物 + PR1 设计/计划文档)。`fix-deletion-hygiene-pr1`
分支本身已被主 worktree 检出,无法在本 worktree 直接切换,因此用
`git merge --ff-only fix-deletion-hygiene-pr1` 把本地分支快进对齐到该分支 tip(`9cbd8e5c`),
过程中工作树确认为 clean、无任何本地改动被丢弃。对齐后 `.superpowers/sdd/task-3-brief.md` 仍不存在于本
worktree(该目录被 `.superpowers/sdd/.gitignore` 挡在外面,是主 worktree 的本地未跟踪文件),改为直接用
`Read` 工具跨 worktree 绝对路径读取主 worktree 里的
`/Users/mac/src/github/jone_qian/expert-work/.superpowers/sdd/task-3-brief.md`。

## 改动文件

- `packages/expert-work-persistence/src/expert_work/persistence/image_upload/base.py`
  - `list_expired` 抽象方法重命名为 `list_reapable`,docstring 改写为准确描述新谓词
    (`created_at < before OR deleted_at IS NOT NULL`),并说明"手删图片不再等到老化才被夜扫清"这一行为变化。
  - 类级 docstring 里对 `list_expired` 的引用同步改名。
- `packages/expert-work-persistence/src/expert_work/persistence/image_upload/sql.py`
  - `list_expired` → `list_reapable`,谓词从 `created_at < before` 改为
    `or_(created_at < before, deleted_at.is_not(None))`,新增 `or_` import。排序/limit 不变。
- `packages/expert-work-persistence/src/expert_work/persistence/image_upload/memory.py`
  - `list_expired` → `list_reapable`,谓词从 `r.created_at < before` 改为
    `r.created_at < before or r.deleted_at is not None`。排序/limit 不变。
- `services/retention-cleanup-job/src/retention_cleanup_job/job.py`
  - `_delete_expired_images` 内唯一调用点:`self._image_upload_store.list_expired(...)` →
    `list_reapable(...)`。方法其余逻辑(object-store 逐条删除、失败计数、hard_delete、docstring)按
    brief 要求**未改动**——docstring 里"regardless of deleted_at state"这句话仍准确描述老行为
    (老行不论是否软删都会被扫),但没提"新软删行也立即可扫"这一新增行为,见下方 Concerns。

## 测试改动 / 新增

- `packages/expert-work-persistence/tests/test_in_memory_image_upload_store.py`
  - 既有 `test_list_expired_and_hard_delete` 改名为 `test_list_reapable_and_hard_delete`,调用点同步改名。
  - 新增 `test_list_reapable_predicate_covers_old_and_soft_deleted`:四行场景
    (新+活跃不命中 / 老+活跃命中 / 新+软删命中(新行为)/ 老+软删命中),断言命中集合 + `created_at` 升序。
- `packages/expert-work-persistence/tests/test_sql_image_upload_store.py`(新建)
  - 该 SQL 测试文件此前不存在(brief 写"SQL 对应文件追加",实际是从零建,仿照
    `test_sql_artifact_store.py` 的 `sql_store` fixture 风格,`pytestmark = pytest.mark.integration`)。
  - `test_list_reapable_predicate_covers_old_and_soft_deleted`:与 in-memory 版本同一四行场景,验证
    SQL 谓词与 in-memory 谓词逐字节等价(真 Postgres,通过直接 `UPDATE image_upload SET created_at/deleted_at`
    回填因为 `insert()` 不接受外部传入的 `created_at`)。
  - `test_list_reapable_respects_limit`:三条老行 + `limit=2` 断言只返回 2 条。
- retention job 既有测试(`test_job_unit.py` / `test_job_integration.py`)未引用 `list_expired`
  字符串(只调用 job 自身的 `_delete_expired_images` 方法),**无需改名**,原样保留、全部通过。

## grep 验证:无残留旧名 / 未误改其他同名方法

```
grep -rn "list_expired" --include="*.py" \
  packages/expert-work-persistence/src/expert_work/persistence/image_upload \
  services/retention-cleanup-job
```
→ 只剩 `job.py:169`(`approval_store.list_expired`)与 `job.py:218`(`artifact_store.list_expired`),
均为 brief 明确要求**不动**的其他 store,未被误改。`quota`/`artifact` 域下的 `list_expired` 方法本身也未触碰。

## 测试结果

```
export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock

uv run pytest packages/expert-work-persistence/tests/test_in_memory_image_upload_store.py -x -q
→ 6 passed

uv run pytest packages/expert-work-persistence/tests/test_sql_image_upload_store.py -x -q -m integration
→ 2 passed

uv run pytest services/retention-cleanup-job/tests -x -q
→ 19 passed（含 integration 用例，真 Postgres）

uv run ruff check packages/expert-work-persistence services/retention-cleanup-job
→ All checks passed!

uv run ruff format --check packages/expert-work-persistence services/retention-cleanup-job
→ 456 files already formatted
```

## TDD 记录

1. 先写四行谓词测试(RED)→ `AttributeError: 'InMemoryImageUploadStore' object has no attribute
   'list_reapable'`(确认失败,1 failed / 4 passed,新老测试都验证过)。
2. 三处 store(base/sql/memory)改名 + 加谓词,job.py 改调用点。
3. 全绿(GREEN),`ruff format` 提示 memory.py 一行可收窄为单行(未超 100 列的单行形式更符合项目风格),已按建议调整。

## Commit

- `ea632431` — `feat(persistence): 图片清扫谓词纳入软删行(list_reapable),手删图片下次夜扫即清`
  (6 files changed, 219 insertions(+), 19 deletions(-))
  文件:`image_upload/base.py`、`image_upload/memory.py`、`image_upload/sql.py`、
  `test_in_memory_image_upload_store.py`、`test_sql_image_upload_store.py`(新建)、
  `retention_cleanup_job/job.py`。只显式 `git add` 了这 6 个路径,无误带其他文件。

## Concerns

1. **job.py docstring 未同步更新(按 brief 明确要求保留)**:`_delete_expired_images` 的 docstring
   说"finds image_upload rows whose created_at is older than now - image_retention_days (regardless
   of deleted_at state...)",这句话对老行为(老 → 不论是否软删都扫)仍准确,但没提新增的"新软删行也立即可扫"
   这一行为。brief 原文明确写"该方法内改调 list_reapable,其余逻辑不动",故按字面遵循未动 docstring。
   如果后续 PR 想让文档跟行为对齐,是一处可以补的小 follow-up,不在本 task 范围内。
2. **worktree 初始状态与任务描述不符**:见上方"worktree / 分支"一节,已自行用 `git merge --ff-only`
   纠正对齐,过程中未丢弃任何本地改动(对齐前工作树已确认 clean)。如果这是编排层的已知/预期步骤,可忽略;
   否则建议后续任务下发前检查 worktree 是否已正确从目标分支切出。
3. `.superpowers/sdd/task-3-report.md`(本文件)所在目录被 `.gitignore`(内容 `*`)挡住,仓库里已有
   `task-5-report.md`/`task-6-report.md` 是靠 `git add -f` 强制加入版本库的先例,本次同样对本报告文件
   `git add -f` 后随其余改动一起 commit。
