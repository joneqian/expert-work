# 21 Network Policy（沙盒网络策略）

## 1. 职责 & 边界

**是什么**：定义并强制 sandbox 容器的出站网络行为——**默认拒绝一切出站**，只放行 manifest 显式声明的 allowlist；同时强制阻断元数据服务、内网服务、未受控 DNS，并把所有出站 HTTP 经 [11 Credential Proxy](./11-credential-proxy.md) 收敛。

**调用方拓扑澄清**：本子系统**仅约束 sandbox 容器内业务代码的 outbound 流量**。
- [12 MCP Gateway](./12-mcp-gateway.md) 由 **orchestrator 进程**调用（非 sandbox 内业务代码 outbound）；
  `sandbox → MCP gateway` 这条路径不存在；`orchestrator → MCP gateway` 走内网 RFC1918 但属**控制面流量**，不在 21 范围。
- [22 Disaster Recovery](./22-disaster-recovery.md) 备份 worker 不属 sandbox 网络域；其 outbound（S3/KMS）走**控制面 egress allowlist**（独立策略，由 control plane 网关层实施），与本子系统无重叠。
- [25 HITL](./25-hitl.md) 回调（Slack/邮件 webhook）流量 = control plane **入站**，不影响本子系统；由 25 自有 webhook 一次性 token 防护。

**不是什么**：
- 不负责入站流量（Sandbox 本身无监听端口；Control Plane 入站是 [15 AuthN/AuthZ](./15-authn-authz.md) 范围）
- 不负责 LLM provider 选路 → [10 LLM Gateway](./10-llm-gateway.md)
- 不负责凭证注入与签名 → [11 Credential Proxy](./11-credential-proxy.md)
- 不负责 sandbox 进程隔离 → [14 Sandbox Pool](./14-sandbox-pool.md)
- 不负责控制面到外部服务（S3 / KMS / Slack webhook）的 egress → 由 control plane 网关层独立策略实施

**核心问题**：
- **SSRF**：LLM 生成 `curl http://169.254.169.254/...` → 拿到云元数据 → 拿到 IAM 凭证 → 横向移动
- **DNS 劫持**：sandbox 用主机 `/etc/resolv.conf` → 被 manifest 注入恶意 DNS → allowlist 域名解析到攻击者 IP
- **内网越权**：sandbox 直连 Postgres / Vault / 其他 tenant 服务
- **Egress 隐通道**：通过 `*.attacker.com` 走 manifest 漏洞放行的域名建立反向通道

---

## 2. 上下游依赖

| 上下游 | 关系 |
|--------|------|
| [02 AGENT MANIFEST](../02-AGENT-MANIFEST.md) | `sandbox.network.egress` / `allowlist` 字段是声明源 |
| [11 Credential Proxy](./11-credential-proxy.md) | sandbox 唯一信任的出站对象；DNS 解析也走 proxy |
| [14 Sandbox Pool](./14-sandbox-pool.md) | sandbox 启动时本子系统注入 iptables / Envoy sidecar |
| [17 Audit Log](./17-audit-log.md) | 所有被拒绝的连接尝试写审计 |
| [18 Manifest 供应链](./18-manifest-supply-chain.md) | manifest 改 allowlist 触发签名要求 |
| [20 Observability](./20-observability.md) | 暴露 `egress_blocked_total`、`egress_allowed_total` |

---

## 3. 数据模型 / 状态机

### 3.1 Postgres DDL

