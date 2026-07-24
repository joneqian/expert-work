# Task 2b 报告(PR2 追加)—— 生产加密后端 + 测试桩补齐 SecretStore.delete

## 状态
DONE

## worktree / 分支
- worktree 路径:`/Users/mac/src/github/jone_qian/expert-work/.claude/worktrees/agent-a830bb5131b4576ba`
- 分支:`worktree-agent-a830bb5131b4576ba`
- 起手 `git merge --ff-only fix-deletion-hygiene-pr2`(621c53f9 → 870970e5,快进,拿到 T2 的 `SecretStore.delete` Protocol,commit aae1a678,以及其余 PR2 已交付任务)。

## 背景
Task 2 报告(`.superpowers/sdd/task-2-report.md` Concerns 1/2)明确记录了两处漏补:`SqlEncryptedSecretStore`(control-plane 生产加密后端)与 `InMemorySecretStore`(`expert_work.testing` 测试桩)当时均未实现新增的 `delete`。本任务补齐这两处,不涉及其余范围。

## 改动文件
1. `services/control-plane/src/control_plane/encrypted_secret_store.py`
   - import 追加 `delete`(`from sqlalchemy import delete, select, update`)。
   - `SqlEncryptedSecretStore.delete(name)`:硬删该 `(tenant_id IS NULL, name)` 组合下的**全部版本行**——即 `DELETE FROM encrypted_secret WHERE tenant_id IS NULL AND name = :name`,照 `get`/`put`/`list_versions` 既有的 `bypass_rls_session() + self._sf()` 会话风格写(NULL-tenant 行本无 RLS 范围可满足,与其余三方法一致)。幂等:`DELETE ... WHERE` 对不存在的 name 天然影响 0 行,不抛。
   - 语义选择:硬删而非仅翻转 `is_current`——与 brief 明确的"密文彻底清除"目标一致(这正是本 PR 的目的),且与 Protocol 文档字符串"Remove every version of the secret"精确对应。
2. `packages/expert-work-common/src/expert_work/testing/__init__.py`
   - `InMemorySecretStore.delete(name)`:`self._store.pop(name, None)`,与 `LocalDevSecretStore.delete` 的实现逐字节同构(dict pop 幂等)。
   - 未碰该类既有的 `list_versions` 缺口(brief 明确指示 surgical,不顺手补)。

## 测试改动
1. `services/control-plane/tests/test_encrypted_secret_store.py`(既有测试文件,真实 Postgres via testcontainers,`@pytest.mark.integration`)
   - `test_delete_removes_all_versions`:put 两版本(`key-v1`/`key-v2`)→ `delete` → `get` 抛 `SecretNotFoundError` + `list_versions` 抛 `SecretNotFoundError`(空,与 `list_versions` 现有"无行即抛"的契约一致)。
   - `test_delete_missing_secret_is_idempotent`:对未写过的 name 直接 `delete`,不抛。
2. `tests/test_fixtures.py`(既有测试文件,`mock_secret_store` fixture)
   - `test_mock_secret_store_delete`:put → delete → get 抛 `KeyError`("secret not found" 匹配,沿用该类既有 `get` 的错误消息格式)。
   - `test_mock_secret_store_delete_missing_is_idempotent`:对未写过的 name 直接 `delete`,不抛,随后 `get` 仍抛。

## TDD 记录
1. 先加 `InMemorySecretStore.delete` 实现,同步补两个新测试跑绿(该类改动小,红→绿间隔短,未单独截红态输出)。
2. `SqlEncryptedSecretStore.delete`:先写两个新集成测试(此时源码未加 `delete`),确认收到 `AttributeError: 'SqlEncryptedSecretStore' object has no attribute 'delete'`(红)→ 实现 `delete` 方法 → 重跑全绿(绿)。

## 测试结果
```
$ uv run pytest tests/test_fixtures.py -v
7 passed in 0.01s

$ export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock
$ uv run pytest services/control-plane/tests/test_encrypted_secret_store.py -v -m integration
test_put_get_round_trip_and_ciphertext_at_rest PASSED
test_repaste_versions_and_current PASSED
test_missing_secret_raises PASSED
test_delete_removes_all_versions PASSED
test_delete_missing_secret_is_idempotent PASSED
5 passed, 8 deselected in 14.16s

$ uv run ruff check services/control-plane packages
All checks passed!

$ uv run ruff format --check services/control-plane packages
1064 files already formatted
```

## 自审
- `SqlEncryptedSecretStore.delete` 的 SQLAlchemy 会话/查询风格(`bypass_rls_session()` + `self._sf()` + `tenant_id.is_(None)` + `name ==` 过滤)与该文件 `get`/`put`/`list_versions` 三处逐字节一致,未引入新模式。
- `delete` 语义为硬删全部版本行(非软删/翻转 `is_current`)——直接满足 Protocol docstring"Remove every version"与 PR 目的"密文彻底清除",未过度设计（如加软删标记）。
- `InMemorySecretStore.delete` 与同 Protocol 下 `LocalDevSecretStore.delete` 写法完全同构,验证过 `git diff` 未误改 `get`/`put`。
- `list_versions` 缺口按 brief 指示未碰(既有缺口,非本任务因果)。
- 未改动其它文件——`git diff --stat` 仅 4 个文件(2 源 + 2 测试),与 brief 声明的范围一致。

## Concerns
1. 未跑 `mypy` 校验(brief 未要求;按既有记忆「CI mypy 不含 control-plane」,`services/control-plane` 本就不在 CI 强制 typecheck 范围内,`packages/expert-work-common` 在范围内但改动极小,风险低)。
2. `InMemorySecretStore` 仍缺 `list_versions`(Task 2 报告已记录的存量缺口,brief 本次明确指示不碰)——若后续任务要用它整测依赖 `list_versions` 的删除接线路径,需要先补。
3. `services/credential-proxy`、`services/control-plane/tests/{test_webhook_delivery_worker,test_dynamic_resolver,test_resolving_callers}.py` 里的局部 `SecretStore` 桩类(Task 2 报告已列出)仍未补 `delete`——不在本任务声明的两个目标范围(`SqlEncryptedSecretStore` + `InMemorySecretStore`)内,未触碰。
