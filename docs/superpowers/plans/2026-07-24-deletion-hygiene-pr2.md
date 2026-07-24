# PR2 安全洞 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 成员撤权同步删授权(+存量一次性清)、SecretStore 补 delete 并接线全部密文删除面(代管/外部引用严格区分)、MCP 目录删除 409+force 级联+FK RESTRICT 兜底、修审计标签。

**Architecture:** persistence 层 2 个 store 加方法 + runtime 层 SecretStore 原语;control-plane 4 个 API 面接线;2 个迁移(存量清理 + FK 翻转)。两波并行:波 1=T1-T4(地基,文件互不相交),波 2=T5-T8(接线,文件互不相交)。

**Tech Stack:** Python 3.12 / SQLAlchemy 2 async / Alembic / pytest(testcontainers)。

**Spec:** `docs/superpowers/specs/2026-07-24-deletion-hygiene-pr2-design.md`

## Global Constraints

- 分支 `fix-deletion-hygiene-pr2`(已建,基于 main@621c53f9)。
- SQL 与 in-memory 双实现谓词逐字节等价 + 平价测试(项目命门)。
- 集成测试前置:`export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock`。
- **canonical 密文名判定是安全命门**:platform_config 只删 `secret_ref == f"secret://{canonical_name}"` 的代管密文,外部 ref(ref-mode 任意 `secret://`/`kms://`)绝不触碰。
- **role_binding join 键 = `rb.subject_id::text = tm.keycloak_user_id`(Text 列)**;`tenant_member.subject_id`(UUID 列,首登回填 tenant_user.id)是另一个东西,严禁用它。
- 所有密文删除 best-effort:失败不阻断主删除,计数/标记进审计 details。
- `SecretStore.delete` 幂等:缺失 name 不抛(对齐 ObjectStore.delete 契约先例)。
- 审计 action 枚举不动(消费方兼容);只修 `resource_type`。
- 迁移编号:0132(存量 role_binding 清理,T4)、0133(FK RESTRICT,T8);T8 在波 2,天然拿到 0132 之后的链尾。
- 终门:`uv run ruff check .` + format --check + CI 同款 mypy(packages + 5 个 services/src,**不含 control-plane**)+ 相关 pytest;control-plane 改动跑其测试文件。
- 提交:conventional commits 中文。

---

### Task 1: RoleBindingStore.delete_for_subject

**Files:**
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/auth/base.py`(RoleBindingStore 抽象,~L223-313,`delete` 之后)
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/auth/sql.py`(照 `delete`/L433-449 风格)
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/auth/memory.py`
- Test: `packages/expert-work-persistence/tests/test_sql_auth_store.py` + in-memory 对应文件追加(先 `ls | grep -i auth` 定位)

**Interfaces:**
- Produces: `async def delete_for_subject(self, *, subject_type: str, subject_id: UUID, tenant_id: UUID) -> int` — 物理 DELETE 该 subject 在该租户的全部**非 platform_scope** 绑定(`platform_scope == false` 谓词硬编码),返回行数。T5 消费。

- [ ] **Step 1: 写失败测试**:同 subject 两行(role 不同)+ 异租户同 subject 一行 + 同租户 platform_scope=true 一行;调用后返回 2,异租户行与 platform_scope 行仍在(`list_for_subject` 验证);再调返回 0(幂等)。双实现同断言。
- [ ] **Step 2: 跑红**(`uv run pytest packages/expert-work-persistence/tests/test_sql_auth_store.py -k delete_for_subject -x`)
- [ ] **Step 3: 实现**。SQL:

```python
async def delete_for_subject(
    self, *, subject_type: str, subject_id: UUID, tenant_id: UUID
) -> int:
    stmt = delete(RoleBindingRow).where(
        RoleBindingRow.subject_type == subject_type,
        RoleBindingRow.subject_id == subject_id,
        RoleBindingRow.tenant_id == tenant_id,
        RoleBindingRow.platform_scope.is_(False),
    )
    async with self._sf() as session:
        result = await session.execute(stmt)
        await session.commit()
    return int(getattr(result, "rowcount", 0) or 0)