```sql
-- 强制黑名单（不可被 manifest 覆盖；运维通过 Admin UI 维护）
CREATE TABLE network_blacklist (
    id          BIGSERIAL PRIMARY KEY,
    cidr        CIDR        NOT NULL,
    description TEXT        NOT NULL,
    is_meta     BOOLEAN     NOT NULL DEFAULT false,  -- 元数据服务标记
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (cidr)
);

-- 内置不可移除项（每次启动 reconcile，确保以下记录存在）
INSERT INTO network_blacklist (cidr, description, is_meta) VALUES
    ('169.254.169.254/32', 'AWS/GCP IMDS',       true),
    ('100.100.100.200/32', 'Aliyun metadata',    true),
    ('192.0.0.192/32',     'OpenStack metadata', true),
    ('169.254.0.0/16',     'IPv4 link-local',    false),
    ('fe80::/10',          'IPv6 link-local',    false),
    ('10.0.0.0/8',         'RFC1918 private',    false),
    ('172.16.0.0/12',      'RFC1918 private',    false),
    ('192.168.0.0/16',     'RFC1918 private',    false),
    ('127.0.0.0/8',        'Loopback',           false);

-- Egress 拒绝事件采样表（高频告警来源；保留 30 天）
CREATE TABLE egress_denial (
    id           BIGSERIAL PRIMARY KEY,
    tenant_id    TEXT        NOT NULL,
    sandbox_id   TEXT        NOT NULL,
    session_id   TEXT,
    target_host  TEXT,
    target_ip    INET,
    target_port  INT,
    proto        TEXT,
    reason       TEXT        NOT NULL,         -- 'blacklist'|'not_allowlist'|'protocol'|'dns_invalid'
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
) PARTITION BY RANGE (created_at);
CREATE INDEX idx_egress_denial_tenant ON egress_denial (tenant_id, created_at DESC);
```

### 3.2 决策状态机

```
sandbox 发起 connect(target_host, port, proto)
   │
   ▼
[1. DNS 解析]  受控 DNS（unbound）→ IP
   │ 解析失败/返回 RFC1918 IP（除非 allowlist 显式包含该 hostname）
   ├──────────► [DENY: dns_invalid]
   │
   ▼
[2. 黑名单匹配]  IP ∈ network_blacklist?
   ├──────────► [DENY: blacklist]
   │
   ▼
[3. 协议白名单]  proto ∈ {HTTPS, gRPC-TLS}?（manifest 可放宽）
   ├──────────► [DENY: protocol]
   │
   ▼
[4. Allowlist]  hostname ∈ manifest.sandbox.network.allowlist?
   ├──────────► [DENY: not_allowlist]
   │
   ▼
[5. Credential Proxy]  egress=proxy → 重写到 proxy
   │
   ▼
[ALLOW]
```

**关键约束**：每一步独立可观测；DENY 必写 `egress_denial` 采样（限流 1/sec/sandbox 防爆）。

---

## 4. 关键接口

### 4.1 Manifest 字段（声明源）

```yaml
sandbox:
  network:
    egress: proxy            # proxy | direct (后者仅限 dev)
    protocols: [https]       # 默认 [https]，可加 grpc/http(慎用)
    allowlist:
      - "api.anthropic.com"
      - "*.internal.example.com"
    # 不允许 "*"；lint 阶段拒绝
```

### 4.2 启动期注入接口

```python
class NetworkPolicyApplier:
    async def apply(self, sandbox_id: str, spec: SandboxSpec, tenant: str) -> None:
        """
        M0：iptables OUTPUT chain（runc hook）
        M1：Envoy sidecar + listener 配置下发
        步骤：
          1. 解析 allowlist hostname → 调受控 DNS 拿 A/AAAA（带 TTL 缓存）
          2. 与 network_blacklist 取交集 → 若有交集直接拒绝启动
          3. 生成规则；落入 sandbox netns
          4. 启动 sandbox 进程
        """
```

### 4.3 运行时拒绝事件回流

```python
class DenialReporter:
    async def record(self, event: DenialEvent) -> None:
        """
        - 限流：每 sandbox + reason 维度 1/sec
        - 落表 egress_denial（分区）
        - reason='blacklist' AND is_meta=true → 立即 P0 告警
        """
```

---

## 5. 算法 / 关键决策

### 5.1 默认拒绝 + 强制黑名单的执行点

| 阶段 | M0 实现 | M1+ 实现 |
|------|---------|----------|
| 网络命名空间隔离 | Docker user-defined bridge per sandbox | 同 |
| 出站过滤 | **iptables OUTPUT** chain（DROP 默认 + 白名单 ACCEPT） | **Envoy egress sidecar**（统一日志 / TLS / ALPN 限制） |
| DNS | sandbox `/etc/resolv.conf` 指向 unbound（容器内 IP） | 同；Envoy 走 strict_dns cluster |
| 协议限制 | iptables L4 + 应用层无 | Envoy listener 限制 ALPN（h2 / http/1.1） |
| 黑名单 reconcile | 启动时一次性写规则 | xDS 动态推送 |

**关键决策**：**强制黑名单不可被 manifest 覆盖**——即便 manifest 写了 `allowlist: ["169.254.169.254"]`，apply 阶段也会拒绝启动 sandbox 并告警。

