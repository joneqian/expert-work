# Task 7 报告 —— retention job 三个新 pass + settings + 接线

## 做了什么

1. **worktree 对齐**:执行 `git merge --ff-only fix-deletion-hygiene-pr1`,从
   `69181a73` 快进合并到 `0edb16bb`(纯 fast-forward,无冲突),拿到波 1 T1-T6
   打好的三个 store 地基(`MemoryStore.hard_delete_expired`、
   `UserWorkspaceStore.list_archived_expired`/`hard_delete`/`list_pending_archive`、
   `TenantUserStore.hard_delete_deactivated`)以及 `.superpowers/sdd/task-7-brief.md`。

2. **`ObjectStore.delete` 真实行为核查**(brief 明确要求实现前先查):
   `grep -n "async def delete" packages/expert-work-runtime/src/expert_work/runtime/storage/*.py`
   + 读三处实现(`base.py` Protocol docstring / `memory.py` / `s3_compatible.py`)。
   结论:**`delete()` 对缺失 key 是幂等的,从不抛异常**——`InMemoryObjectStore.delete`
   用 `dict.pop(key, None)`,`S3CompatibleObjectStore.delete` 靠 S3 `DeleteObject`
   本身对不存在的 key 静默成功。因此 brief 伪代码里的
   `except (KeyError, FileNotFoundError): keys_ok += 1` 分支是死代码,按 brief
   指示的"若它本身静默容忍缺失,去掉 KeyError 分支"删掉了这条,只保留
   `except Exception` 兜住真实失败(权限/网络/后端错误)。

3. **`settings.py`** 加三个 90 天 knob(`memory_hard_delete_grace_days` /
   `workspace_archive_retention_days` / `tenant_user_hard_delete_grace_days`,
   均 `Field(default=90, ge=1, le=3650)`),照 `artifact_retention_days` 先例。

4. **`job.py`**:
   - `CleanupReport` 加 6 个新字段(`memory_hard_deleted` /
     `workspaces_hard_deleted` / `workspace_archives_removed` /
     `workspace_archives_failed` / `workspaces_pending_archive` /
     `tenant_users_hard_deleted`),全部默认 0。
   - 构造参数加 `memory_store` / `memory_hard_delete_grace_days=90`、
     `workspace_store` / `workspace_archive_retention_days=90`、
     `tenant_user_store` / `tenant_user_hard_delete_grace_days=90`(均可选,
     向后兼容既有调用),各自 `< 1` 时 `raise ValueError`(照
     `artifact_hard_delete_grace_days` 先例逐条加校验)。
   - `run_once` 依次追加 `_sweep_memory` / `_sweep_tenant_users` /
     `_sweep_workspaces` 三个 pass,结果并入 `CleanupReport`。
   - `_sweep_memory` / `_sweep_tenant_users`:store 未接线时 0,否则按
     `now - grace_days` 截止时间调用对应 `hard_delete_*` 方法。
   - `_sweep_workspaces`:`workspace_store` 或 `object_store` 缺失时
     `(0, 0, 0, 0)`;`list_pending_archive()` 过滤出 `deleted_at < cutoff`
     的卡住行只计入 `pending`(不碰);`list_archived_expired()` 逐行删
     ObjectStore key(成功或"已不存在"都算 `keys_ok`,因为 `delete()` 本身
     幂等)才 `hard_delete` 该行;真实删除异常 → `keys_failed` 计数、行保留
     (与 image pass 取舍相反——行是找回 key 的唯一线索,留给下次夜扫重试)。

5. **`main.py`**:`SqlMemoryStore` / `SqlTenantUserStore` / `SqlUserWorkspaceStore`
   三个无条件构造接线(照 `artifact_store` / `approval_store` 先例,均
   metadata-only;workspace pass 自身仍靠 `object_store` 门控——`memory` 后端下
   `object_store=None`,workspace pass 保持 no-op),knob 透传,done 日志加 6 个
   新字段,并仿照既有 `image_object_keys_failed` 告警加了一条
   `workspace_archives_failed` 告警(对称场景,同样值得 SRE 关注)。

6. **`test_job_unit.py`** 追加(先读文件学会用 in-memory store + `model_copy`
   回填时间戳的既有风格,复用 `test_in_memory_memory_store.py` /
   `test_in_memory_tenant_user_store.py` / `test_in_memory_user_workspace_store.py`
   里 Task 1/3/4/5 已验证过的 fixture 写法):
   - 3 个新构造校验测试(三个 grace-days 参数 `<1` 报错)。
   - `test_cleanup_report_default_is_all_zero` 补 6 个新字段断言。
   - memory pass:100 天软删行被清 / 10 天软删行保留(同一测试断言两头) +
     store 未接线时 0。
   - tenant_user pass:同构断言(100 天清 / 10 天保留 / 未接线 0)。
   - workspace pass 5 个场景:已归档过期行 key 删 + 行硬删;key 删失败
     (mock `delete` 抛 `RuntimeError`)→ 行保留 + `keys_failed=1`;key 已不存在
     (从不 `put` 进 object_store)→ 视为成功、行照删;未归档卡住行 → 只进
     `pending`,行不动;`object_store` 缺失时 `(0,0,0,0)`。

## 验证

```
uv run pytest services/retention-cleanup-job/tests/test_job_unit.py -x -q
# 26 passed(先跑纯 unit,含全部新增用例)

export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock
uv run pytest services/retention-cleanup-job/tests -x -q
# 31 passed(26 unit + 5 integration,integration 用例未改动,回归确认三个新
# pass 未破坏既有 audit/event/jwt 集成测试)

uv run ruff check services/retention-cleanup-job
# 初次报 1 个 S101(workspace pass 里的 `assert ws.archived_object_key is not None`),
# 加 `# noqa: S101` 注释(照仓库既有 `tenant_user/sql.py:79` 等多处先例)后全绿。

uv run ruff format --check services/retention-cleanup-job
# 7 files already formatted

uv run mypy packages services/audit-backup-worker/src services/billing-rollup-job/src \
  services/event-log-archive-job/src services/orchestrator/src services/retention-cleanup-job/src
# Success: no issues found in 772 source files(CI 同款 mypy 范围,.github/workflows/ci.yml:75)
```

## Concerns

- 无阻塞项。`ObjectStore.delete` 幂等这条核查结果与 brief 的假设分支不一致
  (brief 伪代码写了 `except (KeyError, FileNotFoundError)`),已按 brief 自己的
  兜底指示("若它本身静默容忍缺失,去掉 KeyError 分支,勿写死代码")删除该分支,
  测试改用"从不 `put` 该 key 就调 `delete`"来验证"key 已不存在→视为成功"这条
  语义,而不是 mock 抛 `KeyError`。
- `workspace_archives_failed` 告警是新增的(brief 没有明确要求这一行日志),
  参照对称的 `image_object_keys_failed` 告警补齐,同一 concern 级别(ObjectStore
  失败需要 SRE 感知);如果这不在期望范围内,是唯一一处超出 brief 字面要求的
  微小扩展,可按需去掉。
