# Task 2 报告(PR2)—— SecretStore.delete 原语

## 状态
DONE

## worktree / 分支
- worktree 路径:`/Users/mac/src/github/jone_qian/expert-work/.claude/worktrees/agent-aa023008118d65b2e`
- 分支:`worktree-agent-aa023008118d65b2e`
- 起手 `git merge --ff-only fix-deletion-hygiene-pr2`(621c53f9 → bef52752,快进,仅带入两份 PR2 计划/设计文档,无冲突)。

注:本文件路径此前已被 git 跟踪(PR1 #1048 把它随「删除接口卫生 PR1」一起提交进了历史,内容是 PR1 Task 2——`FeedbackStore.delete_for_threads`——的报告,与本 PR2 Task 2 无关)。本次按 brief 指示整篇覆盖为 PR2 Task 2 的报告。

## 改动文件
- `packages/expert-work-runtime/src/expert_work/runtime/secret_store/base.py`
  - `SecretStore` Protocol(`@runtime_checkable`)新增 `async def delete(self, name: str) -> None`,plain `async def` + docstring-only body,照抄该 Protocol 现有 `get`/`put`/`list_versions` 的写法(历史坑:Protocol 里 async 方法不能写 `...`/`raise NotImplementedError`,只能 docstring-only)。docstring:"Idempotent — deleting an absent name does NOT raise."
- `packages/expert-work-runtime/src/expert_work/runtime/secret_store/local_dev.py`
  - `LocalDevSecretStore.delete`:`self.secrets.pop(name, None)`(该类真实属性名为 `secrets`,非 brief 占位的假设名,已核实)。
- `packages/expert-work-runtime/src/expert_work/runtime/secret_store/aliyun_kms.py`
  - `KmsBackend` Protocol(非 `@runtime_checkable`,普通 `Protocol`)新增 `async def delete_secret(self, name: str) -> None`,同一 docstring-only 写法,docstring 同样注明幂等契约。
  - `AliyunKmsSecretStore.delete`:透传 `await self._backend.delete_secret(name)`,随后按 `put` 的既有失效写法剔除该 name 的全部缓存版本:`self._cache = {k: v for k, v in self._cache.items() if k[0] != name}`(缓存键是 `(name, version)` 元组,与 `put` 逐字节对齐)。

## 测试改动
- `packages/expert-work-runtime/tests/test_secret_store.py`
  - `test_delete_then_get_raises_secret_not_found`:put→delete→get 抛 `SecretNotFoundError`。
  - `test_delete_missing_name_does_not_raise`:对空 store 删不存在的 name,不抛。
- `packages/expert-work-runtime/tests/test_aliyun_kms_secret_store.py`
  - `FakeKmsBackend` 新增 `delete_calls: list[str]` 记录 + `delete_secret` 方法(pop 掉 fake 内部 dict,便于后续 `fetch_secret` 反映"已删")。
  - `test_delete_delegates_to_backend`:`store.delete("k")` → `backend.delete_calls == ["k"]`。
  - `test_delete_invalidates_cached_value`:先 `get` 灌缓存 → `delete` → 重新 `seed` 同名 → 再 `get`,断言 `fetch_calls == 2`(即缓存确实被清,未走命中路径直接透传旧值)。
  - `test_delete_missing_secret_does_not_raise`:对空 backend 删不存在的 name,不抛(幂等)。

## 全仓 grep —— 其他 SecretStore 实现/桩类核查
```
grep -rn "class.*SecretStore\|SecretStore)" --include="*.py" packages services | grep -v test_secret
grep -rln "SecretStore" --include="*.py" packages services
```
命中且需要评估的实现/桩类:
1. `services/control-plane/src/control_plane/encrypted_secret_store.py::SqlEncryptedSecretStore` —— 生产级 Postgres 加密后端,当前只有 get/put/list_versions,未补 `delete`。
2. `packages/expert-work-common/src/expert_work/testing/__init__.py::InMemorySecretStore` —— 测试桩,当前只有 get/put(连既有的 `list_versions` 都还没补,是先于本任务已存在的缺口)。
3. `services/credential-proxy/tests/test_credential_proxy_unit.py::FakeSecretStore`、`services/control-plane/tests/test_webhook_delivery_worker.py::_FakeSecretStore`、`services/control-plane/tests/test_dynamic_resolver.py::_SecretStore`、`services/control-plane/tests/test_resolving_callers.py::_CapturingSecretStore` —— 均为 services 测试文件里的局部桩类,`_CapturingSecretStore` 用处已带 `# type: ignore[arg-type]`。

**判断:均未改动。** 理由:
- brief 的验收命令是 `uv run mypy packages`(仅 packages),CI 实际 typecheck job 的范围是 `packages services/audit-backup-worker/src services/billing-rollup-job/src services/event-log-archive-job/src services/orchestrator/src services/retention-cleanup-job/src`(见 `.github/workflows/ci.yml:75`)——两个范围都**不含** `services/control-plane` 与 `services/credential-proxy`,这与既有记忆「CI mypy 不含 control-plane」吻合。
- 已用两条命令逐一验证:`uv run mypy packages` 与完整 CI 范围命令均 `Success: no issues found`,加 `delete`/`delete_secret` 没有让任何桩类在当前受检范围内报错。
- `InMemorySecretStore` 本身早已不满足 Protocol(缺 `list_versions`),是本任务改动之前就存在、且不在 packages 内被以 `SecretStore` 类型标注消费的既有缺口,不属于本任务改动范围(surgical:只补因我这次改动而新破的桩,不顺手修不相关的存量缺口)。
- `SqlEncryptedSecretStore` 是 T6/T7/T8(密文清理接线)未来大概率要接的真实生产后端,但补 `delete` 涉及新 DELETE/软删语义设计(该表有 `is_current`/多版本行),超出本 Task 2(仅原语定义+两个测试后端)的声明范围,留给后续接线任务判断是硬删全部版本行还是别的策略。

## 测试结果
```
$ uv run pytest packages/expert-work-runtime/tests/test_secret_store.py packages/expert-work-runtime/tests/test_aliyun_kms_secret_store.py -q
........................................                                 [100%]
40 passed in 0.37s

$ uv run pytest packages/expert-work-runtime -q
427 passed, 40 errors in 41.07s
# 40 errors 全部来自 test_sql_run_store.py(testcontainers 需要 DOCKER_HOST,本地未设置,
# 与本改动无关的既有环境缺口 —— 见记忆 local-docker-testcontainers)

$ uv run ruff check packages/expert-work-runtime
All checks passed!

$ uv run ruff format --check packages/expert-work-runtime
99 files already formatted

$ uv run mypy packages
Success: no issues found in 669 source files

$ uv run mypy packages services/audit-backup-worker/src services/billing-rollup-job/src services/event-log-archive-job/src services/orchestrator/src services/retention-cleanup-job/src
Success: no issues found in 772 source files
```

## TDD 记录
1. 先加 5 个新测试(LocalDev 2 个 + AliyunKms 3 个,含 `FakeKmsBackend.delete_secret`),跑红:
   `AttributeError: 'LocalDevSecretStore' object has no attribute 'delete'` /
   `AttributeError: 'AliyunKmsSecretStore' object has no attribute 'delete'`(5 failed, 35 passed)。
2. 实现 base.py / local_dev.py / aliyun_kms.py 三处改动。
3. 全绿(40 passed)。

## 自审
- `base.py` 新增的 `delete` 与 `KmsBackend.delete_secret` 均为 plain `async def` + docstring-only body,与该 Protocol 内其余方法写法逐字节一致(未误写 `...` 或 `raise NotImplementedError`)。
- `LocalDevSecretStore.delete` 用 `.pop(name, None)`,天然幂等,不查存在性、不抛。
- `AliyunKmsSecretStore.delete` 的缓存失效表达式与 `put` 里的失效表达式逐字节相同(`{k: v for k, v in self._cache.items() if k[0] != name}`),避免两处语义漂移(对齐记忆里「同一语义分散多处实现加约束要全处一起加」的教训——这里是新增而非改约束,但特意保持了与 `put` 的镜像写法,便于未来两处一起改)。
- 未改动 `packages/expert-work-common/src/expert_work/testing/__init__.py`、`services/**`、`factory.py`、`refs.py`、`__init__.py`(导出列表不变,`delete` 通过 `SecretStore`/`KmsBackend` 类型本身可见,无需新增导出符号)。
- `git diff --stat` 仅 5 个文件改动,与 brief 声明的改动范围(3 源文件 + 2 测试文件)完全一致,无越界改动。

## Concerns
1. `SqlEncryptedSecretStore`(control-plane 生产加密后端)仍缺 `delete` —— 大概率是 T6/T7/T8 接线时的下一个阻塞点,但其 `delete` 语义(硬删全部版本行 vs 仅标记当前行失效)未在本 brief 定义,留给消费方任务决策,不在本任务擅自实现。
2. `InMemorySecretStore`(`expert_work.testing`)存量即缺 `list_versions`,现在也缺 `delete` —— 与本任务无因果关系(改动前就已不满足 Protocol),按 surgical 原则未顺手补,但如果后续任务要用它整测 T6/T7/T8 的 delete 路径,需要先补全。
3. `uv run pytest packages/expert-work-runtime -q` 全量跑有 40 个 testcontainers 相关 error(缺 `DOCKER_HOST`),与本次改动无关,未尝试修复环境(不在任务范围)。
