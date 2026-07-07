# Stream R — 公司成员 Onboarding + per-user Agent 编排(设计先行)

> 状态:设计先行(PR A)。后续 PR B–… 按本文 Mini-ADR 实现,实现时**按 wave 合并 PR**(§8)。
> 关联:延续 [STREAM-Q-DESIGN](./STREAM-Q-DESIGN.md)(加密金库 `SqlEncryptedSecretStore` —— Keycloak Admin 凭据从此取)、Stream N(`system_admin` / `role_binding` 平台域)、Stream P(`POST /v1/tenants`)、Stream J.14(`tenant_user` per-user 注册表)、Stream J.15(per-user workspace)。
> 范围由用户拍板(2026-06-01),本文不重新质疑已定决策。

---

## 0. 背景与范围

### 0.1 触发问题

跑 canonical-agent E2E 前用户追问:"新增管理员 / 租户新增账号怎么办?" 审计暴露真实缺口 ——
**一个公司无法从零把人接进来用**:

- 新增**平台管理员** ✅ 通(`POST /v1/role_bindings` platform_scope + `SettingsRoleBindings.tsx`);bootstrap 解首个鸡生蛋。
- 租户**新增成员** ⚠️ 半通:员工登录后 JIT 建 `tenant_user` 行 + admin 手工赋 role 能走,但
  **Keycloak `registrationAllowed:false`,新人账号从哪来无解** —— Expert Work 无邀请流程 / 无 Keycloak Admin API 集成 / 无成员 UI。

这与目标产品形态(per-user 持久 agent;租户=公司,用户=公司的人)直接冲突 —— 没有成员
onboarding,公司无法 onboard 员工。当初推迟它的 STREAM-H-DESIGN §0"Business 系统通过 API
消费 agent,Expert Work 不做用户面"是**过期断言**,本 Stream 作废之。

### 0.2 目标链路

```
system_admin 建公司(租户)+ 首个 admin 一步到位
  → admin 邀请员工
  → Expert Work 调 Keycloak Admin API 自动开账号
  → 员工登录(Keycloak JWT 带 tenant_id 属性)
  → 员工首个 run 时惰性初始化专属 agent 实例(thread+checkpoint+memory+workspace 数据对象)
```

### 0.3 四个已定决策(用户拍板,锁定)

1. **邀请态建表**:`tenant_member` 表,状态机(`invited→active→suspended/revoked`)+ 审计 + list。不复用 `tenant_user`(后者是登录后 JIT 注册表,无状态字段)。
2. **删装饰性 Keycloak realm role `admin`**:Keycloak 只管认证 + `tenant_id` 属性;细粒度授权全走 Expert Work `role_binding` 表。已审计确认 `Principal` 不读 `roles` claim 做授权。
3. **Keycloak Admin 凭据走 Stream Q 加密金库**(`SqlEncryptedSecretStore`);service account 用现有 `expert-work-api-internal`(已 `serviceAccountsEnabled:true`)配最小 `realm-management` 角色 `manage-users`。
4. **per-user agent 惰性初始化**:首个 run 时,非首登同步。

### 0.4 范围(2026-06-01 纲要拍板)

- 链路终点 = **员工用上自己的 agent**(不止 admin 能管)。
- 三个 Wave:W1 地基 → W2 成员 onboarding → W3 per-user agent 编排,**W3 本轮就做**(不溢出)。
- **租户可配默认 agent 保留**(W3 做):`tenant_config.default_agent_name`,公司可指定员工默认用哪个 agent;NULL → 平台 fallback `canonical-agent`。

### 0.5 范围纪律(simplicity first)

后端做**生产级**(跨系统事务一致性、状态机、审计天生通用);**出口窄**。显式**不做**:
Keycloak group/role 同步、SCIM、SSO 联邦、邮件模板自定义 UI、member CSV 批量导入、自助注册
(`registrationAllowed` 保持 false)、per-员工 agent 模板(W3 只给"租户默认 agent"一个概念)、
member 角色变更端点(本版只 invite/list/resend/revoke + W3 自动 activate)、suspended→反激活
(本版 suspend 单向,需重邀;见 §5.2)。

---

## 1. 关键事实(已审计,file:line)

