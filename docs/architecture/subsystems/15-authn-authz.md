# 15 AuthN / AuthZ — 身份认证、RBAC 与 Tenant Scoping

> 所有 Expert Work API 入口的"门口大爷"：先验明身份（AuthN），再判断权限（AuthZ），所有决策落审计。M0 本地账号 + JWT，M1 SSO/OIDC + 服务账号 API key，全程 tenant scoping。

---

## 1. 职责 & 边界

### ✅ 做
- 用户身份验证：本地账号（bcrypt）/ OIDC SSO / 服务账号 API key
- JWT 签发 / 校验 / 黑名单（强制 logout）
- Refresh token 管理与轮换
- **RBAC**：`(role, resource, action)` 三元组授权
- **Tenant scoping**：所有 API 强制带 tenant，校验 JWT 与 header 一致
- 失败登录尝试限制（暴力破解防护）
- 服务间 mTLS（M1）+ SPIFFE/SPIRE workload identity（M2）
- 所有 grant / deny 决策写 [17 Audit Log](./17-audit-log.md)

### ❌ 不做
- 用户管理 UI / 自助注册 → Admin UI 自己实现
- API 速率限制 → [16 Quota / Rate Limit](./16-quota-rate-limit.md)
- 凭证（外部 API token）注入 → [11 Credential Proxy](./11-credential-proxy.md)
- Agent 内部对外部系统的细粒度权限 → 由 Agent 自己的 manifest tools allowlist
- 业务级数据访问控制（如哪个用户能看哪个 patient）→ 业务系统自己

---

## 2. 上下游依赖

| 依赖方向 | 子系统 | 关系 |
|---------|--------|------|
| 上游调用方 | API Gateway / Control Plane | 每个 HTTP 请求先过本服务 middleware |
| 上游调用方 | Orchestrator / Sandbox Supervisor | 内部 RPC 验 service account JWT |
| 下游 | Postgres | `users / roles / role_bindings / api_keys / jwt_blacklist` 表 |
| 下游 | OIDC Provider（M1）| Okta / Azure AD / Keycloak |
| 下游 | Vault | JWT signing key（M1：从 HS256 升 RS256 + KMS）|
| 横切 | [17 Audit Log](./17-audit-log.md) | 所有 login / grant / deny 写入 |
| 横切 | [20 Observability](./20-observability.md) | login 失败率、JWT 错误 metric |

---

## 3. 数据模型 / 状态机

### 3.1 Postgres DDL

```sql
-- 用户身份：见 tenant_user(Stream J.14 —— per-user 注册表,从认证后的
-- Principal 解析 (tenant_id, subject_type, subject_id))。原计划的
-- app_user 占位表从未实现,已于 migration 0016 删除;Expert Work 作为 IdP
-- 联邦平台不自持本地密码库。

-- 服务账号（CLI / CI 用）
CREATE TABLE service_account (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name          TEXT NOT NULL,
  tenant_id     TEXT NOT NULL,
  description   TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by    TEXT,                         -- actor_id 统一 TEXT（user.id / 'system' / agent_name@version）
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  UNIQUE (tenant_id, name)
);

-- API key（hash 后存储；前缀 aforge_pat_）
CREATE TABLE api_key (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  service_account_id UUID NOT NULL REFERENCES service_account(id),
  prefix        TEXT NOT NULL,              -- 头 8 字符明文，用于查找
  hash          TEXT NOT NULL,              -- argon2id(secret)
  scopes        JSONB NOT NULL,             -- ["session:read", "manifest:write"]
  expires_at    TIMESTAMPTZ,                -- NULL = never
  last_used_at  TIMESTAMPTZ,
  revoked_at    TIMESTAMPTZ,
  UNIQUE (prefix)
);

-- 角色绑定（一个用户可在多个 tenant 有不同 role）
CREATE TABLE role_binding (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  subject_type TEXT NOT NULL,               -- user / service_account
  subject_id   UUID NOT NULL,
  tenant_id   TEXT NOT NULL,
  role        TEXT NOT NULL,                -- admin / operator / viewer
  granted_by  TEXT,                         -- actor_id 统一 TEXT（user.id / 'system' / agent_name@version）
  granted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (subject_type, subject_id, tenant_id, role)
);
CREATE INDEX ON role_binding (subject_type, subject_id);

-- JWT 黑名单（强制 logout / token 泄漏吊销）
CREATE TABLE jwt_blacklist (
  jti        TEXT PRIMARY KEY,
  reason     TEXT NOT NULL,                 -- logout / compromise / role_change
  expires_at TIMESTAMPTZ NOT NULL           -- 自动清理过期项
);
```

