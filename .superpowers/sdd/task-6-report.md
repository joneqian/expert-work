# Task 6 报告 —— platform_config canonical helper + 5 端点密文清理

## 做了什么

1. **worktree 对齐**:`git merge --ff-only fix-deletion-hygiene-pr2`(纯快进,无冲突),
   拿到 T2 `SecretStore.delete`(`LocalDevSecretStore.delete` / `AliyunKmsSecretStore.delete` /
   `SqlEncryptedSecretStore.delete`),tip 落在 `b630cf7a`。

2. **`_canonical_secret_name` helper**(`services/control-plane/src/control_plane/api/platform_config.py`,
   模块级,`build_platform_config_router()` 之前):四形态签名与 brief 逐字一致
   (`provider`/`key_id`/`tool`/`tenant_id` 全 kwonly)。逐个对照 `git show HEAD:` 里原
   5 处内联拼名后抽取,**逐字节保留原字符串**:
   - `expert-work/platform/llm/{provider}`(+`/{key_id}` 当 `key_id` 既非 `None` 也非 `"default"`)
   - `expert-work/platform/tool/{tool}`
   - `expert-work/platform/tenant/{tenant_id}/llm/{provider}`
   - `expert-work/platform/tenant/{tenant_id}/tool/{tool}`
   4 处写点(`_do_upsert_provider` :273-275、`upsert_tool` :363、`upsert_tenant_provider` :623、
   `upsert_tenant_tool` :667,共 5 处名字构造)全部改用 helper。

3. **`_delete_managed_secret` helper**(同文件,helper 旁边):`secret_ref == f"secret://{canonical}"`
   才 best-effort `secret_store.delete(canonical)`(try/except 记 `logger.warning`,失败返回
   `False`);外部 `secret://`/`kms://` ref 直接短路返回 `False`,**绝不调用 delete**。

4. **5 个 delete 端点**注入 `secret_store: Annotated[SecretStore, Depends(_get_secret_store)]`
   (沿用文件既有 `_get_secret_store` 先例),流程照 brief:`bypass_rls_session()` 内
   先取行(`get_provider` / `get_tool` / 复用已有 `list_providers()` 结果 / 新查
   `list_tenant_providers` / `list_tenant_tools`,后两者 Protocol 无单行 getter)→
   `_delete_managed_secret` best-effort → 照旧 `store.delete_*` → 审计 details 加
   `secret_deleted: bool`(`delete_provider` / `delete_provider_key` / `delete_tool` /
   `delete_tenant_provider` / `delete_tenant_tool` 五处全覆盖)。

5. **测试**(`services/control-plane/tests/test_platform_config_api.py` 追加 11 个):
   - `test_canonical_secret_name_matches_legacy_literals`:四形态 + key_id="default"
     折叠 + 非 default key_id 全部对旧字面量逐字节断言。
   - 5 端点各一对 paste/ref-mode:paste 模式起造 → 删除 → `LocalDevSecretStore` 里密文
     真消失(`SecretNotFoundError`)+ 审计 `secret_deleted: true`;ref-mode 先在
     store 里 `put` 一个外部名哨兵密文、写入行指向 `secret://external/...`,删除后
     哨兵密文原样 + 审计 `secret_deleted: false`。
   - 审计断言复用既有仓库先例(`app.state.audit_logger._store._rows.values()`,见
     `test_platform_embedding_config_api.py`)。

## 验证

```
uv run pytest services/control-plane/tests/test_platform_config_api.py -q
# 29 passed(18 原有 + 11 新增)

uv run ruff check services/control-plane
# All checks passed!

uv run ruff format --check services/control-plane
# 395 files already formatted
```

**变异自验**:把 `_delete_managed_secret` 里的比对 `if secret_ref != f"secret://{canonical}":`
临时改成永假(`if False:`,即"永远走删除分支"),只跑 5 个 ref-mode 测试
(`pytest -k ref_mode`)→ 全部 5 个变红(`secret_deleted` 断言从 `False` 翻成
`True`;因为 mutation 版本会去删 canonical 槽位而非外部 ref 指向的真实名字,
"外部密文原样"断言本身不受影响,但 `secret_deleted` 标记漏出了误删企图,证明
测试确实在盯这条命门)。恢复原比对后 5 个测试变回绿,配合上面的 29-passed 全绿。

## Concerns

- 无阻塞项。`delete_tenant_provider` / `delete_tenant_tool` 的 store Protocol
  没有单行 `get_tenant_provider`/`get_tenant_tool`,改用
  `list_tenant_providers(tenant_id)` / `list_tenant_tools(tenant_id)` 全量拉取后
  客户端过滤——量级是"一个租户的全 catalog 覆盖行",现状足够小,未新增
  store 方法(遵循最小改动;若后续该表变大,可以补一个单行 getter)。
- `mcp_servers.py` 删除端点与 `mcp_oauth_api.py` disconnect 的密文清理是设计文档
  §B2 第 4/5 点,不在本 task 范围(brief 只覆盖 `platform_config.py`)。
