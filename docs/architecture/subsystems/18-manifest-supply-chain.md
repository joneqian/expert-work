# 18 Manifest 供应链（Supply Chain）

## 1. 职责 & 边界

**是什么**：保证从 manifest 编辑、PR 评审、签名、加载到 sandbox 镜像运行的全链路 **完整性 + 不可抵赖性 + 可追溯性**。任何未经授权的 manifest 改动或 sandbox 镜像替换都必须在 **加载前** 被拒绝。

**不是什么**：
- 不负责 manifest 的语义校验（schema、tool 引用、subagent 循环）→ [02 AGENT MANIFEST](../02-AGENT-MANIFEST.md) 静态校验阶段
- 不负责凭证管理 → [11 Credential Proxy](./11-credential-proxy.md)
- 不负责 sandbox 运行时隔离 → [14 Sandbox Pool](./14-sandbox-pool.md)
- 不负责审计日志写入 → [17 Audit Log](./17-audit-log.md)（本子系统**触发**审计事件）

**核心问题**：
- 恶意 PR 注入：研发账号被攻破 → 改 `manifest.yaml` 加恶意 tool / 改 sandbox 网络 allowlist → 流量发往攻击者
- Rogue admin：单个管理员私自改 prod manifest，无四眼审查
- 镜像替换：CI 产物到镜像 registry 的传输被中间人替换
- 供应链投毒：`requirements_from` 拉取的依赖被劫持（typosquatting）

---

## 2. 上下游依赖

| 上下游 | 关系 |
|--------|------|
| [02 AGENT MANIFEST](../02-AGENT-MANIFEST.md) | 提供 manifest YAML；本子系统对其规范化后做哈希签名 |
| [14 Sandbox Pool](./14-sandbox-pool.md) | 加载镜像前调本子系统 `verify_image_signature()`；CI 阻断未签名镜像 |
| [15 AuthN/AuthZ](./15-authn-authz.md) | admin 触发签名时校验 `manifest.sign` 权限 |
| [17 Audit Log](./17-audit-log.md) | 签名 / 验签失败 / 镜像 verify 全部写审计 |
| [20 Observability](./20-observability.md) | 暴露 `manifest_signature_verify_total{result}` 等指标 |
| Vault | 存放 cosign 私钥；admin 调签名服务时由 Vault 动态颁发短 TTL token |
| 镜像 Registry（Harbor / ACR） | 镜像签名落地处；M2 启用 cosign verify-blob policy |

---

## 3. 数据模型 / 状态机

### 3.1 Postgres DDL