- `services/control-plane/src/control_plane/api/tenants.py:39` `CreateTenantRequest(tenant_id?, display_name, plan?)`;建租户走 `bypass_rls_session()`;仅 `is_system_admin`。
- `services/control-plane/src/control_plane/api/role_bindings.py:78` 创建 tenant-scope 绑定写死 `tenant_id=principal.tenant_id` —— 给别租户写**必须新路径**。但底层 `RoleBindingStore.create(*, tenant_id, subject_id: UUID, ...)`(`auth/base.py:218`)**接受显式 `tenant_id`**,新路径只需在 `bypass_rls_session()` 内传目标 `tenant_id`,不改 store。
- ⚠️ `RoleBindingStore.create` 的 `subject_id` 是 **`UUID`**;Keycloak user id 是 UUID 格式字符串 → `UUID(kc_user_id)` 可直接转。`Principal.subject_id` / `tenant_user.subject_id` 是 `str`。
- `packages/expert-work-persistence/.../tenant_user/{base,sql}.py` `resolve()` 幂等 upsert `(tenant_id, subject_type, subject_id)`,bump `last_active_at`;**无状态字段**。
- `services/control-plane/.../auth/middleware.py:107` `Principal.from_jwt_claims`,`tenant_id` 来自 claim;`:109` `resolve_system_admin` 查 platform_scope binding。`roles` claim 仅透传到 `Principal.roles`,**RBAC 不读它**(grep 确认)。
- `infra/keycloak/realm-expert-work.json`:`expert-work-api-internal` `serviceAccountsEnabled:true`/`publicClient:false`;`registrationAllowed:false`;`duplicateEmailsAllowed:false`;dev 用户有 `tenant_id` 属性 + 装饰性 `realmRoles:["admin"]`(待删)。
- `auth/rbac.py:63` `ADMIN` 对 `manifest/session/sandbox/secret/quota/tenant_config/user/role_binding/service_account/api_key/memory` 有 write。
- Stream Q `SqlEncryptedSecretStore`(`control_plane/encrypted_secret_store.py`):`get(name,*,version=None)` / `put(name, value)`,平台行 `tenant_id IS NULL` 走 `bypass_rls_session()`。backend `sql_encrypted`。
- `services/control-plane/.../api/runs.py` `trigger_run`:`resolve_caller_user_id`(走 `tenant_user.resolve`)→ `run_manager.create(user_id=...)` → `current_user_id_var.set`。**当前无 workspace ensure** —— per-user 数据对象初始化是 gap。
- `workspace/base.py` `UserWorkspaceStore.resolve(tenant_id, user_id)` 幂等 upsert + 确定性 volume name,**已存在但 runs.py 未调用**。
- audit Literal 双份:`protocol/audit.py` `resource_type` Literal + `control_plane/audit.py` `ResourceType` Literal —— 加新值**两处同改**([[project_audit_literal_drift]])。
- ⚠️ **`EmailStr` 不可用**:`email-validator` 不在依赖,全仓无人用 → email 字段用普通 `str` + 轻量校验 + `lower()` normalize,**不引入 `pydantic[email]` 新依赖**。
- 最新 migration `0050_encrypted_secret`。alembic revision id ≤ 32 字符([[feedback_alembic_revision_id_32_chars]])。
- RLS 模式(0005/0015):`ENABLE/FORCE` + `USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)`;tenant-less / 跨租户写走 `bypass_rls_session()`。

---

## 2. Mini-ADRs(统一 R- 前缀)