### 3.2 Pydantic schema（JWT claims）

```python
class JWTClaims(BaseModel):
    iss: str = "Expert Work"
    sub: str                               # user_id 或 service_account_id
    sub_type: Literal["user", "service_account"]
    tenant: str                            # 当前请求的 tenant scope
    allowed_tenants: list[str]             # 该 subject 允许 switch 的 tenant
    roles: dict[str, list[str]]            # {tenant: [role, ...]}
    jti: str                               # JWT ID, 用于黑名单
    iat: int
    exp: int                               # 1h
    auth_method: Literal["password", "oidc", "api_key"]
```

### 3.3 角色 & 权限矩阵

| Resource \ Role | viewer | operator | admin |
|----------------|--------|----------|-------|
| manifest | read | read, write | read, write, **sign**, delete |
| session | read | read, write, **debug** | read, write, debug, delete |
| sandbox | read | read, **force_destroy** | read, force_destroy |
| secret | — | read | read, write, delete |
| audit | — | read（自 tenant）| read（全 tenant）|
| quota | read | read | read, write |
| user / role_binding | — | — | read, write |

**Action enum**：`read / write / delete / debug / sign / approve / force_destroy`
**Resource enum**：`manifest / session / sandbox / secret / audit / quota / user`

> 决策代码用 `(role, resource, action) → bool` 表（PostgreSQL `rbac_policy` 表 + 内存缓存 60s）。

---

## 4. 关键接口

### 4.1 HTTP API

```
POST /v1/auth/login              Body: {username, password}     → {access, refresh}
POST /v1/auth/oidc/callback      Body: {code, state}            → {access, refresh}
POST /v1/auth/refresh            Body: {refresh}                → {access, refresh}
POST /v1/auth/logout             Header: Authorization          → 204（jti 入黑名单）
GET  /v1/auth/me                 Header: Authorization          → JWTClaims

# 服务账号 / API key
POST /v1/service_accounts                        admin only
POST /v1/service_accounts/{id}/api_keys          → 一次性返回明文 secret
DELETE /v1/api_keys/{id}                         立即吊销

# 角色绑定
POST /v1/role_bindings                           admin only
DELETE /v1/role_bindings/{id}
```

### 4.2 Python middleware（FastAPI dependency）

```python
async def authenticate(req: Request) -> Principal:
    """从 Authorization 头解出 Principal；失败抛 401。"""

async def authorize(
    principal: Principal,
    *, resource: str, action: str, tenant: str,
) -> None:
    """无权限抛 403；同时写 audit_log（result=denied）。"""

# 用法
@router.get("/sessions/{id}")
async def get_session(
    id: str,
    principal: Principal = Depends(authenticate),
    tenant: str = Depends(extract_tenant),     # X-Expert-Work-Tenant
):
    await authorize(principal, resource="session", action="read", tenant=tenant)
    ...
```

### 4.3 Tenant scoping middleware

每个 API 调用必须：

1. 带 `X-Expert-Work-Tenant: <tenant_id>` header
2. JWT.allowed_tenants 包含该 tenant
3. JWT.roles[tenant] 中查 RBAC

**Admin 跨租户路径（`tenant='*'`）**：管理员查全 tenant 的 audit / quota 等场景，**不在应用代码层用 if-admin-skip-filter** 绕过 tenant 过滤（极易导致 RLS 静默漏检）。统一改走专用 Postgres reader role：

```sql
-- 仅 audit_reader / cross_tenant_reader 这类专用账号可执行
SET ROLE audit_reader;
SET LOCAL row_security = off;
-- 查询 ...
RESET ROLE;
```

应用层 middleware 检测 `tenant='*' && principal.role=admin` 时切换到该专用连接池（连接复用前 `RESET ROLE`），普通业务连接 **永远** 不能 `row_security = off`。

**关键决策**：tenant 不放 path 也不放 query，**统一 header**。理由：避免代码漏检；方便 middleware 集中处理；Postgres RLS 的 `current_setting('app.tenant_id')` 也由该 middleware 设置（与 23 § 8 / 13 等子系统统一变量名 `app.tenant_id`，与 `tenant_id` 列名匹配；CI lint 校验所有 RLS policy 引用一致）。

---