```

(列名以 `git show HEAD:` 该文件既有 `delete`/`list_for_subject` 用的真实属性名为准。)in-memory 同谓词过滤删除。
- [ ] **Step 4: 跑绿**;变异自验:去掉 `platform_scope` 谓词 → platform_scope 保留测试变红,恢复。
- [ ] **Step 5: Commit** `feat(persistence): RoleBindingStore.delete_for_subject(成员撤权同步清授权地基)`

---

### Task 2: SecretStore.delete 原语

**Files:**
- Modify: `packages/expert-work-runtime/src/expert_work/runtime/secret_store/base.py`(Protocol,~L45-76)
- Modify: `packages/expert-work-runtime/src/expert_work/runtime/secret_store/local_dev.py`
- Modify: `packages/expert-work-runtime/src/expert_work/runtime/secret_store/aliyun_kms.py`(`KmsBackend` Protocol 加 `delete_secret` + wrapper 透传 + 失效读缓存)
- Test: `packages/expert-work-runtime/tests/test_secret_store.py` + `test_aliyun_kms_secret_store.py` 追加

**Interfaces:**
- Produces: `async def delete(self, name: str) -> None`(Protocol,docstring:"Idempotent — deleting an absent name does NOT raise.");`KmsBackend.delete_secret(name: str) -> None`。T6/T7/T8 消费。

- [ ] **Step 1: 写失败测试**:LocalDev put→delete→get 抛 SecretNotFoundError;delete 不存在的 name 不抛;AliyunKms wrapper:delete 调 backend.delete_secret 且随后 get 不走缓存(缓存失效断言,照该测试文件既有 fake backend 风格)。
- [ ] **Step 2: 跑红**
- [ ] **Step 3: 实现**。LocalDev:`self.secrets.pop(name, None)`(注意该类真实属性名以文件为准);AliyunKms:透传 + 从读缓存剔除该 name(缓存结构以文件为准)。Protocol 是 `@runtime_checkable`?若是,注意 plain `async def` 加 docstring-only body 的先例写法(历史坑)。
- [ ] **Step 4: 跑绿**(`uv run pytest packages/expert-work-runtime/tests/test_secret_store.py packages/expert-work-runtime/tests/test_aliyun_kms_secret_store.py -q`);**全仓 grep `SecretStore` 的其他实现/测试桩**(`grep -rn "class.*SecretStore\|SecretStore)" --include="*.py" packages services | grep -v test_secret`),Protocol 加方法可能让 mypy 挑剔某些桩——都补上 delete。
- [ ] **Step 5: Commit** `feat(runtime): SecretStore.delete 原语(幂等;LocalDev+KMS wrapper)`

---

### Task 3: McpOAuthConnectionStore 按目录三方法

**Files:**
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/mcp_oauth_connection/base.py` + `sql.py` + `memory.py`(如有;`ls` 确认实现文件)
- Test: `packages/expert-work-persistence/tests/test_memory_mcp_oauth_connection_store.py` + SQL 对应文件追加(无则照 test_sql_* 先例在既有真容器测试文件加)

**Interfaces:**
- Produces(T8 消费,全部跨租户——平台治理视角,SQL 侧注意该表若 FORCE RLS 需 bypass 会话或超级用户测试路径,照 store 内其他跨租户方法先例;若无先例,方法 docstring 注明"platform-scope caller only"):
  - `async def count_for_catalog(self, *, catalog_id: UUID) -> int`
  - `async def list_for_catalog(self, *, catalog_id: UUID, limit: int = 1000) -> list[McpOAuthConnectionRecord]`
  - `async def delete_for_catalog(self, *, catalog_id: UUID) -> int`