### 5.2 RFC1918 例外处理

某些场景必须连内网（如内部 MCP server `mcp.internal`）。规则：
- manifest 必须以 **hostname** 写入 allowlist，不允许直接写 IP
- DNS 解析返回 RFC1918 IP 时：仅当该 hostname 显式在 allowlist 中才放行
- `*.internal` 通配仍允许，但每个具体子域解析后做二次校验

**显式 allowlist 必含项（系统注入，非 manifest 声明）**

| 端点 | 用途 | 备注 |
|------|------|------|
| `credential-proxy.internal:443` | 唯一允许的 outbound（M0 显式代理） | 见 § 5.4 |
| `unbound.internal:53` (容器内 IP) | 受控 DNS | 见 § 5.3 |
| sandbox supervisor unix domain socket | sandbox 内业务代码 → orchestrator 的 memory client 路径（[13 Memory Store](./13-memory-store.md) `MemoryClient` 走 supervisor UDS 转发到 orchestrator，不直连 memory store） | 不占 IP；通过 socket 文件挂载（只读路径） |

### 5.3 DNS 防劫持（受控 DNS）

- sandbox `/etc/resolv.conf` **只读** 挂载，指向受控 unbound 实例
- unbound 配置：
  - 仅向白名单上游（公司 DNS + 1.1.1.1）查询
  - **DNS-over-TLS** 上游
  - 拦截可疑响应：上游返回的 IP 与请求 hostname 的 ASN/CIDR 期望不符 → 丢弃
  - 拒绝返回 `169.254.169.254` 等元数据 IP（响应过滤）
- sandbox 内 `cat /etc/resolv.conf` 看到的是容器视角的 DNS server IP，且**不可修改**

### 5.4 SSRF 防护：所有 HTTP 走 Credential Proxy

**M0：显式代理（与 [11 Credential Proxy § 4.1 / § 9](./11-credential-proxy.md) 一致）**

- iptables 规则：sandbox 出站**仅放行**到 `credential-proxy.internal:443`，其余出站默认 DROP
- sandbox 内业务代码必须**显式 POST `/forward`** 给 Credential Proxy（含 `X-Expert-Work-Upstream` 头声明上游），不再做透明 REDIRECT
- proxy 再做：
  - host header 校验（与 `X-Expert-Work-Upstream` 比对）
  - URL 解析后的 IP 二次校验（防 DNS rebinding）
  - 凭证注入
- sandbox 内 `curl https://api.anthropic.com` 不会"自动被代理"——必须由 SDK / 业务代码主动走 proxy；不过 proxy 的请求一律 fail-closed
- 即使 manifest 写了 `egress: direct`，prod 环境也强制覆盖为 `proxy`

**M1+：透明代理（升级 Envoy egress sidecar）**

- 替换 iptables 兜底，在 Envoy listener 做透明 L7 代理
- sandbox 内业务代码无需显式适配，原 `curl/requests` 调用自动经 Envoy 转发到 Credential Proxy
- 当且仅当 M1 Envoy 上线后才切换；M0 期间保持显式代理协议不变

### 5.5 验收测试（M0 必跑，CI 阻断）

```bash
# 在 sandbox 内执行，全部必须 timeout/refused
curl -m 5 https://api.anthropic.com                     # 直连必须 connection refused（M0 仅放行 credential-proxy.internal:443）
curl -m 5 http://169.254.169.254/latest/meta-data/      # AWS IMDS
curl -m 5 http://100.100.100.200/                       # Aliyun
curl -m 5 http://host.docker.internal/                  # Docker host
nc -w 3 10.0.0.1 22                                     # RFC1918
nslookup attacker.com 8.8.8.8                           # 绕过受控 DNS
echo "nameserver 8.8.8.8" > /etc/resolv.conf            # 改 DNS（应失败：只读）

# 正向：仅显式 POST /forward 到 Credential Proxy 应通过
curl -m 5 -X POST https://credential-proxy.internal/forward \
     -H "X-Expert-Work-Upstream: api.anthropic.com" \
     -H "Content-Type: application/json" -d '{...}'     # 必须 200
```

---

## 6. 失败模式 & 缓解

