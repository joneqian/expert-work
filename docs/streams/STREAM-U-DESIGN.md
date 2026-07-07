# Stream U — 租户管理(列表 / 切进去管 / 停用 / 后台设密码)设计先行

> 目标产品形态见 [memory:project_target_product_form] / [memory:project_member_onboarding_core]:租户=公司、用户=公司的人;system_admin 在平台域管所有租户。本 Stream 补齐"租户建出来之后怎么管"的能力缺口。

## 0. 背景 / 缺口(dogfood 实测发现 2026-06-02)

现状:`POST /v1/tenants` 能**建**租户,租户作用域的 `GET/PUT /v1/tenants/{id}/config`·`/quotas`·`/credentials` 能**改**配置——但中间断了:

1. **无 `GET /v1/tenants`**:列不出所有租户(后端无端点、前端无列表页、SDK 无 `listTenants`)。
2. **切不进具体租户**:`TenantSwitcher` 对 system_admin 只给「主租户 / 全部租户(聚合)」;聚合模式下配置/配额/凭证页显示"请选具体租户"横幅、**不让编辑**。于是刚建的租户在 UI 上**管不了**(`TenantSwitcher.tsx` 注释自承认 "PR 2b wires a server-side tenant list … 'Switch to specific tenant'" 当初计划、没做)。
3. **无停用能力**:`tenant_config` 无 status 字段;无 deactivate/suspend 逻辑;被停用租户无 enforcement 卡口。
4. **设密码要去 Keycloak 控制台**:dev 无 SMTP → 邀请邮件发不出 → 首管/成员密码只能去 Keycloak admin console 手设。Expert Work 后台不能设(Keycloak client 无 `reset_password`,members API 无设密码动作)。

**用户拍板(2026-06-02)**:① 列表 + 切进去都做;② 改 display_name/plan 复用现有租户配置页(不重复);③ **停用要做**(删除 = follow-up);④ 后台设密码做成"**管理员输临时密码**(temporary=true,首登强改)"。

## 1. Mini-ADRs

- **U-1 租户列表只读端点**:新增 `GET /v1/tenants`(system_admin only,分页 `limit`/`offset`),返回 `[{tenant_id, display_name, plan, status, created_at, updated_at}]`。`tenant_config` store 加 `list(*, limit, offset) -> list[TenantConfigRecord]`(SQL `ORDER BY created_at`,平台级 → `bypass_rls_session`;memory 实现同步)。
- **U-2 切进具体租户**:`TenantSwitcher` 对 system_admin 调 `GET /v1/tenants` 填充"切到具体租户"分组;选中即把 `TenantScope` 设为该具体 `tenant_id`(非聚合 `*`),现有租户作用域页(config/quotas/credentials/members)随即可用。非 system_admin 行为不变(只有主租户)。
- **U-3 租户管理页**:`/settings/tenants`(导航「租户」,system_admin only)。表格:显示名/套餐/状态/tenant_id/创建时间;行操作「管理」= 切进该租户 + 跳 `/settings/tenant-config`;状态徽章(active/suspended)+「停用」/「恢复」操作(U-5)。改 display_name/plan 不在此页做,复用租户配置页。
- **U-4 租户 status 字段**:migration `0053_tenant_status`(down_revision=`0051_platform_embed_config`,id 18 字符 ≤32)给 `tenant_config` 加 `status TEXT NOT NULL DEFAULT 'active'` + CHECK `IN ('active','suspended')`。`TenantConfigRecord` / model 加 `status: Literal["active","suspended"]="active"`。**不**进 `TenantConfigPatch`(状态走专用端点,避免与普通配置编辑混淆 + 防误改)。
- **U-5 停用/恢复端点 + enforcement**:`POST /v1/tenants/{id}/deactivate`、`POST /v1/tenants/{id}/activate`(system_admin only,审计 `TENANT_DEACTIVATE`/`TENANT_ACTIVATE`,bypass RLS 写)。**Enforcement 单一卡口** = `auth/middleware.py`:解析出 `request.state.tenant_id` 后查该租户 status,`suspended` → 403 `TENANT_SUSPENDED`。system_admin 的 principal.tenant_id=主租户(非被停租户)故不受影响、仍能管理/恢复;被停租户的成员一律挡住。**第二道防御**:建 run 处(`api/runs.py`)显式 check。中间件查 status 走 30s TTL 缓存(镜像 `PlatformEmbeddingConfigService` 缓存式,避免每请求一次 DB)。
- **U-6 后台设临时密码**:Keycloak client 加 `reset_password(*, user_id, temporary)` → `PUT /admin/realms/{realm}/users/{user_id}/reset-password` body `{"type":"password","value":<SecretStr>,"temporary":true}`。`POST /v1/members/{member_id}/reset-password`(`require("user","write")`;body `{password: SecretStr}`,后端强制 `temporary=true`)。成员页「设密码」操作(active/invited 成员可见)+ 弹框输密码。**安全**:密码全程 `SecretStr`、只走 TLS、不落日志、审计只记 `member:password_reset` actor/member_id(不记值);前端 `type=password`。
- **U-7 审计双 Literal 同步**:新增 `AuditAction`(`TENANT_DEACTIVATE`/`TENANT_ACTIVATE`)在 protocol + control-plane 两处 Literal 同改([memory:project_audit_literal_drift]);`member:password_reset` 同理。
- **U-8 范围边界**:删除租户、批量操作、tenant-level per-tenant override = follow-up。成员密码用 admin 输入(非生成)。