- [ ] **Step 1: 写失败测试**:两租户各 1 连接挂 catalogA + 1 连接挂 catalogB;count(A)=2、list(A) 返回含两租户记录(含 ref 字段)、delete(A)=2 且 B 不动;空 catalog 三方法 0/[]/0。
- [ ] **Step 2: 跑红**
- [ ] **Step 3: 实现**(SQL 谓词 `catalog_id ==`;list 按 created_at 升序 limit;delete 裸 DELETE 返回 rowcount)
- [ ] **Step 4: 跑绿**
- [ ] **Step 5: Commit** `feat(persistence): mcp_oauth_connection 按目录 count/list/delete(目录删除防护地基)`

---

### Task 4: 迁移 0132 存量孤儿 role_binding 清理

**Files:**
- Create: `packages/expert-work-persistence/migrations/versions/0132_role_binding_orphan_cleanup.py`(`down_revision = "0131_retention_grants"`;模块结构照 0131 先例)
- Test: 集成测试(照 test_retention_grants.py 的真容器+alembic 模式):upgrade 前造数据 → upgrade → 断言

**Interfaces:**
- Produces: 升级即清存量孤儿;downgrade no-op(注释说明不可逆是意图)。

- [ ] **Step 1: 写迁移**,upgrade 单条 SQL(spec §A3 原文):

```python
op.execute(
    """
    DELETE FROM role_binding rb
    USING tenant_member tm
    WHERE tm.tenant_id = rb.tenant_id
      AND tm.keycloak_user_id = rb.subject_id::text
      AND rb.subject_type = 'user'
      AND rb.platform_scope = false
      AND tm.status IN ('revoked', 'suspended')
    """
)
```

- [ ] **Step 2: 写集成测试**:迁到 0131 → 插 tenant_member(status=revoked,keycloak_user_id=K)+ 对应 role_binding(subject_id=K)+ 一个 active member 的 binding + 一个 platform_scope binding → upgrade head → 孤儿没了、另两行还在。(alembic 分段迁移做法照仓库既有先例;若无"迁到中间版本"先例,则:全量迁移后手工插入孤儿数据、直接执行 upgrade 的 DELETE SQL 语句本体做等价断言,并在测试 docstring 说明。)
- [ ] **Step 3: 跑绿**(`DOCKER_HOST=... uv run pytest <测试文件> -x`);变异自验:join 键改成 `tm.subject_id::text` → 测试变红,恢复。
- [ ] **Step 4: Commit** `feat(persistence): 0132 一次性清理已撤销/停用成员的存量 role_binding 孤儿`

---

### Task 5: members revoke/suspend 接线删授权

**Files:**
- Modify: `services/control-plane/src/control_plane/api/members.py`(revoke 端点 ~L286-340 两分支)
- Test: `services/control-plane/tests/test_members_api.py` 追加

**Interfaces:**
- Consumes: T1 `delete_for_subject`。
- Produces: revoke/suspend 审计 details 增 `role_bindings_removed: int`。

- [ ] **Step 1: 写失败测试**:invited→revoked 与 active→suspended 两场景——先给 member 配 binding(照 test 文件里 member_ops 邀请流程或直插 store),撤销后 `list_for_subject` 为空、审计 emit 的 details 含 `role_bindings_removed`(测试桩 audit 捕获断言,照该文件既有审计断言风格);`keycloak_user_id=None` 的 member 撤销不炸、计数 0。
- [ ] **Step 2: 跑红**
- [ ] **Step 3: 实现**:两分支状态转移成功后:

```python
removed = 0
if member.keycloak_user_id is not None:
    try:
        removed = await role_bindings.delete_for_subject(
            subject_type="user",
            subject_id=UUID(member.keycloak_user_id),
            tenant_id=tenant_id,
        )
    except Exception:
        logger.warning("member_revoke.role_binding_cleanup_failed", exc_info=True)
```

  handler 签名注入 role_binding store dep(该文件/邻文件取 store 的既有写法为准);审计 details 加计数;删失败不回滚状态转移。
- [ ] **Step 4: 跑绿**(`uv run pytest services/control-plane/tests/test_members_api.py -q`)
- [ ] **Step 5: Commit** `fix(control-plane): 成员撤销/停用同步删除其租户授权(JWT 期内活权限洞)`

