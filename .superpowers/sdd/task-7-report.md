# Task 7 报告 —— mcp_servers 删除清密文 + oauth disconnect 真删 + 审计标签

## 做了什么

1. **worktree 对齐**:执行 `git merge --ff-only fix-deletion-hygiene-pr2`,从
   `621c53f9` 快进合并到 `b630cf7a`(纯 fast-forward,无冲突),拿到 T2
   `SecretStore.delete`(`LocalDevSecretStore.delete` / `AliyunKmsSecretStore.delete`
   / `SqlEncryptedSecretStore.delete`)以及 `.superpowers/sdd/task-7-brief.md`。

2. **`mcp_servers.py` DELETE 端点**(`services/control-plane/src/control_plane/api/mcp_servers.py:987`):
   - 注入 `secret_store: Annotated[SecretStore, Depends(_get_secret_store)]`(该
     依赖已存在,POST/PATCH/GET 均用同一 accessor)。
   - 行删除后新增 `(c2)` 步骤:遍历 `record.token_secret_ref` /
     `record.custom_headers_ref`,存在即 `parse_secret_ref` 取名后
     `secret_store.delete(name)`;`try/except Exception` 包裹 + `logger.warning`,
     不阻断主删除流程(best-effort,与 disconnect 同款取舍)。该域全平台代管
     paste-only(`CreateMcpServerRequest.token` 是 `SecretStr`,从无 ref-mode
     外部引用),不存在"误删外部 KMS 条目"顾虑。

3. **`mcp_oauth_api.py` disconnect 端点**(`:406-449`):
   - `put(parse_secret_ref(ref), "")` 覆写循环改为
     `secret_store.delete(parse_secret_ref(ref))`;保留原有 `try/except + logger.warning`
     结构,日志键从 `disconnect_secret_overwrite_failed` 改
     `disconnect_secret_delete_failed`(语义对齐真删)。
   - 两处 docstring/注释同步("no delete" → 已有 delete)。

4. **审计 `resource_type` 修正**(brief 指定的两处,`action` 枚举不动):
   - OAuth 回调(`:375-385`):`"tenant_mcp_server"` → `"mcp_oauth_connection"`。
   - disconnect(`:438-448`):同上。
   - **`resource_type` 是 `Literal` 类型**(`control_plane.audit.ResourceType` +
     protocol 侧镜像 `expert_work.protocol.audit.ResourceType`),`"mcp_oauth_connection"`
     此前不在两个 Literal 里——brief 未提及这一步,但不加的话是类型错误(即使
     Python 运行时 Literal 不强校验,也违反仓库
     `[memory:audit-literal-drift]` 双侧同步约定)。已在两处 Literal 末尾各加
     一条 `"mcp_oauth_connection"`,注释按仓库既有格式标注来源 + drift 提示。

## 测试(TDD 红→绿)

- `services/control-plane/tests/test_mcp_servers_api.py`:新增
  `test_delete_removes_token_and_headers_secrets`——创建带 `auth_type=bearer` +
  `token` + `custom_headers` 的 server,删除前确认两个密文都存在,删除后两个
  `secret_store.get(...)` 均 `raises SecretNotFoundError`。无 ref 的删除路径
  已被既有 `test_delete_succeeds_when_unreferenced`(`auth_type=none`,无 token/
  headers)覆盖,未新增重复用例。
- `services/control-plane/tests/test_mcp_oauth_api.py`:
  - `test_disconnect_revokes_and_removes`:断言从 `revoked == ""`(覆写空串,锁
    旧行为)改为 `pytest.raises(SecretNotFoundError)`(锁新行为——这正是修
    bug);新增断言用 `app.state.audit_logger.query(AuditQuery(...), actor_id=...)`
    过滤 `details["source"] == "oauth_disconnect"` 的条目,`resource_type ==
    "mcp_oauth_connection"`。
  - `test_full_oauth_roundtrip`:同样追加 callback 审计条目的 `resource_type`
    断言(过滤 `source == "oauth_callback"`)。
  - 两个新 import:`AuditQuery`(`expert_work.protocol`)、`SecretNotFoundError`
    (`expert_work.runtime.secret_store`)。
- **红态验证**:`git stash` 掉 4 个 src 文件(保留测试改动)单独跑新/改测试,
  确认 `test_delete_removes_token_and_headers_secrets` 失败(`DID NOT RAISE
  SecretNotFoundError`),`stash pop` 恢复实现后重跑转绿。

## 验证

```
uv run pytest services/control-plane/tests/test_mcp_servers_api.py \
  services/control-plane/tests/test_mcp_oauth_api.py \
  services/control-plane/tests/test_mcp_oauth.py \
  services/control-plane/tests/test_audit_mcp_server_types.py -q
# 72 passed

uv run pytest services/control-plane/tests/ -k "audit" -q
# 88 passed, 1975 deselected（含 control-plane 全量 audit 相关回归，两处 Literal
# 新增未破坏任何既有断言）

uv run pytest packages/expert-work-protocol/tests/test_audit_actions.py -q
# 2 passed

uv run ruff check services/control-plane packages/expert-work-protocol
# All checks passed!

uv run ruff format --check <6 个改动文件>
# 6 files already formatted

uv run mypy packages
# Success: no issues found in 672 source files
# （services/control-plane 不在 CI mypy 范围内，见 .github/workflows/ci.yml:75
#   与 [memory:ci-mypy-scans-tests] 同款结论，未额外跑）
```

## Concerns

- 无阻塞项。
- brief 字面只列了两处 `resource_type` 字面量替换，未提及 `ResourceType`
  Literal 定义本身需要扩容——已按仓库 `[memory:audit-literal-drift]` 约定的
  "control-plane 侧 + protocol 侧必须同步" 规则补齐两处，属于让改动类型正确
  的必要配套，非范围蔓延。
- `mcp_oauth_api.py` disconnect 的密文清理与 `mcp_servers.py` 一样是
  best-effort(`try/except Exception` + `logger.warning`），与 spec §B5/§错误处理
  一致；未额外加 metrics/告警（brief 未要求，且该端点无先例告警可比照，不同于
  PR1 retention job 的 `image_object_keys_failed` 场景）。
