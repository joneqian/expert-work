# PR2 安全洞:撤权残留 + 密文清理 + OAuth 级联防护 — 设计文档

> 删除接口卫生修复计划第 2 批(共 5 批)。PR1(#1048,软删生命周期)已合并。
> 本批主题:删除动作涉及**权限与密文**的三处安全洞。

## 背景(审计 + 侦察结论,均有代码证据)

1. **成员撤销不删授权**:`members.py:286-340` revoke/suspend 两分支都不碰
   `role_binding`。绑定行在邀请时写入(`member_ops.py:96-144`,
   `subject_type="user"`、`subject_id=UUID(kc_user_id)`)。鉴权中间件每请求实时查
   `role_binding` 表且无缓存——被停用成员的 Keycloak 账号只是 disabled,**已签发
   JWT 在过期前仍携带活权限**。suspend 还是终态(无 un-suspend 路径),这些行永久
   变垃圾。`RoleBindingStore` 现只有按 id 的 `delete`,无按 subject 批删。
2. **密文删不掉**:`SecretStore` Protocol(`secret_store/base.py:45-76`)只有
   get/put/list_versions,**没有 delete**。后果:
   - `platform_config.py` 5 个删除端点(平台/租户 LLM key、工具 key)**连
     secret_store 依赖都没注入**,只删 catalog 指针行,粘贴写入的密文永留。
   - `mcp_servers.py:987` 删租户 MCP 服务器:`token_secret_ref` /
     `custom_headers_ref` 零清理(该域 paste-only,全部平台代管)。
   - `mcp_oauth_api.py:406` disconnect:只能 best-effort `put(ref, "")` 覆写。
3. **删目录静默清空用户 OAuth 连接**:`mcp_connector_catalog → mcp_oauth_connection`
   是 `ON DELETE CASCADE`(migration 0063);名义上的 409 防护走
   `tenant_mcp_server` 的 RESTRICT FK,而 **oauth2 型连接器从不创建
   tenant_mcp_server 行**(全仓唯一 create 调用点在 bearer/none 路径)——防护对
   OAuth 场景永不触发。删除后用户无感知、token 密文成孤儿。
   `McpOAuthConnectionStore` 现无按 catalog_id 的 count/list 方法。
4. **审计标签 bug**:`mcp_oauth_api.py` OAuth 回调(:375-385)与 disconnect
   (:438-448)两处审计把 `resource_type` 写成 `"tenant_mcp_server"`,实际资源是
   `mcp_oauth_connection`。

## 用户拍板(2026-07-24)

| # | 决策 | 结论 |
|---|------|------|
| D1 | 存量孤儿 role_binding | **迁移一次性清**:DELETE join(绑定 → 已 revoked/suspended 的 member),升级即清 |
| D2 | 删目录 vs 用户 OAuth 连接 | **默认 409 拦(报连接数)+ `?force=true` 显式级联**(删连接 + 删 token 密文 + 审计计数);FK 同步改 RESTRICT 做 DB 兜底 |

## 设计

### A. 成员撤销/停用同步删授权

1. `RoleBindingStore.delete_for_subject(*, subject_type: str, subject_id: UUID,
   tenant_id: UUID) -> int`(SQL + in-memory 双实现,谓词逐字节一致;
   `platform_scope=false` 限定——平台级授权不属于租户成员生命周期)。
2. `members.py` revoke 与 suspend 两分支:状态转移成功后调用
   `delete_for_subject(subject_type="user", subject_id=UUID(member.keycloak_user_id),
   tenant_id=...)`;`keycloak_user_id is None`(未 provision)时跳过。既有审计
   details 增加 `role_bindings_removed` 计数。best-effort:删绑定失败记日志/审计,
   不回滚状态转移(半撤销可重跑)。
3. **迁移 0132 存量清理**(D1):

```sql
DELETE FROM role_binding rb
USING tenant_member tm
WHERE tm.tenant_id = rb.tenant_id
  AND tm.keycloak_user_id = rb.subject_id::text
  AND rb.subject_type = 'user'
  AND rb.platform_scope = false
  AND tm.status IN ('revoked', 'suspended');
```

   downgrade 为 no-op(删掉的授权本就不该存在,不可逆是意图)。
4. **坑位声明**:join 键是 `rb.subject_id::text = tm.keycloak_user_id`(Text 列)。
   `tenant_member.subject_id`(UUID 列)是另一个东西(首登回填的 tenant_user.id),
   **严禁**用它 join。
5. purge_user 不加 role_binding 步:purge 目标是外部终端用户(无 member、无 KC
   账号、无绑定),member 用户被 409 闸挡住走成员页流程——该流程即本节 revoke。

### B. SecretStore.delete 原语 + 全删除面接线

1. Protocol 增加:

```python
async def delete(self, name: str) -> None:
    """Remove the secret. Idempotent — deleting an absent name does NOT raise."""
```

   - `LocalDevSecretStore.delete`:`self.secrets.pop(name, None)`。
   - `AliyunKmsSecretStore.delete`:`KmsBackend` Protocol 同步加 `delete_secret`,
     wrapper 透传 + 失效读缓存。真实 SDK 适配器仍是 F-7 部署期后续(现状
     `make_secret_store("aliyun_kms")` 本就 NotImplementedError,不为本批引入)。
2. **canonical 名共享 helper**(消除写/删漂移命门):`platform_config.py` 现有 5
   处写点内联拼名(`:273-275`/`:363`/`:623`/`:667`),抽成模块级函数:

```python
def _canonical_secret_name(kind, *, provider=None, tool=None, key_id=None, tenant_id=None) -> str
```

   写路径与删路径共用同一函数;对既有拼名做逐字节回归测试。
3. **platform_config 5 个删除端点**:注入 `secret_store`;删 catalog 行前先
   `get` 拿 `secret_ref`;**仅当** `secret_ref == f"secret://{canonical_name}"`
   (平台代管密文)时 `secret_store.delete(canonical_name)`;ref-mode 外部引用
   (管理员给的任意 `secret://`/`kms://`)**绝不触碰**——不能删外部拥有的 KMS 条目。
   删除密文 best-effort:失败记日志,catalog 行照删(与 PR1 image blob 同取舍,
   密文孤儿好过卡死删除,且审计里带 `secret_deleted: bool`)。
4. **mcp_servers.py 删除端点**:该域 paste-only(无 ref-mode,`mcp_servers.py:83-102`
   请求模型只有 `token: SecretStr`),`record.token_secret_ref` /
   `record.custom_headers_ref` 全平台代管——存在即 best-effort delete(经
   `parse_secret_ref` 取名)。
5. **mcp_oauth disconnect**:`put(ref, "")` 覆写改为 `secret_store.delete(...)`,
   保留 best-effort 包装与 warning 日志。

### C. MCP 目录删除:409 + force 级联 + FK 兜底(D2)

1. `McpOAuthConnectionStore` 新方法(SQL + in-memory):
   - `count_for_catalog(*, catalog_id: UUID) -> int`(跨租户,平台治理视角)
   - `list_for_catalog(*, catalog_id: UUID, limit: int = 1000) -> list[...]`
   - `delete_for_catalog(*, catalog_id: UUID) -> int`
2. `mcp_catalog.py` DELETE `/{catalog_id}` 增加 `force: bool = False` query 参数:
   - 默认:`count_for_catalog > 0` → **409** `CATALOG_HAS_OAUTH_CONNECTIONS`,body
     携带连接数(管理员看到影响面)。
   - `?force=true`:先 `list_for_catalog` 逐条 best-effort 删两个 token 密文
     (用 B 的 delete),再 `delete_for_catalog` 删行,最后删 catalog 行。审计
     details 带 `connections_removed / secrets_removed / secrets_failed`。
   - 既有 `tenant_mcp_server` 的 RESTRICT 409 逻辑不变,优先级在前(装了实例先
     处理实例)。
3. **迁移 0133**:`mcp_oauth_connection.catalog_id` FK 由 CASCADE 改 **RESTRICT**
   (DB 级兜底,防未来代码绕过 app 闸静默级联)。0063 的 FK 是 inline 定义无显式
   名——迁移里先从 `information_schema`/`pg_constraint` 查真实约束名再 drop,
   **不赌 auto-name**。downgrade 对称改回 CASCADE。
4. store 层 `delete(catalog_id)` 的 `IntegrityError → McpConnectorCatalogInUseError`
   捕获保持——改 RESTRICT 后 OAuth 连接残留(app 闸被绕时)也会落进同一 409 语义。

### D. 审计标签修正

- `mcp_oauth_api.py` 回调(:375-385)与 disconnect(:438-448):`resource_type`
  改为 `"mcp_oauth_connection"`。`action` 保持现值(MCP_SERVER_CREATE/DELETE)——
  改 action 枚举可能影响审计消费方(SettingsAudit 前端解析先例),不在本批。
- 本批新增/修改的删除动作全部落审计:成员撤权带 `role_bindings_removed`;
  platform_config 删除带 `secret_deleted`;catalog force 级联带三计数。

## 错误处理

- 所有密文删除 best-effort:失败不阻断主删除动作,计数/标记进审计 details。
- `delete_for_subject` 失败不回滚 member 状态转移(审计记失败,重跑可补)。
- force 级联中单条连接的密文删失败不阻断该连接行删除(与 PR1 image 取舍一致:
  行是清理的主体,密文孤儿另有 `secrets_failed` 计数暴露)。

## 测试

- store 双实现平价:`delete_for_subject` / `count_for_catalog` / `list_for_catalog`
  / `delete_for_catalog`(含跨租户语义)+ 真容器集成。
- `SecretStore.delete` 幂等(两实现,缺失不抛)。
- members 端点:revoke/suspend 后绑定消失、审计计数;`keycloak_user_id=None` 跳过。
- 迁移 0132:集成测试造孤儿(revoked member + binding)→ upgrade → 孤儿没了、
  active member 的绑定保留、platform_scope 绑定保留。
- platform_config:paste 模式删除 → LocalDev 密文真没了;ref-mode 外部 ref 原样;
  canonical helper 与 5 处写点逐字节回归。
- mcp_servers 删除:双 ref 密文消失。
- oauth disconnect:密文 delete(非覆写)。
- catalog:无连接照删;有连接 409 带 count;force 级联三计数 + 密文消失;
  **FK RESTRICT 兜底**(绕 app 层直调 store.delete → McpConnectorCatalogInUseError)。
- 审计 resource_type 断言两处。
- 变异自验:破坏 canonical 比对(改成永真)→ ref-mode 测试变红;破坏 0132 join
  键 → 存量清理测试变红。

## 范围外

- PR3(孤儿行级联)/ PR4(删除前置检查,含 mcp server servers-留空缝隙)/ PR5(成员页员工清除入口)。
- Aliyun KMS 真实 SDK 适配器(F-7 部署期后续)。
- 审计 action 枚举重命名(消费方兼容性另议)。
- 用户侧"连接被管理员断开"的通知机制(平台无通知设施,审计留痕)。
