# Stream C — Auth & 多租户基础（设计先行）

> 落实 [docs/ITERATION-PLAN.md](../ITERATION-PLAN.md) § Stream C；执行的是
> [subsystems/15 AuthN/AuthZ](../architecture/subsystems/15-authn-authz.md) 全章、
> [subsystems/16 Quota / Rate Limit § 3-5](../architecture/subsystems/16-quota-rate-limit.md) 的
> **业务层（第 2 层）+ provider 层（第 3 层）quota 引擎**、
> [subsystems/23 Postgres Scalability § 8](../architecture/subsystems/23-postgres-scalability.md#8-rls-命名规范) 的
> RLS 强制 + 命名 lint、
> [ADR-0003 Authentication](../adr/0003-authentication.md) 的 Keycloak + RS256 JWT 决策、
> [ADR-0007 Secret Store](../adr/0007-secret-store.md) 的 SecretStore 抽象。

设计先行规则（[memory:feedback_design_first_iteration.md](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_design_first_iteration.md)）：
所有架构 / 接口 / mini-ADR 必须在编码前就锁定，C.1-C.7 PR 仅执行本文档。

---

## 1. 范围 & 边界

### 1.1 In-scope（C.1 – C.7）

| 子项 | 实现内容 | 关联子系统 |
|------|---------|-----------|
| **C.1 OIDC + JWT 中间件** | Keycloak 本地 dev 容器；JWKS 缓存；`AuthMiddleware` 解析 `Authorization: Bearer <jwt>` → `Principal`；签发 `tenant_id` / `actor_id` / `roles` / `scopes`；移除 B 的 `ProdAuthModeNotReadyError` 守卫 | 15 § 4.2 / ADR-0003 § 2.1-2.2 |
| **C.2 mTLS 服务间认证** | `httpx.AsyncClient` 客户端证书；FastAPI ASGI 层 peer cert 验证；Service-Account JWT 二选一；`X-Forwarded-Client-Cert` 头解析（reverse proxy 兜底） | 15 § 5.6 / ADR-0003 § 2.3 |
| **C.3 API Key 管理** | `api_key` 表（argon2id hash + prefix index）；`POST /v1/service_accounts/{id}/api_keys`、`DELETE /v1/api_keys/{id}`；`Bearer aforge_pat_*` 前缀识别 → 不签 JWT，直接构造 Principal | 15 § 3.1 / 4.1 / 5.4 |
| **C.4 RLS + Tenant Scoping** | 迁移 0004：对所有租户表 `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` + `FORCE`，policy 统一引用 `current_setting('app.tenant_id', true)`；SQLAlchemy session bootstrap middleware：每个 unit-of-work 前 `SET LOCAL app.tenant_id`；CI 静态 lint 校验命名 | 23 § 8 / 15 § 4.3 |
| **C.5 Tenant Quota 引擎** | Redis 容器（infra/docker-compose）；`QuotaService` + Lua token-bucket；`tenant_quota` / `token_budget_ledger` / `token_reservation` 三张表；`check / reserve / commit / release` API；fail-closed for tenant 维度 | 16 § 3-5 |
| **C.6 业务层 + Gateway Redis 限流** | `RateLimitMiddleware` 业务层维度（per-tenant + per-agent + per-route）；B.2 的 `InProcessTokenBucketLimiter` 留下；新增 `RedisTokenBucketLimiter`（同 Protocol）；`single_instance` 设置默认翻 `False`；429 envelope + Retry-After | 16 § 5.6（layer 1 + 2） |
| **C.7 Tenant Config 隔离** | `tenant_config` 表（JSONB）；`TenantConfigService` 60s TTL；per-tenant `model_credentials_ref` / `mcp_allowlist` / `rate_limit_override`；secret_ref 由 [F.6 SecretStore](../adr/0007-secret-store.md) 后端解析；admin CRUD endpoint | 11 / 15 / ADR-0007 |

### 1.2 Out-of-scope（明确推迟）

| 推迟项 | 落地 Stream | 备注 |
|-------|------------|------|
| Step-up auth（最近 5min 内重新登录） | M1（subsystems/15 § 5.2 M1 列）| C 不实现 |
| OIDC group → role 映射表 + admin UI 编辑 | M1 / J.x | C.1 只支持 Keycloak realm role 静态映射 |
| JWT signing key 走 KMS secret_ref | M1 | C.1 在 dev 用 Keycloak 自管 RS256 keypair；prod 部署文档 + secret_ref 接入留 M1 |
| Redis Cluster + 跨 region | M2 | C.5 单实例 + Sentinel-free |
| Token reservation 跨子树（subagent commit 反向累加 lead）| Stream E（与 24-subagent 联动）| C.5 落表 + API；累加逻辑随 E 的 LangGraph 节点上线 |
| 月度预算动态分摊（`monthly_token_budget` reshape）| M1 | C.5 只落静态 daily / qps cap |
| ABAC owner 维度（"viewer 只能看自己的 session"）| M1 | C.4 RBAC 只到 (role, resource, action) |
| Postgres TDE / WORM audit | Stream D | C.4 RLS 落地不依赖 TDE |
| OIDC SSO 接入企业 IdP（Okta / Azure AD 等）| M1 | C.1 自建 Keycloak realm 起步 |
| SPIFFE/SPIRE workload identity | M2 | C.2 走静态 X.509 + cert-manager（M1）路径 |

### 1.3 验收门（来自 ITERATION-PLAN）

1. **跨租户访问被 RLS 拒绝** — 集成测试：tenant A 用 JWT 访问 tenant B 资源 → 404 / 0 行
2. **超 quota 返回 429** — 业务层 + provider 层并发压测，第二维度命中时返回 dimension 标识
3. **mTLS 握手失败的请求被拒** — 不带客户端证书的 service-to-service 调用直接被 reverse proxy 阻断 + 服务端 401 兜底
4. **所有 auth 事件已写入 audit_log** — login_success / login_fail / api_key_create / api_key_revoke / role_grant / role_revoke / tenant_config_write 100% 落库；403 / 429 走采样

---

## 2. 架构

### 2.1 中间件栈（C 完成后的最终形态）

`create_app` 注册顺序在 B 基础上扩展（外层 → 内层）：

```
1. ObservabilityMiddleware                          [B.1]
2. AuthMiddleware              ← C.1 替代 dev-only AuditContext
3. AuditContextMiddleware      ← 退化为：从 request.state.principal 投影到 ctxvar
4. RateLimitMiddleware (gateway layer, Redis)        ← C.6 替换 B.2 in-process
5. TenantRateLimitMiddleware (business layer)        ← C.6 新增
6. CancellationMiddleware                            [B.3]
7. DeadlineMiddleware                                [B.3]
8. RLSSessionMiddleware        ← C.4 新增（dependency on dependency-injected DB session）
9. InFlightMiddleware                                [B.1]
```

**关键决策**：
- AuthMiddleware **必须**在 RateLimitMiddleware 之前 — 限流维度依赖 tenant；否则未认证流量按 IP 限流路径，但 tenant 维度全打到默认桶
- TenantRateLimit 在 Gateway RateLimit 之后 — 网关层是粗粒度（IP / API key），先拦广义流量；业务层细粒度（tenant × agent × route），后细粒度拦
- RLSSessionMiddleware 包装的是 **请求级 DB session 的 enter** 时机，不是顶层中间件；通过 FastAPI dependency injection（`Depends(get_db_session)`）实现：每次注入新 session → 立刻 `SET LOCAL app.tenant_id`

### 2.2 服务布局新增

```
services/control-plane/src/control_plane/
├── auth/                              ← C.1 / C.2 / C.3 新增
│   ├── __init__.py
│   ├── principal.py                   # Principal dataclass + 工厂
│   ├── jwt_verifier.py                # JWKS fetch + cache + RS256 verify
│   ├── api_key_verifier.py            # prefix lookup + argon2id verify
│   ├── mtls.py                        # peer cert / X-Forwarded-Client-Cert
│   ├── middleware.py                  # AuthMiddleware（统一三种凭据）
│   ├── errors.py
│   └── rbac.py                        # (role, resource, action) → bool
├── tenancy/                           ← C.4 / C.7 新增
│   ├── __init__.py
│   ├── rls_session.py                 # SQLAlchemy session bootstrap
│   └── tenant_config.py               # TenantConfigService + cache
├── quota/                             ← C.5 / C.6 新增
│   ├── __init__.py
│   ├── base.py                        # QuotaService Protocol
│   ├── redis_impl.py                  # Lua bucket + reservation
│   ├── tenant_rate_limit.py           # 业务层 middleware
│   └── reservation_reaper.py          # 30min 兜底 release job
└── ratelimit/
    ├── redis_impl.py                  ← C.6 新增（同 B.2 Protocol）
    └── ...                            # B.2 已有

packages/helix-protocol/src/helix_agent/protocol/
├── auth.py                            ← C.1：Principal / JWTClaims / Role / Scope
└── quota.py                           ← C.5：CheckRequest/Result / ReserveRequest/Result

packages/helix-persistence/src/helix_agent/persistence/
├── auth/                              ← C.1-C.3 持久化
│   ├── app_user.py
│   ├── service_account.py
│   ├── api_key.py
│   ├── role_binding.py
│   ├── jwt_blacklist.py
│   └── base.py                        # Protocol 集合
├── quota/                             ← C.5
│   ├── tenant_quota.py
│   ├── token_reservation.py
│   ├── token_budget_ledger.py
│   └── base.py
└── tenant_config/                     ← C.7
    ├── store.py
    └── base.py

packages/helix-persistence/migrations/versions/
├── 0004_rls_baseline.py               ← C.4
├── 0005_authn_authz_tables.py         ← C.1/C.2/C.3
├── 0006_quota_tables.py               ← C.5
└── 0007_tenant_config.py              ← C.7

infra/
├── docker-compose.yml                 ← C.1 / C.5：加 keycloak + redis service
├── keycloak/
│   ├── realm-helix-agent.json         # 预导入 realm（dev only）
│   └── README.md
└── redis/
    └── redis.conf                     # AOF + maxmemory-policy

ci/
└── lint_rls_naming.py                 ← C.4：扫描所有 migration 的 CREATE POLICY 检查 `app.tenant_id`
```

### 2.3 Principal 数据流

```
HTTP request
  ↓
AuthMiddleware：
  if Bearer 是 JWT (header.kid 来自 keycloak JWKS)
    → JWTVerifier.verify() → JWTClaims → Principal(sub_type=user|service_account)
  elif Bearer 是 aforge_pat_*
    → ApiKeyVerifier.lookup() → row → Principal(sub_type=service_account, source=api_key)
  elif 客户端证书存在 (X-Forwarded-Client-Cert or scope["client_cert"])
    → MtlsVerifier.parse() → Principal(sub_type=service, peer_cn=...)
  else
    → 401（dev_mode 兜底已删除）

  → request.state.principal
  → request.state.tenant_id = principal.tenant_id（沿用现有 ctxvar 链路）
  → request.state.actor_id = principal.subject_id
  ↓
AuditContextMiddleware：投影到 ctxvar（不再做"header trust"）
  ↓
RBAC（authorize() FastAPI dependency）：handler 入口处调用
  ↓
RLSSessionMiddleware（db 依赖注入路径）：SET LOCAL app.tenant_id
```

### 2.4 数据模型新增（迁移 0004 - 0007）

#### 迁移 0004 — RLS baseline

对**所有现存 + 新增**含 `tenant_id` 列的表执行：

```sql
-- 已有：event_log / thread_meta / audit_log / agent_spec
ALTER TABLE event_log    ENABLE ROW LEVEL SECURITY;
ALTER TABLE event_log    FORCE ROW LEVEL SECURITY;
CREATE POLICY event_log_tenant_isolation ON event_log
  USING      (tenant_id = current_setting('app.tenant_id', true)::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
-- 重复 thread_meta / audit_log / agent_spec
```

> 注：现有 4 张表的 `tenant_id` 是 `UUID`，policy 内做 `::uuid` 强转。subsystem 23 § 8 的 `TEXT` 示例对应未来 schema 的迁移目标 — 项目内部统一用 UUID（已存量），CI lint 只校验**变量名**为 `app.tenant_id`，不限制类型。

```sql
-- 设置专用绕过 role（admin 跨租户路径用，见 subsystems/15 § 4.3）
CREATE ROLE audit_reader NOLOGIN;
GRANT USAGE ON SCHEMA public TO audit_reader;
GRANT SELECT ON audit_log, event_log, thread_meta TO audit_reader;
ALTER ROLE audit_reader BYPASSRLS;
-- 应用 superuser 角色不走 RLS（migration 用），单独说明在 README
```

#### 迁移 0005 — Auth tables（subsystem 15 § 3.1 全套）

```sql
CREATE TABLE app_user (...);                     -- 见 subsystems/15
CREATE TABLE service_account (...);
CREATE TABLE api_key (...);
CREATE TABLE role_binding (...);
CREATE TABLE jwt_blacklist (jti TEXT PK, ...);
```

注：`jwt_blacklist` **不带** `tenant_id` 列（jti 全局唯一），不参与 RLS。同样 `app_user.default_tenant` 是 TEXT 而非租户表的"属于"，也豁免 RLS。落 RLS 的是 `service_account / api_key / role_binding` 三张。

#### 迁移 0006 — Quota tables（subsystem 16 § 3.1 全套）

`tenant_quota` / `token_budget_ledger` / `token_reservation` 三张，全部加 RLS。

#### 迁移 0007 — tenant_config

```sql
CREATE TABLE tenant_config (
  tenant_id              UUID PRIMARY KEY,
  display_name           TEXT NOT NULL,
  plan                   TEXT NOT NULL DEFAULT 'free',   -- free / pro / enterprise
  model_credentials_ref  JSONB NOT NULL DEFAULT '{}',    -- {anthropic: "kms:/path", openai: ...}
  mcp_allowlist          JSONB NOT NULL DEFAULT '[]',    -- ["github-mcp", ...]
  rate_limit_override    JSONB NOT NULL DEFAULT '{}',    -- {qps: 50, burst: 100}
  pii_fields             JSONB NOT NULL DEFAULT '[]',    -- 给 Stream D PII redactor 用
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by             TEXT NOT NULL                    -- actor_id
);
ALTER TABLE tenant_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_config FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_config_self ON tenant_config
  USING      (tenant_id = current_setting('app.tenant_id', true)::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
```

### 2.5 Keycloak 接入（C.1）

`infra/docker-compose.yml` 新增：

```yaml
keycloak:
  image: quay.io/keycloak/keycloak:25.0
  command: ["start-dev", "--import-realm"]
  environment:
    KC_DB: postgres
    KC_DB_URL: jdbc:postgresql://postgres:5432/keycloak
    KC_DB_USERNAME: helix_agent
    KC_DB_PASSWORD: helix_agent_dev
    KEYCLOAK_ADMIN: admin
    KEYCLOAK_ADMIN_PASSWORD: admin_dev   # dev only
  volumes:
    - ./keycloak/realm-helix-agent.json:/opt/keycloak/data/import/realm.json:ro
  ports:
    - "8080:8080"
  depends_on:
    postgres:
      condition: service_healthy
```

`realm-helix-agent.json`（预导入；dev fixtures）：
- realm = `helix-agent`
- clients：`helix-agent-admin-ui`（Public + PKCE）、`helix-agent-api-internal`（Confidential, Service Account）
- realm roles：`admin` / `operator` / `viewer`
- 一个 dev 用户：`dev@helix.local`（password=`devpass`, role=admin, tenant=`00000000-0000-0000-0000-000000000000`）
- 自定义 JWT claim mapper：`tenant_id` ← user attribute、`roles` ← realm roles

settings 新增：

```python
class Settings(BaseSettings):
    # ... 现有字段 ...
    oidc_issuer: str = "http://keycloak:8080/realms/helix-agent"
    oidc_audience: list[str] = Field(default_factory=lambda: ["helix-agent-api-internal"])
    oidc_jwks_cache_ttl_s: int = Field(default=300, gt=0)
    auth_mode: Literal["dev", "prod"] = "dev"      # 现有；C.1 后 prod 生效
    auth_allow_unauthenticated: bool = False       # C.1：仅 /healthz/* /metrics 豁免
```

### 2.6 RLS Session 注入（C.4）

```python
# control_plane/tenancy/rls_session.py
async def get_db_session_with_rls(
    request: Request,
    sm: Annotated[async_sessionmaker, Depends(_session_maker)],
) -> AsyncIterator[AsyncSession]:
    async with sm() as session:
        # SET LOCAL 只在事务内有效，且 PgBouncer transaction pooling 兼容
        await session.execute(
            text("SET LOCAL app.tenant_id = :tid"),
            {"tid": str(request.state.principal.tenant_id)},
        )
        try:
            yield session
        finally:
            # 无须 RESET LOCAL — 事务结束时自动清；连接归还时已干净
            pass

# Admin 跨租户：专用 sessionmaker
async def get_admin_reader_session(request: Request, sm_admin) -> AsyncSession:
    if not request.state.principal.is_global_admin:
        raise HTTPException(403)
    async with sm_admin() as session:
        await session.execute(text("SET ROLE audit_reader"))
        await session.execute(text("SET LOCAL row_security = off"))
        yield session
        await session.execute(text("RESET ROLE"))
```

**关键决策**：业务路由**默认**用 `get_db_session_with_rls`；只有 audit / quota admin 路由用 `get_admin_reader_session`。Postgres BYPASSRLS attribute 使 admin role 不受 policy 约束，但应用层仍走专用 sessionmaker（不会复用业务连接），杜绝 "if-admin-skip-filter" 反模式。

### 2.7 Quota 引擎（C.5/C.6）

`docker-compose.yml` 新增 Redis（AOF + 512 MB maxmemory）。

```python
# control_plane/quota/base.py
class QuotaService(Protocol):
    async def check(self, req: CheckRequest) -> CheckResult: ...
    async def reserve_tokens(self, req: ReserveRequest) -> ReserveResult: ...
    async def commit_tokens(self, req: CommitRequest) -> None: ...
    async def release_tokens(self, reservation_id: UUID) -> None: ...

# control_plane/quota/redis_impl.py
class RedisQuotaService:
    LUA_TOKEN_BUCKET = textwrap.dedent("""...""")   # 见 subsystems/16 § 5.1
    async def check(self, req): ...                # 多维度叠加（subsystems/16 § 5.2）
    async def reserve_tokens(self, req): ...       # 写 token_reservation + budget_ledger
    # commit / release 同章节
```

**关键 mini-ADR**：M0 不做 model 维度（subsystem 16 § 9 M0 列）；维度集合 = `tenant + agent + user` 三类（QPS）+ `tokens_per_day` 一类（per-tenant 累积）。

Reservation reaper：APScheduler / asyncio task，10min 周期扫 `state=RESERVED AND now() - reserved_at > 30min` 的行 → 强制 release。

### 2.8 C.7 TenantConfigService

```python
class TenantConfigService:
    def __init__(self, store: TenantConfigStore, ttl_s: int = 60): ...
    async def get(self, tenant_id: UUID) -> TenantConfig: ...   # 60s TTL cache
    async def upsert(self, tenant_id: UUID, patch: TenantConfigPatch, actor_id: str) -> TenantConfig: ...
        # 写 audit_log: action=tenant_config:write
```

`model_credentials_ref` 字段值是 `kms://<env>/<service>/<key>` URI（与 ADR-0007 § 2.1 命名约定一致），由 F.6 SecretStore 在 LLM 调用时解析；C.7 不直接读 secret，只存引用。

---

## 3. Mini-ADRs

### ADR C-1 — Keycloak 从 M0 上线 + RS256 JWT

**问题**：subsystem 15 § 5.2 把 M0 写成 HS256 共享 secret；ADR-0003（2026-05-11）把 Keycloak + RS256 钉为 M0 决策。何者为准？

**决策**：以 **ADR-0003 为准**：M0 即 Keycloak + RS256。理由：
- ADR-0003 是较新且更显式的决策（含 phase-0-launch 锚定）
- 自建 Keycloak 单机 dev 容器运维负担可接受（已写入 docker-compose）
- RS256 + JWKS 让 JWT 验证零应用侧密钥分发，未来切换 KMS 仅改 issuer 配置
- subsystems/15 § 5.2 的 M0 表会在 C.1 落地后做对齐 PR（属于 doc-updater 范畴）

**Why**：把 HS256 桥接当成增量收益 < 0 的过渡设计。

### ADR C-2 — 三种凭据走同一中间件 + 统一 Principal

**问题**：JWT（用户 + service account）、API Key、mTLS peer cert 三类凭据要不要写三个 middleware？

**决策**：单 `AuthMiddleware`，内部 switch on `Authorization` 头 prefix 与 ASGI scope；产出统一的 `Principal`：

```python
@dataclass(frozen=True)
class Principal:
    subject_id: str                # user.id / service_account.id / peer_cn
    subject_type: Literal["user", "service_account", "service"]
    tenant_id: UUID
    roles: tuple[str, ...]
    scopes: tuple[str, ...]         # API Key 路径来 / JWT scope claim
    auth_method: Literal["jwt", "api_key", "mtls"]
    allowed_tenants: tuple[UUID, ...]   # JWT 路径
    raw: Mapping[str, object]       # 调试用；不参与决策
```

**Why**：下游（RBAC、audit、rate limit）只读 `Principal`，不关心是哪条认证路径，统一缩减 if-else。

### ADR C-3 — RLS via `SET LOCAL` + PgBouncer transaction pooling 兼容

**问题**：subsystems/23 § 9 默认 PgBouncer transaction pooling；`SET` 是 session-level，跨事务会泄漏到下个 client。

**决策**：所有租户感知查询走 `SET LOCAL app.tenant_id = '<uuid>'`（事务级，事务结束自动失效）。SQLAlchemy 用 `async with session.begin()` 包住每个请求 unit-of-work，确保 `SET LOCAL` 与查询同事务。PgBouncer 在事务边界归还连接，下次取出连接时 settings 已干净，跨请求隔离。

**Why**：`SET` 会污染池子；`SET LOCAL` 是 transaction pooling 与 RLS 共存的唯一正确姿势。subsystem 23 § 8 已经把这个规范钉死，C.4 仅落实。

### ADR C-4 — Admin 跨租户：BYPASSRLS role + 专用 sessionmaker

**问题**：admin 看全 tenant 的 audit / quota 需要绕过 RLS；如何避免 "if admin then skip filter" 这种容易漏的模式？

**决策**：
- Postgres 侧：`audit_reader` role 带 `BYPASSRLS` attribute；只 `GRANT SELECT` 到 audit / event / thread 等只读表
- 应用侧：完全独立的 `async_sessionmaker_admin`（不同 dsn / different role 凭据）；只有 admin scope 路由 dependency 注入 `get_admin_reader_session`；业务连接池**永远**不切 role / 不关 row_security

**Why**：单一连接池上"动态 SET ROLE / RESET ROLE" 出现遗漏就会静默泄漏跨租户数据；两个池子物理隔离零风险。

### ADR C-5 — Gateway 限流：保留 in-process Limiter + 新增 Redis impl，按 `single_instance` 选择

**问题**：B.2 的 in-process limiter 在多副本部署下失效；C.6 切 Redis 是必修。但本地 dev 一直跑 Redis 也累赘。

**决策**：保留 `InProcessTokenBucketLimiter`；新增 `RedisTokenBucketLimiter` 实现同 `RateLimiter` Protocol；`Settings.single_instance` 默认 `True`，`create_app` 据此选实例：

```python
if resolved_settings.single_instance:
    limiter = InProcessTokenBucketLimiter(...)
else:
    limiter = RedisTokenBucketLimiter(redis=..., key_prefix="gw")
```

prod 部署文档强制 `HELIX_AGENT_SINGLE_INSTANCE=false`，启动时若 single_instance=True 但容器副本 > 1 直接告警（在 K8s readiness probe 通过 env 探测）。

**Why**：不强迫单人 dev 跑 Redis；prod 路径强制 distributed。

### ADR C-6 — Quota：Redis Lua 原子；fail-closed for tenant，fail-open for IP

**问题**：Redis 不可用时 quota check 怎么办？

**决策**：
- 业务层（tenant / agent / user 维度）：**fail-closed**（拒绝），Prometheus 告警，触发 ops oncall
- 网关层（IP 维度）：**fail-open**（放行），accept 是用户体验损失大于安全损失（M0 单租户内部使用）
- token_reservation：Redis 挂时**全部拒绝** new run，避免 budget 失控
- Lua 脚本通过 `EVALSHA` + `SCRIPT LOAD` 装载；客户端持久缓存 SHA + 拦截 NOSCRIPT 错误自动 fallback EVAL（subsystem 16 § 6 已规范）

**Why**：与 subsystem 16 § 6 / 18 完全一致；C 不创新只落实。

### ADR C-7 — TenantConfig 写路径强制 audit + 60s TTL cache

**问题**：tenant_config 改动频次极低但读取极频（每个 LLM 调用要拿 model_credentials_ref）；缓存策略？

**决策**：
- 读：60s TTL `LRUCache(tenant_id → TenantConfig)`，进程内
- 写：直接落 DB + `audit_log(action="tenant_config:write")` + 不主动 invalidate cache（接受 60s 收敛延迟）
- 紧急回滚：admin 端点 `POST /v1/tenants/{id}/config:invalidate-cache` 立即清

**Why**：多副本不需要全局一致；60s 收敛对配置类数据足够；不引入 Redis pub/sub 复杂度。

---

## 4. 接口

### 4.1 HTTP API 新增

| Path | Method | Body | 返回 | 鉴权 | Audit Action |
|------|--------|------|------|------|--------------|
| `/v1/auth/me` | GET | — | `Principal` | JWT or API Key | `auth:read` |
| `/v1/auth/logout` | POST | — | 204 | JWT | `auth:logout` |
| `/v1/service_accounts` | POST | `{name, description}` | `ServiceAccount` | admin | `service_account:write` |
| `/v1/service_accounts/{id}/api_keys` | POST | `{scopes, expires_at}` | `{api_key, plaintext_once}` | admin | `api_key:create` |
| `/v1/api_keys/{id}` | DELETE | — | 204 | admin | `api_key:revoke` |
| `/v1/role_bindings` | POST | `{subject_id, tenant, role}` | `RoleBinding` | admin | `role:grant` |
| `/v1/role_bindings/{id}` | DELETE | — | 204 | admin | `role:revoke` |
| `/v1/tenants/{tenant_id}/config` | GET | — | `TenantConfig` | self or admin | `tenant_config:read` |
| `/v1/tenants/{tenant_id}/config` | PUT | `TenantConfigPatch` | `TenantConfig` | self-admin or global-admin | `tenant_config:write` |
| `/v1/tenants/{tenant_id}/quotas` | GET | — | `TenantQuota[]` | self-admin or global-admin | `quota:read` |
| `/v1/tenants/{tenant_id}/quotas` | POST | `TenantQuotaPatch` | `TenantQuota` | global-admin | `quota:write` |
| **internal** `/v1/quota/check` | POST | `CheckRequest` | `CheckResult` | service mTLS | — |
| **internal** `/v1/quota/reserve` | POST | `ReserveRequest` | `ReserveResult` | service mTLS | — |
| **internal** `/v1/quota/commit` | POST | `CommitRequest` | 204 | service mTLS | — |
| **internal** `/v1/quota/release/{id}` | POST | — | 204 | service mTLS | — |

错误响应延用 B 的 envelope：
```json
{"success": false, "data": null,
 "error": {"code": "FORBIDDEN", "message": "...", "principal_tenant": "...", "resource_tenant": "..."}}
```

### 4.2 新增 AuditAction 枚举值

`packages/helix-protocol/src/helix_agent/protocol/audit.py` 扩展：

```python
class AuditAction(StrEnum):
    # 现有：manifest:* / session:* / ...
    # C.1
    AUTH_LOGIN = "auth:login"
    AUTH_LOGIN_FAIL = "auth:login_fail"
    AUTH_LOGOUT = "auth:logout"
    AUTH_JWT_REVOKE = "auth:jwt_revoke"
    # C.3
    API_KEY_CREATE = "api_key:create"
    API_KEY_REVOKE = "api_key:revoke"
    # C.4
    ROLE_GRANT = "role:grant"
    ROLE_REVOKE = "role:revoke"
    # C.5/C.6
    QUOTA_WRITE = "quota:write"
    QUOTA_RATE_LIMIT_DENIED = "quota:rate_limit_denied"   # 已存于 B（采样）
    # C.7
    TENANT_CONFIG_READ = "tenant_config:read"
    TENANT_CONFIG_WRITE = "tenant_config:write"
```

### 4.3 RBAC 矩阵（subsystem 15 § 3.3 落实）

按 subsystem 15 § 3.3 的 7×3 表照实落地；C 仅实现 3 类 role + 7 类 resource + 6 类 action 的 `Decision.allow / deny` 决策函数（不做 ABAC，不做 owner 维度，留给 M1）。

---

## 5. 测试矩阵

| 模块 | 单测 | 集成 | E2E |
|------|------|------|-----|
| JWTVerifier（happy + expired + bad iss + bad aud + bad signature + JWKS miss） | ✓ | ✓（vs Keycloak container）| — |
| ApiKeyVerifier（lookup + argon2 verify + revoked + expired） | ✓ | ✓ | — |
| Mtls verifier（X-Forwarded-Client-Cert + scope client_cert + missing） | ✓ | ✓（reverse proxy fixture）| — |
| AuthMiddleware 统一路径（三种凭据 / 三种失败码） | ✓ | ✓ | — |
| RBAC decision table（21 个 cell） | ✓ | — | — |
| RLS 策略（tenant A 读 tenant B → 0 行；CHECK 拒绝跨租户 INSERT） | ✓ | ✓ | ✓（HTTP 跨租户） |
| `SET LOCAL app.tenant_id` 事务边界正确性（PgBouncer transaction pooling） | — | ✓ | — |
| Admin BYPASSRLS 通道（仅在 admin reader session 生效） | — | ✓ | — |
| Quota check 单维度 + 多维度叠加 | ✓ | ✓（Redis container）| — |
| reserve / commit / release / expire reaper | ✓ | ✓ | — |
| 业务层 RateLimit middleware 命中 dimension 返 429 + Retry-After | ✓ | ✓（并发压测）| — |
| Gateway RedisTokenBucketLimiter（与 InProcess 行为一致性） | ✓ | ✓ | — |
| TenantConfigService（TTL 失效 + cache miss 重建 + 缓存击穿保护） | ✓ | — | — |
| CI lint：所有 CREATE POLICY 引用 `app.tenant_id` | — | — | ✓（CI step） |
| Audit：每个 mutation 落一行（含失败路径） | ✓ | ✓ | — |
| 完整 acceptance（C verification gate #1-#4） | — | — | ✓ |

**覆盖率目标**：80%（common/testing.md）。

---

## 6. 风险 & 缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| Keycloak 容器在 CI 慢启动（30s+）拖测试时间 | CI 慢 | C.1 提供两套测试：单测 mock JWKS；集成测试用 testcontainers 但放在独立 CI job（不阻断主流程） |
| Redis 不可达 → 业务层 quota fail-closed → 全租户 500 | 大面积事故 | 加 `HELIX_AGENT_QUOTA_BREAK_GLASS` env 旁路（仅 prod incident），oncall runbook 注明；同时埋 `helix_quota_redis_unavailable_total` metric + 紧急告警 |
| RLS policy 漏配某张租户表 → 数据泄漏 | 严重 | C.4 落地 CI lint：扫 `tenant_id` 列存在但缺 `CREATE POLICY` 的表 → CI 阻断（与 subsystem 23 § 8 一致） |
| PgBouncer transaction pooling 与 `SET` 串扰（用错关键字 `SET` 不是 `SET LOCAL`） | 跨租户数据 | C.4 中所有 RLS-aware session bootstrap 强制 `SET LOCAL`；review 时禁 grep 出 `SET app.tenant_id` 不带 LOCAL 的代码 |
| JWT signing key 泄漏 / Keycloak DB 漏 | 任意身份伪造 | dev only 暴露 8080；prod 部署文档要求 Keycloak 走独立 namespace + KMS 后端 + 公网仅 HTTPS 暴露 issuer 端点 |
| argon2id 参数过强 → API Key 验证 > 100ms | 高 RPS 时性能瓶颈 | 选 m=64MB, t=2, p=1（OWASP 推荐）；命中后 LRU(10000) cache 5min（subsystem 15 § 5.4 已规范） |
| Reservation 泄漏 → budget_ledger 持续 reserved | 超额拒绝 | 30min reaper 兜底；告警 `helix_token_reservation_active{tenant}` > 阈值 |
| dev_mode header trust 已删除 → 旧测试 / 旧 curl 调用 401 | 摩擦 | C.1 PR 同步更新所有现存 B.x 集成测试 fixture（runs_client fixture 加 JWT 注入 helper） |
| Keycloak realm 配置漂移（dev 与 prod 不一致） | 行为差异 | realm-helix-agent.json 是 IaC，prod realm 同 JSON 通过 admin CLI 导入；diff 在 CI 比对 |

---

## 7. 里程碑 / PR 切分

每个 C.x 一 PR；每个 PR 自给自足、可独立合入 main 且 CI 绿；每 PR 收尾必须满足[零技术债规则](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_zero_tech_debt.md)。

```
C.1  Keycloak compose + JWT verifier + AuthMiddleware 骨架（JWT 路径） + 移除 ProdAuthModeNotReadyError
     - infra/docker-compose 加 keycloak service
     - infra/keycloak/realm-helix-agent.json
     - control_plane/auth/{principal,jwt_verifier,middleware}.py
     - 现有 B 测试 fixture 升级带 JWT 注入
     - 5 个 audit_log action 新增

C.2  mTLS 服务间认证
     - control_plane/auth/mtls.py（X-Forwarded-Client-Cert + scope.client_cert）
     - 把 mtls 分支接入 AuthMiddleware
     - infra/docker-compose 加 nginx 反向代理 + 双向证书 dev fixture
     - 服务间 httpx.AsyncClient helper（C.5 quota 内部调用就开始用）

C.3  API Key（数据库 + CRUD）
     - migrate 0005_authn_authz_tables（含 app_user / service_account / api_key / role_binding / jwt_blacklist）
     - persistence/auth/* Protocol + In-memory + SQL impl
     - control_plane/auth/api_key_verifier.py
     - 接 AuthMiddleware bearer-prefix 分支
     - POST /v1/service_accounts, /v1/service_accounts/{id}/api_keys, DELETE /v1/api_keys/{id}
     - role_binding CRUD（admin only）
     - rbac.py decision matrix

C.4  RLS baseline + RLS session middleware + CI lint
     - migrate 0004_rls_baseline（4 张现存表全开 RLS + audit_reader role）
     - tenancy/rls_session.py
     - 把 sessions / agents / runs handler 全部切到 `get_db_session_with_rls`
     - ci/lint_rls_naming.py + CI step
     - 跨租户 e2e 测试

C.5  Quota 引擎（Redis + reservation）
     - infra/docker-compose 加 redis
     - migrate 0006_quota_tables
     - quota/{base,redis_impl,reservation_reaper}.py
     - persistence/quota/*
     - 内部 endpoint /v1/quota/* (mTLS only)
     - 把 sessions:create / runs:create 的 admission 接 reserve_tokens
     - quota:read / quota:write admin endpoints

C.6  业务层 + Gateway Redis 限流
     - quota/tenant_rate_limit.py — TenantRateLimitMiddleware
     - ratelimit/redis_impl.py — RedisTokenBucketLimiter
     - settings 加 single_instance 选择逻辑（已存在但未消费）
     - 429 envelope 带 dimension + retry_after_s
     - quota:rate_limit_denied 采样 audit

C.7  TenantConfig
     - migrate 0007_tenant_config
     - persistence/tenant_config/*
     - tenancy/tenant_config.py — TenantConfigService（60s LRU + audit）
     - GET / PUT /v1/tenants/{tid}/config
     - 在 LLM gateway / MCP gateway 接入点预留 `await tenant_config_service.get(tenant_id)`（Stream E 真正消费）
     - Stream C 完整验收 e2e（gate #1-#4）
```

---

## 8. 横切依赖回看（自下而上验证）

| Stream C 使用的下层能力 | 来源 | 状态 |
|------|------|------|
| audit_log table + AuditLogger | A.4 / B.5 | ✅ |
| Lifecycle / Health / OTel metrics | A.* / B.1 | ✅ |
| ProdAuthModeNotReadyError 守卫位（C.1 移除） | B.1 | ✅（已存） |
| `RateLimiter` Protocol（C.6 注入 Redis 实现） | B.2 | ✅ |
| AgentSpec / Session / Run handler（C.4 切 RLS session） | B.5/B.6/B.7 | ✅ |
| AsyncSession factory + alembic env.py | A.7 | ✅ |
| PgBouncer transaction pooling | A.3 | ✅ |
| SecretStore 抽象 / KMS impl（C.7 model_credentials_ref 由其解析） | F.6 | ⏳（C 仅落"引用"，真正解析在 F.6；C.7 验收只测 store 字段读写） |

所有阻塞依赖均已 main 落地；唯一前向引用 SecretStore 是 C.7 的 `model_credentials_ref` 字段消费方（Stream E LLM gateway），C 阶段只落 schema 不落运行时解析，无反向边。

---

## 9. 与 ITERATION-PLAN 对照

ITERATION-PLAN § Stream C 7 项对照本文档：

| Plan 项 | 本文档 PR | 备注 |
|--------|----------|------|
| C.1 OIDC + JWT — Keycloak | C.1 | 完全覆盖；JWT-only，OIDC SSO 第三方桥接 M1 |
| C.2 mTLS 服务间 | C.2 | 完全覆盖 |
| C.3 API Key 管理 | C.3 | argon2id + scopes + 失效 |
| C.4 会话授权完整化 + RLS | C.3 + C.4 | RBAC decision 在 C.3，RLS 在 C.4 |
| C.5 租户级 quota | C.5 | tokens / qps / sandbox-count；ad-mission 实装于 B.6/B.7 路径 |
| C.6 业务层限流 | C.6 | per-tenant/agent/route 三维 |
| C.7 租户级配置隔离 | C.7 | model_creds_ref / mcp_allowlist / pii_fields / rate_override |

无 plan 项遗漏；无新增 plan 外条目（除一致性必须的 5 类 audit action 与 RBAC matrix）。