| 失败模式 | 影响 | 缓解 |
|---------|------|------|
| LLM 生成访问 IMDS 的代码 | 攻击者拿云凭证 | iptables 黑名单 + Envoy listener；记录到 `egress_denial`（reason=blacklist, is_meta=true）→ P0 告警 |
| DNS rebinding：先返回公网 IP，proxy 命中 allowlist；后返回内网 IP | 内网越权 | 受控 DNS 拒绝返回 RFC1918；Credential Proxy connect 后再次解析并校验 IP |
| manifest allowlist 写 `*.com` | 任意外网放行 | lint 阶段拒绝顶级通配；最少 2 段（`*.example.com` 可，`*.com` 不可）|
| iptables 规则 race（先启进程后注规则） | 启动期短暂裸奔 | 用 OCI runtime hook **prestart**，先注规则再 unfreeze 进程 |
| Envoy sidecar 崩溃 | sandbox 出站失败 / 旁路 | sandbox 与 Envoy 共享 netns；Envoy 死 → iptables 兜底 DROP（fail-closed） |
| 受控 DNS 不可达 | 全部出站失败 | DNS 高可用（≥2 实例）+ sandbox 内可缓存最近 5min 解析；连 5min 不可达则 sandbox 自我终止 |
| allowlist 解析返回的 IP 漂移（CDN） | 旧 IP 放行新 IP 阻断 | 每分钟 reconcile 解析；TTL 严格遵循；Envoy 用 strict_dns cluster 自动重解析 |
| egress_denial 表写入压力 | DB 抖动 | 每 sandbox+reason 维度 1/sec 限流；批量 INSERT；分区按 day RANGE |
| 内部 MCP server 频繁加 IP | 维护负担 | manifest 用 `*.internal` 通配 + 受控 DNS 仅解析公司内网域 |
| 攻击者用 manifest `protocols: [http]` 绕过 TLS | 凭证明文 | lint 阶段：`protocols=[http]` 必须显式 `allow_plaintext: true` 且触发 security-team review |

---

## 7. 可观测性

> 命名规范、日志必填字段、span attrs 强制约定遵循 [20 Observability § 5.1 / § 5.3](./20-observability.md)；
> 本节只列本子系统特有的 metric / span / 日志事件。

**Metrics**（OTel；强制 `expert_work_*` 前缀）：
- `expert_work_network_egress_total{tenant, agent, decision}` — decision ∈ {allowed, denied}
- `expert_work_network_egress_denied_total{reason}` — reason ∈ {blacklist, not_allowlist, protocol, dns_invalid}
- `expert_work_network_egress_meta_attempt_total{tenant}` — 尝试访问元数据服务（**任意 > 0 都是 P0 告警**；与 [20 § 5.2](./20-observability.md) 一致）
- `expert_work_network_dns_query_total{result}` — 受控 DNS 查询计数
- `expert_work_network_dns_response_filtered_total{reason}` — 被过滤的 DNS 响应（rebinding / metadata IP）
- `expert_work_network_egress_proxy_bypass_attempt_total` — 试图绕过 Credential Proxy（应恒为 0）

**Spans**（强制 `expert_work.network_policy.*` 前缀）：
- `expert_work.network_policy.apply`、`expert_work.network_policy.dns_resolve`、`expert_work.network_policy.egress_connect`
- 必填 attrs（遵循 [20 § 5.1](./20-observability.md)）：`tenant`, `agent`, `agent_version`, `session_id`
- **注**：本子系统部分 network 操作（如 `apply` / `dns_resolve` 启动期）可能在 agent 上下文外发生；该场景下 `agent` / `agent_version` / `session_id` 可为 null，需在日志显式标注 `agent_context_absent=true`

**Logs**（遵循 [20 § 5.3](./20-observability.md) 必填字段）：
- 所有 DENY 一律结构化（`tenant, sandbox_id, session_id, target, reason`），关联 `trace_id` / `span_id`

**Dashboard**：
- 「Top denied targets by tenant」「Metadata access attempts (24h)」「Egress allowed by allowlist hit rate」

**告警**（P0/P1）：
- P0：`expert_work_network_egress_meta_attempt_total > 0` 任意时间窗
- P0：`expert_work_network_egress_proxy_bypass_attempt_total > 0`
- P1：单 tenant 1 分钟 denied > 100（可能是 LLM 在试错或注入）

---

## 8. 安全考虑

**威胁模型**：

