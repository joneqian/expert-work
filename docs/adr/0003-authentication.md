# ADR-0003：认证选型 — OIDC + 自建 Keycloak + JWT

- **状态**：✅ 已决策
- **日期**：2026-05-11
- **决策依据**：公司无现成 SSO（[phase-0-launch 决策](../decisions/phase-0-launch.md)）；产品级 IdP 需求需要标准协议 + 细粒度 RBAC，Keycloak 是开源自托管首选
- **背景**：M0 Stream C.1（OIDC + JWT）+ C.2（mTLS 服务间）+ C.3（API Key 管理）需要明确认证方案

---

## TL;DR

- **人类用户**：OIDC + 自建 Keycloak 作为 IdP；前端走 Authorization Code + PKCE 拿 access_token（JWT）
- **服务间**：mTLS（基于 cert-manager 签发 SPIFFE-like SVID，M0 静态证书，M1+ 自动轮换）
- **外部业务系统调用 Expert Work**：API Key（Keycloak Service Account → 派生 JWT），按 key 限流 / 计费

---

## 1. 上下文

### 需求范围

| 主体 | 协议 | 凭据生命周期 | 关联 P0 |
|------|------|------------|---------|
| 内部用户登录 Admin UI | OIDC | 短期 access_token（1h）+ refresh（30d）| P0 #1 |
| Orchestrator ↔ Sandbox-Supervisor ↔ Control Plane | mTLS | 静态 7 天（M0）→ 动态短 TTL（M1）| P0 #2 |
| 第三方业务系统调用 `/v1/runs`、`/v1/sessions` | API Key bearer | 长期（手工轮换）| P0 #3 |
| Agent 内部代表用户调外部服务 | Credential Proxy 注入 | 由 Credential Proxy 管理 | M0 Stream F.5 |

### 关键约束

- 公司无现成 SSO（[phase-0-launch.md](../decisions/phase-0-launch.md) Decision 3 / Q2）— 必须自建
- 国内部署，需选 OSS 自托管方案（避免 Okta / Auth0 等海外 SaaS 数据出境）
- 单人项目，运维负担需可控
- 多租户：JWT 需含 `tenant_id` claim 供下游 RLS 用

---

## 2. 决策

### 2.1 IdP — Keycloak 自托管

| 维度 | 选择 |
|------|------|
| 部署 | Keycloak Operator on Kubernetes（M3）或单机 Docker（M0）|
| 后端 | 阿里云 RDS PostgreSQL（共用集群，隔离 schema）|
| Realm | `expert-work`（所有租户都在此 realm；多租户由 tenant_id claim 标识）|
| 客户端类型 | `expert-work-admin-ui`（Public, PKCE）/ `expert-work-api`（Confidential, Service Account 给外部业务）|
| 用户存储 | 内置 + 后续可接 LDAP（M1+ 看公司需求）|

### 2.2 协议 — OIDC + JWT

- 前端使用 **Authorization Code + PKCE** flow（标准 SPA 模式）
- access_token = **RS256-signed JWT**，有效期 1 小时
- refresh_token 有效期 30 天，存 Keycloak 后端
- JWT 自定义 claims：

```json
{
  "iss": "https://keycloak.expert-work.internal/realms/expert-work",
  "sub": "user-uuid",
  "tenant_id": "tenant-uuid",       // 多租户 RLS 关键
  "roles": ["admin", "developer"],   // RBAC
  "permissions": ["agent:read", "session:create"],  // ABAC 细粒度（M1+）
  "exp": 1234567890
}
```

### 2.3 服务间 — mTLS

- M0：静态 X.509 证书，CA 私钥放 阿里云 KMS；7 天有效，手工轮换
- M1+：迁移到 cert-manager + SPIRE，SVID 自动轮换，1 小时 TTL
- 客户端证书 Subject CN = service name；Orchestrator 用 SAN 标识 tenant context

### 2.4 API Key — Keycloak Service Account

外部业务系统的 API Key 不直接做 bearer。流程：
1. 在 Keycloak 注册 service account 客户端（per 业务系统 / per 集成）
2. 业务系统持有 `client_id + client_secret`
3. Client credentials grant → 换取 JWT access_token（5 min TTL）
4. JWT 作为 bearer 调 Expert Work API

好处：API Key 失效 / 轮换 / 限流 全部由 Keycloak 管理；不需要自建 API Key 表。

---

## 3. 后果

### 正向

- **标准协议**：OIDC + JWT 是行业标准，未来接公司 SSO（如有）只需改 IdP issuer，应用代码不动
- **零外部 SaaS 依赖**：完全自托管，数据不出境
- **多租户原生支持**：`tenant_id` claim 一处声明全链路用
- **细粒度权限**：M1+ 直接用 Keycloak Authorization Services 做 ABAC
- **服务间 + API Key + 用户认证** 统一在 Keycloak，1 个 IdP 管所有 identity

### 负向 / 风险

- **Keycloak 自托管运维成本**：DB 备份 / HA / 升级；M0 单机起步可接受，M1 上 HA
- **JWT 撤销难**：标准 JWT 是无状态，撤销靠短 TTL + refresh token revoke。M0 风险可控（1h 窗口）
- **mTLS 静态证书风险**：M0 妥协方案；M1 cert-manager 是必修
- **Keycloak 学习成本**：单人维护需熟悉 Realm / Client / Role / Group 概念

### 验证手段（Stream C.1 Verification 之一）

- [ ] 未持 JWT → /agents 返回 401
- [ ] JWT 过期 → 401
- [ ] tenant_id 不匹配 → Postgres RLS 拒绝
- [ ] mTLS 握手失败 → 服务间调用被拒
- [ ] API Key 撤销 → access_token 不再续签

---

## 4. 备选方案

| 方案 | 否决理由 |
|------|---------|
| **Okta / Auth0** | 海外 SaaS；国内部署不可行；商业 license 成本 |
| **Authentik** | 比 Keycloak 新，生态薄；国内社区资料少 |
| **自建 JWT（不用 IdP）** | 失去 SSO / refresh token / 标准协议的好处；M1+ 接公司 SSO 时痛苦 |
| **OAuth2 Proxy + LDAP** | 仅适合简单场景；不支持 service account、API Key、ABAC |
| **Cognito / 阿里云 IDaaS** | 锁定云厂商；多云 / 跨境部署受限 |

---

## 5. 落地引用

- **Stream C.1** OIDC 认证落地：`services/control-plane/src/control_plane/auth/`
- **Stream C.2** mTLS 服务间：`environments/{dev,staging,prod}.yaml` 含 cert paths；M1 cert-manager 上 K8s
- **Stream C.3** API Key（Keycloak Service Account）注册流程：管理脚本放 `tools/`
- **Stream C.4** JWT `tenant_id` claim 驱动 Postgres RLS
- **Phase 0.2** 已声明 `environments/dev.yaml` 中的 `auth.oidc.issuer` 字段
- **Phase 0.3** docker-compose dev.yml 应包含 Keycloak 容器（Stream A 实施时落地）