- **R-1** Keycloak Admin client 封装放**新 module** `services/control-plane/src/control_plane/keycloak/`(`admin_client.py` + `token.py` + `errors.py`),不放 expert-work-common(它依赖金库 glue + httpx,expert-work-common 不应反依赖 control-plane)。Protocol `KeycloakAdminClient` + `HttpKeycloakAdminClient` 实现 + `FakeKeycloakAdminClient` 单测替身。
- **R-2** Admin token 走 service-account `client_credentials` grant 打 `/realms/expert-work/protocol/openid-connect/token`;进程内缓存到 `exp-30s` 重取。client_secret 从金库 `expert-work/platform/keycloak/admin-client-secret` 取。
- **R-3** **建号 = Keycloak 原生流**:`POST /admin/realms/{realm}/users` → `PUT .../execute-actions-email`(actions `["UPDATE_PASSWORD","VERIFY_EMAIL"]`)。Expert Work **不自管邀请 token / 不自建邮件**;Keycloak 发设密码邮件,链接有效期由 `lifespan` 控制。零 token 存储面 = 零泄露面。
- **R-4** **跨系统无分布式事务,用 DB-first + 幂等补偿**:先写本地 DB(member `invited` / 建租户),再调 Keycloak(外部、可重试);Keycloak 成功回填 `keycloak_user_id` + 审计;失败留本地一致态,`resend` 端点幂等补偿。**绝不**先建 Keycloak 再写 DB。
- **R-5** W1 建租户 first_admin 用**新 cross-tenant role_binding 写路径**:`tenants.py` 内 `bypass_rls_session()` 直接 `role_binding_repo.create(tenant_id=<new>, subject_id=UUID(kc_user_id), role=ADMIN, ...)`,不复用 `role_bindings.py:78`。不新增 store 方法。
- **R-6** `tenant_member` = 邀请态名册(control-plane 事实源);`tenant_user` = 登录后 JIT 运行态注册表。不合并、无 FK。对齐:员工首登 `tenant_user.resolve` 后,W3 编排经 `keycloak_user_id` 把 member 推 `invited→active`(回填 `subject_id = tenant_user.id` + `activated_at`)。
- **R-7** ⚠️ **实施期纠正(PR #351)**:原设计断言"删装饰 realm role 安全,Expert Work 不读 `roles` 做授权" —— **错的**。`rbac.py` 的 `is_allowed`/`is_admin` **正是**读 `principal.roles`(来自 JWT `roles` claim);只有 `system_admin`(平台级)走 `role_binding` 表,**租户级角色没接授权层**。所以删 realm role 会断 dev 租户权,且邀请来的员工(角色写在 `role_binding`)会无权限。**改为**:新增 `resolve_tenant_roles`,middleware 让 JWT 用户也从 `role_binding` enrich 租户角色进 `principal.roles`(接上 rbac 标的 "future enrichment");**保留** realm role(dev 租户权唯一来源)。这才是"应用自管授权"真正成立的前提。
- **R-8** ⚠️ **实施期纠正(PR #353)**:原设计要 `trigger_run` 调 `UserWorkspaceStore.resolve` 建 workspace 行 —— **砍掉**。`UserWorkspaceStore` 是 **sandbox-supervisor 域、无 RLS**,且 supervisor **已在员工首次 exec 工具时惰性建行**(`supervisor.py:158` "resolve creating on first use");control-plane 再 resolve = 重复 + 跨服务边界拉不属于它的 store。**改为** `ensure_member_active`:`trigger_run` 首 run hook(`resolve_caller_user_id` 后)经 `get_by_keycloak_user_id` 反查,member 仍 `invited` 则 `transition(active)` 回填 `subject_id`(R-6)。workspace 由 supervisor 就近惰性建;memory namespace 由 `current_user_id_var` + user-level RLS 隐式建立,无需建表。
- **R-9** **租户默认 agent**(W3 保留):`tenant_config` 加 `default_agent_name TEXT NULL`(独立 migration `0052`,属 W3 可独立回滚)。员工建 thread 未指定 agent 时:租户默认非 NULL → 用之,否则平台 fallback `canonical-agent`。**不给每员工存模板**。
- **R-10** member 邀请幂等键 = `(tenant_id, lower(email))` partial unique `WHERE status != 'revoked'`:同 email 同租户不能两条活跃邀请;revoke 后可重邀。`resend` 不新建行。
- **R-11** Keycloak `409 User exists` → Expert Work `409 MEMBER_KEYCLOAK_CONFLICT`,member 留 `invited`/`keycloak_user_id=NULL`。**不跨租户自动复用** Keycloak 账号(一邮箱一租户;`duplicateEmailsAllowed:false` 已锁)。
- **R-12** email 字段用普通 `str` + 校验函数(非空、含 `@`、长度上限、`lower()` normalize),**不引入 `email-validator`/`pydantic[email]`**(simplicity first;引依赖单独决策)。

### W4 追加(2026-06-01,实跑 E2E 前置收尾;纯环境接线,零 product 代码改动)

- **R-13** dev Keycloak 启用 = compose 加 `EXPERT_WORK_KEYCLOAK_ENABLED: ${...:-false}`(默认 false→`full`-only 栈 / CI integration 无 `auth` profile 时仍用进程内 Fake;dev 在 `infra/.env` 开 true)。realm `expert-work-admin-ui` 的 `rootUrl`/`redirectUris`/`webOrigins` `:3000`→`:5173`(对齐 vite 真实端口,否则浏览器登录 redirect_uri mismatch)。
- **R-14** Keycloak admin client secret 种入 = 新 CLI `control_plane.seed_keycloak_secret`(镜像 `bootstrap_admin`:建同款 `SqlEncryptedSecretStore` → `put(keycloak_admin_secret_name, value)`,幂等写新版本)。value 走 `--value`/env,**绝不进容器运行时 env**(运行时只从金库取,settings 无该字段)。`sql_encrypted` 后端专用;`local_dev` 走 dev-keys 文件。
- **R-15** first_admin 全程 web = `SettingsCreateTenant` 暴露 `first_admin_email` + `first_admin_display_name` 字段,成功回显 first_admin(email/status,提示去 Keycloak 控制台设密码);`api/tenants.ts` 加 `CreatedTenant`(record + 可选 `first_admin`)+ i18n(en/zh-CN)+ e2e。补 W1 API 能力的 UI 缺口。
- **R-16** 无 SMTP 设密码路径 = 两条 provision 路径(`invite_member`/`provision_first_admin`)已先写账号+role binding、再 best-effort 发邮件且容错失败(代码既有,无需改)→ dev 无 SMTP 时仍 201,管理员在 Keycloak 控制台手设密码。subject-id 改用服务账号 Admin API 查(绕开 admin-ui 已禁的 password grant);文档 exec 目标 `control-plane`→`control-plane-blue`(蓝绿对真实服务名)。

---

## 3. 数据模型

### 3.1 `tenant_member` 表(migration `0051_tenant_member`,down_revision=`0050_encrypted_secret`,id=18 字符 OK)

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | UUID PK `gen_random_uuid()` | member 行代理键 |
| `tenant_id` | UUID NOT NULL | RLS 隔离边界 |
| `email` | Text NOT NULL | 邀请目标;`lower()` 用于幂等键 |
| `display_name` | Text NULL | 邀请时可选 |
| `role` | Text NOT NULL | `admin`/`operator`/`viewer`(tenant-scope 子集,不含 system_admin) |
| `status` | Text NOT NULL | `invited`/`active`/`suspended`/`revoked` |
| `keycloak_user_id` | Text NULL | Keycloak 建号成功后回填;NULL = 待重试 |
| `subject_id` | UUID NULL | accept 时回填 `tenant_user.id`(R-6) |
| `invited_by` | Text NOT NULL | actor(`principal.subject_id`) |
| `invited_at` | timestamptz NOT NULL `now()` | |
| `activated_at` | timestamptz NULL | 首登推进 active 时落 |
| `updated_at` | timestamptz NOT NULL `now()` | 每次状态转移更新 |

**约束 / 索引**:
- `tenant_member_tenant_idx` ON `(tenant_id)`。
- partial unique `tenant_member_active_email_uniq` ON `(tenant_id, lower(email)) WHERE status != 'revoked'`(R-10)。
- `tenant_member_kc_user_idx` ON `(keycloak_user_id) WHERE keycloak_user_id IS NOT NULL`(W3 首登反查)。
- CHECK `status IN ('invited','active','suspended','revoked')`。
- CHECK `tenant_member_active_consistency`:`status='active'` ⇒ `keycloak_user_id IS NOT NULL AND activated_at IS NOT NULL`。

**RLS**(canonical tenant-isolation,同 0005/0015):
```sql
ALTER TABLE tenant_member ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_member FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_member_tenant_isolation ON tenant_member
    USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
```
W1 建租户 / W3 首登推进发生在跨租户 / tenant-less 上下文,这些写走 `bypass_rls_session()`(显式传 `tenant_id`);admin 自己邀请 / list 走请求态 RLS(`app.tenant_id` = admin 的 tenant)。

### 3.2 状态机

```
        invite()                  首登 W3 编排                 admin DELETE(active)
NULL ──────────────► invited ──────────────────► active ──────────────────► suspended
                       │                            (单向,本版不反激活,需重邀)
              revoke() │ (admin DELETE on invited)
                       ▼
                    revoked (软删,可重邀)
```
- `invited→active`:**仅** W3 首登编排触发(非 admin 手动)。
- `active` 的 admin `DELETE` → `suspended` + Keycloak `enabled:false`(active 成员不 hard delete,保审计连续);**本版单向**,反激活留后续。
- `invited` 的 admin `DELETE` → `revoked` + Keycloak `DELETE users/{id}`(若已回填 id)。

### 3.3 `tenant_config.default_agent_name`(R-9,migration `0052_tenant_default_agent`,W3)

`ALTER TABLE tenant_config ADD COLUMN default_agent_name TEXT NULL;`(NULL → 平台 fallback)。
`TenantConfigPatch` + `TenantConfigRecord` 加 `default_agent_name: str | None`。独立 migration 保 wave 可独立回滚。

### 3.4 audit Literal 扩展(两处同改,[[project_audit_literal_drift]])

`protocol/audit.py` + `control_plane/audit.py` 两个 `resource_type` Literal 都加 `"tenant_member"` + `"keycloak_user"`。`AuditAction` 加:`MEMBER_INVITE`/`MEMBER_RESEND`/`MEMBER_REVOKE`/`MEMBER_SUSPEND`/`MEMBER_ACTIVATE`(W3 首登)/`KEYCLOAK_USER_CREATE`/`KEYCLOAK_USER_CREATE_FAILED`。

---

## 4. Keycloak Admin Client 封装(W1 地基)

### 4.1 落点

```
services/control-plane/src/control_plane/keycloak/
  ├── __init__.py
  ├── admin_client.py     # Protocol + HttpKeycloakAdminClient
  ├── token.py            # service-account client_credentials token cache
  └── errors.py           # KeycloakError 层级
services/control-plane/tests/keycloak/fake_admin_client.py   # 单测替身
```

### 4.2 配置(`control_plane/settings.py` 加字段)

```python
keycloak_base_url: str = "http://localhost:8080"
keycloak_realm: str = "expert-work"
keycloak_admin_client_id: str = "expert-work-api-internal"
keycloak_admin_secret_name: str = "expert-work/platform/keycloak/admin-client-secret"  # 金库取
keycloak_email_action_lifespan_s: int = 86400
keycloak_enabled: bool = False   # dev/CI 默认关 → 注入 Fake;integration/prod 开 → Http
```
client_secret 经 `SqlEncryptedSecretStore.get(keycloak_admin_secret_name)` 取(Stream Q 金库);
dev 初值由 runbook `secret_store.put` 写入,prod 轮换。

### 4.3 Protocol / 类签名

```python
@dataclass(frozen=True)
class KeycloakUser:
    id: str; username: str; email: str; enabled: bool

class KeycloakAdminClient(Protocol):
    async def create_user(self, *, email: str, tenant_id: UUID,
                          display_name: str | None) -> KeycloakUser:
        """POST /admin/realms/{realm}/users
           body: username=email,email=email,enabled=True,emailVerified=False,
                 attributes={"tenant_id":[str(tenant_id)]}
           201 → Location 末段拿 id → GET 回填。409 → KeycloakUserExistsError。"""
    async def send_setup_email(self, *, user_id: str, lifespan_s: int) -> None:
        """PUT .../users/{id}/execute-actions-email?lifespan=...
           body ["UPDATE_PASSWORD","VERIFY_EMAIL"];Keycloak 发邮件。"""
    async def set_enabled(self, *, user_id: str, enabled: bool) -> None:
        """PUT .../users/{id} {"enabled":...};suspend 用。"""
    async def delete_user(self, *, user_id: str) -> None:
        """DELETE .../users/{id};revoke 用;404 幂等成功。"""

class ServiceAccountTokenProvider:
    async def bearer(self) -> str:
        """缓存 access_token 到 exp-30s;过期重取(client_credentials grant)。
           asyncio.Lock 保护刷新临界区。"""
```

### 4.4 错误处理(`errors.py`)

`KeycloakError`(base)→ `KeycloakUnavailableError`(连接/5xx/超时 → 端点 502)、
`KeycloakUserExistsError(email)`(409)、`KeycloakAuthError`(token grant 401 = 配置错 → 500)。
httpx timeout 10s connect / 30s read;**不重试 create**(避免重复建号);`send_setup_email` 失败
**不回滚 create**(`resend` 补发)。

### 4.5 realm json 改动(W1 一并)

给 `expert-work-api-internal` service account 加 `realm-management` client role **仅 `manage-users`**
(不给 `realm-admin`)。dev `keycloak_enabled=False` 注入 `FakeKeycloakAdminClient`(内存 dict,建号
返回伪 UUID),W1/W2 CI 不依赖真 Keycloak。

---

## 5. 各 Wave 设计

### W1 — 地基:建租户 + first_admin 事务

**5.W1.1 `CreateTenantRequest` 扩展**(`tenants.py:39`):加 `first_admin_email: str | None`(R-12 普通校验)
+ `first_admin_display_name: str | None`;`None` → 建空租户(向后兼容)。validator:有 display_name 必须有 email。

**5.W1.2 后端步骤 + 失败语义**(R-4,`bypass_rls_session()` 内):
```
1. repo.create(tenant)              # 本地,同事务可回滚。first_admin None → emit + 返回(现状路径)
2. member_repo.create(invited, role=ADMIN, kc_user_id=NULL)   # 本地;到此 DB 一致(租户+invited admin)
3. kc = await keycloak.create_user(...)
     409 → 409 MEMBER_KEYCLOAK_CONFLICT(member 留 invited;租户已建,改邮箱重邀)
     unavailable → 502 KEYCLOAK_UNAVAILABLE(member 留 invited;resend 补)
4. member_repo.set_keycloak_user_id(member.id, kc.id)         # 回填
5. role_binding_repo.create(tenant_id=<new>, subject_id=UUID(kc.id), role=ADMIN, ...)  # cross-tenant(R-5)
6. await keycloak.send_setup_email(...)   # 失败不回滚(用户+binding 已成);member 留 invited,resend 补邮件
7. emit TENANT_CREATE + KEYCLOAK_USER_CREATE + MEMBER_INVITE + ROLE_BINDING_CREATE → 201
```
核心:本地 1–2 同事务原子;Keycloak 段(3–6)外部副作用,DB-first 保证任何失败点落"本地一致 +
可重试"。`resend`(W2)是统一补偿入口。

**5.W1.3 端点 contract**:
```
POST /v1/tenants  (system_admin only)
req: { display_name, plan?, tenant_id?, first_admin_email?, first_admin_display_name? }
201: { success, data:{ tenant:{...}, first_admin:{member_id,email,status:"invited",keycloak_user_id}|null }, error:null }
409 TENANT_ALREADY_EXISTS / 409 MEMBER_KEYCLOAK_CONFLICT / 502 KEYCLOAK_UNAVAILABLE / 403 PLATFORM_SCOPE_FORBIDDEN
```

**5.W1.4 删装饰 realm role(R-7)**:删 realm json dev 用户 `realmRoles` + realm role 定义;保留
`tenant_id` 属性 + mapper。回归测试断言"JWT 无 roles claim 时 system_admin 仍由 binding 解析"。
独立小改动,易回滚。

### W2 — 成员 onboarding:member 表 + 邀请流程

**5.W2.1 Store**(`packages/expert-work-persistence/.../tenant_member/{base,sql,memory}.py`):
`create`(invited,partial-unique 冲突 → `DuplicateMemberError`)、`get`、`get_by_keycloak_user_id`
(W3 反查)、`list_for_tenant(status?, limit, offset)`、`set_keycloak_user_id`、
`transition(member_id, tenant_id, to, subject_id?, now)`(乐观 WHERE 合法前驱,返回 False = 非法/竞态)。

**5.W2.2 邀请 API**(`api/members.py`,`/v1/members`;tenant scope = `principal.tenant_id` 请求态 RLS):
```
POST /v1/members/invite        require user:write   batch(max 50){email,role,display_name?}
  逐条:member.create(invited) → keycloak.create_user → set_kc_id → role_binding.create(本租户)
        → keycloak.send_setup_email → emit;per-item 独立(一条 409 不阻断其它)→ per-item 结果
GET  /v1/members               require user:read    status?,limit,offset → {items,total}
POST /v1/members/{id}/resend   require user:write    幂等补偿(R-4):kc_id NULL→建号+binding;否则补发邮件
DELETE /v1/members/{id}        require user:write    invited→revoked(+kc delete);active→suspended(+kc disable)
```

**5.W2.3 member ↔ tenant_user 对齐(R-6)**:经 `keycloak_user_id` 连接,无 FK(FORCE-RLS 表 FK 是
已知 footgun)。对齐时刻 = W3 首登。

**5.W2.4 前端 `SettingsMembers.tsx`** + `api/members.ts`:遵循 [[project_admin_ui_design_baseline]]
(dark-first / cyan+violet / Inter+JBMono / 单面操作端)。成员表 + 邀请 drawer(batch email+role)+ 行操作
(resend/revoke);status badge:invited=violet / active=cyan / suspended=amber / revoked=muted。
envelope 契约对账([[feedback_envelope_vs_raw_contract_check]])。

### W3 — per-user agent 惰性初始化

**5.W3.1 gap**:per-user 实例 = 数据对象(thread+checkpoint+memory+workspace)。现状缺:
① workspace volume(`UserWorkspaceStore.resolve` 存在但 `trigger_run` 未调用);② member 首登推进
(R-6);③ 租户默认 agent fallback(R-9)。memory namespace 已由 `current_user_id_var` + user-level
RLS 隐式建立。

**5.W3.2 `ensure_user_instance` hook(R-8)**:`trigger_run` `resolve_caller_user_id` 后、`run_manager.create`
前插入(仅人类用户):
```python
async def ensure_user_instance(request, *, tenant_id, user_id, principal):
    await ws_repo.resolve(tenant_id=tenant_id, user_id=user_id)          # ① workspace 行(幂等)
    async with bypass_rls_session():                                     # ② member 首登推进
        m = await member_repo.get_by_keycloak_user_id(keycloak_user_id=principal.subject_id)
        if m and m.status == "invited":
            await member_repo.transition(member_id=m.id, tenant_id=m.tenant_id,
                                         to="active", subject_id=user_id, now=now())
            # emit MEMBER_ACTIVATE
    # ③ memory namespace 无需显式建表
```
幂等(每 run 调,第二次起 no-op upsert + WHERE no-match)。首版**不加缓存**(simplicity first;
代价 = 2 次轻量 DB 命中,可接受)。

**5.W3.3 租户默认 agent(R-9)**:thread 创建路径读 `tenant_config.default_agent_name`,未指定 agent
且非 NULL → 用之,否则平台 fallback `canonical-agent`。`default_agent_name` 写经 `tenant_config`
现有 upsert 端点 + UI select(W3 加)。

---

## 6. PR 拆分(逻辑单元;实现按 wave 合并,§8)

| 逻辑单元 | 内容 | 依赖 |
|----------|------|------|
| **R-1** Keycloak client | `keycloak/` module + Fake + settings + realm json `manage-users` + 金库接线 + 单测 | Stream Q 金库 |
| **R-2** 删 realm role | realm json 删装饰 role + auth 回归 | 无(可并行) |
| **R-3** member 表 | migration 0051 + audit Literal 两处 + `TenantMemberStore` + 单测 | 无 |
| **R-4** 建租户 first_admin | `CreateTenantRequest` 扩展 + DB-first 补偿 + cross-tenant binding + 集成测(Fake) | R-1, R-3 |
| **R-5** 邀请 API | `/v1/members` 五端点 + 状态机 + 幂等补偿 + 审计 + 集成测 | R-1, R-3 |
| **R-6** 成员 UI | `SettingsMembers.tsx` + `api/members.ts` + Playwright + envelope 对账 | R-5 |
| **R-7** 租户默认 agent | migration 0052 + `TenantConfig` 加列 + thread 创建读默认 | 无(可早做) |
| **R-8** per-user 编排 | `ensure_user_instance`(workspace.resolve + member 推进)接入 `trigger_run` + 集成测 | R-3, R-5, R-7 |

**实现合并**(按 wave,减 CI 等待):
- **PR A**(本文)设计先行。
- **W1 PR**:R-1 + R-2 + R-3 + R-4 合(地基 + 建租户事务;Keycloak client/member 表/删 role/事务一处落)。
- **W2 PR**:R-5 + R-6 合(邀请 API + UI)。
- **W3 PR**:R-7 + R-8 合(默认 agent + per-user 编排)。

> 关键路径:设计 A → W1 → W2 → W3。每个 wave PR 独立 CI-green、零债。

---

## 7. 风险

| # | 风险 | 缓解 |
|---|------|------|
| 1 | **Keycloak Admin 凭据泄露**(realm 全用户管理权)| 金库加密存(非 settings 明文 / 非 realm json prod);最小角色 `manage-users`;token 进程内缓存不落盘;审计/日志绝不含 secret;prod 轮换。 |
| 2 | **跨系统事务一致性** | DB-first + 幂等补偿(R-4):本地原子;Keycloak 失败落可恢复态;`resend` 统一补偿;绝不先建 Keycloak。 |
| 3 | **邀请邮件/链接安全** | 不自管 token(R-3)—— Keycloak 原生 `execute-actions-email`,`lifespan` 控期 + `VERIFY_EMAIL`;Expert Work 零 token 存储面。 |
| 4 | **删 realm role 回归** | 已审计 `principal.roles` 不参与授权;R-2 独立小 PR + 回归断言;易回滚。 |
| 5 | **per-user 编排开销** | `ensure_user_instance` 2 次轻量 upsert;workspace `resolve` 只写注册表行(物理 volume 由 supervisor 惰性建);首版不优化。 |
| 6 | **email 跨租户冲突** | `duplicateEmailsAllowed:false`(realm 已锁)+ Keycloak 409 → `MEMBER_KEYCLOAK_CONFLICT`(R-11);不跨租户复用。 |
| 7 | **member ↔ tenant_user 漂移** | `keycloak_user_id` 连接 + W3 首登 `transition(active)`;反查索引;无 FK 但 CHECK `active_consistency` 兜底。 |
| 8 | **audit Literal 双份漂移** | [[project_audit_literal_drift]]:R-3 同改两处;PR 内 grep 全仓确认仅两处定义。 |
| 9 | **subject_id 类型**(binding 要 UUID,KC id 是 str)| `UUID(kc.id)` 转;member `subject_id` 仍 UUID NULL(= tenant_user.id)。 |
| 10 | **EmailStr 缺失**(误引依赖)| R-12 普通 `str` + 校验函数,不引 `email-validator`。 |

---

## 8. Verification(本迭代完成 = 公司从零能用起来)

- **单测**:Keycloak client(token 过期重取 / 409 映射 / unavailable)、`TenantMemberStore`(状态机合法/非法、幂等键、kc 反查)、`ensure_user_instance` 幂等。覆盖 ≥ 80%。
- **集成**(FakeKeycloak,`-m "not integration"` 跑得动):W1 建租户+first_admin 全链 + 各失败点补偿;W2 invite/resend/revoke 幂等;W3 首 run 建 workspace 行 + member→active。真 Postgres 测 RLS + migration(revision id ≤ 32:`0051_tenant_member` / `0052_tenant_default_agent` OK)。
- **回归**:R-2 删 realm role 后全套 auth 绿;canonical-agent E2E 加"建公司→邀请→员工登录→员工 run"闭环。
- **preflight**(push 前,[[feedback_ruff_strict_lint_traps]] / [[reference_ci_lint_type_test_scopes]]):`pre-commit run --all-files`;`uv run python -m pytest -m "not integration"`;`git status` 看 `uv.lock` 漂移;CodeQL 自查(无 side-effect-in-assert / 无 log-injection 把 email/`subject_id` 进 `extra=` / 无 unused-global)。
- **零技术债收尾**([[feedback_zero_tech_debt]]):无 TODO、文档同步(本文 + ITERATION-PLAN backlog checkbox + PR 编号,[[feedback_iteration_plan_sync_after_ship]])、可观测齐全(Keycloak 调用 counter/histogram + member 状态转移 metric)、CI 全绿。

---

## 9. 后续(显式不做 / 留坑)

- 真 KMS-wrap Keycloak secret(等 aliyun_kms,继承 Stream Q 同坑)+ secret 轮换 runbook。
- member 角色变更端点、`suspended→active` 反激活(本版 suspend 单向)、member 与 role_binding 级联 GC。
- SCIM / SSO 联邦 / Keycloak group 同步 / 邮件模板自定义 / member CSV 批量导入。
- per-员工 agent 模板(超出 R-9 租户默认)。
- `ensure_user_instance` 进程内 LRU 缓存(首版不优化)。

---

> 承接 Stream Q 金库 + Stream N system_admin + Stream P `POST /v1/tenants` + J.14/J.15 per-user 基座,
> 无新增跨包依赖反转。落库为本文(PR A 设计先行),后续按 §8 wave PR 实现。