## 5. 算法 / 关键决策

### 5.1 密码存储

- **bcrypt cost=12**（M0）；M1 评估迁移 argon2id（rfc 9106）
- 失败登录：5 次 → 锁 5 分钟（指数退避：5/15/60/240 min）
- 密码策略：≥12 字符、混合大小写+数字+符号、禁用 top-1k 弱密码（zxcvbn 评估）

### 5.2 JWT 选择

| 维度 | M0 | M1 | M2 |
|------|----|----|----|
| 签名算法 | HS256（共享 secret） | RS256（KMS managed key） | RS256 + key rotation 7d |
| TTL | access 1h / refresh 7d | access 15min / refresh 8h | access 5min（高敏 tenant）|
| 黑名单 | Postgres 表 + 30s 缓存 | Redis（毫秒级查询） | 同 M1 |

**关键决策（M0 起）**：JWT signing key（无论 HS256 共享 secret 还是 RS256 KMS managed key）统一注册为 [11 Credential Proxy](./11-credential-proxy.md) 的 `secret_ref`，**不在配置文件 / 环境变量明文存放**。M0 用 HS256 时同样走 secret_ref 引用（启动时由 auth service 通过 credential proxy 解析）；M1 升 RS256 后 secret_ref 指向 KMS key handle，私钥永不出 KMS。

### 5.3 OIDC 集成（M1）

- 协议：OIDC Authorization Code + PKCE
- 支持 issuer：Google / Okta / Azure AD / Keycloak（自托管）
- group → role 映射表（`oidc_group_mapping`）：`{issuer, group}` → `{tenant, role}`
- 首次出现即自动登记 `tenant_user`（Stream J.14；身份由 OIDC 提供，无本地 password_hash）

### 5.4 API Key 格式

```
aforge_pat_<tenant_short>_<32 字符随机>
例：aforge_pat_med01_3kf9d8s7Hf2Lq1pAm5xYzNvB6cWeR0tU
```

- prefix 前 12 字符（含 tenant_short）入 Postgres 索引快速查找
- 全 secret argon2id 哈希存储
- 创建时返回明文一次，UI 提示用户立即保存
- 调用时：`Authorization: Bearer aforge_pat_xxx` → middleware 识别前缀 → 走 API key 流（不签 JWT，每次查表 + 内存 LRU 5min）

### 5.5 RBAC 决策缓存

- `(role, resource, action) → bool` 矩阵 lazily load 到内存
- TTL 60s + 显式失效（admin 改了 policy 时发 PG NOTIFY）
- 高频路径（如 `session:read`）：每节点本地 LRU(10000)

### 5.6 Service-to-service 认证

- M0：nginx 8443 mTLS 终止 + XFCC → `MTLSVerifier` Subject 白名单（A.10/C.2 已交付）；证书运维（生成 / 轮换 / 到期诊断）见 [runbooks/tls-certs.md](../../runbooks/tls-certs.md)
- M1：服务间 JWT（service account, sub_type=service_account）+ mTLS（service mesh）
- M2：SPIFFE/SPIRE workload identity，零信任

---

## 6. 失败模式 & 缓解

| 失败模式 | 影响 | 缓解 |
|---------|------|------|
| JWT signing key 泄漏 | 任意身份伪造 | KMS managed key + 立即 rotate + 全部 jti 入黑名单 + 强制重新登录 |
| OIDC provider 不可达 | 用户无法登录 | 本地账号 fallback；缓存 OIDC discovery 24h |
| 密码暴力破解 | 账号沦陷 | 失败计数锁定 + IP 维度限流（[16](./16-quota-rate-limit.md)）+ 告警 |
| API key 泄漏（提交到 git）| 未授权访问 | GitHub secret scanning webhook + 自动吊销；prefix 让用户能立即识别 |
| Tenant header 篡改 | 跨租户访问 | JWT.allowed_tenants 强制校验；不一致 → 403 + audit |
| Role binding 滞后 | 撤销权限不生效 | role 改变 → JWT jti 入黑名单 + 强制刷新 |
| 黑名单查询慢 | 整体 RPS 下降 | M1 Redis（毫秒级）；过期项 cron 清理 |
| OIDC group 映射缺失 | 新用户无 role | 默认 viewer 或拒绝（按租户 policy）；告警 admin |
| 时钟偏移 | exp 校验误判 | NTP 强制 + JWT leeway 30s |

---

## 7. 可观测性