---

### Task 6: platform_config canonical helper + 5 端点密文清理

**Files:**
- Modify: `services/control-plane/src/control_plane/api/platform_config.py`(新增 `_canonical_secret_name`;5 处写点(:273-275/:363/:623/:667 附近)改用 helper;5 个 delete 端点注入 secret_store + 条件删密文)
- Test: `services/control-plane/tests/` 里 platform_config 测试文件追加(`ls | grep -i platform_config` 定位)

**Interfaces:**
- Consumes: T2 `SecretStore.delete`。
- Produces:

```python
def _canonical_secret_name(
    *, provider: str | None = None, key_id: str | None = None,
    tool: str | None = None, tenant_id: UUID | None = None,
) -> str:
    """重建平台代管密文的 canonical 槽位名(写/删共用,见 spec §B2)。"""
```

  四种形态:`expert-work/platform/llm/{provider}`(+`/{key_id}` if key_id not in (None,"default"))/`expert-work/platform/tool/{tool}`/`expert-work/platform/tenant/{tenant_id}/llm/{provider}`/`expert-work/platform/tenant/{tenant_id}/tool/{tool}`。**逐字节等于现有 5 处内联拼名**(先 `git show HEAD:` 抄原文再抽)。

- [ ] **Step 1: 写失败测试**:① helper 四形态与旧拼名逐字节回归(把旧字符串字面量写死在测试里);② 每个 delete 端点两场景:paste 模式创建(secret_ref=canonical)→ 删除 → LocalDev 密文消失 + 审计 details 含 `secret_deleted: true`;ref-mode 创建(secret_ref="secret://external/thing")→ 删除 → 外部名密文原样(先 put 一个同名密文进 store 作哨兵)+ `secret_deleted: false`。
- [ ] **Step 2: 跑红**
- [ ] **Step 3: 实现**:删除流程 = `get` 拿行 → 行存在且 `secret_ref == f"secret://{canonical}"` → `await secret_store.delete(canonical)` best-effort(try/except 记 warning)→ 照旧 `store.delete_*` → 审计 details 加 `secret_deleted`。写点全部改用 helper。
- [ ] **Step 4: 跑绿** + 变异自验:比对改永真 → ref-mode 哨兵测试变红,恢复。
- [ ] **Step 5: Commit** `fix(control-plane): 平台/租户密钥删除同步清理代管密文(canonical 名判定,外部引用不动)`

---

### Task 7: mcp_servers 删除清密文 + oauth disconnect 真删 + 审计标签

**Files:**
- Modify: `services/control-plane/src/control_plane/api/mcp_servers.py`(delete 端点 ~L987-1041)
- Modify: `services/control-plane/src/control_plane/api/mcp_oauth_api.py`(disconnect ~L406-449 覆写改 delete;:375-385 与 :438-448 两处 `resource_type` 改 `"mcp_oauth_connection"`)
- Test: `services/control-plane/tests/test_mcp_servers_api.py` + `test_mcp_oauth_api.py` 追加/修改

**Interfaces:**
- Consumes: T2 `SecretStore.delete`。

- [ ] **Step 1: 写失败测试**:① mcp server 带 token+headers 创建 → 删除 → 两 ref 的密文从 LocalDev 消失(server 域 paste-only,无外部 ref 顾虑);无 ref 的 server 删除不炸。② disconnect → 密文真没了(get 抛 SecretNotFoundError,而非空串)。③ 两处审计 `resource_type == "mcp_oauth_connection"`(既有断言若锁旧值,改断言——这正是修 bug)。
- [ ] **Step 2: 跑红**
- [ ] **Step 3: 实现**:mcp_servers delete 注入 secret_store,`record.token_secret_ref`/`record.custom_headers_ref` 存在即 `parse_secret_ref` 取名 best-effort delete;disconnect 的 `put(ref, "")` 循环改 `delete(parse_secret_ref(ref))`(保留 try/except + warning);两处 resource_type 字面量替换。
- [ ] **Step 4: 跑绿**(两测试文件 + `test_mcp_oauth.py` 回归)
- [ ] **Step 5: Commit** `fix(control-plane): MCP 服务器删除/OAuth 断开清理密文 + 审计 resource_type 修正`