```sql
-- 核心：manifest 主表（agent 定义的规范化存储；M0 起建表）
CREATE TABLE manifest (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id   TEXT NOT NULL,
    name        TEXT NOT NULL,
    version     TEXT NOT NULL,
    body_yaml   TEXT NOT NULL,
    body_hash   TEXT NOT NULL,                 -- SHA256(canonicalized_yaml)，与 manifest_signature.manifest_hash 同口径
    status      TEXT NOT NULL,                 -- draft / signed / promoted / revoked
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name, version)
);
CREATE INDEX idx_manifest_tenant_name ON manifest (tenant_id, name, version);
CREATE INDEX idx_manifest_status ON manifest (tenant_id, status);

-- 核心：manifest 签名记录（与 manifest 表 1:N，每次重签新增一行）
-- 主键改为 (tenant_id, name, version) 三元组防跨租户签名复用（D2）
CREATE TABLE manifest_signature (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       TEXT      NOT NULL,
    name            TEXT      NOT NULL,        -- agent name
    version         TEXT      NOT NULL,        -- manifest version
    manifest_hash   TEXT      NOT NULL,        -- SHA256(规范化 YAML)，对齐 manifest.body_hash
    signer_id       TEXT      NOT NULL,        -- admin user id（actor_id 统一 TEXT）
    signer_role     TEXT      NOT NULL,        -- 'primary' | 'co_signer' (M2 双签)
    signature       BYTEA     NOT NULL,        -- cosign 输出
    cert_chain      TEXT,                      -- keyless 模式留空
    signed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at      TIMESTAMPTZ,               -- 撤销则非空
    revoke_reason   TEXT,
    UNIQUE (tenant_id, name, version, manifest_hash, signer_id),    -- 防跨租户签名复用
    FOREIGN KEY (tenant_id, name, version) REFERENCES manifest (tenant_id, name, version)
);
CREATE INDEX idx_msig_manifest ON manifest_signature (tenant_id, name, version, signed_at DESC);

-- 镜像签名 attestation（sandbox base image / image_build 产物）
CREATE TABLE image_attestation (
    id              BIGSERIAL PRIMARY KEY,
    image_ref       TEXT      NOT NULL,        -- registry/repo@sha256:...
    digest          TEXT      NOT NULL,
    sbom_url        TEXT,                      -- SBOM 存储 URL（CycloneDX）
    slsa_level      SMALLINT  NOT NULL DEFAULT 0, -- 0/1/2/3
    provenance      JSONB,                     -- in-toto attestation
    signer_id       TEXT      NOT NULL,
    signed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (image_ref, digest)
);

-- 加载策略缓存（避免每次加载查 Vault）
CREATE TABLE signature_policy (
    env             TEXT PRIMARY KEY,          -- 'dev' | 'staging' | 'prod'
    require_signature BOOLEAN NOT NULL,
    require_co_sign  BOOLEAN NOT NULL DEFAULT false,
    trusted_signers  TEXT[]  NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 3.2 状态机：manifest 从 PR 到生效

```
[draft]                        ← 研发本地编辑
  │ git push + PR
  ▼
[pr_open]                      ← CI 自动跑：lint + diff comment
  │ 2 名 reviewer approve
  ▼
[ci_passed]                    ← 触发签名 workflow（admin 手动 dispatch）
  │ admin 在 Admin UI 点击「签名」（密钥不下发个人）
  ▼
[signed]                       ← 写 manifest_signature 表
  │ merge to main
  ▼
[merged]                       ← Control Plane 拉取 + verify 签名
  │ verify ok
  ▼
[active]                       ← 注册到 Registry，新 session 走新版本
  │ 撤销
  ▼
[revoked]                      ← 写 revoked_at；旧 session 跑完即停
```

**关键约束**：未到 `signed` 不能进入 prod；prod 环境 `HELIX_REQUIRE_SIGNATURE=true` 强制开启。

---

## 4. 关键接口

### 4.1 规范化 + 签名（admin 操作）

```python
class ManifestSigner:
    async def canonicalize(self, raw_yaml: str) -> bytes:
        """key 排序、去注释、去 trailing whitespace、UTF-8 NFC，返回规范化字节"""

    async def hash(self, canonical: bytes) -> str:
        """sha256(canonical)"""

    async def sign(
        self,
        tenant_id: str,
        name: str,
        version: str,
        canonical: bytes,
        signer_id: str,
    ) -> ManifestSignatureRecord:
        """
        1. 调 Vault 拉短 TTL（5min）的 cosign signing token
        2. cosign sign-blob --key vault://... → signature
        3. 写 manifest_signature 表（主键 tenant_id + name + version + hash + signer_id）
        4. 触发 audit_log
        """
```

### 4.2 加载时验签（Control Plane 启动 / hot reload）

```python
class ManifestVerifier:
    async def verify(self, tenant_id: str, name: str, version: str, raw_yaml: str) -> VerifyResult:
        """
        - 计算 hash
        - 查 manifest_signature where tenant_id=? AND name=? AND version=? AND manifest_hash=? AND revoked_at IS NULL
          （主键三元组 + tenant 前缀，防跨租户签名复用 — D2）
        - prod 环境必须有 ≥1 条有效签名（M2 双签则 ≥2 条不同 signer）
        - signer_id 必须在 signature_policy.trusted_signers
        - cosign verify-blob --key <pubkey> --signature <sig>
        - 失败 → 拒绝加载 + audit + alert
        """
