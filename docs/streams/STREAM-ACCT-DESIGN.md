# Stream ACCT — 账号自助化(bootstrap 平滑 + 平台账号管理 UI)

> 延伸 Stream P(bootstrap admin)、Stream N(跨租户 system_admin)、Stream R(成员 onboarding)。
> 起因:评估认为 SSO 架构本身不过度(Keycloak OIDC 单 IdP + helix `role_binding` 双层权限是标准多租户形态),
> 但暴露两处运维摩擦——①生产无平台管理账号,要手跑脚本;②平台级账号/角色管理无 UI,只能脚本/直连 Keycloak。
> 本 Stream 消除这两处摩擦,不改 IdP 架构。

## 1. 范围

| 项 | 形态 | 痛点 |
|----|------|------|
| **P1** bootstrap 平滑 | 后端 | ①无平台管理账号要脚本 |
| **P2** 平台管理员管理页 | 前端 | ②平台角色管理无 UI |
| **P3** 跨租户成员视图 | 后端+前端 | system_admin 无法跨租户查成员 |

**不做**:替换 Keycloak;在 helix 内自管密码/身份库(那是重建认证,倒退);service_account 平台管理员(沿用 N 的 M1 推迟)。

## 2. P1 — 邮箱首登自动升 system_admin

### 鸡蛋问题(现状)
`POST /v1/role_bindings` 需 `is_system_admin`,而 `is_system_admin` 要查 `role_binding` 表 → 空表时无人能授第一个 admin。现状唯一出路 = 手跑 `python -m control_plane.bootstrap_admin`(Mini-ADR P-6)。

### 方案(Mini-ADR ACCT-1)
operator 在部署时配 env `HELIX_AGENT_BOOTSTRAP_ADMIN_EMAIL`。当满足**全部**条件时,首次带该邮箱的已验证 JWT 登录即自动获 platform `system_admin` 绑定:

1. `settings.bootstrap_admin_email` 已配(未配则整条逻辑不跑,零开销路径);
2. 当前 principal 经 `resolve_system_admin` 后仍**非** system_admin;
3. JWT 携带 `email` 且 `email_verified=true`,邮箱与配置值大小写不敏感相等;
4. `subject_id` 为 UUID 形(Keycloak `sub`);
5. **全局零 platform admin**(`list_platform_scope()` 空)。

满足 → 复用 `bootstrap_system_admin(store, subject_id=...)`(P-6 已有,per-subject 幂等)建绑定,principal 当场升格 `as_system_admin()`。

### 安全考量
- **零-admin 门控是核心安全闸**:一旦系统存在任一 platform admin(脚本或 API 授的),本逻辑永久不再触发 → 不能被用来事后提权。
- **email_verified 必检**:防未验证账号冒认配置邮箱。邮箱本身由 Keycloak 签发的 token 携带,攻击者无法在不控制该 KC 账号的前提下伪造。
- **break-glass 保留**:`bootstrap_admin.py` 脚本不删,作为 env 不便/邮箱不可用时的兜底。
- 竞态:同一 subject 并发首登 → `bootstrap_system_admin` per-subject 幂等 + 捕获 `DuplicateRoleBindingError` 兜底。

### 触点
- `protocol/auth.py`:`JWTClaims` +`email: str | None`、`email_verified: bool`;`Principal` 同;`from_jwt_claims` 传播。
- `jwt_verifier.py:_claims_from_payload`:解析 `email` / `email_verified`。
- `settings.py`:`bootstrap_admin_email: str | None = None`。
- `auth/system_admin.py`:新增 `maybe_bootstrap_system_admin(principal, store, *, bootstrap_email)`。
- `auth/middleware.py`:JWT 路径 `resolve_system_admin` 后接线;构造参 `bootstrap_admin_email`。
- `app.py`:注入 `settings.bootstrap_admin_email`。
- 复用 `RoleBindingStore.list_platform_scope()`(已有,memory/sql 均具备),不新增 store 方法。

## 3. P2 — 平台管理员管理页(前端)

后端 `/v1/role_bindings`(POST/GET/DELETE,`platform_scope=true`)+ 前端 SDK `api/role_bindings.ts` **均已存在**,缺的只是页面。

新建 `apps/admin-ui/src/pages/SettingsPlatformUsers.tsx`:
- 列现有 platform `system_admin`(`GET /v1/role_bindings?platform_scope=true` / `list_platform_scope`);
- 授予(输 subject UUID → `POST` platform_scope binding);
- 撤销(`DELETE`)。
- 仅 system_admin 可见(复用现有 platform 页门控)。

接线(SE-8 清单):router + Sidebar + CommandPalette + SDK + i18n 双语 + Storybook + Playwright。

## 4. P3 — 跨租户成员视图

### 后端
- `TenantMemberStore.list_all_tenants(*, status?, limit, offset)`:
  - **sql**:`tenant_member` 是 FORCE RLS(migration 0051)→ 跨租户读须 `SET LOCAL ROLE audit_reader`(见 `feedback_store.py`、`webhook/sql.py` 范式),非单用 bypass_var(后者 FORCE 下返零行);
  - **memory**:返全量。
- `GET /v1/members`:当 `tenant_id=*` 且 `principal.is_system_admin` → 走 `list_all_tenants`;否则维持 tenant-scope。非 admin 传 `*` → 403。

### 前端
`scope === "*"`(TenantScope 切到全局)时,成员页调 `?tenant_id=*` 展示全租户成员**只读**列表(跨租户写仍走单租户上下文,避免误操作)。

## 5. 测试计划

| 层 | 用例 |
|----|------|
| unit | claims 解析 email/email_verified;`maybe_bootstrap` 五条件矩阵(含零-admin 门控、verified=false 拒、已存 admin 不触发);幂等竞态 |
| unit | `list_all_tenants`(memory)跨租户聚合 + status 过滤 |
| integration | 真 PG:`list_all_tenants` 经 `SET LOCAL ROLE audit_reader` 跨 FORCE-RLS 返多租户;无 SET ROLE 返零行(负对照) |
| integration | `GET /v1/members?tenant_id=*` system_admin 通、普通租户 403 |
| frontend | vitest:平台管理员页 列/授/撤;跨租户成员只读视图 |

## 6. 满分判定(零技术债)— ✅ 全清 2026-06-17
- [x] 无 TODO 残留
- [x] 单测覆盖五条件矩阵 + 跨租户 SET ROLE 真 PG 验(集成测 1/1 实证)
- [x] 文档同步(本档 + runbook bootstrap-admin 补 env 路径 + ITERATION-PLAN Stream ACCT)
- [x] 可观测:自动升格记审计(沿用 role_binding create 审计 + `system_admin.bootstrap.first_login` 日志)
- [x] CI 全绿:后端 protocol 329 / persistence 44 / control-plane auth 204 / 集成 1;前端 typecheck 净 + vitest 366/366
- [x] 无 bug 遗留