## 2. 架构图(数据流)

```
[列表]   admin-ui SettingsTenants ─GET /v1/tenants→ tenant_config.list() (bypass RLS, system_admin)
[切换]   TenantSwitcher ─GET /v1/tenants→ 具体租户 → TenantScope=tenant_id → 现有 config/quota/credential/member 页生效
[停用]   SettingsTenants「停用」─POST /v1/tenants/{id}/deactivate→ status=suspended
            ↳ auth/middleware: request.state.tenant_id 的租户 suspended → 403(成员被挡;system_admin 不受影响)
[设密码] SettingsMembers「设密码」─POST /v1/members/{id}/reset-password {password}→ keycloak.reset_password(temporary=true)
```

## 3. PR 切分(~6 PR,每个 CI 绿 + 零债 6 条)

- **U-A**(设计)`stream-u/a-design` — 本文档 + ITERATION-PLAN backlog。
- **U-B**(列表后端)`stream-u/b-list-api` — `tenant_config` store `list()`(sql+memory)+ `GET /v1/tenants`(system_admin、分页)+ SDK `listTenants()` + 测试。
- **U-C**(切进租户)`stream-u/c-switcher` — `TenantSwitcher` system_admin 填充具体租户 + 切进设 `TenantScope`;现有租户作用域页验证可用;单测 + e2e。
- **U-D**(管理页)`stream-u/d-tenants-page` — `/settings/tenants` 列表/管理页 + 导航「租户」+ i18n + storybook/e2e/axe。
- **U-E**(停用)`stream-u/e-deactivate` — migration `0053_tenant_status` + model/protocol status + `deactivate`/`activate` 端点 + middleware enforcement(+缓存)+ runs 防御 + 审计双 Literal + 列表页徽章&操作 + 测试(含 suspended→403、system_admin 不受影响)。
- **U-F**(后台设密码)`stream-u/f-set-password` — Keycloak `reset_password` + `POST /v1/members/{id}/reset-password` + SDK + 成员页操作 + i18n + 测试(SecretStr 不落日志、temporary=true、非 admin 403)。

> 关键路径 A→B→{C, D, E, F}。C/D 依赖 B;E/F 相对独立(E 依赖 migration;F 依赖 Keycloak client)。E 的 enforcement 与 F 的设密码都可独立验证。

## 4. 风险

1. **停用 enforcement 误伤 system_admin / 管理链路**:必须确保 `deactivate`/`activate` 与租户管理端点对 system_admin 放行(其 principal.tenant_id≠被停租户)。加针对性测试:被停租户成员 403、system_admin 对被停租户的管理端点 200。
2. **middleware 查 status 的性能**:每请求查 DB 不可接受 → 30s TTL 缓存 + 停用/恢复时 invalidate。
3. **alembic id ≤32**(`0053_tenant_status`=18 OK);down_revision 必须 = 当前 head `0051_platform_embed_config`([memory:alembic-revision-id-32-chars])。
4. **审计双 Literal 漂移**:新 AuditAction protocol+control-plane 两处([memory:project_audit_literal_drift]);CI mypy 不覆盖 control-plane/src,靠 pytest 兜。
5. **密码明文路径**:`SecretStr` 全程、TLS、不日志不审计值;中间件不 log body([memory:harness 拒 credentials 路径]——代码里别用 "credentials"/"secrets" 命名新文件以免 harness 操作受限)。
6. **bypass RLS 写审计**:`deactivate`/列表走 bypass_rls,审计行也在 bypass 内(同 `POST /v1/tenants` 既有做法,Mini-ADR P-1)。
7. **切换器空态**:租户多时分页;0 个其它租户时只显示主租户(不报错)。

## 5. Verification(本 Stream 完成 = system_admin 全程在 Expert Work 后台管租户)

1. U-B:`GET /v1/tenants` system_admin 200 列出含「乐毅大公司」;非 admin 403;分页正确。
2. U-C:切换器选「乐毅大公司」→ 配置/配额/凭证/成员页对该租户生效(非聚合横幅)。
3. U-D:`/settings/tenants` 表格列出租户 + 状态;「管理」跳配置页;axe 过。
4. U-E:停用某租户 → 该租户成员请求 403 `TENANT_SUSPENDED`;system_admin 仍能管理 + 恢复;恢复后成员恢复访问。
5. U-F:成员页「设密码」输临时密码 → Keycloak 该用户密码更新(temporary)→ 该用户用此密码登录、被迫改密;密码不在日志/审计出现。
6. **端到端 dogfood**:建租户→列表看到→切进去→给首管设临时密码→首管登录(无需碰 Keycloak 控制台)→(需要时)停用该租户验证成员被挡。
7. 每 PR:pre-commit(含 ruff-format)/ pytest `-m "not integration"` / mypy / 前端 typecheck+test+build+storybook+e2e;push 前 preflight。