```

### 4.3 镜像 attestation 校验（Sandbox Pool 拉取镜像前）

```python
class ImageAttester:
    async def verify_image(self, image_ref: str) -> bool:
        """
        - cosign verify <image_ref> --certificate-identity <admin>
        - 校验 SBOM 中无 CRITICAL CVE（Trivy DB）
        - 校验 SLSA provenance level ≥ 配置阈值
        """
```

---

## 5. 算法 / 关键决策

### 5.1 规范化算法（**必须可复现**）

输入相同 YAML，任意机器跑出相同 hash。规则：
1. 解析为 dict（PyYAML safe_load）
2. 递归对所有 dict 按 key 字典序排序
3. 移除注释、空行、trailing whitespace
4. 序列化为 JSON（`sort_keys=True, separators=(',', ':')`）
5. UTF-8 NFC 标准化
6. SHA256

**为什么不直接对 YAML 文本签名**：YAML 允许等价多种写法（缩进风格、引号风格、注释），文本签名脆弱。

### 5.2 签名密钥管理

| 维度 | 选择 |
|------|------|
| 算法 | **ECDSA P-256**（cosign 默认，签名快） |
| 私钥存储 | **Vault Transit engine**（密钥不导出；admin 调 `transit/sign/...`） |
| 公钥分发 | Control Plane 启动时从 Vault 拉公钥；本地缓存 5min |
| 旋转 | 季度旋转；旧 key 标 `revoked_at`，仍可验证历史签名 |

**关键决策：admin 不接触私钥**——通过 Web UI 触发签名服务，签名服务持短 TTL（5min）的 Vault token 调 Transit engine。即使 admin 笔记本被攻破也拿不到私钥。

### 5.3 CI Gate 流水线

```
PR 修改 agents/*/manifest.yaml
   │
   ▼
[1. Lint]    schema / secret-ref 是否在 Vault / quota / subagent 循环
   │
   ▼
[2. Diff Comment]  机器人在 PR 留 diff（高亮 sandbox/network/tool 变更）
   │
   ▼
[3. Required Reviewers]  CODEOWNERS：sandbox/network 改动需 security-team
   │
   ▼
[4. Approve + Merge to staging branch]
   │
   ▼
[5. Manual Dispatch: sign-workflow]   admin 在 Admin UI 触发
   │
   ▼
[6. Sign Service]  Vault transit/sign → 写 manifest_signature
   │
   ▼
[7. Auto-merge to main]
```

### 5.4 双签（M2）

高敏感 manifest（`compliance_pack=hipaa` 或 `isolation_level=dedicated_node`）需要 **2 个不同 admin 各自签 1 次**。`signature_policy.require_co_sign=true` 时 verify 阶段强制查 ≥2 条 `signer_role IN ('primary','co_signer')` 的有效签名。

---

## 6. 失败模式 & 缓解

| 失败模式 | 影响 | 缓解 |
|---------|------|------|
| Vault 不可达，admin 想签 | 签名阻塞 | 签名服务降级：返回 503，明确"Vault 不可达，重试"；不允许绕过 |
| Vault 不可达，Control Plane 想 verify | 加载阻塞 | Control Plane 缓存公钥 5min；降级期内可继续 verify 已加载 manifest，新 manifest 排队等待 |
| 签名通过但被 revoke | 已 active session 仍在跑 | `revoked_at` 写入后，触发 hot reload 阻断新 session；旧 session 通过超时自然结束 |
| Hash 算法升级（SHA256 → SHA3） | 历史签名失效 | 签名记录里存算法版本字段；旧版本继续 SHA256 verify，新签名走新算法 |
| 恶意 PR 加 backdoor tool 但绕过 reviewer | sandbox 通信外泄 | CI lint 强制：`tools[].http.url` / `network.allowlist` 改动 → 触发 security-team 必审 |
| 签名服务被攻破，伪造签名 | 任意恶意 manifest 上线 | M2 双签；签名服务的 Vault token TTL 5min；Vault audit 日志独立外发 SIEM |
| 镜像 registry 中间人替换 | sandbox 跑到恶意镜像 | sandbox image 强 cosign 签名 + digest pin（不允许 `:latest`）|
| `requirements_from` 拉到投毒包 | sandbox 内执行恶意代码 | 私有 PyPI 镜像；CI lint 校验包名（typo detection）；image_build 产物本身要签名 |
| 时钟漂移导致 `signed_at` 错误 | 审计混乱 | 服务节点 NTP；`signed_at` 由签名服务统一打点，不取客户端时间 |
| 签名表损坏 | 全量 manifest 不可加载 | 双写：签名记录同时写 Postgres + 对象存储（WORM bucket）；恢复脚本可从 WORM 重建 |

---

## 7. 可观测性

> 日志完整字段遵循 [20 § 5.3](./20-observability.md)。

**Metrics**（OTel，统一 `helix_*` 前缀，与 20 命名规范对齐）：
- `helix_manifest_sign_total{tenant, signer_id, result}` — 签名调用计数
- `helix_manifest_verify_total{tenant, env, result}` — 验签调用计数
- `helix_manifest_verify_duration_seconds` — 验签延迟（影响 hot reload P95）
- `helix_manifest_signature_age_seconds{tenant, manifest_id}` — 当前 active manifest 签名距今秒数（监控陈旧）
- `helix_image_attestation_verify_total{tenant, result}` — 镜像验签
- `helix_manifest_revoked_total{tenant, reason}` — 撤销计数

**Spans**（统一 `helix.*` 前缀）：
- `helix.manifest.canonicalize`、`helix.manifest.sign`、`helix.manifest.verify`、`helix.image.verify`

**Span attrs**（C8 一致性，含 `agent_version`）：
- `helix.manifest.canonicalize`：tenant, manifest_id, agent_name, agent_version, hash
- `helix.manifest.sign`：tenant, manifest_id, agent_name, agent_version, signer_id, signer_role
- `helix.manifest.verify`：tenant, manifest_id, agent_name, agent_version, env, result, reason
- `helix.image.verify`：tenant, image_ref, agent_name, agent_version, slsa_level, result

> **早绑定例外**：当 manifest 操作发生在 agent 上下文外（如 admin 在 Admin UI 直接签名 / Control Plane 启动期 verify），无 `agent_name / agent_version`，这两个 attr 可缺省；其他 attrs 仍必填。

**Logs**（结构化）：
- 每次 verify 失败 → `level=ERROR`，字段 `tenant, manifest_id, expected_hash, actual_hash, reason`（完整字段集见 [20 § 5.3](./20-observability.md)）
- 每次签名 → 同步写 `audit_log`（actor=signer_id, action='manifest:sign', resource_type='manifest', resource_id=manifest_id）

**告警**（P0）：
- `helix_manifest_verify_total{result="fail"}` > 0 in 5min → 立即 P0
- `helix_manifest_signature_age_seconds` > 90 days → P2 提醒

---

## 8. 安全考虑

**威胁模型**（按 STRIDE）：

| 威胁 | 攻击路径 | 防御 |
|------|---------|------|
| **Spoofing** | 攻击者冒充 admin 签 manifest | Vault token 短 TTL + 服务端绑定 admin 会话 + audit 留痕 |
| **Tampering** | PR merge 后篡改 main 分支 manifest 文件 | 加载前 verify hash；hash 不在 manifest_signature 表 → 拒绝 |
| **Repudiation** | admin 否认签过 | `audit_log` WORM 存储 + Vault 独立审计日志 |
| **Information Disclosure** | 攻击者读 manifest_signature 表得签名 | 签名是公开信息无害；私钥在 Vault Transit 不可导出 |
| **Denial of Service** | 大量伪造签名请求耗尽 Vault | 签名 endpoint 限流（per-admin 1/min）；本地 verify 缓存命中率 > 95% |
| **Elevation of Privilege** | 普通研发拿到签名权限 | RBAC：`manifest.sign` 只授给 `role=admin`；Admin UI 签名按钮二次校验 |

**红队验收用例**（M0 必跑）：
1. 直接修改 `agents/foo/manifest.yaml` 后重启 Control Plane → 拒绝加载
2. 篡改 `manifest_signature.signature` 字段 → cosign verify 失败 → 拒绝加载
3. 用过期（rotated）公钥重签 → trusted_signers 不在白名单 → 拒绝
4. 改 `signature_policy.require_signature=false` → 该操作本身要审计 + 双人确认

**密钥泄漏处置**：
- Vault Transit 私钥泄漏（极小概率） → 立即 rekey；所有 manifest 强制重签；旧公钥从 trusted_signers 移除
- admin 签名 token 泄漏 → 由于 5min TTL，影响有限；audit 回查泄漏窗口内所有签名

---

## 9. M0 / M1 / M2 演进

### M0（4-6 周）— 内审 + CI Lint

**交付**：
- `manifest_signature` 表 schema（即使先不签也建好表）
- CI lint：schema / secret-ref / quota / subagent 循环（4 项）
- CODEOWNERS + PR diff bot
- 2 人 reviewer 强制（GitHub branch protection）
- `HELIX_REQUIRE_SIGNATURE=false`（dev/staging）

**不做**：cosign、镜像签名、SLSA、双签

### M1（6-8 周）— cosign 单签

**交付**：
- Vault Transit engine 部署 + ECDSA P-256 密钥
- 签名服务（FastAPI 端点：`POST /v1/manifests/{id}/sign`）
- Admin UI「签名」按钮 + 二次校验
- Control Plane 加载时 verify 签名
- prod 环境 `HELIX_REQUIRE_SIGNATURE=true`
- sandbox base image cosign 签名
- 镜像扫描（Trivy）CI gate（CRITICAL 阻断）

### M2（8-10 周）— SLSA L3 + 双签 + Attestation

**交付**：
- Sandbox base image SLSA L3（GitHub Actions provenance + cosign keyless）
- in-toto attestation 上链（manifest_id ↔ image digest ↔ commit sha 不可抵赖）
- 双签策略（高敏感 manifest）
- 镜像 attestation 校验集成到 Sandbox Pool 拉取流程
- 季度密钥旋转自动化

---

## 10. 开放问题

1. **签名是否要做 keyless（Sigstore Fulcio）？** 优势：无需管理长期私钥，OIDC 身份直签；劣势：依赖外部 Sigstore 网络（国内访问不稳）。**当前倾向**：M1 用 Vault Transit（自托管），M2 评估 keyless 切换。

2. **签名粒度：单 manifest vs manifest bundle？** 一个 PR 改 5 个 agent，要 5 次签 vs 1 次 bundle 签？**当前倾向**：单 manifest，便于撤销粒度精细。

3. **如何处理 `extends` 模板包？** 模板更新但业务 manifest 未重签是否影响？**当前倾向**：模板独立签名 + 版本 pin，业务 manifest 在 hash 计算时**展开后**再 hash（避免间接污染）。

4. **跨集群签名共享**（M3）：多 region 部署时，signature 表怎么同步？**待 M2 末确认**：跟随 Postgres 多 region 复制策略，参见 [22 Disaster Recovery](./22-disaster-recovery.md)。

5. **撤销列表分发延迟**：M1 单点 Postgres 撤销 → 多节点 Control Plane 何时感知？**当前倾向**：Postgres LISTEN/NOTIFY + 本地缓存 30s TTL。