---

### Task 8: mcp_catalog 409+force 级联 + 0133 FK RESTRICT

**Files:**
- Modify: `services/control-plane/src/control_plane/api/mcp_catalog.py`(delete 端点 ~L402-446)
- Create: `packages/expert-work-persistence/migrations/versions/0133_mcp_oauth_catalog_fk_restrict.py`(`down_revision = "0132_role_binding_orphan_cleanup"`)
- Test: `services/control-plane/tests/test_mcp_catalog_api.py` 追加 + FK 行为集成测试

**Interfaces:**
- Consumes: T2 delete、T3 三方法。
- Produces: DELETE `/{catalog_id}?force=` 语义(spec §C2);审计 details `connections_removed/secrets_removed/secrets_failed`。

- [ ] **Step 1: 写失败测试**:①无连接 → 204 照删;②有连接(两租户两条)不带 force → 409 body 含 `CATALOG_HAS_OAUTH_CONNECTIONS` + count=2,连接行还在;③ `?force=true` → 204,连接行没了、4 个 token 密文从 LocalDev 消失、审计 details 三计数正确;④ tenant_mcp_server 在用 → 409 CATALOG_IN_USE 优先(带 force 也拦——实例必须先删);⑤ FK 集成:直调 store.delete(catalog_id) 而 oauth 连接仍在 → McpConnectorCatalogInUseError(RESTRICT 兜底)。
- [ ] **Step 2: 跑红**
- [ ] **Step 3: 写 0133**:用 `information_schema.table_constraints`/`pg_constraint` 查 `mcp_oauth_connection` 上引用 `mcp_connector_catalog` 的真实 FK 名(0063 inline 定义无显式名,**不赌 auto-name**)→ `op.drop_constraint(实名)` → `op.create_foreign_key("mcp_oauth_connection_catalog_id_fkey", ..., ondelete="RESTRICT")`;downgrade 对称回 CASCADE。查名可在 upgrade 里用 `op.get_bind().execute(text(...)).scalar()` 动态取。
- [ ] **Step 4: 实现端点**:`force: bool = False` query 参数;顺序=①tenant_mcp_server 在用检查(既有,优先)→ ②`count_for_catalog>0 and not force` → 409 带 count → ③force:`list_for_catalog` 逐条对 access/refresh 两 ref best-effort `secret_store.delete`(计 secrets_removed/failed)→ `delete_for_catalog` → ④`store.delete(catalog_id)` → 审计。
- [ ] **Step 5: 跑绿**(`test_mcp_catalog_api.py` + `test_mcp_catalog_instantiation.py` 回归 + FK 集成测试,DOCKER_HOST)
- [ ] **Step 6: Commit** `fix(control-plane): MCP 目录删除 409 防护+force 级联清理 OAuth 连接与密文;0133 FK 改 RESTRICT`

---

### Task 9(终门): 全量校验

- [ ] `uv run ruff check .` + `uv run ruff format --check .`
- [ ] CI 同款 mypy:`uv run mypy packages services/audit-backup-worker/src services/billing-rollup-job/src services/event-log-archive-job/src services/orchestrator/src services/retention-cleanup-job/src`
- [ ] pytest:persistence 全套 + `services/control-plane/tests/test_members_api.py test_mcp_servers_api.py test_mcp_oauth_api.py test_mcp_oauth.py test_mcp_catalog_api.py test_mcp_catalog_instantiation.py` + platform_config 测试文件 + runtime secret store 两文件(集成需 DOCKER_HOST;红先对照 main 基线再定性)
- [ ] opus 全分支终审(review-package merge-base..HEAD)
- [ ] push + PR