| 威胁 | 攻击路径 | 防御 |
|------|---------|------|
| **SSRF** | LLM 生成访问 IMDS 的代码 | iptables 黑名单（不可被 manifest 覆盖）+ Credential Proxy 二次校验 |
| **DNS rebinding** | 攻击者域名 TTL=0，先返回公网，再返回内网 | 受控 DNS 过滤 RFC1918 响应；Proxy connect 后重解析并校验 |
| **Manifest 注入** | 恶意 PR 加 `allowlist: ["*.attacker.com"]` | [18 Manifest 供应链](./18-manifest-supply-chain.md) 签名 + CODEOWNERS sandbox/network 强制 review |
| **Sidecar 旁路** | 攻击者改 `iptables -F` | sandbox 内无 root；netns 由 supervisor 管理；CAP_NET_ADMIN drop |
| **隐通道** | 通过放行域名 POST 大数据外泄 | egress 速率限制（per-sandbox bytes/sec）+ DLP 中间件（M2） |
| **元数据 IPv6** | `fd00:ec2::254` | 内置 IPv6 黑名单 + sandbox 默认禁用 IPv6（除非 manifest 显式启用） |

**纵深防御**：
1. **Layer 4**：iptables / netfilter 黑名单 + 默认 DROP
2. **Layer 7**：Envoy 强制 ALPN + host header 校验（M1+）
3. **应用层**：Credential Proxy 凭证收敛 + URL 二次校验
4. **DNS**：受控 unbound + 响应过滤
5. **审计**：每次 DENY 落表 + 元数据访问尝试立即告警
6. **运行时**：sandbox 内 CAP_NET_ADMIN/CAP_NET_RAW drop、`/proc/sys/net` 只读

---

## 9. M0 / M1 / M2 演进

### M0 — iptables + 受控 DNS + 静态 allowlist

**交付**：
- `network_blacklist` 表 + 启动 reconcile
- runc OCI hook：prestart 注入 iptables OUTPUT 规则
- 受控 unbound 实例（容器内 IP，sandbox `/etc/resolv.conf` 只读挂载）
- `egress: proxy` 强制 REDIRECT 到 Credential Proxy
- 验收测试 7 项跑通（CI 必过）
- `egress_denial` 表 + 限流写入

**不做**：Envoy sidecar、动态规则下发、bytes/sec 速率限制

### M1 — Envoy egress + 动态 xDS + DNS 防劫持加固

**交付**：
- 每 sandbox 一个 Envoy sidecar（共享 netns）
- 静态 listener + dynamic cluster（xDS 推送 allowlist）
- 强制 ALPN（h2 / http/1.1）+ TLS 1.2+
- DNS-over-TLS 上游 + ASN 异常响应过滤
- 域名 → IP 漂移自动 reconcile（每分钟）
- 拒绝事件结构化告警上 PagerDuty

### M2 — 零信任 + DLP + 跨集群

**交付**：
- 每 sandbox 一个 SPIFFE/SPIRE workload identity
- mTLS 到所有内部依赖（Vault / Postgres / MCP server）
- DLP 中间件：出站 body 扫描（PII / 凭证模式）
- 出站 bytes/sec 速率限制 + 异常基线检测
- 跨 region：每 region 独立的 receiver-side allowlist 校验
- IPv6 完整支持

---

## 10. 开放问题

1. **iptables vs nftables**：M0 用 iptables 兼容性最好；nftables 更现代但部分 distro 默认未启用。**当前倾向**：iptables 直到 M2，再统一切 nftables。

2. **Envoy sidecar 资源开销**：每 sandbox 多 ~50MB 内存；warm pool 1000 实例 = 50GB。是否值得？**当前倾向**：M1 启用，M2 评估单实例多 sandbox 共享 Envoy（牺牲少量隔离换资源）。

3. **DNS over QUIC vs DoT**：性能差异不大，DoT 工具链成熟。**当前倾向**：DoT。

4. **如何处理动态 hostname**（如 LLM 工具调用返回的 URL）：当前严格 allowlist 卡死；要不要支持「正则 allowlist」？**当前倾向**：不支持，业务通过受控 MCP server 间接访问；正则太容易写错。

5. **跨 tenant 共享 unbound 是否泄漏查询模式**：理论上有侧信道。**当前倾向**：M2 改 per-tenant unbound 实例（资源开销可控）。

6. **bytes/sec 速率限制阈值**如何设？太低影响正常上传/下载；太高难拦隐通道。**待 M2 dogfood 数据后定**。