> 日志完整字段遵循 [20 § 5.3](./20-observability.md)。

### 7.1 Prometheus metric

```
expert_work_auth_login_total{method="password|oidc|api_key", result="success|fail"}        counter
expert_work_auth_login_fail_reason_total{reason="bad_password|locked|inactive|unknown"}    counter
expert_work_authz_decision_total{resource, action, result="allow|deny"}                    counter
expert_work_authz_decision_latency_seconds                                                 histogram
expert_work_jwt_blacklist_size                                                             gauge
expert_work_api_key_active{tenant}                                                         gauge
```

### 7.2 OTel span

- `auth.login`（attrs：method, user_id, tenant, result）— **早绑定豁免**：login 发生在 JWT 签发前，无 agent 上下文，不带 `agent_name / agent_version`
- `auth.authorize`（attrs：principal_id, resource, action, tenant, agent_name, agent_version, result, latency_ms）
- `auth.jwt_verify`（attrs：jti, tenant, agent_name, agent_version, exp_in_s, blacklist_hit）

---

## 8. 安全考虑

- **JWT signing key 走 secret_ref**：M0 起 HS256 共享 secret / M1 RS256 KMS key 都注册为 [11 Credential Proxy](./11-credential-proxy.md) 的 `secret_ref`，配置文件中只出现引用 ID；详见 § 5.2
- **永不日志记录**：密码、JWT、API key 明文、refresh token；redactor 强制脱敏
- **CSRF**：access token 走 Authorization header（不放 cookie），天然防 CSRF
- **JWT replay**：jti 黑名单 + 短 TTL；高敏接口（如 `secret:write`）要求 step-up auth（重新输入密码）
- **Token confusion**：access vs refresh 用不同 audience claim，互不可用
- **OIDC state**：state + nonce + PKCE 防 CSRF / replay
- **API key scope**：只授最小 scope；前端 UI 不展示已创建 key 的明文（只能看 prefix）
- **审计**：login / logout / failed_login / role_grant / role_revoke / api_key_create / api_key_revoke 全部写 [17 Audit Log](./17-audit-log.md)
- **Step-up auth**：admin 操作 secret / role / quota 要求最近 5min 内重新登录过

**关键决策**：所有授权失败都写 audit（result=denied），不只是成功的；这能发现内部越权探测行为。

**Admin 跨租户路径**：`tenant='*'` 必须切专用 Postgres reader role（`SET ROLE audit_reader; SET LOCAL row_security = off`）绕过 RLS，**禁止** 在应用代码层 if-admin-skip-filter（避免 RLS 静默失效）；详见 § 4.3。

---

## 9. M0 / M1 / M2 演进

### M0
- 本地账号 + bcrypt + JWT(HS256) + 三角色（admin/operator/viewer）
- Tenant scoping middleware + Postgres RLS 配合
- API key（service account）基础版
- 失败登录锁定 + JWT 黑名单（Postgres）
- 服务间共享 secret

### M1
- OIDC SSO（Okta/Google/Azure AD/Keycloak）+ group→role 映射
- JWT RS256 + KMS
- 黑名单迁移 Redis
- 服务间 mTLS + service account JWT
- step-up auth 上线
- RBAC policy 表化 + admin UI 编辑

### M2
- SPIFFE/SPIRE workload identity
- Token TTL 缩到 15min + 自动 refresh
- 高敏 tenant 强制 hardware token（WebAuthn）
- SCIM 自动同步用户 / 组（M2 末）

---

## 10. 开放问题

1. **OIDC vs SAML**：企业大客户用 SAML 多，是否在 M2 加 SAML 适配？倾向 OIDC 优先 + SAML 通过 Keycloak 桥接。
2. **多 tenant 同时操作**：admin 同时管多个 tenant，每次切 tenant 都重新签 JWT 还是 JWT 内 allowed_tenants 全装？目前后者，但 token 会变大。
3. **API key 长期不轮换**：是否强制 90 天过期？合规客户必须，普通客户可能反对。倾向 tenant_config.api_key_max_lifetime_days 可配。
4. **服务账号"代用户"操作**：CLI 用 service account 调 API，audit_log 里 actor 是 service_account 还是触发它的用户？倾向：actor=service_account，但额外字段 `on_behalf_of=user_id`。
5. **细粒度资源权限**：能否做到"viewer 只能看自己创建的 session"？M1 用 ABAC（owner 字段）扩展，目前 M0 不做。
